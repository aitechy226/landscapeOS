"""
Superadmin API — only accessible by platform operators (you).
Gated by SUPERADMIN_KEY header, completely separate from tenant auth.
"""
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import Optional
from uuid import UUID
import structlog

from db.database import get_db
from models.models import Tenant, User, Quote, AuditLog, TenantStatus
from repositories.repositories import TenantRepo, AuditLogRepo
from services.supabase_service import SupabaseService
from config import settings

log = structlog.get_logger()
router = APIRouter(prefix="/admin", tags=["Superadmin"])


async def verify_superadmin(x_admin_key: str = Header(...)):
    """Verify the superadmin API key — set SUPERADMIN_KEY in env."""
    if not settings.SUPERADMIN_KEY or x_admin_key != settings.SUPERADMIN_KEY:
        log.warning("admin.unauthorized_access_attempt")
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/tenants")
async def list_all_tenants(
    page: int = 1,
    page_size: int = 50,
    status: Optional[str] = None,
    _: None = Depends(verify_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """List all tenants with usage stats."""
    repo = TenantRepo(db)
    tenants, total = await repo.get_all(page=page, page_size=page_size)

    result = []
    for tenant in tenants:
        # Get user count
        user_count = await db.execute(
            select(func.count(User.id)).where(User.tenant_id == tenant.id)
        )
        # Get quote count
        quote_count = await db.execute(
            select(func.count(Quote.id)).where(Quote.tenant_id == tenant.id)
        )
        result.append({
            "id": str(tenant.id),
            "name": tenant.name,
            "slug": tenant.slug,
            "status": tenant.status,
            "tier": tenant.tier,
            "user_count": user_count.scalar(),
            "quote_count": quote_count.scalar(),
            "created_at": tenant.created_at.isoformat(),
            "trial_ends_at": tenant.trial_ends_at.isoformat() if tenant.trial_ends_at else None,
            "stripe_subscription_id": tenant.stripe_subscription_id,
        })

    return {"items": result, "total": total, "page": page}


@router.get("/tenants/{tenant_id}")
async def get_tenant_detail(
    tenant_id: str,
    _: None = Depends(verify_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Full tenant details including usage."""
    from uuid import UUID
    repo = TenantRepo(db)
    tenant = await repo.get_by_id(UUID(tenant_id))
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    users = await db.execute(
        select(User).where(User.tenant_id == tenant.id)
    )
    users_list = users.scalars().all()

    return {
        "tenant": {
            "id": str(tenant.id),
            "name": tenant.name,
            "slug": tenant.slug,
            "status": tenant.status,
            "tier": tenant.tier,
            "billing_email": tenant.billing_email,
            "stripe_customer_id": tenant.stripe_customer_id,
            "created_at": tenant.created_at.isoformat(),
        },
        "users": [
            {"email": u.email, "role": u.role, "is_active": u.is_active}
            for u in users_list
        ],
    }


@router.patch("/tenants/{tenant_id}/status")
async def update_tenant_status(
    tenant_id: str,
    body: dict,
    _: None = Depends(verify_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Suspend, reactivate, or cancel a tenant."""
    from uuid import UUID
    new_status = body.get("status")
    if new_status not in [s.value for s in TenantStatus]:
        raise HTTPException(400, f"Invalid status: {new_status}")

    repo = TenantRepo(db)
    tenant = await repo.update(UUID(tenant_id), status=new_status)
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    log.info("admin.tenant_status_changed",
             tenant_id=tenant_id, new_status=new_status)
    return {"message": f"Tenant status updated to {new_status}"}


@router.delete("/tenants/{tenant_id}/permanent")
async def delete_tenant_permanent(
    tenant_id: str,
    _: None = Depends(verify_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a tenant and all related data from the DB and Supabase Auth. Cannot be undone."""
    repo = TenantRepo(db)
    tenant = await db.execute(
        select(Tenant).where(Tenant.id == UUID(tenant_id)).options(selectinload(Tenant.users))
    )
    tenant = tenant.scalar_one_or_none()
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    supabase_svc = SupabaseService()
    for user in tenant.users:
        try:
            await supabase_svc.delete_user(user.supabase_user_id)
        except Exception as e:
            log.warning("admin.permanent_delete_supabase_user_failed", user_id=str(user.id), error=str(e))

    deleted = await repo.delete_permanent(UUID(tenant_id))
    if not deleted:
        raise HTTPException(404, "Tenant not found")
    log.info("admin.tenant_permanently_deleted", tenant_id=tenant_id, slug=tenant.slug)
    return {"message": "Tenant and all related data permanently deleted."}


@router.delete("/tenants/{tenant_id}")
async def delete_tenant(
    tenant_id: str,
    _: None = Depends(verify_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Cancel (soft-delete) a tenant. Sets status to cancelled; slug can be reused for new signups."""
    repo = TenantRepo(db)
    tenant = await repo.get_by_id(UUID(tenant_id))
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    await repo.update(UUID(tenant_id), status=TenantStatus.CANCELLED)
    log.info("admin.tenant_cancelled", tenant_id=tenant_id, slug=tenant.slug)
    return {"message": "Tenant cancelled. Company URL can be used again for new signups."}


@router.get("/stats")
async def platform_stats(
    _: None = Depends(verify_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Platform-wide metrics dashboard."""
    tenant_count = await db.execute(select(func.count(Tenant.id)))
    active_count = await db.execute(
        select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.ACTIVE)
    )
    trial_count = await db.execute(
        select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.TRIAL)
    )
    quote_count = await db.execute(select(func.count(Quote.id)))
    user_count = await db.execute(select(func.count(User.id)))

    return {
        "tenants": {
            "total": tenant_count.scalar(),
            "active": active_count.scalar(),
            "trial": trial_count.scalar(),
        },
        "quotes_total": quote_count.scalar(),
        "users_total": user_count.scalar(),
    }


@router.get("/audit-logs")
async def get_audit_logs(
    tenant_id: Optional[str] = None,
    page: int = 1,
    _: None = Depends(verify_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """View audit logs — optionally filtered by tenant."""
    from uuid import UUID
    repo = AuditLogRepo(db)
    if tenant_id:
        logs, total = await repo.get_for_tenant(UUID(tenant_id), page=page)
    else:
        result = await db.execute(
            select(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .offset((page - 1) * 50)
            .limit(50)
        )
        logs = result.scalars().all()
        total = 0

    return {
        "items": [
            {
                "action": l.action,
                "tenant_id": str(l.tenant_id) if l.tenant_id else None,
                "user_id": str(l.user_id) if l.user_id else None,
                "resource_type": l.resource_type,
                "ip_address": l.ip_address,
                "created_at": l.created_at.isoformat(),
            }
            for l in logs
        ],
        "total": total,
        "page": page,
    }
