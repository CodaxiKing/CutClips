"""SQLite como fila e banco. Suficiente até dezenas de milhares de jobs.

WAL ligado porque API e worker são processos separados escrevendo no mesmo arquivo.
A fila usa UPDATE condicional (status='queued') para que dois workers nunca
peguem o mesmo job.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections import Counter
import math
import numpy as np
from datetime import date
from pathlib import Path
from typing import Any

from clipforge.config import STORAGE

DB_PATH = Path(STORAGE) / "clipforge.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    filename    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    stage       TEXT NOT NULL DEFAULT 'na fila',
    progress    REAL NOT NULL DEFAULT 0,
    error       TEXT,
    settings    TEXT NOT NULL DEFAULT '{}',
    source_url  TEXT,
    manifest    TEXT,
    created_at  REAL NOT NULL,
    started_at  REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE TABLE IF NOT EXISTS edits (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, clip_index INTEGER NOT NULL,
    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', error TEXT,
    created_at REAL NOT NULL, heartbeat REAL, token TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_edit ON edits(job_id)
    WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS publication (
    job_id TEXT NOT NULL, clip_index INTEGER NOT NULL, data TEXT NOT NULL,
    updated_at REAL NOT NULL, PRIMARY KEY(job_id, clip_index)
);
CREATE TABLE IF NOT EXISTS metrics (
    job_id TEXT NOT NULL, clip_index INTEGER NOT NULL, date TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL,
    PRIMARY KEY(job_id, clip_index, date, revision)
);
CREATE TABLE IF NOT EXISTS clip_versions (
    job_id TEXT NOT NULL, clip_index INTEGER NOT NULL, revision INTEGER NOT NULL,
    clip TEXT NOT NULL, publication TEXT NOT NULL,
    PRIMARY KEY(job_id, clip_index, revision)
);
CREATE TABLE IF NOT EXISTS pipeline_stages (
    job_id TEXT NOT NULL, stage TEXT NOT NULL, ordinal INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued', token TEXT, heartbeat REAL,
    error TEXT, updated_at REAL NOT NULL, PRIMARY KEY(job_id, stage)
);
CREATE INDEX IF NOT EXISTS idx_pipeline_ready ON pipeline_stages(status, ordinal, updated_at);
"""

PIPELINE = (("transcription", 1), ("analysis", 2), ("tracking", 3), ("render", 4))


# Montagens não transcrevem nem analisam: são uma etapa só, com o nome do tipo.
MONTAGES = {"top5", "quiz", "reaction"}


def pipeline_for(settings):
    kind = (settings or {}).get("kind")
    return ((kind, 1),) if kind in MONTAGES else PIPELINE


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        # bancos criados antes do suporte a URL não têm a coluna
        cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
        if "source_url" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN source_url TEXT")
        for col, kind in (("heartbeat", "REAL"), ("token", "TEXT")):
            if col not in cols:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {kind}")
        for row in c.execute("SELECT id,created_at,settings FROM jobs WHERE status IN ('queued','running')").fetchall():
            for stage, ordinal in pipeline_for(json.loads(row["settings"] or "{}")):
                c.execute("INSERT OR IGNORE INTO pipeline_stages VALUES(?,?,?,?,?,?,?,?)",
                          (row["id"],stage,ordinal,"queued",None,None,None,row["created_at"]))


def create_job(filename: str, title: str = "", settings: dict | None = None,
               source_url: str | None = None, status: str = "queued") -> str:
    job_id = uuid.uuid4().hex[:12]
    with connect() as c:
        c.execute(
            "INSERT INTO jobs (id, title, filename, settings, source_url, created_at, status, stage) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (job_id, title, filename, json.dumps(settings or {}), source_url, time.time(),
             status, "enviando arquivo" if status == "uploading" else "na fila"),
        )
        for stage, ordinal in pipeline_for(settings):
            c.execute("INSERT INTO pipeline_stages VALUES(?,?,?,?,?,?,?,?)",
                      (job_id,stage,ordinal,"queued",None,None,None,time.time()))
    return job_id


def claim_pipeline_stage() -> dict | None:
    """Claim the earliest dependency-ready stage atomically across all workers."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("""SELECT p.* FROM pipeline_stages p JOIN jobs j ON j.id=p.job_id
            WHERE p.status='queued' AND j.status IN ('queued','running')
            AND NOT EXISTS (SELECT 1 FROM pipeline_stages prev WHERE prev.job_id=p.job_id
                            AND prev.ordinal<p.ordinal AND prev.status!='done')
            ORDER BY p.updated_at,p.ordinal LIMIT 1""").fetchone()
        if not row: return None
        token=uuid.uuid4().hex
        if not c.execute("UPDATE pipeline_stages SET status='running',token=?,heartbeat=?,updated_at=? WHERE job_id=? AND stage=? AND status='queued'",
                         (token,time.time(),time.time(),row["job_id"],row["stage"])).rowcount: return None
        c.execute("UPDATE jobs SET status='running',stage=?,started_at=COALESCE(started_at,?),heartbeat=? WHERE id=?",
                  (row["stage"],time.time(),time.time(),row["job_id"]))
        return {**dict(row),"id":f"{row['job_id']}:{row['stage']}","token":token}


def finish_pipeline_stage(task: dict) -> bool:
    with connect() as c:
        return bool(c.execute("UPDATE pipeline_stages SET status='done',updated_at=? WHERE job_id=? AND stage=? AND token=?",
            (time.time(),task["job_id"],task["stage"],task["token"])).rowcount)


def finish_montage(task: dict, manifest: dict) -> bool:
    """Conclui a etapa única de uma montagem e grava o manifesto no mesmo commit."""
    if task["stage"] not in MONTAGES:
        return False
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        if not c.execute("UPDATE pipeline_stages SET status='done',updated_at=? WHERE job_id=? AND stage=? AND token=? AND status='running'",
                         (time.time(),task["job_id"],task["stage"],task["token"])).rowcount:
            return False
        c.execute("UPDATE jobs SET status='done',stage='pronto',progress=1,manifest=?,finished_at=? WHERE id=?",
                  (json.dumps(manifest,ensure_ascii=False),time.time(),task["job_id"]))
        return True


finish_topfive = finish_montage


def fail_pipeline_stage(task: dict, error: str) -> None:
    with connect() as c:
        c.execute("UPDATE pipeline_stages SET status='error',error=?,updated_at=? WHERE job_id=? AND stage=? AND token=?",
                  (error[:2000],time.time(),task["job_id"],task["stage"],task["token"]))
    fail(task["job_id"],error)


def heartbeat_pipeline_stage(task: dict) -> bool:
    with connect() as c:
        ok=c.execute("UPDATE pipeline_stages SET heartbeat=? WHERE job_id=? AND stage=? AND token=? AND status='running'",
                     (time.time(),task["job_id"],task["stage"],task["token"])).rowcount
        if ok: c.execute("UPDATE jobs SET heartbeat=? WHERE id=?",(time.time(),task["job_id"]))
        return bool(ok)


def set_source(job_id: str, filename: str, title: str, token: str | None = None) -> None:
    """Grava o arquivo baixado da URL (e o título do vídeo, se o job não tinha)."""
    with connect() as c:
        c.execute(
            "UPDATE jobs SET filename=?, title=CASE WHEN title='' THEN ? ELSE title END "
            "WHERE id=? AND (? IS NULL OR token=?)",
            (filename, title, job_id, token, token),
        )


def claim_next() -> dict[str, Any] | None:
    """Pega o job mais antigo da fila de forma atômica."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        changed = c.execute(
            "UPDATE jobs SET status='running', started_at=?, heartbeat=?, token=?, stage='iniciando' "
            "WHERE id=? AND status='queued'",
            (time.time(), time.time(), uuid.uuid4().hex, row["id"]),
        ).rowcount
        if changed == 0:
            return None  # outro worker levou
        job = c.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
        return dict(job)


def set_progress(job_id: str, stage: str, progress: float, token: str | None = None) -> None:
    with connect() as c:
        c.execute("UPDATE jobs SET stage=?, progress=? WHERE id=? AND (? IS NULL OR token=?)",
                  (stage, round(progress, 4), job_id, token, token))


def finish(job_id: str, manifest: dict, token: str | None = None) -> bool:
    with connect() as c:
        return bool(c.execute(
            "UPDATE jobs SET status='done', stage='pronto', progress=1.0, "
            "manifest=?, finished_at=? WHERE id=? AND (? IS NULL OR token=?)",
            (json.dumps(manifest, ensure_ascii=False), time.time(), job_id, token, token),
        ).rowcount)


def fail(job_id: str, error: str, token: str | None = None) -> None:
    with connect() as c:
        c.execute(
            "UPDATE jobs SET status='error', stage='erro', error=?, finished_at=? WHERE id=? AND (? IS NULL OR token=?)",
            (error[:4000], time.time(), job_id, token, token),
        )


def retry(job_id: str) -> bool:
    with connect() as c:
        changed = bool(c.execute(
            "UPDATE jobs SET status='queued',stage='na fila',progress=0,error=NULL,"
            "started_at=NULL,finished_at=NULL,heartbeat=NULL,token=NULL "
            "WHERE id=? AND status='error'", (job_id,)
        ).rowcount)
        if changed:
            c.execute("UPDATE pipeline_stages SET status='queued',token=NULL,heartbeat=NULL,error=NULL,updated_at=? WHERE job_id=?",
                      (time.time(),job_id))
        return changed


def update_settings(job_id: str, settings: dict) -> None:
    with connect() as c:
        c.execute("UPDATE jobs SET settings=? WHERE id=?", (json.dumps(settings, ensure_ascii=False), job_id))


def batch_jobs(limit: int = 300) -> list[dict[str, Any]]:
    """Projetos que pertencem a algum lote, do mais novo para o mais antigo."""
    with connect() as c:
        rows = c.execute(
            "SELECT id, title, status, stage, progress, error, settings, created_at, finished_at FROM jobs "
            "WHERE json_extract(settings, '$.batch.id') IS NOT NULL ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
    jobs = []
    for row in rows:
        job = dict(row)
        job["settings"] = json.loads(job["settings"] or "{}")
        jobs.append(job)
    return jobs


def recent_backgrounds(limit: int = 30, exclude: str = "") -> list[str]:
    """Fundos da pasta usados pelos quizzes mais recentes, do mais novo ao mais antigo."""
    with connect() as c:
        rows = c.execute(
            "SELECT id, json_extract(settings, '$.background_pick.file') AS file FROM jobs "
            "WHERE json_extract(settings, '$.background_pick.file') IS NOT NULL "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [row["file"] for row in rows if row["id"] != exclude]


def batch_questions(batch_id: str, exclude: str = "") -> list[str]:
    """Perguntas já usadas pelos outros quizzes do lote, para a IA não repetir."""
    with connect() as c:
        rows = c.execute("SELECT id, settings FROM jobs WHERE json_extract(settings, '$.batch.id')=?",
                         (batch_id,)).fetchall()
    used = []
    for row in rows:
        if row["id"] != exclude:
            used += [q.get("question", "") for q in
                     json.loads(row["settings"] or "{}").get("quiz", {}).get("questions", [])]
    return [q for q in used if q]


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    job = dict(row)
    job["settings"] = json.loads(job["settings"] or "{}")
    job["manifest"] = json.loads(job["manifest"]) if job["manifest"] else None
    return job


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute(
            "SELECT id, title, filename, source_url, status, stage, progress, error, "
            "created_at, started_at, finished_at, manifest, json_extract(settings, '$.kind') AS kind "
            "FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    jobs = []
    for r in rows:
        job = dict(r)
        manifest = json.loads(job.pop("manifest") or "null") or {}
        clips = manifest.get("clips", [])
        # resumo para os cards da listagem, sem mandar o manifesto inteiro
        job["clip_count"] = len(clips)
        job["cover"] = next((c["thumbnail"] for c in clips if c.get("thumbnail")), None)
        job["source_duration"] = manifest.get("source_duration")
        jobs.append(job)
    return jobs


def delete_job(job_id: str) -> bool:
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        active = c.execute("SELECT 1 FROM edits WHERE job_id=? AND status IN ('queued','running')", (job_id,)).fetchone()
        if row and (row["status"] in ("running", "uploading") or active):
            raise ValueError("aguarde o processamento terminar antes de excluir")
        for table in ("edits", "publication", "metrics", "clip_versions", "pipeline_stages"):
            c.execute(f"DELETE FROM {table} WHERE job_id=?", (job_id,))
        return c.execute("DELETE FROM jobs WHERE id=?", (job_id,)).rowcount > 0


def requeue_stale(max_seconds: float = 180) -> int:
    """Worker morreu no meio? Devolve o job para a fila."""
    cutoff = time.time() - max_seconds
    with connect() as c:
        count = c.execute(
            "UPDATE jobs SET status='queued', stage='na fila', progress=0, token=NULL "
            "WHERE status='running' AND COALESCE(heartbeat,started_at) < ?", (cutoff,)
        ).rowcount
        count += c.execute("UPDATE edits SET status='queued', token=NULL WHERE status='running' AND heartbeat < ?", (cutoff,)).rowcount
        count += c.execute("UPDATE pipeline_stages SET status='queued',token=NULL WHERE status='running' AND heartbeat < ?", (cutoff,)).rowcount
        c.execute("UPDATE jobs SET status='error',stage='erro',error='Upload interrompido; envie novamente.' WHERE status='uploading' AND created_at < ?", (time.time() - 86400,))
        return count


def enqueue_upload(job_id: str) -> None:
    with connect() as c:
        changed = c.execute("UPDATE jobs SET status='queued',stage='na fila' WHERE id=? AND status='uploading'", (job_id,)).rowcount
        if not changed:
            raise ValueError("upload não está disponível")


def heartbeat(task_id: str, token: str, edit: bool = False) -> bool:
    with connect() as c:
        table = "edits" if edit else "jobs"
        return bool(c.execute(f"UPDATE {table} SET heartbeat=? WHERE id=? AND token=? AND status='running'", (time.time(), task_id, token)).rowcount)


def queue_edit(job_id: str, index: int, payload: dict) -> str:
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT manifest,status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["status"] != "done":
            raise ValueError("projeto ainda não concluído")
        clip = next((x for x in json.loads(row["manifest"])["clips"] if x["index"] == index), None)
        if not clip or clip.get("revision", 0) != payload["revision"]:
            raise ValueError("o clipe mudou; reabra o editor para carregar a versão atual")
        task_id = uuid.uuid4().hex[:12]
        try:
            c.execute("INSERT INTO edits(id,job_id,clip_index,payload,created_at) VALUES(?,?,?,?,?)",
                      (task_id, job_id, index, json.dumps(payload, ensure_ascii=False), time.time()))
        except sqlite3.IntegrityError as exc:
            raise ValueError("já existe uma edição em andamento neste projeto") from exc
        return task_id


def claim_edit() -> dict | None:
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT id FROM edits WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return None
        c.execute("UPDATE edits SET status='running',heartbeat=?,token=? WHERE id=?", (time.time(), uuid.uuid4().hex, row["id"]))
        return dict(c.execute("SELECT * FROM edits WHERE id=?", (row["id"],)).fetchone())


def finish_edit(task: dict, clip: dict) -> dict | None:
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        lease = c.execute("SELECT 1 FROM edits WHERE id=? AND token=? AND status='running'", (task["id"], task["token"])).fetchone()
        if not lease:
            return None
        row = c.execute("SELECT manifest FROM jobs WHERE id=?", (task["job_id"],)).fetchone()
        if not row:
            return None
        manifest = json.loads(row["manifest"])
        old = next(x for x in manifest["clips"] if x["index"] == task["clip_index"])
        pub = c.execute("SELECT data FROM publication WHERE job_id=? AND clip_index=?", (task["job_id"], task["clip_index"])).fetchone()
        c.execute("INSERT OR IGNORE INTO clip_versions VALUES(?,?,?,?,?)", (task["job_id"], task["clip_index"], old.get("revision", 0), json.dumps(old, ensure_ascii=False), pub["data"] if pub else "{}"))
        manifest["clips"] = [clip if x["index"] == task["clip_index"] else x for x in manifest["clips"]]
        manifest["updated_at"] = time.time()
        c.execute("UPDATE jobs SET manifest=? WHERE id=?", (json.dumps(manifest, ensure_ascii=False), task["job_id"]))
        # New video starts as a draft; previous publication and metrics remain archived.
        c.execute("DELETE FROM publication WHERE job_id=? AND clip_index=?", (task["job_id"], task["clip_index"]))
        c.execute("UPDATE edits SET status='done' WHERE id=?", (task["id"],))
        return manifest


def fail_edit(task: dict, error: str) -> None:
    with connect() as c:
        c.execute("UPDATE edits SET status='error',error=? WHERE id=? AND token=?", (error[:2000], task["id"], task["token"]))


def edit_status(job_id: str) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT id,clip_index,status,error,created_at FROM edits WHERE job_id=? ORDER BY created_at DESC LIMIT 20", (job_id,))]


def publications(job_id: str) -> dict:
    with connect() as c:
        return {str(r["clip_index"]): {**json.loads(r["data"]), "updated_at": r["updated_at"]} for r in c.execute("SELECT * FROM publication WHERE job_id=?", (job_id,))}


def save_publication(job_id: str, index: int, data: dict) -> None:
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT manifest FROM jobs WHERE id=?", (job_id,)).fetchone()
        clip = next((x for x in json.loads(row["manifest"])["clips"] if x["index"] == index), None) if row else None
        if not clip or clip.get("revision", 0) != data["revision"]:
            raise ValueError("a versão mudou; recarregue os dados")
        if c.execute("SELECT 1 FROM edits WHERE job_id=? AND status IN ('queued','running')", (job_id,)).fetchone():
            raise ValueError("aguarde a edição terminar antes de aprovar")
        c.execute("INSERT INTO publication VALUES(?,?,?,?) ON CONFLICT(job_id,clip_index) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at", (job_id, index, json.dumps(data, ensure_ascii=False), time.time()))


def save_metrics(job_id: str, index: int, data: dict) -> None:
    with connect() as c:
        c.execute("INSERT INTO metrics VALUES(?,?,?,?,?) ON CONFLICT(job_id,clip_index,date,revision) DO UPDATE SET data=excluded.data", (job_id, index, data["date"], data.get("revision", 0), json.dumps(data)))


def versions(job_id: str, index: int) -> list[dict]:
    with connect() as c:
        return [{"clip": json.loads(r["clip"]), "publication": json.loads(r["publication"])} for r in c.execute("SELECT * FROM clip_versions WHERE job_id=? AND clip_index=? ORDER BY revision DESC", (job_id, index))]


def metric_rows() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT m.*, j.title AS project_title, j.settings, j.manifest, j.finished_at FROM metrics m JOIN jobs j ON j.id=m.job_id ORDER BY m.date DESC")]


def learning_profile(min_samples: int = 5) -> dict:
    """Compact, anonymous-to-the-model performance priors from latest clip snapshots."""
    rows = metric_rows()
    latest = {}
    for row in rows:
        key = (row["job_id"], row["clip_index"], row["revision"])
        latest.setdefault(key, row)
    samples = []
    for row in latest.values():
        data = json.loads(row["data"])
        manifest = json.loads(row["manifest"] or "{}")
        clip = next((c for c in manifest.get("clips", []) if c.get("index") == row["clip_index"]), {})
        if data.get("average_percentage") is None and data.get("views") is None:
            continue
        try:
            age_days = max(1, (date.fromisoformat(data["date"]) - date.fromtimestamp(row["finished_at"] or time.time())).days)
        except Exception:
            age_days = 1
        samples.append({"duration": clip.get("actual_duration"), "topic": clip.get("topic", ""),
                        "format": clip.get("format", ""), "criteria": clip.get("criteria", {}),
                        "retention": data.get("average_percentage"), "views": data.get("views"),
                        "views_per_day": (data.get("views") or 0)/age_days,
                        "subscribers": data.get("subscribers"), "revenue_brl": data.get("revenue_brl")})
    if len(samples) < min_samples:
        return {"sample_count": len(samples), "ready": False}
    retained = [s for s in samples if isinstance(s["retention"], (int, float))]
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.] * len(values)
        for rank, index in enumerate(order): out[index] = rank/max(1, len(values)-1)
        return out
    rr = ranks([s["retention"] for s in retained]) if retained else []
    vr = ranks([math.log1p(s["views_per_day"]) for s in retained]) if retained else []
    for i, sample in enumerate(retained): sample["performance"] = .7*rr[i] + .3*vr[i]
    best = sorted(retained, key=lambda s: s["performance"], reverse=True)[:max(2, len(retained)//3)]
    durations = [s["duration"] for s in best if isinstance(s["duration"], (int, float))]
    formats = Counter(s["format"] for s in best if s["format"])
    topics = Counter(s["topic"] for s in samples if s["topic"])
    weights = {}
    for criterion in ("gancho", "clareza", "conclusao", "emocao_utilidade", "densidade", "titulo", "retencao"):
        pairs = [(s["criteria"].get(criterion), s["performance"]) for s in retained
                 if isinstance(s["criteria"].get(criterion), (int, float))]
        if len(pairs) >= 5:
            xs, ys = np.asarray([p[0] for p in pairs]), np.asarray([p[1] for p in pairs])
            corr = float(np.corrcoef(xs, ys)[0, 1]) if np.std(xs) and np.std(ys) else 0
            shrink = len(pairs)/(len(pairs)+20)
            weights[criterion] = round(1 + np.clip(corr*shrink, -.3, .3), 3)
    return {"sample_count": len(samples), "ready": True,
            "confidence": round(min(.9, len(samples)/(len(samples)+20)), 2),
            "best_duration_seconds": round(sum(durations)/len(durations), 1) if durations else None,
            "best_formats": [x for x, _ in formats.most_common(3)],
            "known_topics": [x for x, _ in topics.most_common(20)], "weights": weights}
