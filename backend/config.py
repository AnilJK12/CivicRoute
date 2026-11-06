"""Environment-backed settings shared by the backend package."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

from .url_validation import is_absolute_https_url, is_http_origin


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


def load_project_environment(project_root: Path = PROJECT_ROOT) -> None:
    """Load local project settings while keeping explicitly supplied env vars authoritative."""
    load_dotenv(project_root / ".env", override=False)


load_project_environment()


@dataclass(frozen=True)
class Settings:
    catalog_path: Path = BASE_DIR / "data" / "services.json"
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    log_level: str = "INFO"
    cors_origins: tuple[str, ...] = ()
    verified_general_portal_url: str | None = None
    official_source_provider: str | None = None
    brave_search_api_key: str | None = None
    official_source_approved_hosts: tuple[str, ...] = ()

    @classmethod
    def from_environment(cls) -> "Settings":
        origins = tuple(origin.strip().rstrip("/") for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip())
        for origin in origins:
            if "*" in origin or not is_http_origin(origin):
                raise ValueError("CORS_ORIGINS must contain explicit HTTP(S) origins only")
        portal_url = os.getenv("CIVICROUTE_VERIFIED_GENERAL_PORTAL_URL") or None
        if portal_url and not is_absolute_https_url(portal_url):
            raise ValueError("CIVICROUTE_VERIFIED_GENERAL_PORTAL_URL must be an absolute HTTPS URL")
        gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip() or None
        gemini_model = os.getenv("GEMINI_MODEL", "").strip() or None
        official_source_provider = os.getenv("OFFICIAL_SOURCE_PROVIDER", "").strip().casefold() or None
        brave_search_api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip() or None
        approved_hosts = tuple(
            host.strip().casefold().rstrip(".")
            for host in os.getenv("OFFICIAL_SOURCE_APPROVED_HOSTS", "").split(",")
            if host.strip()
        )
        return cls(
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            cors_origins=origins,
            verified_general_portal_url=portal_url,
            official_source_provider=official_source_provider,
            brave_search_api_key=brave_search_api_key,
            official_source_approved_hosts=approved_hosts,
        )


settings = Settings.from_environment()
