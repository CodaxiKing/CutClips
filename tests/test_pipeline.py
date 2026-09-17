"""Testes das invariantes que realmente importam.

O foco não é cobertura: é provar as três coisas que quebram em pipeline de
clipe automático — duração que não bate, corte no meio da frase, e legenda
sobreposta.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipforge.captions import build_ass, group_words  # noqa: E402
from clipforge.config import Config  # noqa: E402
from clipforge.probe import probe  # noqa: E402
from clipforge.segment import build_sentences, fit_span, span_bounds  # noqa: E402
from clipforge.select import select_clips  # noqa: E402
from clipforge.transcribe import Transcript  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
OUT = ROOT / "out"
_TS = re.compile(r"Dialogue: \d+,(\d):(\d\d):(\d\d\.\d\d),(\d):(\d\d):(\d\d\.\d\d)")

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FALHA'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def _secs(m: re.Match, off: int) -> float:
    return int(m.group(off)) * 3600 + int(m.group(off + 1)) * 60 + float(m.group(off + 2))


def test_sentences(tr: Transcript) -> list:
    print("\n[frases]")
    s = build_sentences(tr)
    check("frases geradas", len(s) > 0, f"{len(s)} frases")
    check("ordem temporal", all(s[i].end <= s[i + 1].start + 1e-6 for i in range(len(s) - 1)))
    check("nenhuma frase vazia", all(x.text.strip() for x in s))
    check("nenhuma frase de duração zero", all(x.duration > 0 for x in s))
    covered = sum(x.word_count for x in s)
    check("todas as palavras usadas", covered == len(tr.words), f"{covered}/{len(tr.words)}")
    return s


def test_fit_span(sentences: list) -> None:
    print("\n[ajuste de duração]")
    cfg = Config(min_duration=20, max_duration=45)
    ok_min = ok_max = True
    tested = 0
    for i in range(len(sentences)):
        span = fit_span(sentences, i, i, cfg.min_duration, cfg.max_duration)
        if span is None:
            continue
        tested += 1
        a, b = span_bounds(sentences, *span)
        if b - a < cfg.min_duration - 1e-6:
            ok_min = False
        if b - a > cfg.max_duration + 1e-6:
            ok_max = False
    check("respeita duração mínima", ok_min, f"{tested} intervalos")
    check("respeita duração máxima", ok_max)


def test_hallucination_guard(sentences: list) -> None:
    """O contrato central: IDs inventados pelo modelo não podem virar clipe."""
    print("\n[proteção contra alucinação]")
    import clipforge.select as sel

    n = len(sentences)
    poison = {"clips": [
        {"first": 999, "last": 1200, "score": 99, "title": "id inexistente"},
        {"first": -5, "last": 3, "score": 95, "title": "id negativo"},
        {"first": 8, "last": 2, "score": 90, "title": "invertido"},
        {"first": 0, "last": 4, "score": 85, "title": "valido"},
        {"first": 2, "last": 6, "score": 80, "title": "sobreposto ao anterior"},
    ]}
    cfg = Config(llm_provider="anthropic", min_duration=10, max_duration=60)
    sel.PROVIDERS["anthropic"] = lambda s, u, c: __import__("json").dumps(poison)

    plans = select_clips(sentences, cfg=cfg)
    check("IDs fora da faixa descartados", all(0 <= p.first < n and 0 <= p.last < n for p in plans))
    check("nenhum intervalo invertido", all(p.last >= p.first for p in plans))
    check("nenhuma sobreposição",
          not any(a.first <= b.last and b.first <= a.last
                  for i, a in enumerate(plans) for b in plans[i + 1:]))
    check("duração derivada das frases, não do modelo",
          all(abs(p.duration - (sentences[p.last].end - sentences[p.first].start)) < 1e-6
              for p in plans))
    check("sobrou pelo menos um clipe válido", len(plans) >= 1, f"{len(plans)} clipes")


def test_captions(tr: Transcript, tmp: Path) -> None:
    print("\n[legendas]")
    cfg = Config()
    path = build_ass(tr.words, 0.0, 30.0, tmp / "t.ass", cfg)
    body = path.read_text(encoding="utf-8")
    events = [( _secs(m, 1), _secs(m, 4)) for m in _TS.finditer(body)]
    check("eventos gerados", len(events) > 0, f"{len(events)} eventos")
    check("todo evento tem duração positiva", all(e > s for s, e in events))
    overlaps = [(a, b) for (a, b), (c, d) in zip(events, events[1:]) if b > c + 1e-9]
    check("nenhuma sobreposição de legenda", not overlaps, f"{len(overlaps)} sobreposições")
    check("nada além do fim do clipe", all(e <= 30.0 + 1e-6 for _, e in events))
    groups = group_words(tr.words, cfg)
    check("grupos respeitam limite de palavras",
          all(len(g.words) <= cfg.caption_max_words for g in groups))


def test_rendered_output() -> None:
    print("\n[saída renderizada]")
    manifest_path = OUT / "manifest.json"
    if not manifest_path.exists():
        check("manifest existe", False, "rode o pipeline antes")
        return
    import json
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    clips = manifest["clips"]
    check("clipes produzidos", len(clips) > 0, f"{len(clips)} clipes")

    frame = 1 / 30
    worst = 0.0
    for c in clips:
        worst = max(worst, abs(c["actual_duration"] - c["planned_duration"]))
    check("duração real bate com a planejada (< 1 frame)", worst < frame,
          f"maior desvio {worst * 1000:.0f} ms")

    dims_ok = True
    for c in clips:
        info = probe(OUT / "clips" / c["file"])
        if (info.width, info.height) != (1080, 1920):
            dims_ok = False
    check("todos 1080x1920", dims_ok)
    check("nenhum clipe vazio",
          all((OUT / "clips" / c["file"]).stat().st_size > 10_000 for c in clips))


def main() -> int:
    tr = Transcript.from_json(FIX / "transcript.json")
    tmp = ROOT / "out" / "_test"
    tmp.mkdir(parents=True, exist_ok=True)

    sentences = test_sentences(tr)
    test_fit_span(sentences)
    test_hallucination_guard(sentences)
    test_captions(tr, tmp)
    test_rendered_output()

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} FALHA(S): " + ", ".join(failures))
        return 1
    print("todas as invariantes passaram")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
