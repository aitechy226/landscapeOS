"""
Tenant context middleware.
Resolves tenant from JWT on every request.
Sets request.state.tenant_id — never trust client-supplied tenant_id.
"""
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import jwt as pyjwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidAlgorithmError, PyJWTError
import structlog

from config import settings

log = structlog.get_logger()

PUBLIC_PATHS = {
    "/health",
    "/api/v1/auth/login",
    "/api/v1/auth/signup",
    "/api/v1/auth/refresh",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/resend-confirmation",
    "/api/v1/webhooks/stripe",
    "/docs",
    "/redoc",
    "/openapi.json",
}


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Extracts tenant_id from JWT on every authenticated request.
    Sets request.state.tenant_id for use anywhere in the request lifecycle.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip public paths
        if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/docs"):
            return await call_next(request)

        # Initialize state
        request.state.tenant_id = None
        request.state.user_id = None
        request.state.user_role = None

        # Extract tenant from Authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = None
            try:
                payload = pyjwt.decode(
                    token,
                    settings.SUPABASE_JWT_SECRET,
                    algorithms=["HS256"],
                    options={"verify_aud": False},
                )
            except InvalidAlgorithmError:
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
                except PyJWTError:
                    pass
            except PyJWTError:
                pass  # Will be caught by get_current_user dependency
            if payload:
                # tenant_id may be in custom claims; Supabase JWTs use sub for user id
                tenant_id = payload.get("tenant_id")
                if tenant_id:
                    request.state.tenant_id = tenant_id

        # Subdomain-based tenant resolution (greenthumb.landscapeos.com)
        host = request.headers.get("host", "")
        if "." in host:
            subdomain = host.split(".")[0]
            if subdomain not in ("www", "app", "api", "admin"):
                request.state.subdomain = subdomain

        response = await call_next(request)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; "
                "connect-src 'self' https://*.supabase.co;"
            )

        return response
