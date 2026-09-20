"""FastAPI application entry point and router registration."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

import app.models
from app.api import alerts, clients, entries, imports, reports, thresholds
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.repositories import threshold_repo


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        threshold_repo.seed_defaults(db)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

for module in (clients, imports, entries, thresholds, reports, alerts):
    app.include_router(module.router)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Simple liveness check used by the frontend and deployment probes."""
    return {"status": "ok"}
