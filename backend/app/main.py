from fastapi import FastAPI

from app.api.analytics import router as analytics_router
from app.api.leagues import router as leagues_router

app = FastAPI(
    title="Fantasy What-If API",
    description=("Backend API and analytics engine for ESPN fantasy football what-if analysis."),
    version="0.1.0",
)

app.include_router(leagues_router)
app.include_router(analytics_router)


@app.get("/health")
def read_health() -> dict[str, str]:
    return {"status": "ok"}
