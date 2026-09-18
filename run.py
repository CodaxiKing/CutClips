"""Orquestrador: vídeo longo -> N clipes verticais legendados."""
from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .boundaries import detect_silences, refine_plan_bounds
from .editor import atomic_json, render_clip
from .config import CONFIG, Config, apply_orientation
from .download import download, is_supported_url
from .probe import probe
from .render import ensure_ffmpeg
from .segment import build_sentences
from .select import select_clips
from .transcribe import transcribe
from .reframe import plan_crop
from .signals import opening_signals, visual_timeline
from .media_index import build_media_index, multimodal_context
from .prospect import (analysis_windows, fallback_windows, find_moments, loudness_envelope,
                       should_prospect)

Progress = Callable[[str, float], None]


def _slug(text: str, limit: int = 48) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return (text[:limit].strip("-") or "clipe")


def process(
    video: str | Path,
    job_dir: str | Path,
    cfg: Config = CONFIG,
    on_progress: Progress | None = None,
    title: str = "",
    metadata: dict | None = None,
    learning: dict | None = None,
    write_manifest: bool = True,
) -> dict:
    cfg.validate()
    ensure_ffmpeg()
    video = Path(video).resolve()
    job_dir = Path(job_dir).resolve()
    (job_dir / "clips").mkdir(parents=True, exist_ok=True)
    (job_dir / "work").mkdir(parents=True, exist_ok=True)

    def report(stage: str, pct: float) -> None:
        if on_progress:
            on_progress(stage, max(0.0, min(1.0, pct)))

    t0 = time.time()
    report("probe", 0.02)
    info = probe(video)

    report("transcrição e análise visual", 0.05)
    transcription_context = "\n".join(str(x) for x in [title, (metadata or {}).get("channel", ""),
        (metadata or {}).get("description", "")[:2000], " ".join((metadata or {}).get("tags", [])[:30])] if x)
    windows: list[tuple[float, float]] | None = None
    moments: list = []
    if should_prospect(info.duration, cfg):
        # Transmissão longa: encontrar os momentos primeiro e transcrever só eles.
        # Transcrever 6h para aproveitar 8 minutos é o que torna o processo inviável.
        report("garimpo de momentos", 0.06)
        with ThreadPoolExecutor(max_workers=2) as scout:
            visual_future = scout.submit(visual_timeline, video)
            envelope_future = scout.submit(loudness_envelope, video)
            visual_index = visual_future.result()
            envelope = envelope_future.result()
        moments = find_moments(video, info.duration, cfg, visual_index,
                               max(cfg.max_clips, cfg.max_clips * cfg.shortlist_multiplier), envelope)
        windows = analysis_windows(moments, info.duration, cfg) or             fallback_windows(info.duration, cfg, cfg.max_clips * 2)
        report("transcrição dos momentos", 0.12)
        tr = transcribe(video, job_dir / "transcript.json", cfg, transcription_context, windows)
    else:
        with ThreadPoolExecutor(max_workers=2) as early_pool:
            transcript_future = early_pool.submit(transcribe, video, job_dir / "transcript.json", cfg, transcription_context)
            visual_future = early_pool.submit(visual_timeline, video)
            tr = transcript_future.result()
            visual_index = visual_future.result()
    metadata = {**(metadata or {}), "visual_index": visual_index}

    report("segmentacao", 0.45)
    sentences = build_sentences(tr)
    if not sentences:
        raise RuntimeError("transcrição vazia: o vídeo tem fala audível?")

    report("seleção e indexação", 0.50)
    with ThreadPoolExecutor(max_workers=2) as analysis_pool:
        index_future = analysis_pool.submit(build_media_index, video, tr, sentences,
                                             job_dir / "media-index.json", visual_index, windows)
        plans_future = analysis_pool.submit(select_clips, sentences, title or video.stem, tr.language, cfg,
                                             metadata, learning, job_dir / "analysis.json")
        plans = plans_future.result()
        media_index = index_future.result()
    if not plans:
        raise RuntimeError("nenhum trecho aproveitável encontrado")

    for plan in plans:
        mm=multimodal_context(media_index,plan.start,plan.end,plan.text)
        plan.criteria["multimodal"]=mm
        visual_bonus=min(4,mm["scene_changes"]*.4+mm["smile_ratio"]*2+mm["visual_speech_support"]*2)
        plan.score=max(0,min(100,plan.score+visual_bonus))

    report("silencio", 0.55)
    silences = detect_silences(video, cfg) if info.has_audio else []
    media_index["silences"]=silences
    atomic_json(job_dir/"media-index.json",media_index)

    prepared = []
    for plan in plans:
        start, end = refine_plan_bounds(plan.first, plan.last, sentences, info.duration, silences, cfg)
        if end - start > cfg.max_duration:
            start, end = plan.start, plan.end
        prepared.append((plan, start, end))
    # Face detection is CPU/ffmpeg work and can run concurrently for shortlisted clips.
    workers = min(4, max(1, len(prepared)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        crop_futures = [pool.submit(plan_crop, video, info, start, end-start, cfg, media_index)
                        for _, start, end in prepared]
        signal_futures = [pool.submit(opening_signals, video, start, min(2, end-start))
                          for _, start, end in prepared]

    results = []
    for i, ((plan, start, end), crop_future, signal_future) in enumerate(zip(prepared, crop_futures, signal_futures)):
        base = 0.58 + 0.40 * (i / max(1, len(plans)))
        report(f"clipe {i + 1}/{len(plans)}", base)

        signals = signal_future.result()
        plan.criteria.update(signals)
        # Blend transcript judgment with observed opening energy, motion and silence.
        plan.score = max(0, min(100, plan.score*.9 + signals["audiovisual_hook"]*2))
        results.append(render_clip(video, job_dir, tr.words, {
            "index": i + 1,
            "title": plan.title,
            "hook": plan.hook,
            "score": round(plan.score, 1),
            "reason": plan.reason,
            "source_start": round(start, 3),
            "source_end": round(end, 3),
            "planned_duration": round(end - start, 3),
            "sentences": [plan.first, plan.last],
            "text": plan.text,
            "provider": plan.provider,
            "warnings": plan.warnings,
            "title_options": plan.title_options or [plan.title],
            "description": plan.description or plan.text,
            "criteria": plan.criteria,
            "topic": plan.topic, "format": plan.format,
        }, cfg, crop_plan=crop_future.result(), media_index=media_index))

    manifest = {
        "source": video.name,
        "source_duration": round(info.duration, 3),
        "source_resolution": f"{info.width}x{info.height}",
        "language": tr.language,
        "provider": plans[0].provider,
        "requested_provider": cfg.llm_provider,
        # Vazio quando a IA pedida realmente rodou. Preenchido, a interface avisa
        # em destaque em vez de esconder a troca numa lista de avisos do clipe.
        "provider_fallback": plans[0].fallback_reason,
        "model": cfg.resolved_model() if plans[0].provider != "heuristic" else None,
        "niche": cfg.niche, "audience": cfg.audience, "rights_notes": cfg.rights_notes,
        "analysis": {"hierarchical": info.duration > cfg.analysis_block_seconds * 1.2,
                     "speakers": tr.speakers or [], "learning": learning or {"ready": False}},
        "prospect": {"used": bool(windows), "moments": [m.as_dict() for m in moments[:40]],
                     "analysed_seconds": round(sum(b - a for a, b in (windows or [])), 1),
                     "windows": windows or []},
        "orientation": cfg.orientation,
        "media_index": {"faces": sum(len(x.get("faces",[])) for x in media_index.get("faces",[])),
                        "scene_changes": len(media_index.get("visual",{}).get("scene_changes",[])),
                        "text_regions": sum(len(x.get("regions",[])) for x in media_index.get("text_regions",[]))},
        "clips": results,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    if write_manifest:
        atomic_json(job_dir / "manifest.json", manifest)
    report("pronto", 1.0)
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="CutClips — vídeo longo -> clipes verticais")
    ap.add_argument("video", help="arquivo de vídeo ou link do YouTube, Twitch ou Kick")
    ap.add_argument("-o", "--out", default="./out")
    ap.add_argument("-n", "--max-clips", type=int)
    ap.add_argument("--provider", choices=["anthropic", "openai", "ollama", "heuristic"])
    ap.add_argument("--model")
    ap.add_argument("--min", type=float, dest="min_duration")
    ap.add_argument("--max", type=float, dest="max_duration")
    ap.add_argument("--whisper-model")
    ap.add_argument("--language")
    ap.add_argument("--niche", default="")
    ap.add_argument("--audience", default="")
    ap.add_argument("--layout", choices=["track", "active", "manual", "fit", "split"], default="track")
    ap.add_argument("--orientation", choices=["vertical", "horizontal"], default="vertical",
                    help="vertical = 1080x1920 (Shorts); horizontal = 1920x1080 (YouTube)")
    ap.add_argument("--live-minutes", type=float, default=0.0, dest="live_minutes",
                    help="grava esta janela se o link estiver ao vivo (tempo real)")
    ap.add_argument("--no-normalize", action="store_true")
    ap.add_argument("--denoise", action="store_true")
    args = ap.parse_args()

    cfg = apply_orientation(Config(), args.orientation)
    if args.provider and args.provider != cfg.llm_provider and not args.model:
        cfg.llm_model = ""
    for attr, val in [
        ("max_clips", args.max_clips), ("llm_provider", args.provider),
        ("llm_model", args.model), ("min_duration", args.min_duration),
        ("max_duration", args.max_duration), ("whisper_model", args.whisper_model),
        ("language", args.language),
        ("niche", args.niche), ("audience", args.audience), ("layout", args.layout),
        ("normalize_audio", not args.no_normalize), ("denoise_audio", args.denoise),
    ]:
        if val is not None:
            setattr(cfg, attr, val)

    def show(stage: str, pct: float) -> None:
        print(f"[{pct * 100:5.1f}%] {stage}", flush=True)

    video, title = args.video, ""
    if is_supported_url(video):
        show("download", 0.0)
        path, info = download(video, Path(args.out) / "source", cfg=cfg,
                              live_minutes=args.live_minutes)
        video, title = path, info.get("title") or ""

    manifest = process(video, args.out, cfg=cfg, on_progress=show, title=title)
    print()
    for c in manifest["clips"]:
        drift = abs(c["actual_duration"] - c["planned_duration"])
        print(f"  {c['index']:2d}. {c['title'][:52]:<52} "
              f"{c['actual_duration']:5.1f}s  score {c['score']:5.1f}  drift {drift:.3f}s")
    print(f"\n{len(manifest['clips'])} clipes em {args.out}/clips "
          f"({manifest['elapsed_seconds']}s)")


if __name__ == "__main__":
    main()
