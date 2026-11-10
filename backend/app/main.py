from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.analytics import router as analytics_router
from app.api.auth import router as auth_router
from app.api.leagues import router as leagues_router
from app.api.setup import router as setup_router
from app.core.settings import get_settings
from app.db.session import SessionFactory
from app.error_handlers import (
    unexpected_exception_handler,
    validation_exception_handler,
)
from app.security_headers import (
    security_headers_middleware,
)
from app.services.season_lifecycle_reconciliation import (
    reconcile_stale_active_season,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    with SessionFactory() as session:
        reconcile_stale_active_season(session)

    yield


app = FastAPI(
    title="Fantasy What-If API",
    description=("Backend API and analytics engine for ESPN fantasy football what-if analysis."),
    version="0.1.0",
    debug=settings.app_debug,
    lifespan=lifespan,
)

app.add_exception_handler(
    RequestValidationError,
    validation_exception_handler,
)
app.add_exception_handler(
    Exception,
    unexpected_exception_handler,
)


app.middleware("http")(security_headers_middleware(settings))

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.trusted_origin_list),
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ],
    allow_headers=[
        "Accept",
        "Authorization",
        "Content-Type",
        "X-CSRF-Token",
    ],
)

app.include_router(auth_router)
app.include_router(setup_router)
app.include_router(admin_router)
app.include_router(leagues_router)
app.include_router(analytics_router)


@app.get("/health")
def read_health() -> dict[str, str]:
    return {"status": "ok"}
