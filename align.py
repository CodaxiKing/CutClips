"""Local forced boundary alignment for corrected captions."""
from __future__ import annotations
import subprocess
from dataclasses import replace
from pathlib import Path
import numpy as np
from .transcribe import Word

def force_align_boundaries(video:Path,start:float,end:float,words:list[Word])->list[Word]:
    proc=subprocess.run(["ffmpeg","-v","error","-ss",str(start),"-t",str(end-start),"-i",str(video),
        "-vn","-ac","1","-ar","16000","-f","f32le","pipe:1"],capture_output=True)
    audio=np.frombuffer(proc.stdout,dtype="<f4")
    if len(audio)<320: return words
    frame=160
    env=np.asarray([np.sqrt(np.mean(audio[i:i+frame]**2)) for i in range(0,len(audio),frame) if len(audio[i:i+frame])])
    def snap(t:float)->float:
        center=int((t-start)*100); lo=max(0,center-10); hi=min(len(env)-1,center+10)
        if hi<=lo:return t
        # Speech boundary: strongest local energy slope, constrained to 100 ms.
        slopes=np.abs(np.diff(env[lo:hi+1])); idx=lo+int(np.argmax(slopes))
        return start+idx/100
    out=[]
    for i,w in enumerate(words):
        a=snap(w.start); b=snap(w.end)
        if i and a<out[-1].end: a=out[-1].end
        out.append(replace(w,start=max(start,a),end=max(a+.04,min(end,b))))
    return out
