"""
Authentication endpoints — signup, login, refresh, logout.
Uses Supabase Auth for token management.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from uuid import UUID
import structlog

from db.database import get_db
from models.models import Tenant, User, UserRole, TenantStatus, TenantTier
from schemas.schemas import (
    SignupRequest, LoginRequest, TokenResponse, RefreshRequest,
    ResetPasswordRequest, TenantResponse, UserResponse,
)
from repositories.repositories import TenantRepo, UserRepo
from config import settings
from services.supabase_service import SupabaseService, EmailNotConfirmedError
from services.onboarding_service import OnboardingService
from middleware.security import get_current_user

log = structlog.get_logger()
router = APIRouter(prefix="/auth", tags=["Authentication"])
supabase_svc = SupabaseService()


def _raise_email_not_confirmed():
    """Raise 401 with structured detail for unconfirmed email (frontend can show resend button)."""
    raise HTTPException(
        status_code=401,
        detail={
            "message": "Your email address isn’t confirmed yet. Check your inbox for the confirmation link, or request a new one below.",
            "code": "EMAIL_NOT_CONFIRMED",
            "resend_confirmation_available": True,
        },
    )


@router.post("/signup", response_model=dict, status_code=201)
async def signup(
    body: SignupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new tenant account.
    1. Validate slug is unique
    2. Create Supabase auth user
    3. Create Tenant + owner User in DB
    4. Return tokens
    """
    tenant_repo = TenantRepo(db)

    # Slug unique among active tenants; cancelled/suspended can be reused (we free the slug below)
    existing_active = await tenant_repo.get_active_by_slug(body.company_slug)
    if existing_active:
        raise HTTPException(
            status_code=400,
            detail={"message": "Company URL is already taken. Please choose another."},
        )
    # If slug exists on a cancelled/suspended tenant, free it so the new signup can use it
    existing_any = await tenant_repo.get_by_slug(body.company_slug)
    if existing_any:
        await tenant_repo.update(
            existing_any.id,
            slug=f"{existing_any.slug}-cancelled-{str(existing_any.id)[:8]}",
        )

    # Create Supabase auth user
    try:
        auth_user = await supabase_svc.create_user(
            email=body.email,
            password=body.password,
        )
    except Exception as e:
        log.error("auth.signup_failed", error=str(e))
        raise HTTPException(
            status_code=400,
            detail={"message": str(e) if str(e) else "Signup failed. Please try again."},
        )

    # Create tenant
    trial_ends = datetime.now(timezone.utc) + timedelta(days=14)
    tenant = await tenant_repo.create(
        name=body.company_name,
        slug=body.company_slug,
        status=TenantStatus.TRIAL,
        tier=TenantTier.STARTER,
        trial_ends_at=trial_ends,
        timezone=body.timezone,
        billing_email=body.email,
    )

    # Create owner user
    user_repo = UserRepo(db, tenant.id)
    user = await user_repo.create(
        supabase_user_id=auth_user["id"],
        email=body.email.lower(),
        first_name=body.first_name,
        last_name=body.last_name,
        role=UserRole.OWNER,
        is_active=True,
    )

    log.info(
        "auth.signup_success",
        tenant_id=str(tenant.id),
        tenant_slug=tenant.slug,
    )
    return {
        "message": "Account created! Please check your email to confirm your account before logging in.",
        "tenant_slug": tenant.slug,
    }


@router.post("/login", response_model=dict)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Login with email/password."""
    try:
        tokens = await supabase_svc.sign_in(body.email, body.password)
    except EmailNotConfirmedError:
        _raise_email_not_confirmed()
    except Exception:
        # Supabase often returns generic "Invalid login credentials" for unconfirmed users.
        # Fallback: check our DB + Supabase admin for this email's confirmation status.
        try:
            result = await db.execute(
                select(User).where(User.email == body.email.lower()).limit(1)
            )
            app_user = result.scalar_one_or_none()
            if app_user:
                supabase_user = await supabase_svc.get_user_by_id(app_user.supabase_user_id)
                if supabase_user and not supabase_user.get("email_confirmed_at"):
                    _raise_email_not_confirmed()
        except HTTPException:
            raise
        log.warning("auth.login_failed", email_domain=body.email.split("@")[-1])
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Load user from DB
    result = await db.execute(
        __import__("sqlalchemy").select(User).where(
            User.supabase_user_id == tokens["user"]["id"],
            User.is_active == True,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="Account not found")

    # Update last login
    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()

    # Load tenant
    tenant_repo = TenantRepo(db)
    tenant = await tenant_repo.get_by_id(user.tenant_id)

    # Onboarding: use explicit flag first (set when user completes step 5), else infer from catalog/crew data
    if getattr(tenant, "onboarding_completed_at", None) is not None:
        onboarding_required = False
    else:
        onboarding_svc = OnboardingService(db, user.tenant_id)
        onboarding_status = await onboarding_svc.get_status()
        onboarding_required = not onboarding_status.get("is_complete", False)

    log.info("auth.login_success", tenant_id=str(user.tenant_id))

    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "onboarding_required": onboarding_required,
        "tenant": {
            "id": str(tenant.id),
            "name": tenant.name,
            "slug": tenant.slug,
            "tier": tenant.tier,
            "status": tenant.status,
        },
        "user": {
            "id": str(user.id),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "role": user.role,
            "mfa_enabled": user.mfa_enabled,
        },
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest):
    """Rotate refresh token and issue new access token."""
    try:
        tokens = await supabase_svc.refresh_session(body.refresh_token)
        return TokenResponse(
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")


@router.post("/logout")
async def logout(
    user: User = Depends(get_current_user),
):
    """Invalidate session — all devices."""
    try:
        await supabase_svc.sign_out(user.supabase_user_id)
    except Exception as e:
        log.error("auth.logout_error", error=str(e))
    return {"message": "Logged out successfully"}


@router.post("/forgot-password")
async def forgot_password(email: str):
    """Send password reset email via Supabase."""
    # Always return success (don't reveal if email exists)
    try:
        await supabase_svc.send_password_reset(email)
    except Exception:
        pass
    return {"message": "If that email exists, a reset link has been sent"}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest):
    """Confirm password reset with token from reset email link."""
    try:
        await supabase_svc.reset_password(
            email=body.email,
            token=body.token,
            new_password=body.new_password,
        )
        return {"message": "Password has been reset. You can now log in with your new password."}
    except Exception as e:
        log.warning("auth.reset_password_failed", error=str(e))
        raise HTTPException(
            status_code=400,
            detail={"message": "Invalid or expired reset link. Please request a new one."},
        )


@router.post("/resend-confirmation")
async def resend_confirmation(body: dict):
    """Resend the signup confirmation email. No auth required — used after login returns EMAIL_NOT_CONFIRMED."""
    email = (body.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail={"message": "Email is required."})
    try:
        await supabase_svc.resend_confirmation(email)
        return {
            "message": "A new confirmation link has been sent to your email. Check your inbox and spam folder.",
        }
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"message": str(e) if str(e) else "Could not resend. Please try again."},
        )


