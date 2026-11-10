import os, re, uuid, shutil, subprocess, threading
from pathlib import Path
from typing import Any
from fastapi import FastAPI, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

ROOT=Path(__file__).resolve().parent.parent
DATA=Path(os.getenv("DATA_DIR",str(ROOT/"data"))); DATA.mkdir(parents=True,exist_ok=True)
JOBS={}; LOCK=threading.Lock(); LIMIT=2*1024*1024*1024
app=FastAPI(title="ClipForge AI")
app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")

def cmd(args):
 p=subprocess.run(args,capture_output=True,text=True)
 if p.returncode: raise RuntimeError((p.stderr or p.stdout)[-2000:])
 return p.stdout

def score(text,dur):
 t=text.lower(); s=42; why=[]
 hooks=["why","how","secret","mistake","never","truth","imagine","what if","the reason","most people","actually"]
 if any(x in t for x in hooks): s+=15; why.append("Hook or curiosity language")
 if "?" in text or "!" in text: s+=7; why.append("Question or emphasis")
 if any(x in t for x in ["amazing","shocking","surprising","incredible","fail","success","crazy","beautiful"]): s+=8; why.append("Emotion or surprise cues")
 pace=len(text.split())/max(dur,1)
 if 1.8<=pace<=3.4: s+=10; why.append("Energetic speech pace")
 if 20<=dur<=27: s+=5; why.append("Short-form length")
 if len(text.split())<18: s-=8; why.append("May need more context")
 return max(1,min(99,s)),why or ["Baseline content and length score"]

def srt_time(t):
 ms=int(t*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
 return f"{h:02}:{m:02}:{s:02},{ms:03}"

def write_srt(words,start,end,path,n):
 selected=[w for w in words if w["end"]>start and w["start"]<end]; rows=[]
 for i in range(0,len(selected),n):
  group=selected[i:i+n]; a=max(0,group[0]["start"]-start); b=min(end-start,group[-1]["end"]-start)
  if b>a: rows.append(f"{i//n+1}\n{srt_time(a)} --> {srt_time(b)}\n"+" ".join(w["word"].strip() for w in group)+"\n")
 path.write_text("\n".join(rows),encoding="utf-8")

def work(jid,source,o):
 folder=DATA/jid
 try:
  with LOCK: JOBS[jid].update(status="transcribing",progress=5)
  from faster_whisper import WhisperModel
  model=WhisperModel(o["model"],device="cpu",compute_type="int8")
  segments,info=model.transcribe(str(source),word_timestamps=True,vad_filter=True)
  segs=[]; words=[]
  for x in segments:
   segs.append({"start":float(x.start),"end":float(x.end),"text":x.text.strip()})
   for w in (x.words or []):
    if w.start is not None and w.end is not None: words.append({"start":float(w.start),"end":float(w.end),"word":w.word})
  if not segs: raise RuntimeError("No speech detected. Try a video with clearer audio.")
  end_all=max(x["end"] for x in segs)
  with LOCK: JOBS[jid].update(status="finding moments",progress=40)
  candidates=[]
  for start in range(0,max(1,int(end_all-o["min"])+1),max(4,o["min"]//2)):
   for length in sorted(set([o["min"],min(o["max"],20),o["max"]])):
    end=min(end_all,start+length); dur=end-start
    if dur<o["min"]-1: continue
    text=" ".join(x["text"] for x in segs if x["end"]>start and x["start"]<end).strip()
    if len(text.split())<12: continue
    sc,why=score(text,dur); candidates.append({"start":start,"end":end,"duration":round(dur,1),"text":text,"score":sc,"reasons":why})
  candidates.sort(key=lambda x:x["score"],reverse=True); chosen=[]
  for c in candidates:
   if all(c["end"]<=v["start"] or c["start"]>=v["end"] for v in chosen): chosen.append(c)
   if len(chosen)>=o["count"]: break
  if not chosen: raise RuntimeError("No suitable speech sections found for this duration range.")
  with LOCK: JOBS[jid].update(status="rendering",progress=55)
  results=[]
  for i,c in enumerate(chosen,1):
   name=f"clip-{i:02}.mp4"; sub=folder/f"clip-{i:02}.srt"; write_srt(words,c["start"],c["end"],sub,o["words"])
   subpath=str(sub.resolve()).replace("\\","/").replace(":","\\:")
   style=f"FontName=Arial,FontSize={o['font']},Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV={o['margin']},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000"
   y={"top":"0","center":"(ih-1920)/2","bottom":"ih-1920"}[o["crop"]]
   vf=f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920:(iw-1080)/2:{y},setsar=1,subtitles='{subpath}':force_style='{style}'"
   out=folder/name
   cmd(["ffmpeg","-y","-ss",str(c["start"]),"-i",str(source),"-t",str(c["duration"]),"-vf",vf,"-r",str(o["fps"]),"-c:v","libx264","-preset","veryfast","-crf",str(o["quality"]),"-c:a","aac","-b:a","128k","-movflags","+faststart",str(out)])
   results.append({**c,"filename":name,"download":f"/api/jobs/{jid}/clips/{name}","subtitle":f"/api/jobs/{jid}/clips/clip-{i:02}.srt"})
   with LOCK: JOBS[jid]["progress"]=55+int(43*i/len(chosen))
  with LOCK: JOBS[jid].update(status="complete",progress=100,clips=results,language=info.language)
 except Exception as e:
  with LOCK: JOBS[jid].update(status="error",progress=100,error=str(e))

@app.get("/",response_class=HTMLResponse)
def home(): return (ROOT/"static"/"index.html").read_text()

@app.post("/api/process")
async def create(background:BackgroundTasks,video:UploadFile=File(...),youtube_url:str=Form(""),min_duration:int=Form(10),max_duration:int=Form(30),max_clips:int=Form(5),model:str=Form("small"),font_size:int=Form(18),subtitle_margin:int=Form(90),words_per_line:int=Form(5),crop_position:str=Form("center"),quality:int=Form(23),fps:int=Form(30)):
 if not 10<=min_duration<=max_duration<=30: raise HTTPException(400,"Clip length must be between 10 and 30 seconds.")
 if not 1<=max_clips<=20 or not 12<=font_size<=48 or not 2<=words_per_line<=10 or not 20<=subtitle_margin<=300: raise HTTPException(400,"Advanced setting outside allowed range.")
 if model not in {"tiny","base","small"} or crop_position not in {"top","center","bottom"} or fps not in {24,30,60} or quality not in {18,23,28}: raise HTTPException(400,"Invalid setting.")
 ext=Path(video.filename or "video.mp4").suffix.lower()
 if ext not in {".mp4",".mov",".mkv",".webm",".m4v"}: raise HTTPException(400,"Upload MP4, MOV, MKV, WEBM, or M4V.")
 jid=uuid.uuid4().hex[:12]; folder=DATA/jid; folder.mkdir(); source=folder/("source"+ext); size=0
 with source.open("wb") as f:
  while chunk:=await video.read(1024*1024):
   size+=len(chunk)
   if size>LIMIT: shutil.rmtree(folder,ignore_errors=True); raise HTTPException(413,"Maximum upload is 2 GB.")
   f.write(chunk)
 o={"min":min_duration,"max":max_duration,"count":max_clips,"model":model,"font":font_size,"margin":subtitle_margin,"words":words_per_line,"crop":crop_position,"quality":quality,"fps":fps}
 with LOCK: JOBS[jid]={"id":jid,"status":"queued","progress":0,"clips":[],"youtube_url":youtube_url[:500]}
 background.add_task(work,jid,source,o); return {"job_id":jid}

@app.get("/api/jobs/{jid}")
def status(jid):
 with LOCK:
  if jid not in JOBS: raise HTTPException(404,"Job not found; in-memory jobs reset on restart.")
  return JOBS[jid]

@app.get("/api/jobs/{jid}/clips/{filename}")
def clip(jid,filename):
 if not re.fullmatch(r"clip-\d{2}\.(mp4|srt)",filename): raise HTTPException(400,"Invalid filename.")
 p=DATA/jid/filename
 if not p.is_file(): raise HTTPException(404,"Clip not found.")
 return FileResponse(p,filename=filename)
