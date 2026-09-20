import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    import app.models  # noqa: F401  ensure all models are registered

    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session: Session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
