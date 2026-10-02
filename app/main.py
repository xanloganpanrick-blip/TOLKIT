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
Path(settings.work_path).mkdir(parents=True, exist_ok=True)
app = FastAPI(title="DBFLOW", version="1.0.0")
static_dir = Path(__file__).parent/"static"
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xlsm", ".parquet"}
ALLOWED_OPERATIONS = {"convert_csv", "normalize", "remove7", "dedupe", "merge", "split", "stats"}

class Credentials(BaseModel):
    username: str
    password: str

class JobRequest(BaseModel):
    operation: str
    input_file_ids: list[int]
    options: dict = {}
    mode: str = "cloud"

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

def _upload_name(upload: UploadFile) -> tuple[str, str]:
    display_name = Path(upload.filename or "upload.bin").name
    suffix = Path(display_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Поддерживаются только CSV, XLSX, XLSM и Parquet")
    return display_name, suffix

async def _write_upload(upload: UploadFile, destination: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    total = 0
    with destination.open("wb") as out:
        while True:
            chunk = await upload.read(8 * 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > settings.max_upload_bytes:
                destination.unlink(missing_ok=True)
                raise HTTPException(413, "File too large")
            h.update(chunk)
            out.write(chunk)
    return total, h.hexdigest()

@app.get("/api/files")
def list_files(u:User=Depends(current_user),db:Session=Depends(get_db)):
    rows=db.scalars(select(StoredFile).where(StoredFile.owner_id==u.id,StoredFile.deleted==False).order_by(StoredFile.created_at.desc())).all()
    return [{"id":r.id,"name":r.name,"size":r.size,"kind":r.kind,"created_at":r.created_at,"sha256":r.sha256} for r in rows]

@app.post("/api/files/upload")
async def upload(files:list[UploadFile]=File(...),u:User=Depends(current_user),db:Session=Depends(get_db)):
    out=[]
    for f in files:
        display_name, suffix = _upload_name(f)
        with tempfile.NamedTemporaryFile(delete=False,suffix=suffix,dir=settings.work_path) as tmp:
            p=Path(tmp.name)
        total, sha = await _write_upload(f, p)
        key=storage.new_key(u.id,display_name)
        storage.put_path(p,key)
        rec=StoredFile(owner_id=u.id,name=display_name,storage_key=key,size=total,mime=f.content_type or "application/octet-stream",kind="source",sha256=sha)
        db.add(rec);db.commit();db.refresh(rec);out.append({"id":rec.id,"name":rec.name,"size":rec.size})
    return out

@app.post("/api/quick/jobs")
async def create_quick_job(
    operation: str = Form(...),
    options_json: str = Form("{}"),
    files: list[UploadFile] = File(...),
    u: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if operation not in ALLOWED_OPERATIONS:
        raise HTTPException(400, "Unsupported operation")
    try:
        options = json.loads(options_json or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid options")
    if not isinstance(options, dict) or not files:
        raise HTTPException(400, "Choose at least one file")
    session_id = hashlib.sha256(os.urandom(24)).hexdigest()[:24]
    session_dir = Path(settings.work_path) / f"quick_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    records = []
    try:
        for index, upload in enumerate(files):
            display_name, suffix = _upload_name(upload)
            path = session_dir / f"{index:04d}_{display_name}"
            total, sha = await _write_upload(upload, path)
            temp_key = f"__temp__/{session_dir.relative_to(settings.work_path).as_posix()}/{path.name}"
            rec = StoredFile(owner_id=u.id, name=display_name, storage_key=temp_key, size=total,
                             mime=upload.content_type or "application/octet-stream", kind="temporary", sha256=sha)
            db.add(rec); db.flush(); records.append(rec)
        options.update({"mode": "quick", "quick_session": session_id, "source_names": [r.name for r in records]})
        job = Job(owner_id=u.id, operation=operation, input_file_ids=json.dumps([r.id for r in records]),
                  options_json=json.dumps(options, ensure_ascii=False))
        db.add(job); db.commit(); db.refresh(job)
        enqueue(job.id)
        return {"id": job.id, "status": job.status, "mode": "quick", "files": [{"name": r.name, "size": r.size} for r in records]}
    except Exception:
        db.rollback()
        shutil.rmtree(session_dir, ignore_errors=True)
        raise

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
    if req.operation not in ALLOWED_OPERATIONS: raise HTTPException(400,"Unsupported operation")
    if req.mode not in {"cloud", "quick"}: raise HTTPException(400,"Unsupported mode")
    rows=db.scalars(select(StoredFile).where(StoredFile.id.in_(req.input_file_ids))).all() if req.input_file_ids else []
    if len(rows)!=len(set(req.input_file_ids)) or any(r.owner_id!=u.id or r.deleted for r in rows):
        raise HTTPException(400,"Invalid input files")
    options = dict(req.options or {})
    options["mode"] = req.mode
    j=Job(owner_id=u.id,operation=req.operation,input_file_ids=json.dumps(req.input_file_ids),options_json=json.dumps(options,ensure_ascii=False))
    db.add(j);db.commit();db.refresh(j);enqueue(j.id)
    return {"id":j.id,"status":j.status}

@app.get("/api/jobs")
def list_jobs(u:User=Depends(current_user),db:Session=Depends(get_db)):
    rows=db.scalars(select(Job).where(Job.owner_id==u.id).order_by(Job.created_at.desc()).limit(200)).all()
    return [{"id":j.id,"operation":j.operation,"status":j.status,"progress":j.progress,"mode":json.loads(j.options_json or "{}").get("mode", "cloud"),"source_names":json.loads(j.options_json or "{}").get("source_names", []),"input_file_ids":json.loads(j.input_file_ids or "[]"),"output_file_ids":json.loads(j.output_file_ids or "[]"),"created_at":j.created_at,"finished_at":j.finished_at,"error":j.error} for j in rows]

@app.get("/api/jobs/{jid}")
def get_job(jid:int,u:User=Depends(current_user),db:Session=Depends(get_db)):
    j=db.get(Job,jid)
    if not j or j.owner_id!=u.id: raise HTTPException(404,"Not found")
    options = json.loads(j.options_json or "{}")
    return {"id":j.id,"operation":j.operation,"status":j.status,"progress":j.progress,"mode":options.get("mode", "cloud"),"source_names":options.get("source_names", []),"input_file_ids":json.loads(j.input_file_ids or "[]"),"output_file_ids":json.loads(j.output_file_ids or "[]"),"log":j.log_text,"error":j.error,"created_at":j.created_at,"started_at":j.started_at,"finished_at":j.finished_at}

@app.post("/api/jobs/{jid}/save-results")
def save_job_results(jid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(Job, jid)
    if not job or job.owner_id != u.id:
        raise HTTPException(404, "Not found")
    saved = []
    for fid in json.loads(job.output_file_ids or "[]"):
        rec = db.get(StoredFile, fid)
        if not rec or rec.owner_id != u.id or rec.deleted or rec.kind != "temporary":
            continue
        tmp = Path(settings.work_path) / f"save_{jid}_{rec.id}_{rec.name}"
        storage.open_local_copy(rec.storage_key, tmp)
        new_key = storage.new_key(u.id, rec.name)
        storage.put_path(tmp, new_key)
        storage.delete(rec.storage_key)
        rec.storage_key = new_key
        rec.kind = "result"
        saved.append(rec.id)
    db.commit()
    return {"saved_file_ids": saved}

app.mount("/",StaticFiles(directory=static_dir,html=True),name="static")
