from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "DBFLOW"
    secret_key: str = "dev-change-me"
    database_url: str = "sqlite:////data/dbflow.db"
    storage_backend: str = "local"
    local_storage_path: str = "/data/storage"
    work_path: str = "/data/work"
    max_upload_bytes: int = 20 * 1024**3
    access_token_minutes: int = 60 * 24 * 7
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
