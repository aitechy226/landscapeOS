"""
LandscapeOS — FastAPI Application Entry Point
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import structlog
import logging
import sys

from config import settings
from db.database import check_db_connection
from middleware.tenant import TenantMiddleware, SecurityHeadersMiddleware
from api.v1.auth import router as auth_router
from api.v1.tenant import router as tenant_router
from api.v1.admin.admin import router as admin_router
from schemas.schemas import HealthResponse


# ─── Structured Logging Setup ────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer() if settings.is_production
        else structlog.dev.ConsoleRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.BoundLogger,
    cache_logger_on_first_use=True,
)

logging.basicConfig(
    format="%(message)s",
    stream=sys.stdout,
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
)

log = structlog.get_logger()


# ─── App Lifecycle ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("app.starting", env=settings.APP_ENV)
    db_ok = await check_db_connection()
    if not db_ok:
        log.warning("app.db_connection_failed — starting anyway")
    else:
        log.info("app.started", env=settings.APP_ENV)
    yield
    log.info("app.shutting_down")



# ─── App Instance ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="LandscapeOS API",
    description="Multi-tenant SaaS platform for landscaping businesses",
    version="1.0.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url="/openapi.json" if not settings.is_production else None,
    lifespan=lifespan,
)


# ─── Middleware (order matters — outermost first) ─────────────────────────────

app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
)

app.add_middleware(TenantMiddleware)


# ─── Rate Limiting ────────────────────────────────────────────────────────────

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ─── Routes ──────────────────────────────────────────────────────────────────

app.include_router(auth_router, prefix=settings.API_PREFIX)
app.include_router(tenant_router, prefix=settings.API_PREFIX)
app.include_router(admin_router, prefix=settings.API_PREFIX)


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    db_ok = await check_db_connection()
    return HealthResponse(
        status="healthy" if db_ok else "degraded",
        database=db_ok,
    )


# ─── Global Exception Handlers ───────────────────────────────────────────────

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={"detail": "Resource not found", "code": "NOT_FOUND"},
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    log.error("app.unhandled_exception",
               path=str(request.url.path),
               error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "code": "SERVER_ERROR"},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    log.error("app.exception",
               path=str(request.url.path),
               error=str(exc),
               exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred", "code": "SERVER_ERROR"},
    )
