"""Worker da fila. Roda como processo separado da API.

Um worker por GPU (ou por núcleo, se CPU). Escalar = subir mais processos:
a fila é atômica no SQLite, então não há coordenação extra.
"""
from __future__ import annotations

import os
import signal
import sys
import time
import traceback
import threading
import json
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import db  # noqa: E402
from cutclips.config import STORAGE, Config, apply_orientation  # noqa: E402
from cutclips.download import download, describe_url  # noqa: E402
from cutclips.run import process  # noqa: E402
from cutclips.editor import render_clip, atomic_json, EDIT_FIELDS
from cutclips.transcribe import Transcript
from cutclips.transcribe import transcribe
from cutclips.segment import build_sentences
from cutclips.select import select_clips
from cutclips.media_index import build_media_index
from cutclips.signals import visual_timeline
from cutclips.probe import probe
from concurrent.futures import ThreadPoolExecutor

POLL_SECONDS = float(os.getenv("CUTCLIPS_POLL", "2"))
_running = True


def _stop(signum, frame) -> None:  # noqa: ANN001, ARG001
    global _running
    _running = False
    print("[worker] encerrando após o job atual...", flush=True)


def build_config(settings: dict) -> Config:
    cfg = Config()
    allowed = {
        "max_clips", "min_duration", "max_duration", "llm_provider", "llm_model", "triage_model", "review_model",
        "whisper_model", "language", "caption_size", "caption_max_words",
        "out_width", "out_height", "crf", "preset", "caption_highlight",
        "niche", "audience", "rights_notes",
        "diarization", "analysis_block_seconds", "analysis_context_sentences", "shortlist_multiplier",
        "live_minutes", "prospect_after_minutes", "prospect_margin",
    } | EDIT_FIELDS
    for key, value in (settings or {}).items():
        if key in allowed and value not in (None, ""):
            current = getattr(cfg, key)
            try:
                setattr(cfg, key, type(current)(value) if current is not None else value)
            except (TypeError, ValueError):
                setattr(cfg, key, value)
    if settings.get("orientation"):
        apply_orientation(cfg, str(settings["orientation"]))
    if not settings.get("llm_model") and cfg.llm_provider != os.getenv("CUTCLIPS_LLM_PROVIDER", "anthropic"):
        cfg.llm_model = ""
    cfg.validate()
    return cfg


@contextmanager
def lease(task: dict, edit: bool = False):
    stop = threading.Event()
    def pulse():
        while not stop.wait(20):
            try:
                if not db.heartbeat(task["id"], task["token"], edit):
                    return
            except Exception:
                traceback.print_exc()
    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


@contextmanager
def stage_lease(task: dict):
    stop=threading.Event()
    def pulse():
        while not stop.wait(20):
            try:
                if not db.heartbeat_pipeline_stage(task): return
            except Exception: traceback.print_exc()
    thread=threading.Thread(target=pulse,daemon=True); thread.start()
    try: yield
    finally: stop.set(); thread.join(timeout=2)


def run_edit(task: dict) -> None:
    from api.studio import source_path, job_dir
    try:
        job = db.get_job(task["job_id"])
        if not job:
            raise ValueError("projeto não encontrado")
        clip = next(c for c in job["manifest"]["clips"] if c["index"] == task["clip_index"])
        payload = json.loads(task["payload"])
        if clip.get("revision", 0) != payload["revision"]:
            raise ValueError("versão do clipe mudou; reabra o editor")
        directory = job_dir(task["job_id"])
        tr = Transcript.from_json(directory / "transcript.json")
        with lease(task, edit=True):
            result = render_clip(source_path(task["job_id"], job), directory, tr.words, clip,
                                 build_config(job["settings"]), edit=payload)
            manifest = db.finish_edit(task, result)
            if manifest:
                atomic_json(directory / "manifest.json", manifest)
    except Exception as exc:
        traceback.print_exc()
        db.fail_edit(task, f"{type(exc).__name__}: {exc}")


def run_job(job: dict) -> None:
    job_id = job["id"]
    import json
    settings = json.loads(job["settings"] or "{}")
    cfg = build_config(settings)

    job_dir = Path(STORAGE) / "jobs" / job_id
    source = job_dir / "source" / job["filename"]
    title = job["title"] or ""
    metadata = {}
    print(f"[worker] job {job_id}: {job['filename']}", flush=True)
    t0 = time.time()

    if job.get("source_url") and not source.exists():
        live_minutes = float(settings.get("live_minutes") or 0)
        what = describe_url(job["source_url"])
        # Gravar uma live roda em tempo real; o rótulo evita a impressão de travado.
        label = f"gravando {live_minutes:.0f} min da transmissão" if live_minutes else f"baixando {what}"

        def on_download(pct: float) -> None:
            db.set_progress(job_id, f"{label} {pct * 100:.0f}%", 0.0, job["token"])

        try:
            db.set_progress(job_id, label, 0.0, job["token"])
            source, info = download(job["source_url"], job_dir / "source", cfg=cfg,
                                    on_progress=on_download, live_minutes=live_minutes)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            db.fail(job_id, str(exc), job["token"])
            return
        title = title or info.get("title") or ""
        metadata = {k: info.get(k) for k in ("title", "description", "channel", "uploader", "tags", "categories") if info.get(k)}
        db.set_source(job_id, source.name, title, job["token"])
        print(f"[worker] job {job_id}: baixado {source.name} em {time.time() - t0:.0f}s",
              flush=True)

    if not source.exists():
        db.fail(job_id, f"arquivo de origem sumiu: {source}", job["token"])
        return

    def on_progress(stage: str, pct: float) -> None:
        db.set_progress(job_id, stage, pct, job["token"])

    try:
        manifest = process(source, job_dir, cfg=cfg, on_progress=on_progress, title=title,
                           metadata=metadata, learning=db.learning_profile(), write_manifest=False)
        if db.finish(job_id, manifest, job["token"]):
            atomic_json(job_dir / "manifest.json", manifest)
        print(f"[worker] job {job_id} ok: {len(manifest['clips'])} clipes "
              f"em {time.time() - t0:.0f}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        db.fail(job_id, f"{type(exc).__name__}: {exc}", job["token"])


def run_pipeline_stage(task: dict) -> None:
    job_id=task["job_id"]
    try:
        job=db.get_job(job_id)
        if not job: raise ValueError("projeto não encontrado")
        if task["stage"] in db.MONTAGES:
            from cutclips import narrate, quiz, reaction, topfive
            directory = Path(STORAGE)/"jobs"/job_id
            progress = lambda s,p:db.set_progress(job_id,s,p)
            if task["stage"] == "quiz":
                batch = (job["settings"].get("batch") or {}).get("id")
                manifest = quiz.process_quiz(
                    job["settings"], directory, progress,
                    avoid=lambda: db.batch_questions(batch, exclude=job_id) if batch else [],
                    save_settings=lambda settings: db.update_settings(job_id, settings),
                    recent_backgrounds=lambda: db.recent_backgrounds(exclude=job_id))
            elif task["stage"] == "narration":
                # A narração é a única montagem que chama IA: o provedor e o
                # modelo vêm das configurações do projeto, como no corte.
                manifest = narrate.process_narration(job["settings"], directory, progress,
                                                     build_config(job["settings"]))
            else:
                process_montage = {"top5": topfive.process_topfive, "reaction": reaction.process_reaction}[task["stage"]]
                manifest = process_montage(job["settings"],directory,progress)
            if db.finish_montage(task,manifest):
                atomic_json(directory/"manifest.json",manifest)
            return
        cfg=build_config(job["settings"]); directory=Path(STORAGE)/"jobs"/job_id
        source=directory/"source"/job["filename"]; metadata_path=directory/"source-metadata.json"
        metadata=json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        if not source.exists() and job.get("source_url"):
            source,info=download(job["source_url"],directory/"source",cfg=cfg)
            metadata={k:info.get(k) for k in ("title","description","channel","uploader","tags","categories") if info.get(k)}
            metadata_path.write_text(json.dumps(metadata,ensure_ascii=False),encoding="utf-8")
            db.set_source(job_id,source.name,job["title"] or info.get("title") or "")
        if not source.exists(): raise ValueError("arquivo de origem não encontrado")
        context="\n".join(str(x) for x in [job["title"],metadata.get("channel",""),metadata.get("description","")[:2000],
                           " ".join(metadata.get("tags",[])[:30])] if x)
        progress={"transcription":.20,"analysis":.45,"tracking":.60,"render":.65}[task["stage"]]
        db.set_progress(job_id,task["stage"],progress)
        if task["stage"]=="transcription":
            with ThreadPoolExecutor(max_workers=2) as pool:
                tf=pool.submit(transcribe,source,directory/"transcript.json",cfg,context)
                vf=pool.submit(visual_timeline,source)
                tf.result(); (directory/"visual-index.json").write_text(json.dumps(vf.result()),encoding="utf-8")
        elif task["stage"]=="analysis":
            tr=Transcript.from_json(directory/"transcript.json"); sentences=build_sentences(tr)
            plans=select_clips(sentences,job["title"] or source.stem,tr.language,cfg,metadata,db.learning_profile(),directory/"analysis.json")
            if not plans: raise RuntimeError("nenhum trecho aproveitável encontrado")
        elif task["stage"]=="tracking":
            tr=Transcript.from_json(directory/"transcript.json"); visual={}
            if (directory/"visual-index.json").exists(): visual=json.loads((directory/"visual-index.json").read_text())
            build_media_index(source,tr,build_sentences(tr),directory/"media-index.json",visual)
        else:
            manifest=process(source,directory,cfg=cfg,on_progress=lambda s,p:db.set_progress(job_id,s,.65+p*.35),
                             title=job["title"],metadata=metadata,learning=db.learning_profile(),write_manifest=False)
            if db.finish(job_id,manifest): atomic_json(directory/"manifest.json",manifest)
        db.finish_pipeline_stage(task)
        print(f"[worker] {job_id} etapa {task['stage']} concluída",flush=True)
    except Exception as exc:
        traceback.print_exc(); db.fail_pipeline_stage(task,f"{type(exc).__name__}: {exc}")


def main() -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    db.init()
    requeued = db.requeue_stale()
    if requeued:
        print(f"[worker] {requeued} job(s) travado(s) devolvido(s) à fila", flush=True)
    print(f"[worker] pronto, storage={STORAGE}", flush=True)

    idle = 0
    checked_at = time.monotonic()
    while _running:
        if time.monotonic() - checked_at > 60:
            db.requeue_stale()
            checked_at = time.monotonic()
        edit = db.claim_edit()
        if edit:
            run_edit(edit)
            continue
        stage_task = db.claim_pipeline_stage()
        if stage_task is None:
            idle += 1
            if idle % 30 == 1:
                print("[worker] ocioso", flush=True)
            time.sleep(POLL_SECONDS)
            continue
        idle = 0
        try:
            with stage_lease(stage_task): run_pipeline_stage(stage_task)
        except Exception as exc:
            traceback.print_exc(); db.fail_pipeline_stage(stage_task,f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
