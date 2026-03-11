"""
Repository layer — ALL database access goes through here.
Never query DB directly in endpoints.
Every method is scoped to tenant_id — cross-tenant leaks are impossible.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, delete
from sqlalchemy.orm import selectinload
from typing import Optional, TypeVar, Generic, Type
from uuid import UUID
from datetime import datetime
import math

from models.models import (
    Tenant, User, Client, ServiceCatalog, MaterialCatalog,
    LaborRate, Crew, Quote, Job, AuditLog, BackgroundJob, AICache,
    QuoteStatus, TenantStatus
)

T = TypeVar("T")


# ─── Base Repository ─────────────────────────────────────────────────────────

class TenantRepository(Generic[T]):
    """
    Base class for all tenant-scoped repositories.
    tenant_id is baked in at construction — never passed per-method.
    """

    def __init__(self, db: AsyncSession, tenant_id: UUID, model: Type[T]):
        self.db = db
        self.tenant_id = tenant_id  # NEVER changes after construction
        self.model = model

    def _base_query(self):
        """All queries start here — always scoped to tenant."""
        return select(self.model).where(
            self.model.tenant_id == self.tenant_id
        )

    async def get_by_id(self, record_id: UUID) -> Optional[T]:
        result = await self.db.execute(
            self._base_query().where(self.model.id == record_id)
        )
        return result.scalar_one_or_none()

    async def get_paginated(
        self,
        page: int = 1,
        page_size: int = 20,
        order_by=None,
    ) -> tuple[list[T], int]:
        page_size = min(page_size, 100)  # enforce max page size
        offset = (page - 1) * page_size

        # Total count
        count_result = await self.db.execute(
            select(func.count()).select_from(self.model).where(
                self.model.tenant_id == self.tenant_id
            )
        )
        total = count_result.scalar()

        # Paginated items
        query = self._base_query().offset(offset).limit(page_size)
        if order_by is not None:
            query = query.order_by(order_by)

        result = await self.db.execute(query)
        items = result.scalars().all()

        return list(items), total

    async def create(self, **kwargs) -> T:
        obj = self.model(tenant_id=self.tenant_id, **kwargs)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def update(self, record_id: UUID, **kwargs) -> Optional[T]:
        obj = await self.get_by_id(record_id)
        if not obj:
            return None
        for key, value in kwargs.items():
            if value is not None and hasattr(obj, key):
                setattr(obj, key, value)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def soft_delete(self, record_id: UUID) -> bool:
        obj = await self.get_by_id(record_id)
        if not obj:
            return False
        obj.is_active = False
        await self.db.flush()
        return True

    async def hard_delete(self, record_id: UUID) -> bool:
        obj = await self.get_by_id(record_id)
        if not obj:
            return False
        await self.db.delete(obj)
        await self.db.flush()
        return True


# ─── Tenant Repository ────────────────────────────────────────────────────────

class TenantRepo:
    """Not scoped — used for tenant lookup and admin operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, tenant_id: UUID) -> Optional[Tenant]:
        result = await self.db.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Optional[Tenant]:
        result = await self.db.execute(
            select(Tenant).where(Tenant.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_active_by_slug(self, slug: str) -> Optional[Tenant]:
        """Return tenant by slug only if it is not cancelled/suspended (so slug can be reused after deletion)."""
        result = await self.db.execute(
            select(Tenant).where(
                Tenant.slug == slug,
                Tenant.status.not_in([TenantStatus.CANCELLED, TenantStatus.SUSPENDED]),
            )
        )
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> Tenant:
        tenant = Tenant(**kwargs)
        self.db.add(tenant)
        await self.db.flush()
        await self.db.refresh(tenant)
        return tenant

    async def update(self, tenant_id: UUID, **kwargs) -> Optional[Tenant]:
        tenant = await self.get_by_id(tenant_id)
        if not tenant:
            return None
        for key, value in kwargs.items():
            if value is not None and hasattr(tenant, key):
                setattr(tenant, key, value)
        await self.db.flush()
        await self.db.refresh(tenant)
        return tenant

    async def get_all(self, page: int = 1, page_size: int = 50) -> tuple[list[Tenant], int]:
        offset = (page - 1) * page_size
        count = await self.db.execute(select(func.count(Tenant.id)))
        total = count.scalar()
        result = await self.db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def delete_permanent(self, tenant_id: UUID) -> bool:
        """
        Permanently delete a tenant and all related data from the DB.
        Caller must delete Supabase auth users first if desired.
        Order: audit_logs, background_jobs, ai_cache (no FK CASCADE from tenant), then tenant (CASCADEs the rest).
        """
        # Remove rows that reference tenant but don't CASCADE from tenant
        await self.db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        await self.db.execute(delete(BackgroundJob).where(BackgroundJob.tenant_id == tenant_id))
        await self.db.execute(delete(AICache).where(AICache.tenant_id == tenant_id))
        tenant = await self.get_by_id(tenant_id)
        if not tenant:
            return False
        await self.db.delete(tenant)
        await self.db.flush()
        return True


# ─── User Repository ─────────────────────────────────────────────────────────

class UserRepo(TenantRepository[User]):

    def __init__(self, db: AsyncSession, tenant_id: UUID):
        super().__init__(db, tenant_id, User)

    async def get_by_supabase_id(self, supabase_user_id: str) -> Optional[User]:
        result = await self.db.execute(
            select(User).where(
                User.supabase_user_id == supabase_user_id,
                User.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(
            select(User).where(
                User.email == email.lower(),
                User.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def count_active(self) -> int:
        result = await self.db.execute(
            select(func.count(User.id)).where(
                User.tenant_id == self.tenant_id,
                User.is_active == True,
            )
        )
        return result.scalar()


# ─── Client Repository ───────────────────────────────────────────────────────

class ClientRepo(TenantRepository[Client]):

    def __init__(self, db: AsyncSession, tenant_id: UUID):
        super().__init__(db, tenant_id, Client)

    async def search(self, query: str, page: int = 1, page_size: int = 20):
        """Search clients by name, email, or phone."""
        from sqlalchemy import or_, cast, String
        search = f"%{query.lower()}%"
        stmt = (
            self._base_query()
            .where(
                Client.is_active == True,
                or_(
                    func.lower(Client.first_name).like(search),
                    func.lower(Client.last_name).like(search),
                    func.lower(Client.email).like(search),
                    Client.phone.like(search),
                )
            )
            .order_by(Client.last_name)
            .offset((page - 1) * page_size)
            .limit(min(page_size, 100))
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())


# ─── Catalog Repositories ────────────────────────────────────────────────────

class ServiceCatalogRepo(TenantRepository[ServiceCatalog]):
    def __init__(self, db: AsyncSession, tenant_id: UUID):
        super().__init__(db, tenant_id, ServiceCatalog)

    async def get_active(self) -> list[ServiceCatalog]:
        result = await self.db.execute(
            self._base_query()
            .where(ServiceCatalog.is_active == True)
            .order_by(ServiceCatalog.sort_order, ServiceCatalog.name)
        )
        return list(result.scalars().all())


class MaterialCatalogRepo(TenantRepository[MaterialCatalog]):
    def __init__(self, db: AsyncSession, tenant_id: UUID):
        super().__init__(db, tenant_id, MaterialCatalog)

    async def get_active(self) -> list[MaterialCatalog]:
        result = await self.db.execute(
            self._base_query()
            .where(MaterialCatalog.is_active == True)
            .order_by(MaterialCatalog.name)
        )
        return list(result.scalars().all())


class LaborRateRepo(TenantRepository[LaborRate]):
    def __init__(self, db: AsyncSession, tenant_id: UUID):
        super().__init__(db, tenant_id, LaborRate)

    async def get_all(self) -> list[LaborRate]:
        result = await self.db.execute(
            self._base_query().order_by(LaborRate.role)
        )
        return list(result.scalars().all())


# ─── Crew Repository ─────────────────────────────────────────────────────────

class CrewRepo(TenantRepository[Crew]):
    def __init__(self, db: AsyncSession, tenant_id: UUID):
        super().__init__(db, tenant_id, Crew)

    async def get_active(self) -> list[Crew]:
        result = await self.db.execute(
            self._base_query()
            .where(Crew.is_active == True)
            .order_by(Crew.name)
        )
        return list(result.scalars().all())


# ─── Quote Repository ────────────────────────────────────────────────────────

class QuoteRepo(TenantRepository[Quote]):
    def __init__(self, db: AsyncSession, tenant_id: UUID):
        super().__init__(db, tenant_id, Quote)

    async def get_with_client(self, quote_id: UUID) -> Optional[Quote]:
        result = await self.db.execute(
            self._base_query()
            .options(selectinload(Quote.client))
            .where(Quote.id == quote_id)
        )
        return result.scalar_one_or_none()

    async def get_next_number(self) -> str:
        """Generate next sequential quote number: Q-2024-0001"""
        result = await self.db.execute(
            select(func.count(Quote.id)).where(Quote.tenant_id == self.tenant_id)
        )
        count = result.scalar() + 1
        year = datetime.now().year
        return f"Q-{year}-{count:04d}"

    async def get_by_status(
        self, status: QuoteStatus, page: int = 1, page_size: int = 20
    ) -> tuple[list[Quote], int]:
        offset = (page - 1) * page_size
        count_result = await self.db.execute(
            select(func.count(Quote.id)).where(
                Quote.tenant_id == self.tenant_id,
                Quote.status == status,
            )
        )
        total = count_result.scalar()
        result = await self.db.execute(
            self._base_query()
            .where(Quote.status == status)
            .options(selectinload(Quote.client))
            .order_by(Quote.created_at.desc())
            .offset(offset)
            .limit(min(page_size, 100))
        )
        return list(result.scalars().all()), total

    async def list_quotes(
        self,
        client_id: Optional[UUID] = None,
        status: Optional[QuoteStatus] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Quote], int]:
        """List quotes with optional client_id and status filters. Returns (items, total)."""
        page_size = min(max(1, page_size), 100)
        offset = (page - 1) * page_size
        base = select(Quote).where(Quote.tenant_id == self.tenant_id)
        count_base = select(func.count(Quote.id)).where(Quote.tenant_id == self.tenant_id)
        if client_id is not None:
            base = base.where(Quote.client_id == client_id)
            count_base = count_base.where(Quote.client_id == client_id)
        if status is not None:
            base = base.where(Quote.status == status)
            count_base = count_base.where(Quote.status == status)
        total_result = await self.db.execute(count_base)
        total = total_result.scalar()
        result = await self.db.execute(
            base.order_by(Quote.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total


# ─── Audit Log Repository ────────────────────────────────────────────────────

class AuditLogRepo:
    """Global — not tenant scoped (used by admin)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_for_tenant(
        self,
        tenant_id: UUID,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[AuditLog], int]:
        offset = (page - 1) * page_size
        count_result = await self.db.execute(
            select(func.count(AuditLog.id)).where(AuditLog.tenant_id == tenant_id)
        )
        total = count_result.scalar()
        result = await self.db.execute(
            select(AuditLog)
            .where(AuditLog.tenant_id == tenant_id)
            .order_by(AuditLog.created_at.desc())
            .offset(offset)
            .limit(min(page_size, 100))
        )
        return list(result.scalars().all()), total


# ─── Background Job Repository ───────────────────────────────────────────────

class BackgroundJobRepo:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def enqueue(self, job_type: str, payload: dict, tenant_id: Optional[UUID] = None) -> BackgroundJob:
        job = BackgroundJob(
            tenant_id=tenant_id,
            job_type=job_type,
            payload=payload,
            status="pending",
        )
        self.db.add(job)
        await self.db.flush()
        return job

    async def get_pending(self, limit: int = 10) -> list[BackgroundJob]:
        result = await self.db.execute(
            select(BackgroundJob)
            .where(
                BackgroundJob.status == "pending",
                BackgroundJob.run_at <= func.now(),
                BackgroundJob.retry_count < BackgroundJob.max_retries,
            )
            .order_by(BackgroundJob.run_at)
            .limit(limit)
        )
        return list(result.scalars().all())
