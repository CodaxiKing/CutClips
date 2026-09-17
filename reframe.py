"""Reframe 16:9 -> 9:16, rastreamento de rosto e estimativa visual de fala.

Estratégia:
  1. amostra frames pequenos via ffmpeg (rápido, sem seek impreciso do cv2)
  2. detecta rostos (frontais e de perfil), escolhe o alvo com continuidade temporal
  3. suaviza a trajetória e aplica zona morta (mata o tremor)
  4. emite keyframes para o filtro `sendcmd` do ffmpeg -> crop animado

A câmera prefere o centro da imagem, mas essa preferência nunca pode empurrar o
assunto para fora da área segura do recorte: quem fala encostado na lateral do
quadro continua enquadrado. Sem rosto confiável em nenhum frame, o recorte segue
a região com mais movimento e detalhe (gameplay, slide, tela compartilhada) e só
então cai para o centro.
"""
from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import CONFIG, Config
from .probe import MediaInfo

_SAMPLE_WIDTH = 480
DETECT_WIDTH = 640           # largura de trabalho da detecção; rosto minúsculo em 1080p é ruído
MIN_FACE_FRACTION = 0.07     # altura mínima do rosto em relação à altura do frame
# Versão da detecção gravada no índice. Subir aqui refaz o índice e faz os jobs
# antigos ignorarem rostos detectados pelo método anterior.
FACE_INDEX_VERSION = 3
_local = threading.local()


@dataclass
class CropPlan:
    crop_w: int
    crop_h: int
    keyframes: list[tuple[float, int]]  # (tempo relativo ao clipe, x em px do original)
    y: int
    static: bool
    faces_found: int = 0
    camera_mode: str = "static"
    target_source: str = "faces"  # faces|saliency|center|manual|full
    # Preenchido quando duas pessoas não cabem no mesmo recorte: posições
    # normalizadas para a tela dividida. Quem renderiza troca o layout.
    split_x: tuple[float, float] | None = None
    # Fração do maior recorte possível. Abaixo de 1 a câmera aproximou.
    zoom: float = 1.0

    @property
    def x0(self) -> int:
        return self.keyframes[0][1] if self.keyframes else 0


# --------------------------------------------------------------------------- #
# detecção de rostos
# --------------------------------------------------------------------------- #

def _cv2():
    import cv2
    return cv2


def _detectors() -> dict:
    """Detectores por thread: o plano de corte roda em paralelo por clipe."""
    cached = getattr(_local, "detectors", None)
    if cached is not None:
        return cached
    cv2 = _cv2()
    haar = cv2.data.haarcascades
    yunet = None
    model = os.getenv("CLIPFORGE_FACE_MODEL", "")
    if model and Path(model).exists() and hasattr(cv2, "FaceDetectorYN"):
        try:
            yunet = cv2.FaceDetectorYN.create(model, "", (320, 320), 0.6, 0.3, 5000)
        except Exception:
            yunet = None
    cached = {
        "frontal": cv2.CascadeClassifier(haar + "haarcascade_frontalface_alt2.xml"),
        "frontal_loose": cv2.CascadeClassifier(haar + "haarcascade_frontalface_default.xml"),
        "profile": cv2.CascadeClassifier(haar + "haarcascade_profileface.xml"),
        "yunet": yunet,
    }
    _local.detectors = cached
    return cached


def _merge_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """NMS simples: o mesmo rosto costuma aparecer em mais de um detector."""
    kept: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda b: b[2] * b[3], reverse=True):
        x, y, w, h = box
        overlap = False
        for kx, ky, kw, kh in kept:
            ix = max(0, min(x + w, kx + kw) - max(x, kx))
            iy = max(0, min(y + h, ky + kh) - max(y, ky))
            inter = ix * iy
            if inter and inter / max(1, min(w * h, kw * kh)) > 0.35:
                overlap = True
                break
        if not overlap:
            kept.append(box)
    return kept


def _plausible(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Descarta o que não tem proporção de rosto e o que é pequeno demais.

    Um "rosto" com 1/10 da área do maior do quadro é quase sempre textura de
    fundo (quadro na parede, plateia desfocada) e roubaria a câmera de quem fala.
    """
    boxes = [b for b in boxes if b[3] > 0 and 0.55 <= b[2] / b[3] <= 1.8]
    if not boxes:
        return []
    biggest = max(b[2] * b[3] for b in boxes)
    return [b for b in boxes if b[2] * b[3] >= biggest * 0.12]


def detect_faces(gray: np.ndarray, bgr: np.ndarray | None = None) -> list[tuple[int, int, int, int]]:
    """Rostos (x, y, w, h) em pixels do frame recebido.

    Cascatas frontais perdem quem vira o rosto para o interlocutor — o caso mais
    comum em podcast e entrevista, e a causa clássica do enquadramento que
    "esquece" o convidado. O perfil é procurado nos dois sentidos e as caixas são
    fundidas. Com CLIPFORGE_FACE_MODEL apontando para o YuNet (.onnx), a detecção
    fica bem melhor e as cascatas viram só a rede de segurança.
    """
    cv2 = _cv2()
    det = _detectors()
    height, width = gray.shape[:2]
    side = max(18, int(round(height * MIN_FACE_FRACTION)))

    if det["yunet"] is not None and bgr is not None:
        try:
            det["yunet"].setInputSize((width, height))
            _, raw = det["yunet"].detect(bgr)
            boxes = [(int(f[0]), int(f[1]), int(f[2]), int(f[3])) for f in (raw if raw is not None else [])]
            if boxes:
                return _plausible(boxes)
        except Exception:
            pass

    boxes = [tuple(map(int, b)) for b in
             det["frontal"].detectMultiScale(gray, 1.15, 5, minSize=(side, side))]
    if not boxes:
        boxes = [tuple(map(int, b)) for b in
                 det["frontal_loose"].detectMultiScale(gray, 1.15, 6, minSize=(side, side))]
    for flip in (False, True):
        source = cv2.flip(gray, 1) if flip else gray
        for x, y, w, h in det["profile"].detectMultiScale(source, 1.15, 7, minSize=(side, side)):
            boxes.append((int(width - x - w) if flip else int(x), int(y), int(w), int(h)))
    return _plausible(_merge_boxes(boxes))


def _score_candidate(cx: float, area: float, biggest: float, last: float | None,
                     width: float) -> float:
    """Tamanho manda quando não há histórico; depois, continuidade manda."""
    size = area / max(1e-9, biggest)
    if last is None:
        return size
    continuity = max(0.0, 1.0 - abs(cx - last) / max(1.0, 0.25 * width))
    return size + 1.5 * continuity


# --------------------------------------------------------------------------- #
# amostragem e rastreamento
# --------------------------------------------------------------------------- #

def crop_geometry(info: MediaInfo, cfg: Config = CONFIG) -> tuple[int, int]:
    """Maior retângulo 9:16 que cabe no frame de origem (par, para yuv420p)."""
    target = cfg.out_width / cfg.out_height
    crop_w = int(round(info.height * target))
    crop_h = info.height
    if crop_w > info.width:
        crop_w = info.width
        crop_h = int(round(info.width / target))
    crop_w -= crop_w % 2
    crop_h -= crop_h % 2
    return max(2, crop_w), max(2, crop_h)


def _sample_frames(video: Path, start: float, duration: float, info: MediaInfo,
                   cfg: Config) -> tuple[np.ndarray, float]:
    """Frames BGR reduzidos. Devolve (array NxHxWx3, escala origem/amostra)."""
    sw = _SAMPLE_WIDTH if info.width > _SAMPLE_WIDTH else info.width
    sw -= sw % 2
    sh = int(round(sw * info.height / info.width))
    sh -= sh % 2
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video),
        "-vf", f"fps={cfg.track_fps},scale={sw}:{sh}",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    frame_bytes = sw * sh * 3
    n = len(proc.stdout) // frame_bytes if frame_bytes else 0
    if n == 0:
        return np.zeros((0, sh, sw, 3), dtype=np.uint8), info.width / sw
    arr = np.frombuffer(proc.stdout[: n * frame_bytes], dtype=np.uint8)
    return arr.reshape(n, sh, sw, 3), info.width / sw


def _track_centers(frames: np.ndarray, cfg: Config, speech: list[bool] | None = None,
                   speakers: list[str | None] | None = None,
                   observed: list[list[tuple[float, float, float]]] | None = None
                   ) -> tuple[list[float | None], int, list[int]]:
    """Centro x do alvo por frame amostrado (em px da amostra). None = sem detecção.

    `observed` recebe, por amostra, todos os rostos plausíveis como (cx, cy, altura)
    em px da amostra. É a matéria-prima das duas decisões que o alvo único não
    permite: se duas pessoas cabem no mesmo recorte e se o plano está aberto demais.
    """
    cv2 = _cv2()
    centers: list[float | None] = []
    found = 0
    last: float | None = None
    previous_faces = []
    previous_gray = None
    cuts = [0]
    challenger = None
    streak = 0
    previous_speaker = None
    width = float(frames.shape[2]) if frames.size else 1.0

    for frame_index, frame in enumerate(frames):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        if previous_gray is not None and np.mean(np.abs(gray.astype(float) - previous_gray.astype(float))) > 65:
            last, previous_faces, challenger, streak = None, [], None, 0
            cuts.append(frame_index)
        previous_gray = gray
        faces = detect_faces(gray, frame)
        if observed is not None:
            observed.append([(x + w / 2.0, y + h / 2.0, float(h)) for x, y, w, h in faces])
        if not faces:
            centers.append(None)
            continue
        found += 1
        biggest = max(w * h for (_, _, w, h) in faces)
        moving = []
        current_faces = []
        for x, y, w, h in faces:
            cx = x + w / 2.0
            roi = cv2.resize(gray[y:y+h, x:x+w], (32, 32)).astype(float) / 255
            near = min(previous_faces, key=lambda p: abs(p[0] - cx), default=None)
            motion = 0.0
            if near is not None and abs(near[0] - cx) < w * 0.65:
                delta = np.abs(roi - near[1])
                motion = max(0, float(delta[19:30, 7:25].mean() - delta[3:15, 5:27].mean()))
            moving.append((cx, motion))
            current_faces.append((cx, roi))
        previous_faces = current_faces
        # Sem histórico o rosto maior manda; com histórico, a continuidade manda.
        # Tamanho sozinho não identifica o locutor.
        best = max(faces, key=lambda f: _score_candidate(f[0] + f[2] / 2.0, f[2] * f[3],
                                                         biggest, last, width))
        cx = best[0] + best[2] / 2.0
        audible = speech is None or (frame_index < len(speech) and speech[frame_index])
        if cfg.layout == "active" and audible and moving:
            candidate, activity = max(moving, key=lambda p: p[1])
            if activity > 0.025:
                streak = streak + 1 if challenger is not None and abs(challenger - candidate) < 30 else 1
                challenger = candidate
                current_speaker = speakers[frame_index] if speakers and frame_index < len(speakers) else None
                speaker_changed = bool(current_speaker and previous_speaker and current_speaker != previous_speaker)
                pause_boundary = frame_index > 0 and speech is not None and frame_index-1 < len(speech) and not speech[frame_index-1]
                if streak >= max(2, round(cfg.track_fps * 0.5)) and (speaker_changed or pause_boundary or frame_index in cuts):
                    cx = candidate
            else:
                streak = 0
        centers.append(cx)
        last = cx
        if speakers and frame_index < len(speakers) and speakers[frame_index]: previous_speaker=speakers[frame_index]
    return centers, found, cuts


def _saliency_centers(frames: np.ndarray, window: float) -> np.ndarray | None:
    """Centro da faixa vertical mais "viva" quando não há rosto para seguir.

    Soma detalhe (bordas) e movimento por coluna e escolhe a janela da largura do
    recorte com maior energia. É o que salva gameplay, tela compartilhada, slide e
    plano aberto, onde o recorte central mostra justamente o que não importa.
    """
    n = frames.shape[0]
    if n == 0:
        return None
    width = frames.shape[2]
    span = max(1, min(width, int(round(window))))
    detail = np.zeros((n, width), dtype=np.float32)
    motion = np.zeros((n, width), dtype=np.float32)
    previous = None
    for i in range(n):
        gray = frames[i].mean(axis=2, dtype=np.float32)
        detail[i, :-1] = np.abs(np.diff(gray, axis=1)).mean(axis=0)
        if previous is not None:
            motion[i] = np.abs(gray - previous).mean(axis=0)
        previous = gray
    if n > 1:
        motion[0] = motion[1]
    score = detail / max(float(detail.max()), 1e-6) + 2.0 * motion / max(float(motion.max()), 1e-6)
    if span >= width:
        return np.full(n, width / 2.0)
    cumulative = np.cumsum(np.pad(score, ((0, 0), (1, 0))), axis=1)
    windows = cumulative[:, span:] - cumulative[:, :-span]
    # Um assunto menor que o recorte cabe inteiro em muitas janelas empatadas, e
    # argmax pegaria sempre a primeira — o assunto encostaria na borda direita.
    # O centro do platô de janelas boas deixa o assunto no meio do recorte.
    best = windows.max(axis=1, keepdims=True)
    plateau = windows >= best - 0.005 * np.abs(best)
    positions = np.arange(windows.shape[1], dtype=float)
    left = (plateau * positions).sum(axis=1) / np.maximum(1, plateau.sum(axis=1))
    return left + span / 2.0


def _smooth_pad(values: np.ndarray, pad: int) -> np.ndarray:
    """Prolonga a tendência nas pontas antes da média móvel.

    Repetir a borda achata a rampa: numa panorâmica contínua a câmera chega ao fim
    do clipe com quase um segundo de atraso, e o assunto sai de quadro justo no
    final. A reflexão ímpar mantém a inclinação e a média móvel não atrasa nada.
    """
    if pad <= 0:
        return values
    if len(values) > pad:
        return np.pad(values, (pad, pad), mode="reflect", reflect_type="odd")
    return np.pad(values, (pad, pad), mode="edge")


def _fill_and_smooth(centers: list[float | None], default: float,
                     cfg: Config) -> np.ndarray:
    n = len(centers)
    if n == 0:
        return np.array([default])

    vals = np.full(n, np.nan, dtype=np.float64)
    for i, c in enumerate(centers):
        if c is not None:
            vals[i] = c

    if np.all(np.isnan(vals)):
        return np.full(n, default)

    idx = np.arange(n)
    good = ~np.isnan(vals)
    filled = np.interp(idx, idx[good], vals[good])  # preenche buracos curtos

    # Buraco longo (rosto de costas, corte para a plateia, B-roll): segura a
    # última posição conhecida em vez de arrastar a câmera lentamente pelo quadro
    # até onde o próximo rosto aparecer.
    limit = max(2, int(round(cfg.hold_seconds * cfg.track_fps)))
    gap = None
    for i in range(n + 1):
        missing = i < n and not good[i]
        if missing and gap is None:
            gap = i
        elif not missing and gap is not None:
            if i - gap > limit and gap > 0:
                filled[gap:i] = vals[gap - 1]
            gap = None
    vals = filled

    win = max(1, int(round(cfg.smooth_seconds * cfg.track_fps)))
    if win > 1:
        padded = _smooth_pad(vals, win // 2)
        kernel = np.ones(win) / win
        vals = np.convolve(padded, kernel, mode="valid")[:n]
    return vals


def _camera_path(targets: np.ndarray, crop_w: int, max_x: int, duration: float,
                 source_fps: float, cfg: Config) -> tuple[list[tuple[float, int]], str]:
    """Turn sparse face targets into a comfortable, frame-dense camera path.

    A face target says where a perfect face-centred crop would sit. The camera
    leans toward the source centre, but only as far as the safe area allows: a
    subject standing off to one side keeps a comfortable margin instead of being
    dragged to the edge of the crop — or out of it. Small movements collapse to a
    tripod-like static shot. Larger movements are low-pass filtered and rate
    limited before being sampled at render cadence.
    """
    if not len(targets) or max_x <= 0:
        return [(0.0, 0)], "static"
    center = max_x / 2.0
    # Os alvos chegam sem limitar às bordas: um rosto encostado na lateral pede um
    # recorte impossível, e é justamente aí que a margem precisa ser medida a
    # partir do rosto real, não do recorte já achatado contra a borda.
    targets = np.asarray(targets, dtype=float)
    # Distância máxima tolerada entre o assunto e o centro do recorte.
    safe = max(1.0, cfg.safe_area / 2.0 * crop_w)
    # A preferência pelo centro vale só enquanto o assunto continua dentro da
    # área segura; passou disso, quem manda é o alvo.
    desired = targets * (1.0 - cfg.center_bias) + center * cfg.center_bias
    desired = np.clip(desired, targets - safe, targets + safe)
    desired = np.clip(desired, 0, max_x)

    # A nearly stationary subject gets one locked crop for the entire clip.
    span = float(np.percentile(desired, 90) - np.percentile(desired, 10)) if len(desired) > 2 else 0.0
    if span <= cfg.stationary_threshold * crop_w:
        locked = float(np.median(desired))
        anchor = float(np.median(targets))
        if abs(locked - center) <= cfg.deadzone * crop_w:
            locked = center
        locked = float(np.clip(locked, anchor - safe, anchor + safe))
        return [(0.0, int(round(np.clip(locked, 0, max_x))))], "locked"

    sample_times = np.arange(len(desired), dtype=float) / cfg.track_fps
    motion_fps = min(max(10.0, cfg.motion_fps), max(10.0, source_fps or 30.0))
    count = max(2, int(np.ceil(duration * motion_fps)) + 1)
    times = np.linspace(0.0, duration, count)
    dense = np.interp(times, sample_times, desired, left=desired[0], right=desired[-1])
    dense_targets = np.interp(times, sample_times, targets, left=targets[0], right=targets[-1])

    # A second low-pass pass operates at output cadence, so the values sent to
    # FFmpeg never contain the 4 fps stair steps from face detection.
    window = max(3, int(round(cfg.smooth_seconds * motion_fps)))
    if window % 2 == 0:
        window += 1
    padded = _smooth_pad(dense, window // 2)
    dense = np.convolve(padded, np.ones(window) / window, mode="valid")[:len(dense)]
    dense = np.clip(dense, 0, max_x)

    # Rate limit in pixels per frame. This is the key protection against a
    # visible teleport when the detector changes faces. The dead zone keeps the
    # camera parked while the subject only breathes.
    max_step = cfg.max_pan_speed * crop_w / motion_fps
    hold = cfg.deadzone * crop_w * 0.5
    path = np.empty_like(dense)
    # O primeiro frame já entra enquadrado: partir do centro deixaria o assunto
    # fora do quadro justo na abertura, que é onde o clipe prende ou perde.
    path[0] = np.clip(dense[0], 0, max_x)
    for i in range(1, len(dense)):
        delta = dense[i] - path[i - 1]
        if abs(delta) <= hold:
            path[i] = path[i - 1]   # zona morta: o assunto só respira, a câmera fica parada
            continue
        # Fora da zona morta a câmera vai até o alvo, não até a borda da zona:
        # descontar a zona morta do passo deixava um atraso fixo atrás do assunto.
        path[i] = np.clip(path[i - 1] + np.clip(delta, -max_step, max_step), 0, max_x)

    # Movimento único e contínuo é mais agradável que uma sucessão de correções.
    # Quando o assunto vai de um lado a outro, ou fica indo e voltando entre
    # posições recorrentes, uma reta no tempo vale mais que perseguir cada quadro.
    line = _linear_path(times, dense, dense_targets, path, crop_w, max_x, safe, cfg)
    if line is not None:
        path, camera_mode = line, "linear"
    else:
        camera_mode = "smooth"

    keyframes: list[tuple[float, int]] = []
    last_x = None
    for t, value in zip(times, path):
        x = int(round(value))
        if x != last_x:
            keyframes.append((round(float(t), 3), x))
            last_x = x
    return keyframes or [(0.0, int(round(center)))], camera_mode


def _linear_path(times: np.ndarray, dense: np.ndarray, dense_targets: np.ndarray,
                 path: np.ndarray, crop_w: int, max_x: int, safe: float,
                 cfg: Config) -> np.ndarray | None:
    """Reta no tempo quando ela descreve o movimento tão bem quanto a perseguição.

    Dois casos justificam trocar a câmera reativa por uma animação linear:
    o assunto atravessa o quadro em uma direção só (a reta é o próprio movimento),
    ou ele fica indo e voltando entre posições recorrentes — aí perseguir vira um
    vaivém cansativo, e uma travelling lenta cobre as duas pontas.
    """
    if len(times) < 3 or times[-1] <= times[0]:
        return None
    slope, intercept = np.polyfit(times, dense, 1)
    line = np.clip(slope * times + intercept, 0, max_x)
    drift = float(np.max(np.abs(dense - line)))

    # Vaivém: quantas vezes a câmera reativa inverteu o sentido. Contado em passos
    # de 0,25 s — por quadro o movimento é sub-pixel e nenhuma inversão apareceria.
    stride = max(1, int(round(len(times) / max(1.0, times[-1]) / 4.0)))
    coarse = np.diff(path[::stride])
    direction = np.sign(coarse[np.abs(coarse) > 1.0])
    reversals = int(np.count_nonzero(np.diff(direction))) if len(direction) > 1 else 0
    ping_pong = reversals >= max(2, int(times[-1] / 4.0))

    fits = drift <= cfg.linear_tolerance * crop_w
    # No vaivém a régua afrouxa: enquanto a reta segurar o assunto dentro do
    # quadro, parar de persegui-lo é melhor que ir e voltar oito vezes. Só quando
    # as posições recorrentes não cabem juntas o rastreamento volta a valer a pena.
    covers = bool(np.all(np.abs(dense_targets - line) <= crop_w * 0.40))
    # Vaivém de amplitude pequena é indecisão da câmera, não movimento do assunto:
    # a reta quase plana que sai daqui equivale a deixar o enquadramento parado.
    indecisive = float(np.max(path) - np.min(path)) <= cfg.stationary_threshold * crop_w
    if fits or (ping_pong and (covers or indecisive)):
        return line
    return None


# --------------------------------------------------------------------------- #
# plano de corte
# --------------------------------------------------------------------------- #

def _targets_from_index(media_index: dict, info: MediaInfo, start: float, duration: float,
                        crop_w: int, max_x: int, cfg: Config) -> tuple[np.ndarray, int] | None:
    """Alvos por amostra a partir do índice de rostos (1 Hz). None = índice fraco."""
    # Índice gravado por uma versão anterior da detecção: os rostos dele são
    # justamente os que enquadravam errado. Melhor reanalisar o trecho.
    if media_index.get("signature", {}).get("version", 0) < FACE_INDEX_VERSION:
        return None
    entries = [x for x in media_index.get("faces", [])
               if start - 1 <= x.get("time", -1) <= start + duration + 1]
    if not entries:
        return None
    times: list[float] = []
    points: list[float] = []
    last: float | None = None
    found = 0
    for item in entries:
        faces = item.get("faces", [])
        if not faces:
            continue
        biggest = max(float(f.get("size", 0.0)) for f in faces)
        target = max(faces, key=lambda f: _score_candidate(float(f.get("x", .5)) * info.width,
                                                           float(f.get("size", 0.0)), biggest,
                                                           last, float(info.width)))
        last = float(target.get("x", .5)) * info.width
        found += 1
        times.append(max(0.0, float(item["time"]) - start))
        points.append(last - crop_w / 2.0)  # pode estourar a borda; quem apara é _camera_path
    # Poucos segundos com rosto significam detecção duvidosa (ou trecho sem
    # gente): melhor reanalisar quadro a quadro do que confiar em duas detecções.
    if found < max(2, cfg.min_face_coverage * len(entries)):
        return None
    samples = max(1, int(np.ceil(duration * cfg.track_fps)))
    regular = np.arange(samples) / cfg.track_fps
    sparse = np.interp(regular, np.asarray(times), np.asarray(points, dtype=float))
    # Amostras dentro de um buraco longo do índice ficam vazias e são tratadas
    # pela mesma retenção usada no rastreio quadro a quadro.
    seen = {min(samples - 1, int(round(t * cfg.track_fps))) for t in times}
    centers = [float(sparse[i]) if i in seen else None for i in range(samples)]
    return _fill_and_smooth(centers, max_x / 2.0, cfg), found


def split_tile_width(info: MediaInfo, cfg: Config) -> int:
    """Largura de cada metade na tela dividida. Espelha o cálculo do filtergraph."""
    half = max(1, cfg.out_height // 2)
    return max(2, min(info.width, round(info.height * cfg.out_width / half)) // 2 * 2)


def _split_decision(observed: list[list[tuple[float, float, float]]], scale: float,
                    crop_w: int, info: MediaInfo, cfg: Config) -> tuple[float, float] | None:
    """Posições das duas pessoas quando elas não cabem no mesmo recorte.

    É o caso que a câmera única resolve mal: perseguir quem fala produz vaivém, e
    ficar no meio mostra o vão entre os dois. Só vale quando o par aparece separado
    na maior parte do trecho — dois rostos num quadro isolado é gente passando.
    """
    if not cfg.auto_split or not observed:
        return None
    lows: list[float] = []
    highs: list[float] = []
    seen = 0
    for faces in observed:
        if not faces:
            continue
        seen += 1
        if len(faces) < 2:
            continue
        xs = sorted(f[0] for f in faces)
        if (xs[-1] - xs[0]) * scale > crop_w * 1.05:
            lows.append(xs[0] * scale)
            highs.append(xs[-1] * scale)
    if seen < max(4, int(cfg.track_fps * 2)) or len(lows) < seen * cfg.split_coverage:
        return None
    tile = split_tile_width(info, cfg)
    room = max(1, info.width - tile)
    left = float(np.clip((float(np.median(lows)) - tile / 2) / room, 0, 1))
    right = float(np.clip((float(np.median(highs)) - tile / 2) / room, 0, 1))
    return (round(left, 4), round(right, 4))


def _zoom_factor(observed: list[list[tuple[float, float, float]]], scale: float,
                 crop_h: int, cfg: Config) -> float:
    """Quanto encolher o recorte para aproximar um plano aberto.

    Recortar menos e ampliar mais é o que o AutoFlip faz: um rosto de 5% da altura
    vira um ponto no Short. O piso protege a nitidez — abaixo dele a ampliação
    aparece mais que o enquadramento melhora.
    """
    if not cfg.auto_zoom or crop_h <= 0:
        return 1.0
    heights = [face[2] * scale for faces in observed for face in faces]
    if len(heights) < 4:
        return 1.0
    face = float(np.median(heights))
    if face <= 0:
        return 1.0
    # Teto de ampliação total. Um recorte 9:16 de 1080p já sai em 1,78x, então o
    # limite é sobre o resultado final, não sobre o quanto o zoom acrescenta:
    # fonte em 4K aceita aproximar bem mais que fonte em 1080p.
    floor = max(cfg.min_zoom, min(1.0, cfg.out_height / (crop_h * cfg.max_upscale)))
    return float(min(1.0, max(floor, face / (cfg.face_target * crop_h))))


def plan_crop(video: Path, info: MediaInfo, start: float, duration: float,
              cfg: Config = CONFIG, media_index: dict | None = None) -> CropPlan:
    crop_w, crop_h = crop_geometry(info, cfg)
    y = max(0, round((info.height - crop_h) * cfg.crop_y))
    max_x = max(0, info.width - crop_w)
    if cfg.layout in {"manual", "fit", "split"}:
        return CropPlan(crop_w, crop_h, [(0.0, round(max_x * cfg.crop_x))], y, static=True,
                        target_source="manual")

    # Origem já vertical o suficiente: nada para seguir.
    if max_x == 0:
        return CropPlan(crop_w, crop_h, [(0.0, 0)], y, static=True, target_source="full")

    if media_index and cfg.layout == "track":
        indexed = _targets_from_index(media_index, info, start, duration, crop_w, max_x, cfg)
        if indexed is not None:
            xs, found = indexed
            keyframes, camera_mode = _camera_path(xs, crop_w, max_x, duration, info.fps, cfg)
            return CropPlan(crop_w, crop_h, keyframes, y, len(keyframes) == 1, found,
                            camera_mode, "faces")

    frames, scale = _sample_frames(video, start, duration, info, cfg)
    speech = None
    if cfg.layout == "active" and info.has_audio:
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(duration),
                              "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                              "-f", "f32le", "pipe:1"], capture_output=True)
        audio = np.frombuffer(pcm.stdout, dtype="<f4")
        step = max(1, round(16000 / cfg.track_fps))
        speech = [bool(np.sqrt(np.mean(audio[i:i+step] ** 2)) > 0.008) for i in range(0, len(audio), step)]
    speaker_frames = None
    if cfg.layout == "active" and media_index:
        speaker_frames=[]
        words=media_index.get("audio_words",[])
        for i in range(len(frames)):
            t=start+i/cfg.track_fps
            speaker_frames.append(next((w.get("speaker") for w in words if w.get("start",0)<=t<=w.get("end",0)),None))
    observed: list[list[tuple[float, float, float]]] = []
    centers, found, cuts = _track_centers(frames, cfg, speech, speaker_frames, observed)
    sample_center = (frames.shape[2] / 2.0) if frames.size else (info.width / 2.0 / scale)
    source = "faces"

    if found == 0:
        # Nenhum rosto no trecho. Recortar o centro no escuro é o que faz o clipe
        # mostrar a parede em vez do jogo, do slide ou da mão que aponta.
        saliency = _saliency_centers(frames, crop_w / scale)
        if saliency is not None:
            centers, source = [float(x) for x in saliency], "saliency"
        else:
            source = "center"

    if found:
        split = _split_decision(observed, scale, crop_w, info, cfg)
        if split is not None:
            # A montagem vira tela dividida; as posições vão para quem renderiza.
            return CropPlan(crop_w, crop_h, [(0.0, round(max_x * cfg.crop_x))], y, static=True,
                            faces_found=found, camera_mode="split", target_source="faces",
                            split_x=split)

    zoom = _zoom_factor(observed, scale, crop_h, cfg) if found else 1.0
    if zoom < 0.995:
        crop_w = max(2, int(crop_w * zoom) & ~1)
        crop_h = max(2, int(crop_h * zoom) & ~1)
        max_x = max(0, info.width - crop_w)
        # Com o recorte menor a altura passa a importar: centra no rosto.
        ys = [face[1] * scale for faces in observed for face in faces]
        middle = float(np.median(ys)) if ys else info.height / 2.0
        y = int(np.clip(middle - crop_h / 2.0, 0, max(0, info.height - crop_h)))

    smooth = _fill_and_smooth(centers, sample_center, cfg)

    # amostra -> px do original, e centro do alvo -> canto esquerdo do crop
    xs = smooth * scale - crop_w / 2.0
    keyframes, camera_mode = _camera_path(xs, crop_w, max_x, duration, info.fps, cfg)

    return CropPlan(crop_w, crop_h, keyframes, y,
                    static=len(keyframes) == 1, faces_found=found, camera_mode=camera_mode,
                    target_source=source, zoom=round(zoom, 3))


def write_sendcmd(plan: CropPlan, path: Path) -> Path | None:
    """Arquivo de comandos do ffmpeg. None se o crop é estático."""
    if plan.static or len(plan.keyframes) < 2:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{t:.3f} crop x {x};" for t, x in plan.keyframes]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
