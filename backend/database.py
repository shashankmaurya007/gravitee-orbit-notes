from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from pathlib import Path
from .config import settings


Path("data").mkdir(exist_ok=True)

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    from . import models  # noqa: F401 — registers models with Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrate: add activity_level column if it doesn't exist yet
        try:
            await conn.execute(
                __import__("sqlalchemy").text(
                    "ALTER TABLE weekly_notes ADD COLUMN activity_level VARCHAR DEFAULT 'low'"
                )
            )
        except Exception:
            pass  # column already exists
    # Backfill existing rows: compute from structured fields
    async with AsyncSessionLocal() as db:
        from sqlalchemy import text
        await db.execute(text("""
            UPDATE weekly_notes SET activity_level =
              CASE
                WHEN risks_blockers IS NOT NULL THEN 'significant'
                WHEN onboarding_summary IS NOT NULL AND production_summary IS NOT NULL THEN 'significant'
                WHEN onboarding_summary IS NOT NULL OR production_summary IS NOT NULL THEN 'moderate'
                ELSE 'none'
              END
            WHERE activity_level IS NULL OR activity_level = 'low'
        """))
        await db.commit()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
