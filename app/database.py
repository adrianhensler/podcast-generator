from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    # Add columns introduced after initial schema (safe to run repeatedly)
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE stage_logs ADD COLUMN thinking TEXT",
            "ALTER TABLE projects ADD COLUMN language VARCHAR(32) DEFAULT 'English'",
            "ALTER TABLE projects ADD COLUMN flow_type VARCHAR(32) DEFAULT 'explainer'",
            "ALTER TABLE stage_logs ADD COLUMN truncated BOOLEAN DEFAULT 0",
            "ALTER TABLE projects ADD COLUMN callback_url TEXT",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists
