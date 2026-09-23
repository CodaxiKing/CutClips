"""Persistent multimodal index shared by selection, reframing and captions."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import os
from pathlib import Path
import numpy as np

from .transcribe import Transcript
from .segment import Sentence
from .signals import visual_timeline
from .reframe import DETECT_WIDTH, FACE_INDEX_VERSION, detect_faces


def _signature(video: Path) -> dict:
    stat = video.stat()
    return {"path": str(video.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "version": FACE_INDEX_VERSION}


def _ocr(frame: np.ndarray) -> list[dict]:
    if not shutil.which("tesseract"):
        return []
    ok, encoded = __import__("cv2").imencode(".png", frame)
    if not ok: return []
    proc = subprocess.run(["tesseract", "stdin", "stdout", "tsv"], input=encoded.tobytes(), capture_output=True)
    out = []
    h, w = frame.shape[:2]
    for line in proc.stdout.decode("utf-8", "ignore").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 12 and cols[11].strip():
            try:
                x,y,bw,bh,conf = map(float, cols[6:11])
                if conf >= 45: out.append({"text":cols[11][:80], "x":round(x/w,3), "y":round(y/h,3),
                    "w":round(bw/w,3), "h":round(bh/h,3), "confidence":round(conf/100,2)})
            except ValueError: pass
    return out[:30]


def _object_model():
    weights=os.getenv("CUTCLIPS_OBJECT_MODEL"); config=os.getenv("CUTCLIPS_OBJECT_CONFIG")
    labels_path=os.getenv("CUTCLIPS_OBJECT_LABELS")
    if not weights or not Path(weights).exists(): return None,[]
    import cv2
    model=cv2.dnn_DetectionModel(weights,config or "")
    model.setInputSize(320,320); model.setInputScale(1/127.5); model.setInputMean((127.5,127.5,127.5)); model.setInputSwapRB(True)
    labels=Path(labels_path).read_text(encoding="utf-8").splitlines() if labels_path and Path(labels_path).exists() else []
    return model,labels


def build_media_index(video: Path, transcript: Transcript, sentences: list[Sentence], path: Path,
                      precomputed_visual: dict | None = None,
                      windows: list[tuple[float, float]] | None = None) -> dict:
    """`windows` limita a indexação a trechos: percorrer 6h de VOD segundo a
    segundo levaria mais tempo que transcrever os momentos que importam."""
    signature = _signature(video)
    if windows:
        signature = {**signature, "windows": [[round(a, 1), round(b, 1)] for a, b in windows]}
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("signature") == signature: return saved
        except Exception: pass
    import cv2
    cap = cv2.VideoCapture(str(video)); fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); duration = total/fps if fps else 0
    smile_cascade = cv2.CascadeClassifier(cv2.data.haarcascades+"haarcascade_smile.xml")
    faces, text_regions, objects = [], [], []
    try: object_model,object_labels=_object_model()
    except Exception: object_model,object_labels=None,[]
    previous_tracks={}; next_track=0
    for second in np.arange(0, duration, 1.0):
        if windows and not any(a <= second <= b for a, b in windows): continue
        cap.set(cv2.CAP_PROP_POS_MSEC, second*1000); ok, frame = cap.read()
        if not ok: continue
        # Detectar em 1080p cheio custa caro e ainda faz textura de fundo virar
        # "rosto"; o índice trabalha numa versão reduzida do frame.
        if frame.shape[1] > DETECT_WIDTH:
            frame = cv2.resize(frame, (DETECT_WIDTH, round(frame.shape[0]*DETECT_WIDTH/frame.shape[1])))
        gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)); h,w = gray.shape
        found = detect_faces(gray, frame)
        entries=[]; used=set()
        for x,y,bw,bh in found:
            roi=gray[y:y+bh,x:x+bw]
            smiling = len(smile_cascade.detectMultiScale(roi,1.5,15,minSize=(15,8)))>0
            nx,ny=(x+bw/2)/w,(y+bh/2)/h
            choices=[(abs(nx-px)+abs(ny-py),track) for track,(px,py) in previous_tracks.items() if track not in used]
            if choices and min(choices)[0]<.22: track=min(choices)[1]
            else: track=f"FACE_{next_track:02d}"; next_track+=1
            used.add(track)
            entries.append({"id":track,"x":round(nx,4),"y":round(ny,4),
                            "size":round(bw*bh/(w*h),5),"expression":"smile" if smiling else "neutral"})
        if entries: previous_tracks={e["id"]:(e["x"],e["y"]) for e in entries}
        faces.append({"time":round(float(second),2),"faces":entries})
        if object_model is not None and int(second)%2==0:
            try:
                classes,scores,boxes=object_model.detect(frame,confThreshold=.5,nmsThreshold=.4)
                labels=[object_labels[int(c)-1] if 0<int(c)<=len(object_labels) else f"object_{int(c)}" for c in np.asarray(classes).flatten()]
                if labels: objects.append({"time":round(float(second),2),"labels":labels[:20]})
            except Exception: pass
        if int(second)%10==0:
            regions=_ocr(frame)
            if regions: text_regions.append({"time":round(float(second),2),"regions":regions})
    cap.release()
    visual=precomputed_visual or visual_timeline(video)
    energy=[{"start":round(w.start,3),"end":round(w.end,3),"energy":w.energy,"pitch":w.pitch,
             "speaker":w.speaker} for w in transcript.words]
    index={"signature":signature,"duration":duration,"visual":visual,"faces":faces,"objects":objects,
           "text_regions":text_regions,"audio_words":energy,
           "sentences":[{"id":s.id,"start":s.start,"end":s.end,"text":s.text,"speaker":s.speaker,
                         "pause_before":s.pause_before,"emphasis":s.emphasis,"topic_boundary":s.topic_boundary}
                        for s in sentences]}
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".tmp"); temp.write_text(json.dumps(index,ensure_ascii=False),encoding="utf-8"); temp.replace(path)
    return index


def multimodal_context(index:dict,start:float,end:float,text:str)->dict:
    faces=[f for frame in index.get("faces",[]) if start<=frame["time"]<=end for f in frame.get("faces",[])]
    ocr=[r["text"] for frame in index.get("text_regions",[]) if start-5<=frame["time"]<=end+5 for r in frame.get("regions",[])]
    objects=[x for frame in index.get("objects",[]) if start<=frame["time"]<=end for x in frame.get("labels",[])]
    scenes=[t for t in index.get("visual",{}).get("scene_changes",[]) if start<=t<=end]
    spoken={x.casefold() for x in __import__("re").findall(r"[\wÀ-ÿ]{4,}",text)}
    visible={x.casefold() for x in __import__("re").findall(r"[\wÀ-ÿ]{4,}"," ".join(ocr))}
    support=len(spoken&visible)/max(1,min(8,len(spoken)))
    return {"faces":len(faces),"smile_ratio":round(sum(f.get("expression")=="smile" for f in faces)/max(1,len(faces)),3),
            "objects":list(dict.fromkeys(objects))[:20],"visible_text":ocr[:20],"scene_changes":len(scenes),
            "visual_speech_support":round(min(1,support),3)}


def caption_position(index: dict, start: float, end: float, default: str) -> str:
    if default != "bottom": return default
    regions=[r for frame in index.get("text_regions",[]) if start-5 <= frame["time"] <= end+5 for r in frame["regions"]]
    if regions and sum(r["y"]+r["h"]/2 > .58 for r in regions) >= len(regions)/2:
        return "top"
    return default
