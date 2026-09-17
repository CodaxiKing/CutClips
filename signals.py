"""Cheap audiovisual signals used to audit the first two seconds of a clip."""
from __future__ import annotations

import subprocess
from pathlib import Path
import numpy as np


def opening_signals(video: Path, start: float, duration: float = 2.0) -> dict:
    length = max(.2, min(duration, 2.0))
    audio_proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
        "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"], capture_output=True)
    audio = np.frombuffer(audio_proc.stdout, dtype="<f4")
    if len(audio):
        frame = 320
        rms = np.asarray([np.sqrt(np.mean(audio[i:i+frame]**2)) for i in range(0, len(audio), frame) if len(audio[i:i+frame])])
        threshold = max(.003, float(np.percentile(rms, 70))*.18) if len(rms) else .003
        first_voice = next((i*frame/16000 for i, x in enumerate(rms) if x > threshold), length)
        energy = float(np.clip(np.mean(rms)/.12, 0, 1)) if len(rms) else 0.
    else:
        first_voice, energy = length, 0.
    video_proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
        "-i", str(video), "-vf", "fps=4,scale=160:90,format=gray", "-f", "rawvideo", "pipe:1"], capture_output=True)
    size = 160*90; count = len(video_proc.stdout)//size
    frames = np.frombuffer(video_proc.stdout[:count*size], dtype=np.uint8).reshape((-1, size)) if count else np.empty((0,size))
    motion = float(np.mean(np.abs(np.diff(frames.astype(float), axis=0)))/32) if len(frames)>1 else 0.
    visual = float(np.clip(motion, 0, 1))
    score = float(np.clip(5*(.45*energy + .35*visual + .20*(1-first_voice/length)), 0, 5))
    return {"audio_energy": round(energy, 3), "visual_change": round(visual, 3),
            "initial_silence": round(first_voice, 3), "audiovisual_hook": round(score, 2)}


def visual_timeline(video: Path) -> dict:
    """Low-cost whole-video visual index, designed to run beside transcription."""
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf",
        "fps=0.5,scale=160:90,format=gray", "-f", "rawvideo", "pipe:1"], capture_output=True)
    size = 160*90; count = len(proc.stdout)//size
    if count < 2:
        return {"scene_changes": [], "visual_activity": 0.0}
    frames = np.frombuffer(proc.stdout[:count*size], dtype=np.uint8).reshape((-1,size)).astype(float)
    changes = np.mean(np.abs(np.diff(frames, axis=0)), axis=1)
    threshold = max(35., float(np.percentile(changes, 95)))
    cuts = [round((i+1)*2., 1) for i, value in enumerate(changes) if value >= threshold]
    return {"scene_changes": cuts[:200], "visual_activity": round(float(np.clip(changes.mean()/32,0,1)),3)}
