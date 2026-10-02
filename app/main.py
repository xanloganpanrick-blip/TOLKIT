from pathlib import Path
import hashlib, json, os, re, shutil

from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .models import User, StoredFile, Job
from .storage import storage
from .config import settings
from .jobs import enqueue

Base.metadata.create_all(bind=engine)
Path(settings.work_path).mkdir(parents=True, exist_ok=True)

app = FastAPI(title="DBFLOW Local", version="2.0.0")
static_dir = Path(__file__).parent / "static"
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xlsm", ".parquet"}
ALLOWED_OPERATIONS = {
    "convert_csv", "clean", "normalize", "remove7", "dedupe",
    "merge", "split", "filter", "sort", "stats",
}
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def current_local_user(
    x_session_id: str = Header(default="", alias="X-Session-ID"),
    db: Session = Depends(get_db),
) -> User:
    """Create an internal session owner without exposing accounts or registration."""
    session_id = (x_session_id or "local-browser").strip()
    if not SESSION_RE.fullmatch(session_id):
        session_id = "local-browser"
    username = "local_" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    user = db.scalar(select(User).where(User.username == username))
    if not user:
        user = User(username=username, password_hash="local-session")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@app.get("/api/health")
def health():
    return {"ok": True, "service": settings.app_name, "mode": "local"}


def _upload_name(upload: UploadFile) -> tuple[str, str]:
    display_name = Path(upload.filename or "upload.bin").name
    suffix = Path(display_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Поддерживаются только CSV, XLSX, XLSM и Parquet")
    return display_name, suffix


async def _write_upload(upload: UploadFile, destination: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(8 * 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > settings.max_upload_bytes:
                destination.unlink(missing_ok=True)
                raise HTTPException(413, "File too large")
            digest.update(chunk)
            output.write(chunk)
    return total, digest.hexdigest()


@app.post("/api/quick/jobs")
async def create_quick_job(
    operation: str = Form(...),
    options_json: str = Form("{}"),
    files: list[UploadFile] = File(...),
    u: User = Depends(current_local_user),
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
    records: list[StoredFile] = []
    try:
        for index, upload in enumerate(files):
            display_name, suffix = _upload_name(upload)
            path = session_dir / f"{index:04d}_{display_name}"
            total, sha = await _write_upload(upload, path)
            temp_key = f"__temp__/{session_dir.relative_to(settings.work_path).as_posix()}/{path.name}"
            record = StoredFile(
                owner_id=u.id,
                name=display_name,
                storage_key=temp_key,
                size=total,
                mime=upload.content_type or "application/octet-stream",
                kind="temporary",
                sha256=sha,
            )
            db.add(record)
            db.flush()
            records.append(record)

        options.update({
            "mode": "quick",
            "quick_session": session_id,
            "source_names": [record.name for record in records],
        })
        job = Job(
            owner_id=u.id,
            operation=operation,
            input_file_ids=json.dumps([record.id for record in records]),
            options_json=json.dumps(options, ensure_ascii=False),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        enqueue(job.id)
        return {
            "id": job.id,
            "status": job.status,
            "mode": "quick",
            "files": [{"name": record.name, "size": record.size} for record in records],
        }
    except Exception:
        db.rollback()
        shutil.rmtree(session_dir, ignore_errors=True)
        raise


def _download_path(record: StoredFile) -> Path:
    local_path = storage.local_path(record.storage_key)
    if local_path and local_path.exists():
        return local_path
    target = Path(settings.work_path) / f"download_{record.id}_{record.name}"
    storage.open_local_copy(record.storage_key, target)
    return target


@app.get("/api/files/{fid}/download")
def download_result(
    fid: int,
    u: User = Depends(current_local_user),
    db: Session = Depends(get_db),
):
    record = db.get(StoredFile, fid)
    if not record or record.owner_id != u.id or record.deleted:
        raise HTTPException(404, "Result expired")
    if record.kind != "temporary":
        raise HTTPException(404, "Result expired")
    return FileResponse(_download_path(record), filename=record.name, media_type=record.mime)


@app.get("/api/jobs")
def list_jobs(u: User = Depends(current_local_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Job).where(Job.owner_id == u.id).order_by(Job.created_at.desc()).limit(200)
    ).all()
    return [
        {
            "id": job.id,
            "operation": job.operation,
            "status": job.status,
            "progress": job.progress,
            "source_names": json.loads(job.options_json or "{}").get("source_names", []),
            "input_file_ids": json.loads(job.input_file_ids or "[]"),
            "output_file_ids": json.loads(job.output_file_ids or "[]"),
            "created_at": job.created_at,
            "finished_at": job.finished_at,
            "error": job.error,
        }
        for job in rows
    ]


@app.get("/api/jobs/{jid}")
def get_job(jid: int, u: User = Depends(current_local_user), db: Session = Depends(get_db)):
    job = db.get(Job, jid)
    if not job or job.owner_id != u.id:
        raise HTTPException(404, "Not found")
    options = json.loads(job.options_json or "{}")
    return {
        "id": job.id,
        "operation": job.operation,
        "status": job.status,
        "progress": job.progress,
        "source_names": options.get("source_names", []),
        "input_file_ids": json.loads(job.input_file_ids or "[]"),
        "output_file_ids": json.loads(job.output_file_ids or "[]"),
        "log": job.log_text,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
