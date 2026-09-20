"""FastAPI application entry point and router registration."""
from fastapi import FastAPI

from app.config import settings

app = FastAPI(title=settings.app_name)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Simple liveness check used by the frontend and deployment probes."""
    return {"status": "ok"}


# Routers are registered here as they are built out in later stages:
# from app.api import clients, imports, entries, thresholds, reports, alerts
# app.include_router(clients.router)
# app.include_router(imports.router)
# app.include_router(entries.router)
# app.include_router(thresholds.router)
# app.include_router(reports.router)
# app.include_router(alerts.router)
