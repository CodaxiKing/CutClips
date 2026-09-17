"""Render one revision using the cached transcript, without retranscribing."""
from __future__ import annotations

import json
import time
import uuid
import hashlib
import shutil
from dataclasses import asdict, replace
from pathlib import Path

from .captions import build_ass, build_srt
from .config import Config
from .probe import probe
from .reframe import CropPlan, plan_crop, write_sendcmd
from .render import RenderSpec, make_thumbnail, render
from .transcribe import Word
from .media_index import caption_position
from .editing import edit_ranges, map_time, remap_crop, remap_words
from .align import force_align_boundaries

EDIT_FIELDS = {"layout", "crop_x", "crop_y", "secondary_x", "caption_size",
               "caption_max_words", "caption_position", "caption_style",
               "captions_enabled", "normalize_audio", "denoise_audio", "auto_edit"}


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def energy_accents(media_index: dict | None, start: float, end: float,
                   cuts: list[tuple[float, float, str]], limit: int = 3) -> list[float]:
    """Instantes de maior energia vocal do clipe, para a ênfase de zoom.

    São os gritos, as risadas, a frase dita mais alto — o que um editor marcaria
    com um empurrãozinho de zoom. Poucos por clipe: a ênfase vale justamente por
    ser rara, e a cada dois segundos viraria pisca-pisca.
    """
    words = [w for w in (media_index or {}).get("audio_words", [])
             if w.get("energy") is not None and start <= float(w.get("start", -1)) <= end]
    if len(words) < 8:
        return []
    ranked = sorted(float(w["energy"]) for w in words)
    floor = ranked[int(len(ranked) * 0.88)]
    picked: list[float] = []
    for word in sorted(words, key=lambda w: -float(w["energy"])):
        if float(word["energy"]) < floor or len(picked) >= limit:
            break
        moment = float(word["start"]) - start
        if any(lo <= moment <= hi for lo, hi, _ in cuts):
            continue
        if any(abs(moment - other) < 2.5 for other in picked):
            continue
        picked.append(moment)
    return sorted(round(map_time(t, cuts), 3) for t in picked)


def render_clip(source: Path, job_dir: Path, words: list[Word], clip: dict,
                cfg: Config, *, edit: dict | None = None, crop_plan: CropPlan | None = None,
                media_index: dict | None = None) -> dict:
    started = time.monotonic()
    edit = edit or {}
    options = {**clip.get("edit_settings", {}), **edit.get("settings", {})}
    cfg = replace(cfg, **{k: v for k, v in options.items() if k in EDIT_FIELDS})
    cfg.validate()
    # O que a pessoa escolheu. A montagem pode trocar o layout sozinha (tela
    # dividida automática) sem que a escolha original suma do editor.
    requested = cfg
    info = probe(source)
    start = float(edit.get("start", clip["source_start"]))
    end = float(edit.get("end", clip["source_end"]))
    if not 0 <= start < end <= info.duration + 0.01 or not 0.5 <= end - start <= 180:
        raise ValueError("intervalo inválido: selecione de 0,5 a 180 segundos dentro do vídeo")
    if media_index is None and (job_dir / "media-index.json").exists():
        try: media_index = json.loads((job_dir / "media-index.json").read_text(encoding="utf-8"))
        except Exception: media_index = None
    if media_index:
        cfg = replace(cfg, caption_position=caption_position(media_index, start, end, cfg.caption_position))
    replacements = {int(k): v for k, v in clip.get("word_edits", {}).items()}
    replacements.update({x["id"]: x["text"].strip() for x in edit.get("words", [])})
    corrected = [replace(w, text=replacements.get(i, w.text)) for i, w in enumerate(words)]
    if edit.get("words"):
        corrected = force_align_boundaries(source,start,end,corrected)
    inside = [w for w in corrected if w.end > start and w.start < end and w.text]
    cuts = edit_ranges(inside,start,end,cfg,(media_index or {}).get("silences"))
    output_duration=(end-start)-sum(b-a for a,b,_ in cuts)
    timeline_words=remap_words(inside,start,cuts)
    revision = clip.get("revision", 0) + (1 if edit else 0)
    stem = f"{clip['index']:02d}-r{revision}-{uuid.uuid4().hex[:8]}"
    work = job_dir / "work" / stem
    work.mkdir(parents=True, exist_ok=True)
    out = job_dir / "clips" / f"{stem}.mp4"
    cache_payload = {"source": str(source.resolve()), "size": source.stat().st_size,
        "mtime": source.stat().st_mtime_ns, "start": round(start,3), "end": round(end,3),
        "layout": cfg.layout, "crop_x": cfg.crop_x, "crop_y": cfg.crop_y,
        "secondary_x": cfg.secondary_x, "width": cfg.out_width, "height": cfg.out_height,
        "normalize": cfg.normalize_audio, "denoise": cfg.denoise_audio,
        # Mudar a régua do enquadramento precisa invalidar o vídeo já renderizado.
        "framing": [cfg.center_bias, cfg.safe_area, cfg.deadzone, cfg.stationary_threshold,
                    cfg.max_pan_speed, cfg.hold_seconds, cfg.min_face_coverage, 2]}
    cache_payload["internal_cuts"]=[(round(a,3),round(b,3),r) for a,b,r in cuts]
    accents = sorted({*(round(map_time(a, cuts), 3) for a, _, _ in cuts),
                      *energy_accents(media_index, start, end, cuts)})
    cache_payload["accents"] = accents
    cache_key = hashlib.sha256(json.dumps(cache_payload, sort_keys=True).encode()).hexdigest()[:24]
    cache_dir = job_dir / "cache" / cache_key
    base = cache_dir / "base.mp4"
    crop_json = cache_dir / "crop.json"
    cache_hit = base.exists() and crop_json.exists()
    if cache_hit:
        crop = CropPlan(**json.loads(crop_json.read_text(encoding="utf-8")))
    else:
        cache_dir.mkdir(parents=True, exist_ok=True)
        crop = crop_plan or plan_crop(source, info, start, end - start, cfg, media_index)
        crop = remap_crop(crop,cuts)
        if crop.split_x:
            # Duas pessoas separadas demais para um recorte só: a montagem passa a
            # ser tela dividida, com cada quadro centrado em uma delas. A decisão é
            # determinística, então um acerto de cache reproduz o mesmo vídeo.
            cfg = replace(cfg, layout="split", crop_x=crop.split_x[0], secondary_x=crop.split_x[1])
        crop_json.write_text(json.dumps(asdict(crop)), encoding="utf-8")
        cmd = write_sendcmd(crop, cache_dir / "camera.cmd")
        base_spec=RenderSpec(source, start, end, cache_dir, base, crop,
                          cmd.name if cmd else None, None, False, cuts, accents)
        render(base_spec, info, cfg)
        (cache_dir/"encoder.txt").write_text(base_spec.encoder_used or "libx264",encoding="utf-8")
    ass = build_ass(timeline_words, 0, output_duration, work / "captions.ass", cfg) if cfg.captions_enabled else None
    if ass:
        base_info = probe(base)
        full = CropPlan(base_info.width, base_info.height, [(0.0, 0)], 0, True)
        render(RenderSpec(base, 0, output_duration, work, out, full, None, ass.name, audio_copy=True),
               base_info, replace(cfg, normalize_audio=False, denoise_audio=False))
    else:
        shutil.copyfile(base, out)
    srt = build_srt(timeline_words, 0, output_duration, out.with_suffix(".srt"), cfg)
    thumbs = []
    for i, fraction in enumerate((0.15, 0.5, 0.8)):
        cached_thumb = cache_dir / f"cover{i}.jpg"
        if not cached_thumb.exists(): make_thumbnail(base, cached_thumb, at=output_duration*fraction)
        if cached_thumb.exists():
            thumb = out.with_name(f"{stem}-cover{i}.jpg")
            shutil.copyfile(cached_thumb, thumb); thumbs.append(thumb.name)
    actual = probe(out)
    # Avisos recalculados a cada render saem da lista antiga para não duplicar.
    warnings = [w for w in clip.get("warnings", []) if not w.startswith(
        ("Resolução", "Enquadramento", "Texto editado", "Tela dividida", "Aproximação"))]
    if cfg.layout in {"manual", "track", "active"} and crop.crop_w < cfg.out_width * 0.6:
        warnings.append("Resolução do recorte baixa: a exportação amplia a imagem original.")
    if crop.split_x:
        warnings.append("Tela dividida automática: as duas pessoas não cabiam no mesmo recorte.")
    if crop.zoom < 0.995:
        warnings.append(f"Aproximação automática de {1 / crop.zoom:.1f}x: o plano estava aberto "
                        "para um vídeo vertical.")
    if cfg.layout == "active":
        warnings.append("Enquadramento por atividade labial e áudio é uma estimativa; revise as trocas de participante.")
    if cfg.layout in {"track", "active"} and crop.target_source in {"saliency", "center"}:
        warnings.append("Enquadramento sem rosto detectado no trecho: seguiu a área de maior movimento. "
                        "Se cortou o que importa, use “Escolher posição” ou “Imagem inteira com fundo”.")
    if edit:
        warnings.append("Texto editado ou corte revisado: a avaliação editorial original não foi recalculada.")
    return {**clip, "revision": revision, "file": out.name,
            "thumbnail": thumbs[1 if len(thumbs) > 1 else 0] if thumbs else None,
            "thumbnails": thumbs, "subtitle": srt.name, "source_start": round(start, 3),
            "source_end": round(end, 3), "actual_duration": actual.duration,
            "planned_duration": round(output_duration, 3), "camera_moves": len(crop.keyframes),
            "faces_detected": crop.faces_found, "text": " ".join(w.text for w in timeline_words),
            "camera_mode": crop.camera_mode, "framing_source": crop.target_source,
            "emphasis_moments": accents,
            "render_cache_key": cache_key, "incremental_cache_hit": cache_hit,
            "video_encoder": (cache_dir/"encoder.txt").read_text(encoding="utf-8") if (cache_dir/"encoder.txt").exists() else "unknown",
            "internal_edits": [{"start":round(a,3),"end":round(b,3),"reason":r} for a,b,r in cuts],
            "hook": " ".join(w.text for w in inside[:12]) if edit else clip.get("hook", ""),
            "word_edits": replacements, "edit_settings": {k: getattr(requested, k) for k in EDIT_FIELDS},
            "warnings": list(dict.fromkeys(warnings)), "editorial_stale": bool(edit),
            "render_seconds": round(time.monotonic()-started, 2),
            "uncertain_words": sum(w.prob < 0.7 for w in inside)}
