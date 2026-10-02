from __future__ import annotations
from pathlib import Path
from datetime import datetime
import json, traceback, shutil, threading, hashlib, zipfile
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import Job, StoredFile
from .storage import storage
from .config import settings
from . import table_ops

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
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()

def cleanup_quick_job(job_id: int, work: Path):
    db = SessionLocal()
    session_dir = None
    try:
        job = db.get(Job, job_id)
        if job:
            options = json.loads(job.options_json or "{}")
            session_id = options.get("quick_session")
            if session_id:
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
            local=work/f"{fid}_{rec.name}"
            storage.open_local_copy(rec.storage_key, local)
            local_paths.append(local)
            job.progress=min(20, 2+int(idx/max(1,len(input_ids))*18)); db.commit()

        opts=job_options
        op=job.operation

        if op=="convert_csv":
            for i,p in enumerate(local_paths,1):
                df=table_ops.read_any(p)
                out=work/(p.stem+".csv")
                table_ops.write_csv(df,out)
                _save_output(db,job,job.owner_id,out)
                job.progress=20+int(i/max(1,len(local_paths))*75); db.commit()

        elif op=="normalize":
            for i,p in enumerate(local_paths,1):
                df=table_ops.normalize(table_ops.read_any(p))
                out=work/(p.stem+"_normalized.csv")
                df.write_csv(out); _save_output(db,job,job.owner_id,out)
                job.progress=20+int(i/max(1,len(local_paths))*75); db.commit()

        elif op=="clean":
            for i,p in enumerate(local_paths,1):
                df=table_ops.clean(table_ops.read_any(p))
                out=work/(p.stem+"_clean.csv")
                df.write_csv(out); _save_output(db,job,job.owner_id,out)
                job.progress=20+int(i/max(1,len(local_paths))*75); db.commit()

        elif op=="remove7":
            for i,p in enumerate(local_paths,1):
                df=table_ops.remove_leading_7(table_ops.read_any(p))
                out=work/(p.stem+"_minus7.csv")
                df.write_csv(out); _save_output(db,job,job.owner_id,out)
                job.progress=20+int(i/max(1,len(local_paths))*75); db.commit()

        elif op=="dedupe":
            keys=opts.get("keys") or None
            for i,p in enumerate(local_paths,1):
                df=table_ops.dedupe(table_ops.read_any(p),keys)
                out=work/(p.stem+"_dedup.csv")
                df.write_csv(out); _save_output(db,job,job.owner_id,out)
                job.progress=20+int(i/max(1,len(local_paths))*75); db.commit()

        elif op=="filter":
            column=opts.get("column","")
            value=opts.get("value","")
            contains=bool(opts.get("contains",False))
            for i,p in enumerate(local_paths,1):
                df=table_ops.filter_rows(table_ops.read_any(p),column,value,contains)
                out=work/(p.stem+"_filtered.csv")
                df.write_csv(out); _save_output(db,job,job.owner_id,out)
                job.progress=20+int(i/max(1,len(local_paths))*75); db.commit()

        elif op=="sort":
            column=opts.get("column","")
            descending=bool(opts.get("descending",False))
            for i,p in enumerate(local_paths,1):
                df=table_ops.sort_rows(table_ops.read_any(p),column,descending)
                out=work/(p.stem+"_sorted.csv")
                df.write_csv(out); _save_output(db,job,job.owner_id,out)
                job.progress=20+int(i/max(1,len(local_paths))*75); db.commit()

        elif op=="merge":
            df=table_ops.merge(local_paths)
            out=work/"merged.csv"; df.write_csv(out)
            _save_output(db,job,job.owner_id,out); job.progress=95; db.commit()

        elif op=="split":
            if len(local_paths)!=1: raise ValueError("Split accepts one file")
            df=table_ops.read_any(local_paths[0])
            max_rows=max(1000,int(opts.get("max_rows",500000)))
            parts=table_ops.split_csv(df,work/"parts",max_rows)
            zip_path=work/"split.zip"
            with zipfile.ZipFile(zip_path,"w",compression=zipfile.ZIP_DEFLATED,allowZip64=True) as z:
                for p in parts: z.write(p,p.name)
            _save_output(db,job,job.owner_id,zip_path); job.progress=95; db.commit()

        elif op=="stats":
            data=[]
            for p in local_paths:
                data.append({"file":p.name,**table_ops.basic_stats(table_ops.read_any(p))})
            out=work/"stats.json"; out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
            _save_output(db,job,job.owner_id,out); job.progress=95; db.commit()

        else:
            raise ValueError("Unknown operation")

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
            timer = threading.Timer(settings.quick_job_ttl_seconds, cleanup_quick_job, args=(job_id, work))
            timer.daemon = True
            timer.start()
        else:
            shutil.rmtree(work, ignore_errors=True)
        db.close()
