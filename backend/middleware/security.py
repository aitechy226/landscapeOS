"""
Security layer — authentication, authorization, audit logging.
Every endpoint must use require_permission(). No exceptions.
"""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import jwt as pyjwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidAlgorithmError, PyJWTError
from functools import wraps
from typing import Optional, Callable
from uuid import UUID
from datetime import datetime
import structlog

from config import settings
from db.database import get_db
from models.models import User, UserRole, AuditLog

log = structlog.get_logger()
security = HTTPBearer()


# ─── Permissions Map ─────────────────────────────────────────────────────────

PERMISSIONS: dict[str, list[UserRole]] = {
    # Quotes
    "quotes:read":           [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD],
    "quotes:read_own":       [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD],
    "quotes:read_all":       [UserRole.OWNER, UserRole.ADMIN],
    "quotes:create":         [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD],
    "quotes:write":          [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD],
    "quotes:update":         [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD],
    "quotes:delete":         [UserRole.OWNER, UserRole.ADMIN],
    "quotes:send":           [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD],
    "quotes:approve":        [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD],
    # Clients
    "clients:create":        [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD],
    "clients:read":          [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD],
    "clients:update":        [UserRole.OWNER, UserRole.ADMIN],
    "clients:delete":        [UserRole.OWNER, UserRole.ADMIN],
    # Crews & Jobs
    "crews:manage":          [UserRole.OWNER, UserRole.ADMIN],
    "jobs:read_all":         [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD],
    "jobs:read_own":         [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD, UserRole.LABORER],
    "jobs:update_status":    [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD, UserRole.LABORER],
    # Catalog
    "catalog:read":          [UserRole.OWNER, UserRole.ADMIN, UserRole.CREW_LEAD],
    "catalog:manage":        [UserRole.OWNER, UserRole.ADMIN],
    # Tenant Settings
    "settings:read":         [UserRole.OWNER, UserRole.ADMIN],
    "settings:manage":       [UserRole.OWNER, UserRole.ADMIN],
    "billing:manage":        [UserRole.OWNER],
    # Users
    "users:invite":          [UserRole.OWNER, UserRole.ADMIN],
    "users:manage":          [UserRole.OWNER, UserRole.ADMIN],
    "users:read":            [UserRole.OWNER, UserRole.ADMIN],
}


# ─── Token Validation ────────────────────────────────────────────────────────

async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Validate JWT from Supabase Auth.
    Extracts tenant_id and user info, sets on request.state.
    SUPABASE_JWT_SECRET in .env must match Supabase Dashboard → Project Settings → API → JWT Secret.
    """
    token = credentials.credentials
    err_detail = {"message": "Your session has expired or is invalid. Please sign in again.", "code": "AUTH_FAILED"}

    payload = None
    try:
        # Prefer legacy HS256 with JWT secret (backward compatibility)
        payload = pyjwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except InvalidAlgorithmError:
        # Token is signed with ES256/RS256 (Supabase signing keys); verify via JWKS
        try:
            jwks_url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
            jwks_client = PyJWKClient(jwks_url, cache_jwk_set=True)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            payload = pyjwt.decode(
                token,
                signing_key.key,
                algorithms=["ES256", "RS256"],
                options={"verify_aud": False},
            )
        except PyJWTError as e:
            log.warning("auth.jwt_invalid", error=str(e), path=str(request.url.path), method="JWKS")
            err_msg = str(e).lower()
            if "expired" in err_msg or "exp" in err_msg:
                err_detail = {"message": "Your session has expired. Please sign in again.", "code": "TOKEN_EXPIRED"}
            else:
                err_detail = {"message": "Invalid session. Please sign in again.", "code": "TOKEN_INVALID"}
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=err_detail,
                headers={"WWW-Authenticate": "Bearer"},
            )
    except PyJWTError as e:
        err_msg = str(e).lower()
        if "expired" in err_msg or "exp" in err_msg:
            err_detail = {"message": "Your session has expired. Please sign in again.", "code": "TOKEN_EXPIRED"}
        else:
            err_detail = {"message": "Invalid session. Please sign in again.", "code": "TOKEN_INVALID"}
        log.warning(
            "auth.jwt_invalid",
            error=str(e),
            path=str(request.url.path),
            hint="Ensure SUPABASE_JWT_SECRET in .env matches Supabase Dashboard → API → JWT Secret",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=err_detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    supabase_user_id: str = payload.get("sub")
    if not supabase_user_id:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid session. Please sign in again.", "code": "TOKEN_INVALID"},
        )

    # Load user from DB
    result = await db.execute(
        select(User).where(
            User.supabase_user_id == supabase_user_id,
            User.is_active == True,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        log.warning("auth.user_not_found", sub=supabase_user_id, path=str(request.url.path))
        raise HTTPException(
            status_code=401,
            detail={"message": "Account not found or inactive. Please sign in again.", "code": "USER_NOT_FOUND"},
        )

    # Set tenant context on request — available everywhere
    request.state.tenant_id = user.tenant_id
    request.state.user_id = user.id
    request.state.user_role = user.role

    return user


# ─── Permission Enforcement ──────────────────────────────────────────────────

def require_permission(permission: str):
    """
    FastAPI dependency factory — enforces RBAC.
    Usage: user: User = Depends(require_permission("quotes:create"))
    """
    async def check_permission(
        request: Request,
        user: User = Depends(get_current_user),
    ) -> User:
        if permission not in PERMISSIONS:
            log.error("authz.unknown_permission", permission=permission)
            raise HTTPException(
                status_code=500,
                detail={"message": "Unknown permission.", "code": "UNKNOWN_PERMISSION"},
            )

        allowed_roles = PERMISSIONS[permission]
        if user.role not in allowed_roles:
            log.warning(
                "authz.denied",
                user_id=str(user.id),
                tenant_id=str(user.tenant_id),
                permission=permission,
                role=user.role,
                path=str(request.url.path),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"message": "Insufficient permissions.", "code": "FORBIDDEN"},
            )

        return user

    return check_permission


def require_admin():
    """Shortcut for owner/admin only endpoints."""
    return require_permission("settings:manage")


def require_owner():
    """Shortcut for owner-only endpoints (billing, delete tenant)."""
    return require_permission("billing:manage")


# ─── Audit Logging ───────────────────────────────────────────────────────────

def audit_log(action: str, resource_type: Optional[str] = None):
    """
    Decorator that logs every write action to audit_logs table.
    Usage: @audit_log("quote.created", "quote")
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request: Optional[Request] = kwargs.get("request") or next(
                (a for a in args if isinstance(a, Request)), None
            )
            db: Optional[AsyncSession] = kwargs.get("db") or next(
                (a for a in args if isinstance(a, AsyncSession)), None
            )

            result = await func(*args, **kwargs)

            # Log after successful execution
            if request and db:
                try:
                    tenant_id = getattr(request.state, "tenant_id", None)
                    user_id = getattr(request.state, "user_id", None)

                    # Extract resource_id from result if it has an id
                    res_id = None
                    if hasattr(result, "id"):
                        res_id = result.id
                    elif isinstance(result, dict) and "id" in result:
                        res_id = result["id"]

                    audit = AuditLog(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        action=action,
                        resource_type=resource_type,
                        resource_id=res_id,
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("user-agent", "")[:500],
                    )
                    db.add(audit)
                    await db.flush()

                    log.info(
                        "audit.logged",
                        action=action,
                        tenant_id=str(tenant_id) if tenant_id else None,
                        resource_type=resource_type,
                    )
                except Exception as e:
                    # Never let audit logging break the actual request
                    log.error("audit.log_failed", action=action, error=str(e))

            return result
        return wrapper
    return decorator


async def log_security_event(
    db: AsyncSession,
    event_type: str,
    user_id: Optional[UUID],
    tenant_id: Optional[UUID],
    request: Optional[Request] = None,
    meta: Optional[dict] = None,
):
    """Log a security event and check thresholds."""
    SECURITY_THRESHOLDS = {
        "auth.failed_login":       {"threshold": 5,  "window_minutes": 10},
        "authz.denied":            {"threshold": 10, "window_minutes": 5},
        "api.rate_limit_exceeded": {"threshold": 3,  "window_minutes": 1},
    }

    audit = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=event_type,
        ip_address=request.client.host if request and request.client else None,
        meta=meta or {},
    )
    db.add(audit)
    await db.flush()

    config = SECURITY_THRESHOLDS.get(event_type)
    if config:
        log.warning(
            "security.event",
            event_type=event_type,
            user_id=str(user_id) if user_id else None,
            tenant_id=str(tenant_id) if tenant_id else None,
        )
