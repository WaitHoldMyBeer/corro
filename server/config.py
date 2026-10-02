"""Settings, read from the environment (`.env` is loaded if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    clio_client_id: str
    clio_client_secret: str
    clio_redirect_uri: str
    clio_base_url: str
    clio_matter_id: int | None
    openai_api_key: str
    digest_model: str
    digest_model_bulk: str
    data_dir: Path
    # The small, fast model the interactive features use (checker, card design, assistant). Defaults to the bulk digest model.
    check_model: str = ""
    check_effort: str = ""
    # How long a matter must sit unopened before the next open counts as a new visit.
    visit_gap_hours: float = 4.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "swans.db"

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def api_base(self) -> str:
        return f"{self.clio_base_url}/api/v4"


def get_settings() -> Settings:
    data_dir = Path(os.environ.get("SWANS_DATA_DIR") or "data")
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    matter_id = (os.environ.get("CLIO_MATTER_ID") or "").strip()
    digest_model = os.environ.get("DIGEST_MODEL", "").strip()
    return Settings(
        clio_client_id=os.environ.get("CLIO_CLIENT_ID", "").strip(),
        clio_client_secret=os.environ.get("CLIO_CLIENT_SECRET", "").strip(),
        clio_redirect_uri=(os.environ.get("CLIO_REDIRECT_URI") or "http://127.0.0.1:8000/oauth/callback").strip(),
        clio_base_url=(os.environ.get("CLIO_BASE_URL") or "https://app.clio.com").strip().rstrip("/"),
        clio_matter_id=int(matter_id) if matter_id else None,
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        digest_model=digest_model,
        digest_model_bulk=os.environ.get("DIGEST_MODEL_BULK", "").strip() or digest_model,
        data_dir=data_dir,
        check_model=os.environ.get("CHECK_MODEL", "").strip() or os.environ.get("DIGEST_MODEL_BULK", "").strip() or digest_model,
        check_effort=os.environ.get("CHECK_EFFORT", "").strip(),
        visit_gap_hours=float(os.environ.get("VISIT_GAP_HOURS", "").strip() or 4.0),
    )
