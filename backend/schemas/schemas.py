"""
Pydantic schemas — request/response validation.
Sanitize all inputs. Never expose internal fields.
"""
from pydantic import BaseModel, EmailStr, validator, Field, ConfigDict
from typing import Optional
from uuid import UUID
from datetime import datetime
from decimal import Decimal
import bleach
import re

from models.models import (
    TenantTier, TenantStatus, UserRole, QuoteStatus,
    JobStatus
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def sanitize_text(v: str) -> str:
    """Strip HTML/JS from any text input."""
    if v:
        return bleach.clean(v, tags=[], strip=True).strip()
    return v


def validate_hex_color(v: str) -> str:
    if v and not re.match(r'^#[0-9A-Fa-f]{6}$', v):
        raise ValueError("Invalid hex color")
    return v


# ─── Base ─────────────────────────────────────────────────────────────────────

class BaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
    pages: int


# ─── Auth ─────────────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=100)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    company_name: str = Field(min_length=1, max_length=255)
    company_slug: str = Field(min_length=2, max_length=100, pattern=r'^[a-z0-9-]+$')
    timezone: str = "America/New_York"

    @validator("first_name", "last_name", "company_name")
    def sanitize(cls, v):
        return sanitize_text(v)

    @validator("password")
    def validate_password(cls, v):
        if not re.search(r'[A-Z]', v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r'[0-9]', v):
            raise ValueError("Password must contain at least one number")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class ResetPasswordRequest(BaseModel):
    """Confirm password reset with token from email link."""
    email: EmailStr
    token: str
    new_password: str = Field(min_length=8, max_length=100)

    @validator("new_password")
    def validate_password(cls, v):
        if not re.search(r'[A-Z]', v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r'[0-9]', v):
            raise ValueError("Password must contain at least one number")
        return v


# ─── Tenant ───────────────────────────────────────────────────────────────────

class TenantResponse(BaseResponse):
    id: UUID
    name: str
    slug: str
    status: TenantStatus
    tier: TenantTier
    logo_url: Optional[str]
    primary_color: str
    company_phone: Optional[str]
    company_email: Optional[str]
    company_address: Optional[str]
    timezone: str
    currency: str
    tax_rate: Decimal
    minimum_quote: Decimal
    trial_ends_at: Optional[datetime]
    created_at: datetime


class UpdateTenantRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    company_phone: Optional[str] = Field(None, max_length=20)
    company_email: Optional[EmailStr] = None
    company_address: Optional[str] = Field(None, max_length=500)
    company_website: Optional[str] = Field(None, max_length=255)
    primary_color: Optional[str] = None
    timezone: Optional[str] = None
    currency: Optional[str] = Field(None, max_length=3)
    tax_rate: Optional[Decimal] = Field(None, ge=0, le=1)
    minimum_quote: Optional[Decimal] = Field(None, ge=0, le=500)
    travel_surcharge_miles: Optional[int] = Field(None, ge=0)
    travel_surcharge_amount: Optional[Decimal] = Field(None, ge=0)

    @validator("primary_color")
    def validate_color(cls, v):
        return validate_hex_color(v) if v else v


# ─── User ─────────────────────────────────────────────────────────────────────

class UserResponse(BaseResponse):
    id: UUID
    email: str
    first_name: Optional[str]
    last_name: Optional[str]
    phone: Optional[str]
    role: UserRole
    is_active: bool
    mfa_enabled: bool
    last_login_at: Optional[datetime]
    avatar_url: Optional[str]
    created_at: datetime


class InviteUserRequest(BaseModel):
    email: EmailStr
    role: UserRole
    first_name: str = Field(max_length=100)
    last_name: str = Field(max_length=100)

    @validator("role")
    def cannot_invite_owner(cls, v):
        if v == UserRole.OWNER:
            raise ValueError("Cannot invite a user as owner")
        return v


class UpdateUserRequest(BaseModel):
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


# ─── Client ───────────────────────────────────────────────────────────────────

class CreateClientRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    company_name: Optional[str] = Field(None, max_length=255)
    property_type: str = Field(default="residential", pattern=r'^(residential|commercial)$')
    address: Optional[str] = Field(None, max_length=500)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=50)
    zip_code: Optional[str] = Field(None, max_length=10)
    property_sqft: Optional[int] = Field(None, gt=0, lt=1000000)
    notes: Optional[str] = Field(None, max_length=5000)
    tags: list[str] = []

    @validator("first_name", "last_name", "company_name", "notes", pre=True)
    def sanitize(cls, v):
        return sanitize_text(v) if v else v


class ClientResponse(BaseResponse):
    id: UUID
    first_name: str
    last_name: str
    email: Optional[str]
    phone: Optional[str]
    company_name: Optional[str]
    property_type: str
    address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip_code: Optional[str]
    property_sqft: Optional[int]
    notes: Optional[str]
    tags: list
    is_active: bool
    created_at: datetime


# ─── Service Catalog ─────────────────────────────────────────────────────────

class CreateServiceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    category: Optional[str] = Field(None, max_length=100)
    base_price: Decimal = Field(gt=0, le=999999)
    unit: str = Field(default="flat", max_length=50)
    estimated_hours: Optional[Decimal] = Field(None, gt=0, le=999)
    is_active: bool = True

    @validator("name", "description", pre=True)
    def sanitize(cls, v):
        return sanitize_text(v) if v else v


class ServiceResponse(BaseResponse):
    id: UUID
    name: str
    description: Optional[str]
    category: Optional[str]
    base_price: Decimal
    unit: str
    estimated_hours: Optional[Decimal]
    is_active: bool
    sort_order: int


# ─── Material Catalog ────────────────────────────────────────────────────────

class CreateMaterialRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    unit: str = Field(min_length=1, max_length=50)
    cost_price: Decimal = Field(gt=0, le=999999)
    sell_price: Decimal = Field(gt=0, le=999999)
    supplier: Optional[str] = Field(None, max_length=255)
    sku: Optional[str] = Field(None, max_length=100)

    @validator("name", pre=True)
    def sanitize(cls, v):
        return sanitize_text(v) if v else v


class MaterialResponse(BaseResponse):
    id: UUID
    name: str
    description: Optional[str]
    unit: str
    cost_price: Decimal
    sell_price: Decimal
    supplier: Optional[str]
    sku: Optional[str]
    is_active: bool


# ─── Labor Rates ─────────────────────────────────────────────────────────────

class CreateLaborRateRequest(BaseModel):
    role: str = Field(min_length=1, max_length=100)
    property_type: str = Field(pattern=r'^(residential|commercial|any)$')
    rate_per_hour: Decimal = Field(gt=0, le=9999)
    overtime_multiplier: Decimal = Field(default=Decimal("1.5"), gt=1, le=3)


class LaborRateResponse(BaseResponse):
    id: UUID
    role: str
    property_type: str
    rate_per_hour: Decimal
    overtime_multiplier: Decimal


# ─── Crew ────────────────────────────────────────────────────────────────────

class CreateCrewRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    color: str = "#16a34a"

    @validator("color")
    def validate_color(cls, v):
        return validate_hex_color(v)


class CrewResponse(BaseResponse):
    id: UUID
    name: str
    is_active: bool
    color: str


# ─── Onboarding ───────────────────────────────────────────────────────────────

class OnboardingStep1(BaseModel):
    """Company info"""
    company_phone: Optional[str] = Field(None, max_length=20)
    company_address: Optional[str] = Field(None, max_length=500)
    timezone: str = "America/New_York"
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    minimum_quote: Decimal = Field(default=Decimal("150"), ge=0, le=500)


class OnboardingStep2(BaseModel):
    """Services — use template or custom"""
    template: Optional[str] = None  # lawn_care | hardscape | full_service
    services: list[CreateServiceRequest] = []


class OnboardingStep3(BaseModel):
    """Materials"""
    materials: list[CreateMaterialRequest] = []


class OnboardingStep4(BaseModel):
    """Labor rates"""
    labor_rates: list[CreateLaborRateRequest] = []


class OnboardingStep5(BaseModel):
    """First crew"""
    crew_name: str = Field(default="Team A", max_length=255)


class OnboardingStatusResponse(BaseModel):
    step: int
    completed_steps: list[int]
    is_complete: bool


# ─── Health ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    database: bool
    version: str = "1.0.0"
