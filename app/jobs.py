from __future__ import annotations
from pathlib import Path
from datetime import datetime
import json, traceback, shutil, threading, hashlib, re
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import Job, StoredFile
from .storage import storage
from .config import settings
from .toolkit_ops import run_operation
from concurrent.futures import ThreadPoolExecutor

executor=ThreadPoolExecutor(max_workers=2, thread_name_prefix="dbflow")

def _log(job: Job, msg: str):
    job.log_text = (job.log_text or "") + f"[{datetime.utcnow().isoformat(timespec='seconds')}] {msg}\n"

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4*1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _save_output(db: Session, job: Job, owner_id: int, path: Path, kind="result") -> StoredFile:
    size = path.stat().st_size
    sha = _sha256(path)
    options = json.loads(job.options_json or "{}")
    if options.get("mode") == "quick":
        relative = path.relative_to(settings.work_path).as_posix()
        key = f"__temp__/{relative}"
        stored_kind = "temporary"
        name = path.name
    else:
        key = storage.new_key(owner_id, path.name)
        storage.put_path(path, key)
        stored_kind = kind
        name = Path(key).name.split("_",1)[-1]
    rec = StoredFile(owner_id=owner_id, name=name, storage_key=key, size=size, kind=stored_kind, sha256=sha)
    db.add(rec); db.flush()
    ids = json.loads(job.output_file_ids or "[]"); ids.append(rec.id); job.output_file_ids = json.dumps(ids)
    return rec

def enqueue(job_id: int):
    executor.submit(run_job, job_id)

def cleanup_quick_job(job_id: int, work: Path):
    db = SessionLocal()
    session_dir = None
    try:
        job = db.get(Job, job_id)
        if job:
            options = json.loads(job.options_json or "{}")
            session_id = options.get("quick_session")
            if session_id and re.fullmatch(r"[a-f0-9]{24}",session_id):
                session_dir = Path(settings.work_path) / f"quick_{session_id}"
            ids = json.loads(job.input_file_ids or "[]") + json.loads(job.output_file_ids or "[]")
            for fid in set(ids):
                rec = db.get(StoredFile, fid)
                if rec and rec.kind == "temporary" and not rec.deleted:
                    storage.delete(rec.storage_key)
                    rec.deleted = True
            db.commit()
    finally:
        db.close()
        shutil.rmtree(work, ignore_errors=True)
        if session_dir:
            shutil.rmtree(session_dir, ignore_errors=True)

def schedule_cleanup(job_id: int, work: Path, delay: float):
    timer = threading.Timer(max(0,delay), cleanup_quick_job, args=(job_id, work))
    timer.daemon = True
    timer.start()

def restore_cleanup():
    """Restore expiry after Railway restarts; do not leave interrupted jobs running."""
    db=SessionLocal()
    pending=[]
    try:
        for job in db.scalars(select(Job)).all():
            options=json.loads(job.options_json or "{}")
            if options.get("mode")!="quick": continue
            ids=json.loads(job.input_file_ids or "[]")+json.loads(job.output_file_ids or "[]")
            if not any((rec:=db.get(StoredFile,fid)) and not rec.deleted for fid in ids): continue
            if job.status in ("queued","running"):
                job.status="error"; job.error="Обработка прервана перезапуском сервера. Запусти задачу заново."
                job.finished_at=datetime.utcnow()
            age=(datetime.utcnow()-(job.finished_at or job.created_at)).total_seconds()
            pending.append((job.id,Path(settings.work_path)/f"job_{job.id}",settings.quick_job_ttl_seconds-age))
        db.commit()
    finally:
        db.close()
    for job_id,work,delay in pending:
        if delay<=0: cleanup_quick_job(job_id,work)
        else: schedule_cleanup(job_id,work,delay)

def run_job(job_id: int):
    db = SessionLocal()
    work = Path(settings.work_path) / f"job_{job_id}"
    shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True, exist_ok=True)
    job_options = {}
    try:
        job = db.get(Job, job_id)
        if not job: return
        job_options = json.loads(job.options_json or "{}")
        job.status="running"; job.started_at=datetime.utcnow(); job.progress=2; _log(job,"started"); db.commit()
        input_ids = json.loads(job.input_file_ids or "[]")
        local_paths=[]
        for idx,fid in enumerate(input_ids,1):
            rec=db.get(StoredFile,fid)
            if not rec or rec.owner_id!=job.owner_id or rec.deleted:
                raise ValueError(f"Input file {fid} missing")
            local=work/"inputs"/str(fid)/rec.name
            storage.open_local_copy(rec.storage_key, local)
            local_paths.append(local)
            job.progress=min(20, 2+int(idx/max(1,len(input_ids))*18)); db.commit()

        opts=job_options
        op=job.operation

        def progress(value):
            job.progress=value
            db.commit()

        reference_ids=set(job_options.get("reference_ids", []))
        source_paths=[path for path, fid in zip(local_paths,input_ids) if fid not in reference_ids]
        reference_paths=[path for path, fid in zip(local_paths,input_ids) if fid in reference_ids]
        outputs=run_operation(op,source_paths,reference_paths,opts,work,progress)
        for output in outputs:
            _save_output(db,job,job.owner_id,output)
        _log(job,f"Результатов: {len(outputs)}")

        job.status="done"; job.progress=100; job.finished_at=datetime.utcnow(); _log(job,"completed"); db.commit()
    except Exception as e:
        try:
            job=db.get(Job,job_id)
            if job:
                job.status="error"; job.error=str(e); job.finished_at=datetime.utcnow()
                _log(job,"ERROR: "+str(e)); _log(job,traceback.format_exc()[-4000:]); db.commit()
        except Exception:
            pass
    finally:
        if job_options.get("mode") == "quick":
            schedule_cleanup(job_id,work,settings.quick_job_ttl_seconds)
        else:
            shutil.rmtree(work, ignore_errors=True)
        db.close()
