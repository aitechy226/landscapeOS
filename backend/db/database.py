"""
Database connection abstraction.
Migration path: shared DB → schema-per-tenant → DB-per-tenant
Only this file needs to change when migrating.
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
import ssl
import asyncpg
from sqlalchemy import MetaData, text
from typing import AsyncGenerator
from uuid import UUID
import structlog
from sqlalchemy.pool import NullPool
from config import settings

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


log = structlog.get_logger()

# Naming convention for Alembic migrations
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)


class Base(DeclarativeBase):
    metadata = metadata

async def create_asyncpg_connection(**kwargs):
    return await asyncpg.connect(
        settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"),
        ssl=ssl_context,
        statement_cache_size=0,
    )


engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
    echo=settings.DEBUG,
    connect_args={
        "ssl": ssl_context,
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    },
)


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_db_for_tenant(tenant_id: UUID) -> AsyncGenerator[AsyncSession, None]:
    """
    Tenant-aware DB session factory.

    STAGE 1 (current): Returns shared DB session — tenant isolation via tenant_id column + RLS.
    STAGE 2 (future): Route to tenant's own Postgres schema.
    STAGE 3 (future): Route to tenant's own database instance.

    Changing this function is the ONLY thing needed to migrate between stages.
    """
    # Stage 1: shared DB — same session for all tenants
    return get_db()
    # Stage 2 (uncomment when ready):
    # return get_schema_db(tenant_id)
    # Stage 3 (uncomment when ready):
    # return get_tenant_db(tenant_id)


async def init_db():
    """Create all tables. Used in development only — use Alembic in production."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("db.initialized")


async def check_db_connection() -> bool:
    """Health check — verify DB is reachable using raw asyncpg."""
    url = getattr(settings, "DATABASE_URL", None)
    if not url or not str(url).strip():
        log.error("db.health_check_failed", error="DATABASE_URL not set")
        return False
    try:
        conn = await asyncpg.connect(
            url.replace("postgresql+asyncpg://", "postgresql://"),
            ssl=ssl_context,
            statement_cache_size=0,
        )
        await conn.fetchval("SELECT 1")
        await conn.close()
        return True
    except Exception as e:
        log.error("db.health_check_failed", error=str(e))
        return False


