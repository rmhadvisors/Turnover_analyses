"""FastAPI application entry point and router registration."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.models
from app.api import alerts, clients, entries, imports, reports, tds, thresholds, workspace
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.migrations import upgrade
from app.repositories import tds_repo, threshold_repo


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    # create_all only creates missing tables; add indexes declared since a table was made.
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(engine, checkfirst=True)
    upgrade(engine)
    with SessionLocal() as db:
        threshold_repo.seed_defaults(db)
        tds_repo.seed_sections(db)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

for module in (clients, imports, entries, thresholds, reports, alerts, workspace, tds):
    app.include_router(module.router)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Simple liveness check used by the frontend and deployment probes."""
    return {"status": "ok"}
