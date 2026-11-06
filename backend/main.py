"""Minimal FastAPI routes; no secrets or deployment details are exposed."""

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .centres import CentreSearchResponse, find_verified_centres
from .discovery_store import DiscoveryStore
from .knowledge_base import KnowledgeBaseError
from .http_errors import sanitized_validation_error
from .official_sources import build_configured_official_source_retriever
from .pipeline import answer_request
from .schemas import AskRequest, ClarificationResponse, SupportedResponse, UnsupportedResponse

app = FastAPI(title="CivicRoute API", version="1.0.0")
# The live adapter factory returns None unless provider, key, and host allowlist are configured.
app.state.official_source_retriever = build_configured_official_source_retriever(settings)
app.state.discovery_store = DiscoveryStore()
LOCAL_FRONTEND_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
allowed_origins = settings.cors_origins or LOCAL_FRONTEND_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allowed_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
app.add_exception_handler(RequestValidationError, sanitized_validation_error)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask", response_model=SupportedResponse | UnsupportedResponse | ClarificationResponse)
def ask(request: AskRequest) -> SupportedResponse | UnsupportedResponse | ClarificationResponse:
    return answer_request(
        request,
        official_source_retriever=app.state.official_source_retriever,
        discovery_store=app.state.discovery_store,
    )


@app.get("/centres", response_model=CentreSearchResponse)
def centres(
    service_id: str = Query(min_length=1, max_length=100),
    district: str | None = Query(default=None, max_length=100),
    taluk: str | None = Query(default=None, max_length=100),
    area: str | None = Query(default=None, max_length=100),
    latitude: float | None = Query(default=None, ge=-90, le=90),
    longitude: float | None = Query(default=None, ge=-180, le=180),
    max_results: int = Query(default=20, ge=1, le=50),
) -> CentreSearchResponse:
    try:
        return find_verified_centres(
            service_id, district=district, taluk=taluk, area=area, latitude=latitude,
            longitude=longitude, max_results=max_results,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Service not found") from exc
    except KnowledgeBaseError as exc:
        raise HTTPException(status_code=503, detail="Centre data is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Centre data is unavailable") from exc
