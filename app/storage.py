from pathlib import Path
import shutil, uuid
import boto3
from .config import settings

class Storage:
    def __init__(self):
        self.backend = settings.storage_backend.lower()
        self.local = Path(settings.local_storage_path)
        self.local.mkdir(parents=True, exist_ok=True)
        self.s3 = None
        if self.backend == "s3":
            self.s3 = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url or None,
                region_name=settings.s3_region or None,
                aws_access_key_id=settings.s3_access_key_id,
                aws_secret_access_key=settings.s3_secret_access_key,
            )

    def new_key(self, user_id: int, filename: str) -> str:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[:180]
        return f"u{user_id}/{uuid.uuid4().hex}_{safe}"

    def put_path(self, src: Path, key: str):
        if self.backend == "s3":
            self.s3.upload_file(str(src), settings.s3_bucket, key)
        else:
            dst = self.local / key
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

    def open_local_copy(self, key: str, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if key.startswith("__temp__/"):
            shutil.copy2(Path(settings.work_path) / key[len("__temp__/"):], dst)
        elif self.backend == "s3":
            self.s3.download_file(settings.s3_bucket, key, str(dst))
        else:
            shutil.copy2(self.local / key, dst)
        return dst

    def delete(self, key: str):
        if key.startswith("__temp__/"):
            p = Path(settings.work_path) / key[len("__temp__/"):]
            if p.exists():
                p.unlink()
        elif self.backend == "s3":
            self.s3.delete_object(Bucket=settings.s3_bucket, Key=key)
        else:
            p = self.local / key
            if p.exists():
                p.unlink()

    def local_path(self, key: str) -> Path | None:
        if key.startswith("__temp__/"):
            return Path(settings.work_path) / key[len("__temp__/"):]
        if self.backend == "local":
            return self.local / key
        return None

storage = Storage()
