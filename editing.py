"""Conservative internal edit decisions and timeline remapping."""
from __future__ import annotations
import re
from dataclasses import replace
from .config import Config
from .transcribe import Word
from .reframe import CropPlan

FILLERS={"é","eh","hum","hã","tipo","assim","né","entendeu","ah","uh","um","like","you know"}

def edit_ranges(words:list[Word],start:float,end:float,cfg:Config,
                silences:list[tuple[float,float]]|None=None)->list[tuple[float,float,str]]:
    if not cfg.auto_edit: return []
    cuts=[]
    for a,b in zip(words,words[1:]):
        gap=b.start-a.end
        if gap>=cfg.internal_silence_seconds:
            lo=max(start,a.end+.16); hi=min(end,b.start-.16)
            verified = silences is None or any(max(lo,s)<min(hi,e) and min(hi,e)-max(lo,s)>=.25 for s,e in silences)
            if hi-lo>=.3 and verified: cuts.append((lo-start,hi-start,"silêncio"))
    for i,w in enumerate(words):
        token=re.sub(r"\W+","",w.text.casefold())
        before=w.start-(words[i-1].end if i else start); after=(words[i+1].start if i+1<len(words) else end)-w.end
        if token in FILLERS and w.end-w.start>=.22 and before>=.12 and after>=.12:
            cuts.append((max(0,w.start-start-.02),min(end-start,w.end-start+.02),"vício de linguagem"))
    # Merge nearby decisions so FFmpeg receives a stable, minimal edit list.
    merged=[]
    for lo,hi,reason in sorted(cuts):
        if merged and lo<=merged[-1][1]+.05:
            merged[-1]=(merged[-1][0],max(hi,merged[-1][1]),merged[-1][2]+"/"+reason)
        else: merged.append((lo,hi,reason))
    return merged

def map_time(t:float,cuts:list[tuple[float,float,str]])->float:
    removed=0.
    for lo,hi,_ in cuts:
        if t>=hi: removed+=hi-lo
        elif t>lo: removed+=t-lo
    return max(0,t-removed)

def remap_words(words:list[Word],start:float,cuts:list[tuple[float,float,str]])->list[Word]:
    out=[]
    for w in words:
        a,b=w.start-start,w.end-start
        if any(a>=lo and b<=hi for lo,hi,_ in cuts): continue
        out.append(replace(w,start=map_time(a,cuts),end=max(map_time(b,cuts),map_time(a,cuts)+.04)))
    return out

def remap_crop(plan:CropPlan,cuts:list[tuple[float,float,str]])->CropPlan:
    points=[]
    for t,x in plan.keyframes:
        if any(lo<=t<=hi for lo,hi,_ in cuts): continue
        point=(round(map_time(t,cuts),3),x)
        if not points or point!=points[-1]: points.append(point)
    return replace(plan,keyframes=points or [(0.,plan.x0)],static=len(points)<=1)
