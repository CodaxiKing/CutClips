import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from api import db, main, studio, worker


def test_upload_not_claimable_before_complete(client, monkeypatch):
    real = db.create_job
    observed = []
    def create(*args, **kwargs):
        job_id = real(*args, **kwargs)
        observed.append(db.get_job(job_id)["status"])
        assert db.claim_next() is None
        return job_id
    monkeypatch.setattr(db, "create_job", create)
    response = client.post("/api/jobs", files={"file":("clip.mp4", b"video-data", "video/mp4")}, data={"llm_provider":"heuristic"})
    assert response.status_code == 200, response.text
    job = db.claim_next()
    assert observed == ["uploading"] and job["status"] == "running"
    assert (studio.job_dir(job["id"]) / "source/clip.mp4").read_bytes() == b"video-data"


def test_upload_empty_and_oversized_never_queued(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 3)
    for content, expected in ((b"",400),(b"1234",413)):
        response = client.post("/api/jobs", files={"file":("clip.mp4",content)}, data={"llm_provider":"heuristic"})
        assert response.status_code == expected
        assert db.claim_next() is None
    assert not list(studio.JOBS.rglob("*.part"))


@pytest.mark.parametrize("settings", [{"min_duration":90,"max_duration":20}, {"max_clips":0}, {"layout":"bad"}, {"caption_size":1000}, {"min_duration":"nan"}])
def test_creation_validation(client, settings):
    response = client.post("/api/jobs", files={"file":("clip.mp4",b"x")}, data={"llm_provider":"heuristic", **settings})
    assert response.status_code == 422
    assert db.list_jobs() == []


def test_windows_paths_and_traversal(client, completed):
    job_id, _ = completed
    assert client.get(f"/api/jobs/{job_id}/clips/original.mp4").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/source", headers={"Range":"bytes=0-99"}).status_code == 206
    for job, name in ((job_id,"../source/sample.mp4"),(job_id,"..\\source\\sample.mp4"),("..","original.mp4")):
        with pytest.raises(Exception) as exc:
            main._clip_path(job, name)
        assert exc.value.status_code == 404


def test_claim_atomic_and_heartbeat_fencing(client):
    job_id = db.create_job("sample.mp4")
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: db.claim_next(), range(4)))
    assert sum(c is not None for c in claims) == 1
    old = next(c for c in claims if c)
    assert db.heartbeat(job_id, old["token"])
    assert db.requeue_stale(180) == 0
    assert db.requeue_stale(-1) == 1
    new = db.claim_next()
    assert new["token"] != old["token"]
    assert not db.finish(job_id, {"clips":[]}, old["token"])
    assert db.get_job(job_id)["status"] == "running"


def test_pipeline_stages_are_dependency_ordered_and_atomic(client):
    job_id=db.create_job("sample.mp4")
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims=list(pool.map(lambda _:db.claim_pipeline_stage(),range(4)))
    claimed=[x for x in claims if x]
    assert len(claimed)==1 and claimed[0]["stage"]=="transcription"
    assert db.heartbeat_pipeline_stage(claimed[0])
    assert db.finish_pipeline_stage(claimed[0])
    assert db.claim_pipeline_stage()["stage"]=="analysis"


def test_edit_validation_and_duplicate(client, completed):
    job_id, _ = completed
    url = f"/api/jobs/{job_id}/editor/1"
    assert client.get(url).json()["words"]
    good = {"revision":0,"start":0,"end":2}
    for change in ({"end":99},{"end":0.1},{"words":[{"id":9999,"text":"x"}]},{"settings":{"layout":"bogus"}}):
        assert client.post(url,json={**good,**change}).status_code == 422
    assert client.post(url,json={**good,"revision":8}).status_code == 409
    assert client.post(url,json=good).status_code == 202
    assert client.post(url,json=good).status_code == 409
    assert client.delete(f"/api/jobs/{job_id}").status_code == 409


def test_render_edit_preserves_history_and_metrics(client, completed):
    job_id, directory = completed
    pub = {"revision":0,"title":"Título revisado","status":"approved"}
    assert client.put(f"/api/jobs/{job_id}/publication/1",json=pub).status_code == 200
    metric = {"date":"2020-01-01","revision":0,"views":100,"subscribers":2}
    assert client.put(f"/api/jobs/{job_id}/metrics/1",json=metric).status_code == 200
    response = client.post(f"/api/jobs/{job_id}/editor/1",json={"revision":0,"start":0.1,"end":2.1,
                          "words":[{"id":1,"text":"acerto"}],"settings":{"layout":"manual"}})
    assert response.status_code == 202
    worker.run_edit(db.claim_edit())
    job = db.get_job(job_id)
    result = job["manifest"]["clips"][0]
    assert db.edit_status(job_id)[0]["status"] == "done", db.edit_status(job_id)
    assert result["revision"] == 1 and "acerto" in result["text"]
    assert (directory / "clips/original.mp4").is_file()
    assert (directory / "clips" / result["subtitle"]).is_file()
    assert db.versions(job_id,1)[0]["publication"]["status"] == "approved"
    assert db.publications(job_id) == {}
    assert client.get("/api/analytics").json()["totals"]["views"] == 100
    assert client.post(f"/api/jobs/{job_id}/editor/1",json={"revision":0,"start":0,"end":2}).status_code == 409


def test_failed_edit_keeps_original(client, completed, monkeypatch):
    job_id, directory = completed
    client.post(f"/api/jobs/{job_id}/editor/1",json={"revision":0,"start":0,"end":2})
    def fail(*args,**kwargs): raise RuntimeError("render failed")
    monkeypatch.setattr(worker,"render_clip",fail)
    worker.run_edit(db.claim_edit())
    assert db.edit_status(job_id)[0]["status"] == "error"
    assert db.get_job(job_id)["manifest"]["clips"][0]["file"] == "original.mp4"


def test_failed_project_can_retry(client):
    job_id = db.create_job("sample.mp4")
    db.fail(job_id, "falha temporária")
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 200
    job = db.get_job(job_id)
    assert job["status"] == "queued" and job["error"] is None
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 409


def test_metrics_latest_snapshot_nulls_and_atomic_import(client, completed):
    job_id, _ = completed
    url=f"/api/jobs/{job_id}/metrics/1"
    for day, views in (("2020-01-01",100),("2020-01-02",150)):
        assert client.put(url,json={"date":day,"revision":0,"views":views,"subscribers":3}).status_code==200
    data=client.get("/api/analytics").json()
    assert data["totals"]["views"]==150 and data["totals"]["revenue_brl"] is None
    assert data["clips"][0]["subscribers_per_1000"]==20
    raw=f"job_id,clip_index,date,revision,views\n{job_id},1,2020-01-03,0,200\n{job_id},1,bad,0,300\n"
    assert client.post("/api/analytics/import",files={"file":("m.csv",raw)}).status_code==422
    assert client.get("/api/analytics").json()["totals"]["views"]==150
    raw=f"job_id,clip_index,date,revision,views\n{job_id},1,2020-01-03,0,200\n"
    assert client.post("/api/analytics/import",files={"file":("m.csv",raw)}).status_code==200
    assert client.get("/api/analytics").json()["totals"]["views"]==200


def test_publication_and_zip(client, completed):
    job_id, directory = completed
    url=f"/api/jobs/{job_id}/publication/1"
    assert client.put(url,json={"revision":0,"title":"Teste","status":"published"}).status_code==422
    assert client.put(url,json={"revision":0,"title":"Teste","youtube_url":"javascript:alert(1)"}).status_code==422
    assert client.get(f"/api/jobs/{job_id}/download?approved_only=true").status_code==404
    assert client.put(url,json={"revision":0,"title":"Título certo","description":"Descrição nova","status":"approved"}).status_code==200
    (directory/"clips/stale.mp4").write_bytes(b"old")
    response=client.get(f"/api/jobs/{job_id}/download?approved_only=true")
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "stale.mp4" not in archive.namelist()
        assert "Título certo" in archive.read("01-publicacao.txt").decode()
