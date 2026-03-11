"""
Tenant management — settings, onboarding wizard, user management.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID
import structlog

from db.database import get_db
from models.models import User
from schemas.schemas import (
    TenantResponse, UpdateTenantRequest,
    UserResponse, InviteUserRequest, UpdateUserRequest,
    ServiceResponse, CreateServiceRequest,
    MaterialResponse, CreateMaterialRequest,
    LaborRateResponse, CreateLaborRateRequest,
    CrewResponse, CreateCrewRequest,
    OnboardingStep1, OnboardingStep2, OnboardingStep3,
    OnboardingStep4, OnboardingStep5, OnboardingStatusResponse,
    PaginatedResponse, CreateClientRequest,
)
from repositories.repositories import (
    TenantRepo, UserRepo, ServiceCatalogRepo,
    MaterialCatalogRepo, LaborRateRepo, CrewRepo, ClientRepo,
)
from middleware.security import (
    require_permission, require_admin, audit_log, get_current_user
)
from services.onboarding_service import OnboardingService

log = structlog.get_logger()
router = APIRouter(tags=["Tenant"])


# ─── Tenant Settings ─────────────────────────────────────────────────────────

@router.get("/tenant", response_model=TenantResponse)
async def get_tenant(
    request: Request,
    user: User = Depends(require_permission("settings:read")),
    db: AsyncSession = Depends(get_db),
):
    repo = TenantRepo(db)
    tenant = await repo.get_by_id(user.tenant_id)
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    return tenant


def _user_error(message: str):
    """Return 400 with a consistent shape the frontend can show to the user."""
    return HTTPException(status_code=400, detail={"message": message})


@router.patch("/tenant", response_model=TenantResponse)
async def update_tenant(
    body: UpdateTenantRequest,
    request: Request,
    user: User = Depends(require_permission("settings:manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = body.model_dump(exclude_none=True)
        if "tax_rate" in data and (data["tax_rate"] is None or data["tax_rate"] < 0 or data["tax_rate"] > 1):
            raise _user_error("Tax rate must be between 0% and 100%.")
        if "minimum_quote" in data and (
            data["minimum_quote"] is None
            or data["minimum_quote"] < 0
            or float(data["minimum_quote"]) > 500
        ):
            raise _user_error("Minimum quote must be between $0 and $500.")
        if "name" in data and (not data["name"] or not str(data["name"]).strip()):
            raise _user_error("Company name is required.")
        repo = TenantRepo(db)
        tenant = await repo.update(user.tenant_id, **data)
        if not tenant:
            raise HTTPException(404, "Tenant not found")
        log.info("tenant.updated", tenant_id=str(user.tenant_id))
        return tenant
    except HTTPException:
        raise
    except Exception as e:
        log.exception("tenant.update_failed", error=str(e))
        raise _user_error("Could not save settings. Please try again.")


# ─── Onboarding ───────────────────────────────────────────────────────────────

@router.get("/onboarding/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    request: Request,
    user: User = Depends(require_permission("settings:read")),
    db: AsyncSession = Depends(get_db),
):
    svc = OnboardingService(db, user.tenant_id)
    return await svc.get_status()


def _onboarding_error(message: str):
    """Return 400 with a consistent shape the frontend can show to the user."""
    return HTTPException(status_code=400, detail={"message": message})


@router.post("/onboarding/step/1")
async def onboarding_step1(
    body: OnboardingStep1,
    request: Request,
    user: User = Depends(require_permission("settings:manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        repo = TenantRepo(db)
        data = body.model_dump(exclude_none=True)
        phone = (data.get("company_phone") or "").strip()
        if not phone:
            raise _onboarding_error("Business phone is required (10–15 digits).")
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) < 10:
            raise _onboarding_error("Business phone must be at least 10 digits.")
        if len(digits) > 15:
            raise _onboarding_error("Business phone must be at most 15 digits.")
        if data.get("tax_rate") is not None and (float(data["tax_rate"]) < 0 or float(data["tax_rate"]) > 1):
            raise _onboarding_error("Tax rate must be between 0% and 100%.")
        if data.get("minimum_quote") is not None:
            mq = float(data["minimum_quote"])
            if mq < 0 or mq > 500:
                raise _onboarding_error("Minimum quote must be between $0 and $500.")
        await repo.update(user.tenant_id, **data)
        return {"step": 1, "completed": True}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("onboarding.step1_failed", tenant_id=str(user.tenant_id))
        raise _onboarding_error("Could not save company info. Please try again.")


@router.post("/onboarding/step/2")
async def onboarding_step2(
    body: OnboardingStep2,
    request: Request,
    user: User = Depends(require_permission("settings:manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        svc = OnboardingService(db, user.tenant_id)
        await svc.setup_services(body.template, body.services)
        return {"step": 2, "completed": True}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("onboarding.step2_failed", tenant_id=str(user.tenant_id))
        raise _onboarding_error("Could not save services. Please try again.")


@router.post("/onboarding/step/3")
async def onboarding_step3(
    body: OnboardingStep3,
    request: Request,
    user: User = Depends(require_permission("settings:manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        svc = OnboardingService(db, user.tenant_id)
        await svc.setup_materials(body.materials)
        return {"step": 3, "completed": True}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("onboarding.step3_failed", tenant_id=str(user.tenant_id))
        raise _onboarding_error("Could not save materials. Please try again.")


@router.post("/onboarding/step/4")
async def onboarding_step4(
    body: OnboardingStep4,
    request: Request,
    user: User = Depends(require_permission("settings:manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        svc = OnboardingService(db, user.tenant_id)
        await svc.setup_labor_rates(body.labor_rates)
        return {"step": 4, "completed": True}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("onboarding.step4_failed", tenant_id=str(user.tenant_id))
        raise _onboarding_error("Could not save labor rates. Please try again.")


@router.post("/onboarding/step/5")
async def onboarding_step5(
    body: OnboardingStep5,
    request: Request,
    user: User = Depends(require_permission("settings:manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        name = (body.crew_name or "").strip()
        if not name:
            raise _onboarding_error("Please enter a crew name (e.g. Team A, North Crew).")
        if len(name) > 255:
            raise _onboarding_error("Crew name is too long.")
        crew_repo = CrewRepo(db, user.tenant_id)
        await crew_repo.create(name=name)
        tenant_repo = TenantRepo(db)
        await tenant_repo.update(user.tenant_id, onboarding_completed_at=datetime.now(timezone.utc))
        return {"step": 5, "completed": True, "onboarding_complete": True}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("onboarding.step5_failed", tenant_id=str(user.tenant_id))
        raise _onboarding_error("Could not create crew. Please try again.")


# ─── User Management ─────────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserResponse])
async def list_users(
    request: Request,
    user: User = Depends(require_permission("users:read")),
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepo(db, user.tenant_id)
    users, _ = await repo.get_paginated(page_size=100)
    return users


@router.post("/users/invite", response_model=UserResponse, status_code=201)
async def invite_user(
    body: InviteUserRequest,
    request: Request,
    user: User = Depends(require_permission("users:invite")),
    db: AsyncSession = Depends(get_db),
):
    from services.supabase_service import SupabaseService
    from config import settings as cfg

    # Check tier user limits
    repo = UserRepo(db, user.tenant_id)
    current_count = await repo.count_active()

    tenant_repo = TenantRepo(db)
    tenant = await tenant_repo.get_by_id(user.tenant_id)

    TIER_USER_LIMITS = {"starter": 3, "pro": 10, "enterprise": -1}
    limit = TIER_USER_LIMITS.get(tenant.tier, 3)
    if limit != -1 and current_count >= limit:
        raise HTTPException(
            400,
            detail={"message": f"Your {tenant.tier} plan allows up to {limit} users. Upgrade to add more."},
        )

    # Check email not already in tenant
    existing = await repo.get_by_email(body.email)
    if existing:
        raise HTTPException(400, detail={"message": "User with this email already exists in your account."})

    # Create Supabase auth user and send invite email
    svc = SupabaseService()
    try:
        auth_user = await svc.invite_user(body.email)
        new_user = await repo.create(
            supabase_user_id=auth_user["id"],
            email=body.email.lower(),
            first_name=body.first_name,
            last_name=body.last_name,
            role=body.role,
            is_active=True,
        )
        log.info(
            "user.invited",
            tenant_id=str(user.tenant_id),
            invited_role=body.role,
        )
        return new_user
    except HTTPException:
        raise
    except Exception as e:
        log.exception("users.invite_failed", error=str(e))
        raise HTTPException(
            400,
            detail={"message": str(e) if str(e) else "Failed to send invite. Please try again."},
        )


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    body: UpdateUserRequest,
    request: Request,
    current_user: User = Depends(require_permission("users:manage")),
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepo(db, current_user.tenant_id)

    # Cannot demote yourself
    if user_id == current_user.id and body.role and body.role != current_user.role:
        raise HTTPException(400, detail={"message": "Cannot change your own role."})

    try:
        updated = await repo.update(user_id, **body.model_dump(exclude_none=True))
        if not updated:
            raise HTTPException(404, "User not found")
        return updated
    except HTTPException:
        raise
    except Exception as e:
        log.exception("users.update_failed", error=str(e))
        raise _user_error("Could not update user. Please try again.")


@router.delete("/users/{user_id}")
async def deactivate_user(
    user_id: UUID,
    request: Request,
    current_user: User = Depends(require_permission("users:manage")),
    db: AsyncSession = Depends(get_db),
):
    if user_id == current_user.id:
        raise HTTPException(400, "Cannot deactivate your own account")

    repo = UserRepo(db, current_user.tenant_id)
    success = await repo.soft_delete(user_id)
    if not success:
        raise HTTPException(404, "User not found")
    return {"message": "User deactivated"}


# ─── Service Catalog ─────────────────────────────────────────────────────────

@router.get("/catalog/services", response_model=list[ServiceResponse])
async def list_services(
    request: Request,
    user: User = Depends(require_permission("catalog:read")),
    db: AsyncSession = Depends(get_db),
):
    repo = ServiceCatalogRepo(db, user.tenant_id)
    return await repo.get_active()


@router.post("/catalog/services", response_model=ServiceResponse, status_code=201)
async def create_service(
    body: CreateServiceRequest,
    request: Request,
    user: User = Depends(require_permission("catalog:manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        repo = ServiceCatalogRepo(db, user.tenant_id)
        return await repo.create(**body.model_dump())
    except HTTPException:
        raise
    except Exception as e:
        log.exception("catalog.service_create_failed", error=str(e))
        raise _user_error("Could not add service. Please check the form and try again.")


@router.patch("/catalog/services/{service_id}", response_model=ServiceResponse)
async def update_service(
    service_id: UUID,
    body: CreateServiceRequest,
    request: Request,
    user: User = Depends(require_permission("catalog:manage")),
    db: AsyncSession = Depends(get_db),
):
    repo = ServiceCatalogRepo(db, user.tenant_id)
    updated = await repo.update(service_id, **body.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(404, "Service not found")
    return updated


@router.delete("/catalog/services/{service_id}")
async def delete_service(
    service_id: UUID,
    request: Request,
    user: User = Depends(require_permission("catalog:manage")),
    db: AsyncSession = Depends(get_db),
):
    repo = ServiceCatalogRepo(db, user.tenant_id)
    success = await repo.soft_delete(service_id)
    if not success:
        raise HTTPException(404, "Service not found")
    return {"message": "Service removed"}


# ─── Material Catalog ────────────────────────────────────────────────────────

@router.get("/catalog/materials", response_model=list[MaterialResponse])
async def list_materials(
    request: Request,
    user: User = Depends(require_permission("catalog:read")),
    db: AsyncSession = Depends(get_db),
):
    repo = MaterialCatalogRepo(db, user.tenant_id)
    return await repo.get_active()


@router.post("/catalog/materials", response_model=MaterialResponse, status_code=201)
async def create_material(
    body: CreateMaterialRequest,
    request: Request,
    user: User = Depends(require_permission("catalog:manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        repo = MaterialCatalogRepo(db, user.tenant_id)
        return await repo.create(**body.model_dump())
    except HTTPException:
        raise
    except Exception as e:
        log.exception("catalog.material_create_failed", error=str(e))
        raise _user_error("Could not add material. Please check the form and try again.")


# ─── Labor Rates ─────────────────────────────────────────────────────────────

@router.get("/catalog/labor-rates", response_model=list[LaborRateResponse])
async def list_labor_rates(
    request: Request,
    user: User = Depends(require_permission("catalog:read")),
    db: AsyncSession = Depends(get_db),
):
    repo = LaborRateRepo(db, user.tenant_id)
    return await repo.get_all()


@router.post("/catalog/labor-rates", response_model=LaborRateResponse, status_code=201)
async def create_labor_rate(
    body: CreateLaborRateRequest,
    request: Request,
    user: User = Depends(require_permission("catalog:manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        repo = LaborRateRepo(db, user.tenant_id)
        return await repo.create(**body.model_dump())
    except HTTPException:
        raise
    except Exception as e:
        log.exception("catalog.labor_rate_create_failed", error=str(e))
        raise _user_error("Could not add labor rate. Please check the form and try again.")


# ─── Crews ───────────────────────────────────────────────────────────────────

@router.get("/crews", response_model=list[CrewResponse])
async def list_crews(
    request: Request,
    user: User = Depends(require_permission("crews:manage")),
    db: AsyncSession = Depends(get_db),
):
    repo = CrewRepo(db, user.tenant_id)
    return await repo.get_active()


@router.post("/crews", response_model=CrewResponse, status_code=201)
async def create_crew(
    body: CreateCrewRequest,
    request: Request,
    user: User = Depends(require_permission("crews:manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        repo = CrewRepo(db, user.tenant_id)
        return await repo.create(**body.model_dump())
    except HTTPException:
        raise
    except Exception as e:
        log.exception("crews.create_failed", error=str(e))
        raise _user_error("Could not add crew. Please check the form and try again.")


# ─── Clients ─────────────────────────────────────────────────────────────────

@router.get("/clients")
async def list_clients(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    user: User = Depends(require_permission("clients:read")),
    db: AsyncSession = Depends(get_db),
):
    import math
    repo = ClientRepo(db, user.tenant_id)

    if search:
        items = await repo.search(search, page, page_size)
        return {"items": items, "page": page, "page_size": page_size}

    items, total = await repo.get_paginated(page=page, page_size=page_size)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": math.ceil(total / page_size),
    }


@router.post("/clients", status_code=201)
async def create_client(
    body: CreateClientRequest,
    request: Request,
    user: User = Depends(require_permission("clients:create")),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = body.model_dump()
        if not (data.get("first_name") or "").strip():
            raise _user_error("First name is required.")
        if not (data.get("last_name") or "").strip():
            raise _user_error("Last name is required.")
        repo = ClientRepo(db, user.tenant_id)
        return await repo.create(**data)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("clients.create_failed", error=str(e))
        raise _user_error("Could not add client. Please check the form and try again.")
