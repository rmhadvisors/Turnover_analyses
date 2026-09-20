from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models
from app.database import Base, get_db
from app.main import app
from app.repositories import threshold_repo

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "sample_data"


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with factory() as seed_session:
        threshold_repo.seed_defaults(seed_session)
    return factory


@pytest.fixture()
def db_session(session_factory):
    session: Session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def api(session_factory):
    """TestClient wired to an isolated in-memory database (lifespan not run)."""

    def override():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def sample_dir() -> Path:
    return SAMPLE_DIR
