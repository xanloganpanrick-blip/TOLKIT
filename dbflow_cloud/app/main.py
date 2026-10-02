from pathlib import Path
from typing import Annotated
import json, os, shutil, hashlib, tempfile
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel
from .db import Base, engine, get_db
from .models import User, StoredFile, Job
from .auth import hash_password, verify_password, create_token, current_user
from .storage import storage
from .config import settings
from .jobs import enqueue

Base.metadata.create_all(bind=engine)
app = FastAPI(title="DBFLOW", version="1.0.0")
static_dir = Path(__file__).parent/"static"

class Credentials(BaseModel):
    username: str
    password: str

class JobRequest(BaseModel):
    operation: str
    input_file_ids: list[int]
    options: dict = {}

@app.get("/api/health")
def health():
    return {"ok": True, "service": settings.app_name}

@app.post("/api/auth/register")
def register(c: Credentials, db: Session=Depends(get_db)):
    username=c.username.strip().lower()
    if len(username)<3 or len(c.password)<8:
        raise HTTPException(400,"Username >=3 chars, password >=8 chars")
    if db.scalar(select(User).where(User.username==username)):
        raise HTTPException(409,"Username exists")
    u=User(username=username,password_hash=hash_password(c.password)); db.add(u); db.commit(); db.refresh(u)
    return {"token":create_token(u.id),"user":{"id":u.id,"username":u.username}}

@app.post("/api/auth/login")
def login(c: Credentials, db: Session=Depends(get_db)):
    u=db.scalar(select(User).where(User.username==c.username.strip().lower()))
    if not u or not verify_password(c.password,u.password_hash):
        raise HTTPException(401,"Invalid credentials")
    return {"token":create_token(u.id),"user":{"id":u.id,"username":u.username}}

@app.get("/api/me")
def me(u: User=Depends(current_user)):
    return {"id":u.id,"username":u.username,"created_at":u.created_at}

@app.get("/api/files")
def list_files(u:User=Depends(current_user),db:Session=Depends(get_db)):
    rows=db.scalars(select(StoredFile).where(StoredFile.owner_id==u.id,StoredFile.deleted==False).order_by(StoredFile.created_at.desc())).all()
    return [{"id":r.id,"name":r.name,"size":r.size,"kind":r.kind,"created_at":r.created_at,"sha256":r.sha256} for r in rows]

@app.post("/api/files/upload")
async def upload(files:list[UploadFile]=File(...),u:User=Depends(current_user),db:Session=Depends(get_db)):
    out=[]
    for f in files:
        suffix=Path(f.filename or "upload.bin").suffix
        with tempfile.NamedTemporaryFile(delete=False,suffix=suffix,dir=settings.work_path) as tmp:
            h=hashlib.sha256(); total=0
            while True:
                chunk=await f.read(8*1024*1024)
                if not chunk: break
                total+=len(chunk)
                if total>settings.max_upload_bytes:
                    os.unlink(tmp.name); raise HTTPException(413,"File too large")
                h.update(chunk); tmp.write(chunk)
            p=Path(tmp.name)
        key=storage.new_key(u.id,f.filename or "upload.bin")
        storage.put_path(p,key)
        rec=StoredFile(owner_id=u.id,name=f.filename or "upload.bin",storage_key=key,size=total,mime=f.content_type or "application/octet-stream",kind="source",sha256=h.hexdigest())
        db.add(rec);db.commit();db.refresh(rec);out.append({"id":rec.id,"name":rec.name,"size":rec.size})
    return out

@app.delete("/api/files/{fid}")
def delete_file(fid:int,u:User=Depends(current_user),db:Session=Depends(get_db)):
    r=db.get(StoredFile,fid)
    if not r or r.owner_id!=u.id or r.deleted: raise HTTPException(404,"Not found")
    storage.delete(r.storage_key);r.deleted=True;db.commit()
    return {"ok":True}

def _download_path(r:StoredFile)->Path:
    lp=storage.local_path(r.storage_key)
    if lp and lp.exists(): return lp
    tmp=Path(settings.work_path)/f"download_{r.id}_{r.name}"
    storage.open_local_copy(r.storage_key,tmp)
    return tmp

@app.get("/api/files/{fid}/download")
def download(fid:int,u:User=Depends(current_user),db:Session=Depends(get_db)):
    r=db.get(StoredFile,fid)
    if not r or r.owner_id!=u.id or r.deleted: raise HTTPException(404,"Not found")
    return FileResponse(_download_path(r),filename=r.name,media_type=r.mime)

@app.get("/api/files/{fid}/take")
def take_and_delete(fid:int,bg:BackgroundTasks,u:User=Depends(current_user),db:Session=Depends(get_db)):
    r=db.get(StoredFile,fid)
    if not r or r.owner_id!=u.id or r.deleted: raise HTTPException(404,"Not found")
    p=_download_path(r)
    key=r.storage_key
    def cleanup():
        storage.delete(key)
        d=next(get_db())
        try:
            rr=d.get(StoredFile,fid)
            if rr: rr.deleted=True;d.commit()
        finally:
            d.close()
        if settings.storage_backend=="s3" and p.exists():
            p.unlink(missing_ok=True)
    bg.add_task(cleanup)
    return FileResponse(p,filename=r.name,background=bg)

@app.post("/api/jobs")
def create_job(req:JobRequest,u:User=Depends(current_user),db:Session=Depends(get_db)):
    allowed={"convert_csv","normalize","remove7","dedupe","merge","split","stats"}
    if req.operation not in allowed: raise HTTPException(400,"Unsupported operation")
    rows=db.scalars(select(StoredFile).where(StoredFile.id.in_(req.input_file_ids))).all() if req.input_file_ids else []
    if len(rows)!=len(set(req.input_file_ids)) or any(r.owner_id!=u.id or r.deleted for r in rows):
        raise HTTPException(400,"Invalid input files")
    j=Job(owner_id=u.id,operation=req.operation,input_file_ids=json.dumps(req.input_file_ids),options_json=json.dumps(req.options,ensure_ascii=False))
    db.add(j);db.commit();db.refresh(j);enqueue(j.id)
    return {"id":j.id,"status":j.status}

@app.get("/api/jobs")
def list_jobs(u:User=Depends(current_user),db:Session=Depends(get_db)):
    rows=db.scalars(select(Job).where(Job.owner_id==u.id).order_by(Job.created_at.desc()).limit(200)).all()
    return [{"id":j.id,"operation":j.operation,"status":j.status,"progress":j.progress,"input_file_ids":json.loads(j.input_file_ids or "[]"),"output_file_ids":json.loads(j.output_file_ids or "[]"),"created_at":j.created_at,"finished_at":j.finished_at,"error":j.error} for j in rows]

@app.get("/api/jobs/{jid}")
def get_job(jid:int,u:User=Depends(current_user),db:Session=Depends(get_db)):
    j=db.get(Job,jid)
    if not j or j.owner_id!=u.id: raise HTTPException(404,"Not found")
    return {"id":j.id,"operation":j.operation,"status":j.status,"progress":j.progress,"input_file_ids":json.loads(j.input_file_ids or "[]"),"output_file_ids":json.loads(j.output_file_ids or "[]"),"log":j.log_text,"error":j.error,"created_at":j.created_at,"started_at":j.started_at,"finished_at":j.finished_at}

app.mount("/",StaticFiles(directory=static_dir,html=True),name="static")
