"""
SQLAlchemy models — all production tables.
EVERY model has tenant_id. NEVER query without filtering by tenant_id.
"""
from sqlalchemy import (
    Column, String, Boolean, Integer, Numeric, DateTime, Text,
    ForeignKey, Enum as SAEnum, JSON, Index, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
import enum

from db.database import Base


# ─── Enums ───────────────────────────────────────────────────────────────────

class TenantTier(str, enum.Enum):
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class TenantStatus(str, enum.Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    SUSPENDED = "suspended"


class UserRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    CREW_LEAD = "crew_lead"
    LABORER = "laborer"


class JobStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class QuoteStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    VIEWED = "viewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class AuditAction(str, enum.Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGOUT = "logout"
    EXPORT = "export"


# ─── Mixins ──────────────────────────────────────────────────────────────────

class TimestampMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class TenantMixin:
    """Every tenant-scoped model inherits this."""
    tenant_id = Column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)


# ─── Tenant (the landscaping company) ────────────────────────────────────────

class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)  # greenthumb → greenthumb.landscapeos.com
    status = Column(SAEnum(TenantStatus), nullable=False, default=TenantStatus.TRIAL)
    tier = Column(SAEnum(TenantTier), nullable=False, default=TenantTier.STARTER)

    # Branding (pro+ only)
    logo_url = Column(String(500))
    primary_color = Column(String(7), default="#16a34a")  # hex color
    company_phone = Column(String(20))
    company_email = Column(String(255))
    company_address = Column(String(500))
    company_website = Column(String(255))

    # Billing
    stripe_customer_id = Column(String(100), unique=True)
    stripe_subscription_id = Column(String(100), unique=True)
    trial_ends_at = Column(DateTime(timezone=True))
    billing_email = Column(String(255))

    # Settings
    timezone = Column(String(50), default="America/New_York")
    currency = Column(String(3), default="USD")
    tax_rate = Column(Numeric(5, 4), default=0.0)  # e.g. 0.0875 = 8.75%
    minimum_quote = Column(Numeric(10, 2), default=150.00)
    travel_surcharge_miles = Column(Integer, default=20)
    travel_surcharge_amount = Column(Numeric(10, 2), default=45.00)

    # Relationships
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    clients = relationship("Client", back_populates="tenant", cascade="all, delete-orphan")
    service_catalog = relationship("ServiceCatalog", back_populates="tenant", cascade="all, delete-orphan")
    material_catalog = relationship("MaterialCatalog", back_populates="tenant", cascade="all, delete-orphan")
    labor_rates = relationship("LaborRate", back_populates="tenant", cascade="all, delete-orphan")
    crews = relationship("Crew", back_populates="tenant", cascade="all, delete-orphan")
    quotes = relationship("Quote", back_populates="tenant", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="tenant", cascade="all, delete-orphan")


# ─── User ─────────────────────────────────────────────────────────────────────

class User(Base, TenantMixin, TimestampMixin):
    __tablename__ = "users"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supabase_user_id = Column(String(255), unique=True, nullable=False, index=True)  # Supabase auth UID
    email = Column(String(255), nullable=False)
    first_name = Column(String(100))
    last_name = Column(String(100))
    phone = Column(String(20))
    role = Column(SAEnum(UserRole), nullable=False, default=UserRole.LABORER)
    is_active = Column(Boolean, default=True, nullable=False)
    mfa_enabled = Column(Boolean, default=False, nullable=False)
    last_login_at = Column(DateTime(timezone=True))
    avatar_url = Column(String(500))

    tenant = relationship("Tenant", back_populates="users")
    audit_logs = relationship("AuditLog", back_populates="user")

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index("ix_users_tenant_id_role", "tenant_id", "role"),
    )


# ─── Client (the landscaping company's customers) ────────────────────────────

class Client(Base, TenantMixin, TimestampMixin):
    __tablename__ = "clients"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255))
    phone = Column(String(20))
    company_name = Column(String(255))  # for commercial clients
    property_type = Column(String(20), default="residential")  # residential | commercial
    address = Column(String(500))
    city = Column(String(100))
    state = Column(String(50))
    zip_code = Column(String(10))
    latitude = Column(Numeric(10, 8))
    longitude = Column(Numeric(11, 8))
    property_sqft = Column(Integer)
    notes = Column(Text)
    is_active = Column(Boolean, default=True, nullable=False)
    tags = Column(JSON, default=list)  # ["vip", "commercial", "seasonal"]

    tenant = relationship("Tenant", back_populates="clients")
    quotes = relationship("Quote", back_populates="client")
    jobs = relationship("Job", back_populates="client")

    __table_args__ = (
        Index("ix_clients_tenant_id_active", "tenant_id", "is_active"),
    )


# ─── Service Catalog ─────────────────────────────────────────────────────────

class ServiceCatalog(Base, TenantMixin, TimestampMixin):
    __tablename__ = "service_catalog"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    category = Column(String(100))  # maintenance | installation | cleanup | hardscape
    base_price = Column(Numeric(10, 2), nullable=False)
    unit = Column(String(50), default="flat")  # flat | per_sqft | hourly | per_unit
    estimated_hours = Column(Numeric(5, 2))
    is_active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0)

    tenant = relationship("Tenant", back_populates="service_catalog")

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_service_catalog_tenant_name"),
    )


# ─── Material Catalog ────────────────────────────────────────────────────────

class MaterialCatalog(Base, TenantMixin, TimestampMixin):
    __tablename__ = "material_catalog"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    unit = Column(String(50), nullable=False)  # cubic_yard | bag | ton | each | sqft
    cost_price = Column(Numeric(10, 2), nullable=False)   # what tenant pays supplier
    sell_price = Column(Numeric(10, 2), nullable=False)   # what tenant charges client
    supplier = Column(String(255))
    sku = Column(String(100))
    is_active = Column(Boolean, default=True, nullable=False)

    tenant = relationship("Tenant", back_populates="material_catalog")


# ─── Labor Rates ─────────────────────────────────────────────────────────────

class LaborRate(Base, TenantMixin, TimestampMixin):
    __tablename__ = "labor_rates"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role = Column(String(100), nullable=False)        # crew_lead | laborer | operator
    property_type = Column(String(20), nullable=False) # residential | commercial | any
    rate_per_hour = Column(Numeric(10, 2), nullable=False)
    overtime_multiplier = Column(Numeric(4, 2), default=1.5)

    tenant = relationship("Tenant", back_populates="labor_rates")

    __table_args__ = (
        UniqueConstraint("tenant_id", "role", "property_type", name="uq_labor_rates_tenant_role_type"),
    )


# ─── Crew ────────────────────────────────────────────────────────────────────

class Crew(Base, TenantMixin, TimestampMixin):
    __tablename__ = "crews"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)   # "Team A", "North Squad"
    is_active = Column(Boolean, default=True, nullable=False)
    color = Column(String(7), default="#16a34a")  # for calendar display

    tenant = relationship("Tenant", back_populates="crews")
    jobs = relationship("Job", back_populates="crew")


# ─── Quote ────────────────────────────────────────────────────────────────────

class Quote(Base, TenantMixin, TimestampMixin):
    __tablename__ = "quotes"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quote_number = Column(String(50), nullable=False)  # Q-2024-0001
    client_id = Column(PGUUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    status = Column(SAEnum(QuoteStatus), nullable=False, default=QuoteStatus.DRAFT)

    # Job details
    job_type = Column(String(255))
    description = Column(Text)
    property_sqft = Column(Integer)
    photos = Column(JSON, default=list)  # list of storage URLs

    # AI-generated content
    ai_line_items = Column(JSON, default=list)   # [{service, qty, unit_price, total}]
    ai_notes = Column(Text)
    ai_input_hash = Column(String(64))  # for caching — md5 of inputs
    ai_tokens_used = Column(Integer)
    ai_cost_usd = Column(Numeric(10, 6))

    # Pricing
    subtotal = Column(Numeric(10, 2), default=0)
    tax_amount = Column(Numeric(10, 2), default=0)
    discount_amount = Column(Numeric(10, 2), default=0)
    total = Column(Numeric(10, 2), default=0)

    # Meta
    valid_until = Column(DateTime(timezone=True))
    sent_at = Column(DateTime(timezone=True))
    viewed_at = Column(DateTime(timezone=True))
    accepted_at = Column(DateTime(timezone=True))
    pdf_url = Column(String(500))
    internal_notes = Column(Text)
    created_by_id = Column(PGUUID(as_uuid=True), ForeignKey("users.id"))

    tenant = relationship("Tenant", back_populates="quotes")
    client = relationship("Client", back_populates="quotes")
    created_by = relationship("User")
    job = relationship("Job", back_populates="quote", uselist=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "quote_number", name="uq_quotes_tenant_number"),
        Index("ix_quotes_tenant_status", "tenant_id", "status"),
        Index("ix_quotes_client", "tenant_id", "client_id"),
    )


# ─── Job ─────────────────────────────────────────────────────────────────────

class Job(Base, TenantMixin, TimestampMixin):
    __tablename__ = "jobs"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quote_id = Column(PGUUID(as_uuid=True), ForeignKey("quotes.id"))
    client_id = Column(PGUUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    crew_id = Column(PGUUID(as_uuid=True), ForeignKey("crews.id"))
    status = Column(SAEnum(JobStatus), nullable=False, default=JobStatus.DRAFT)

    title = Column(String(255), nullable=False)
    description = Column(Text)
    scheduled_date = Column(DateTime(timezone=True))
    estimated_hours = Column(Numeric(5, 2))
    actual_hours = Column(Numeric(5, 2))
    completion_notes = Column(Text)
    completion_photos = Column(JSON, default=list)

    tenant = relationship("Tenant", back_populates="jobs")
    client = relationship("Client", back_populates="jobs")
    crew = relationship("Crew", back_populates="jobs")
    quote = relationship("Quote", back_populates="job")

    __table_args__ = (
        Index("ix_jobs_tenant_scheduled", "tenant_id", "scheduled_date"),
        Index("ix_jobs_crew_date", "crew_id", "scheduled_date"),
    )


# ─── Background Jobs Queue ────────────────────────────────────────────────────

class BackgroundJob(Base, TimestampMixin):
    """Reliable job queue — never fire-and-forget for side effects."""
    __tablename__ = "background_jobs"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), index=True)  # nullable for system jobs
    job_type = Column(String(100), nullable=False)  # send_sms | send_email | generate_pdf
    payload = Column(JSON, nullable=False)
    status = Column(String(20), default="pending")  # pending | running | done | failed
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    run_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    error = Column(Text)

    __table_args__ = (
        Index("ix_bg_jobs_status_run_at", "status", "run_at"),
    )


# ─── Audit Log ────────────────────────────────────────────────────────────────

class AuditLog(Base):
    """Immutable audit trail — no updates, no deletes ever."""
    __tablename__ = "audit_logs"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), index=True)
    user_id = Column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    action = Column(String(100), nullable=False)        # "quote.created"
    resource_type = Column(String(100))                  # "quote"
    resource_id = Column(PGUUID(as_uuid=True))
    ip_address = Column(String(45))                      # supports IPv6
    user_agent = Column(String(500))
    changes = Column(JSON)                               # before/after for updates
    meta = Column(JSON, default=dict)               # extra context
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
    )


# ─── AI Response Cache ────────────────────────────────────────────────────────

class AICache(Base):
    """Cache Anthropic responses to reduce costs."""
    __tablename__ = "ai_cache"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PGUUID(as_uuid=True), nullable=False, index=True)
    input_hash = Column(String(64), nullable=False)  # md5 of normalized inputs
    model = Column(String(100), nullable=False)
    response = Column(JSON, nullable=False)
    tokens_used = Column(Integer)
    cost_usd = Column(Numeric(10, 6))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "input_hash", name="uq_ai_cache_tenant_hash"),
    )
