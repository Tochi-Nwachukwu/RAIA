"""Runtime settings, read from the environment and backend/.env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Provider API keys (AI_GATEWAY_API_KEY, OPENAI_API_KEY, ...) are read from os.environ by src/llm.py.
load_dotenv(BACKEND_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # The station's clock: block start times in clock.yaml are local times in this zone.
    timezone: str = "Africa/Lagos"

    config_dir: Path = BACKEND_DIR / "config"
    runs_dir: Path = BACKEND_DIR / "runs"
    cache_dir: Path = BACKEND_DIR / ".cache"
    assets_dir: Path = BACKEND_DIR / "assets"

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "raia"

    # Audio files: "local" serves them from runs/ through the API; "s3" uploads to S3 or Cloudflare R2.
    blob_backend: Literal["local", "s3"] = "local"
    public_base_url: str = "http://localhost:8000"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None  # R2: https://<account-id>.r2.cloudflarestorage.com
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_public_base_url: str | None = None  # public URL prefix of the bucket

    # WhatsApp Cloud API. Without an access token, replies are written to the outbox instead of sent.
    whatsapp_verify_token: str = "raia-dev-verify-token"
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_app_secret: str | None = None  # verifies X-Hub-Signature-256 on inbound webhooks
    # Secret salt for hashing listener phone numbers. Phone numbers have little entropy, so an
    # unsalted hash is reversible; when unset, a random salt is generated into cache_dir.
    listener_hash_salt: str | None = None

    # The newsroom side (/admin). Demo credentials: the judges sign in with these.
    admin_username: str = "test"
    admin_password: str = "password1"
    admin_token_secret: str | None = None  # signs admin sessions; falls back to listener_hash_salt
    admin_session_hours: int = 12
    # The agents prepare the coming day's programme overnight, in station time.
    nightly_build_at: str = "00:30"
    nightly_build_enabled: bool = True

    tts_device: str | None = None  # cpu, mps or cuda; YarnGPT picks cuda, else cpu, when unset
    http_user_agent: str = "RAIA-bot/0.1 (Radio AI Africa civic newsroom)"


@lru_cache
def get_settings() -> Settings:
    return Settings()
