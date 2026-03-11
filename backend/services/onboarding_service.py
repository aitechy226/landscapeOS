"""
Onboarding service — industry templates and setup wizard logic.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from uuid import UUID
from decimal import Decimal
import structlog

from models.models import ServiceCatalog, MaterialCatalog, LaborRate, Crew
from repositories.repositories import (
    ServiceCatalogRepo, MaterialCatalogRepo, LaborRateRepo
)

log = structlog.get_logger()

# ─── Industry Templates ───────────────────────────────────────────────────────

INDUSTRY_TEMPLATES = {
    "lawn_care": {
        "services": [
            {"name": "Lawn Mowing", "category": "maintenance", "base_price": 50.00, "unit": "flat", "estimated_hours": 1.0},
            {"name": "Lawn Mowing (Large)", "category": "maintenance", "base_price": 85.00, "unit": "flat", "estimated_hours": 1.5},
            {"name": "Edging", "category": "maintenance", "base_price": 25.00, "unit": "flat", "estimated_hours": 0.5},
            {"name": "Spring Cleanup", "category": "cleanup", "base_price": 200.00, "unit": "flat", "estimated_hours": 4.0},
            {"name": "Fall Cleanup / Leaf Removal", "category": "cleanup", "base_price": 175.00, "unit": "flat", "estimated_hours": 3.0},
            {"name": "Overseeding", "category": "maintenance", "base_price": 150.00, "unit": "per_sqft", "estimated_hours": 2.0},
            {"name": "Fertilization", "category": "maintenance", "base_price": 75.00, "unit": "flat", "estimated_hours": 1.0},
            {"name": "Weed Control (Spray)", "category": "maintenance", "base_price": 65.00, "unit": "flat", "estimated_hours": 1.0},
            {"name": "Aeration", "category": "maintenance", "base_price": 120.00, "unit": "flat", "estimated_hours": 1.5},
        ],
        "materials": [
            {"name": "Grass Seed (Sun Mix)", "unit": "lb", "cost_price": 2.50, "sell_price": 5.00},
            {"name": "Grass Seed (Shade Mix)", "unit": "lb", "cost_price": 3.00, "sell_price": 6.00},
            {"name": "Fertilizer (Granular)", "unit": "bag", "cost_price": 18.00, "sell_price": 35.00},
            {"name": "Pre-Emergent Herbicide", "unit": "bag", "cost_price": 22.00, "sell_price": 45.00},
        ],
        "labor_rates": [
            {"role": "crew_lead", "property_type": "residential", "rate_per_hour": 65.00},
            {"role": "crew_lead", "property_type": "commercial", "rate_per_hour": 85.00},
            {"role": "laborer", "property_type": "residential", "rate_per_hour": 45.00},
            {"role": "laborer", "property_type": "commercial", "rate_per_hour": 55.00},
        ],
    },
    "hardscape": {
        "services": [
            {"name": "Patio Installation", "category": "hardscape", "base_price": 1500.00, "unit": "per_sqft", "estimated_hours": 16.0},
            {"name": "Walkway Installation", "category": "hardscape", "base_price": 800.00, "unit": "flat", "estimated_hours": 8.0},
            {"name": "Retaining Wall", "category": "hardscape", "base_price": 2500.00, "unit": "flat", "estimated_hours": 24.0},
            {"name": "Fire Pit Installation", "category": "hardscape", "base_price": 1200.00, "unit": "flat", "estimated_hours": 8.0},
            {"name": "Driveway Edging", "category": "hardscape", "base_price": 400.00, "unit": "flat", "estimated_hours": 4.0},
            {"name": "Mulch Bed Installation", "category": "installation", "base_price": 200.00, "unit": "flat", "estimated_hours": 3.0},
        ],
        "materials": [
            {"name": "Paver (Natural Stone)", "unit": "sqft", "cost_price": 4.50, "sell_price": 9.00},
            {"name": "Paver (Concrete)", "unit": "sqft", "cost_price": 2.50, "sell_price": 5.50},
            {"name": "Gravel Base", "unit": "ton", "cost_price": 28.00, "sell_price": 55.00},
            {"name": "Sand (Paver Base)", "unit": "bag", "cost_price": 4.50, "sell_price": 9.00},
            {"name": "Landscape Block", "unit": "each", "cost_price": 3.00, "sell_price": 6.50},
            {"name": "Mulch (Hardwood)", "unit": "cubic_yard", "cost_price": 28.00, "sell_price": 65.00},
        ],
        "labor_rates": [
            {"role": "crew_lead", "property_type": "residential", "rate_per_hour": 75.00},
            {"role": "crew_lead", "property_type": "commercial", "rate_per_hour": 95.00},
            {"role": "laborer", "property_type": "residential", "rate_per_hour": 55.00},
            {"role": "laborer", "property_type": "commercial", "rate_per_hour": 65.00},
            {"role": "operator", "property_type": "any", "rate_per_hour": 85.00},
        ],
    },
    "full_service": {
        "services": [
            # Lawn maintenance
            {"name": "Lawn Mowing", "category": "maintenance", "base_price": 50.00, "unit": "flat", "estimated_hours": 1.0},
            {"name": "Spring Cleanup", "category": "cleanup", "base_price": 200.00, "unit": "flat", "estimated_hours": 4.0},
            {"name": "Fall Cleanup", "category": "cleanup", "base_price": 175.00, "unit": "flat", "estimated_hours": 3.0},
            {"name": "Fertilization Program", "category": "maintenance", "base_price": 75.00, "unit": "flat", "estimated_hours": 1.0},
            # Planting
            {"name": "Shrub Trimming", "category": "maintenance", "base_price": 120.00, "unit": "flat", "estimated_hours": 2.0},
            {"name": "Tree Planting", "category": "installation", "base_price": 350.00, "unit": "per_unit", "estimated_hours": 3.0},
            {"name": "Shrub Planting", "category": "installation", "base_price": 125.00, "unit": "per_unit", "estimated_hours": 1.5},
            {"name": "Annual Flower Bed", "category": "installation", "base_price": 250.00, "unit": "flat", "estimated_hours": 3.0},
            # Hardscape
            {"name": "Patio Installation", "category": "hardscape", "base_price": 1500.00, "unit": "flat", "estimated_hours": 16.0},
            {"name": "Mulch Installation", "category": "installation", "base_price": 150.00, "unit": "flat", "estimated_hours": 2.0},
            # Irrigation
            {"name": "Irrigation System Install", "category": "installation", "base_price": 3500.00, "unit": "flat", "estimated_hours": 24.0},
            {"name": "Irrigation Startup", "category": "maintenance", "base_price": 85.00, "unit": "flat", "estimated_hours": 1.0},
            {"name": "Irrigation Winterization", "category": "maintenance", "base_price": 85.00, "unit": "flat", "estimated_hours": 1.0},
        ],
        "materials": [
            {"name": "Mulch (Hardwood)", "unit": "cubic_yard", "cost_price": 28.00, "sell_price": 65.00},
            {"name": "Mulch (Black Dyed)", "unit": "cubic_yard", "cost_price": 32.00, "sell_price": 72.00},
            {"name": "River Rock", "unit": "ton", "cost_price": 45.00, "sell_price": 95.00},
            {"name": "Topsoil", "unit": "cubic_yard", "cost_price": 22.00, "sell_price": 50.00},
            {"name": "Grass Seed", "unit": "lb", "cost_price": 2.50, "sell_price": 5.00},
            {"name": "Fertilizer", "unit": "bag", "cost_price": 18.00, "sell_price": 35.00},
            {"name": "Paver (Concrete)", "unit": "sqft", "cost_price": 2.50, "sell_price": 5.50},
        ],
        "labor_rates": [
            {"role": "crew_lead", "property_type": "residential", "rate_per_hour": 70.00},
            {"role": "crew_lead", "property_type": "commercial", "rate_per_hour": 90.00},
            {"role": "laborer", "property_type": "residential", "rate_per_hour": 48.00},
            {"role": "laborer", "property_type": "commercial", "rate_per_hour": 60.00},
            {"role": "operator", "property_type": "any", "rate_per_hour": 80.00},
        ],
    },
}


MAX_SERVICES = 500
MAX_MATERIALS = 200
MAX_LABOR_RATES = 50


class OnboardingService:

    def __init__(self, db: AsyncSession, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    async def get_status(self) -> dict:
        """Determine which onboarding steps are complete."""
        completed = []

        # Step 1: tenant settings (always considered done after signup)
        completed.append(1)

        # Step 2: services catalog
        service_count = await self.db.execute(
            select(func.count(ServiceCatalog.id)).where(
                ServiceCatalog.tenant_id == self.tenant_id
            )
        )
        if service_count.scalar() > 0:
            completed.append(2)

        # Step 3: materials
        material_count = await self.db.execute(
            select(func.count(MaterialCatalog.id)).where(
                MaterialCatalog.tenant_id == self.tenant_id
            )
        )
        if material_count.scalar() > 0:
            completed.append(3)

        # Step 4: labor rates
        labor_count = await self.db.execute(
            select(func.count(LaborRate.id)).where(
                LaborRate.tenant_id == self.tenant_id
            )
        )
        if labor_count.scalar() > 0:
            completed.append(4)

        # Step 5: first crew
        crew_count = await self.db.execute(
            select(func.count(Crew.id)).where(
                Crew.tenant_id == self.tenant_id
            )
        )
        if crew_count.scalar() > 0:
            completed.append(5)

        current_step = max(completed) + 1 if len(completed) < 5 else 5
        is_complete = len(completed) >= 5

        return {
            "step": current_step,
            "completed_steps": completed,
            "is_complete": is_complete,
        }

    async def setup_services(self, template: str = None, custom_services: list = None):
        """Load industry template or custom services. Template takes precedence when valid."""
        repo = ServiceCatalogRepo(self.db, self.tenant_id)
        services_to_create = []

        if template and str(template).strip() and template in INDUSTRY_TEMPLATES:
            services_to_create = INDUSTRY_TEMPLATES[template]["services"]
            log.info("onboarding.template_applied", template=template, tenant_id=str(self.tenant_id))
        elif custom_services:
            if len(custom_services) > MAX_SERVICES:
                raise ValueError(f"Too many services (max {MAX_SERVICES}).")
            services_to_create = [
                s.model_dump() if hasattr(s, "model_dump") else s
                for s in custom_services
            ]

        for i, svc in enumerate(services_to_create):
            if not isinstance(svc, dict):
                continue
            await repo.create(**svc, sort_order=i)

    async def setup_materials(self, custom_materials: list = None, template: str = None):
        """Load materials from template or custom list. Template takes precedence when valid."""
        repo = MaterialCatalogRepo(self.db, self.tenant_id)
        materials_to_create = []
        if template and str(template).strip() and template in INDUSTRY_TEMPLATES:
            materials_to_create = INDUSTRY_TEMPLATES[template]["materials"]
        elif custom_materials:
            if len(custom_materials) > MAX_MATERIALS:
                raise ValueError(f"Too many materials (max {MAX_MATERIALS}).")
            materials_to_create = [
                m.model_dump() if hasattr(m, "model_dump") else m
                for m in custom_materials
            ]
        for mat in materials_to_create:
            if not isinstance(mat, dict):
                continue
            await repo.create(**mat)

    async def setup_labor_rates(self, custom_rates: list = None, template: str = None):
        """Load labor rates from template or custom list. Template takes precedence when valid."""
        repo = LaborRateRepo(self.db, self.tenant_id)
        rates_to_create = []
        if template and str(template).strip() and template in INDUSTRY_TEMPLATES:
            rates_to_create = INDUSTRY_TEMPLATES[template]["labor_rates"]
        elif custom_rates:
            if len(custom_rates) > MAX_LABOR_RATES:
                raise ValueError(f"Too many labor rates (max {MAX_LABOR_RATES}).")
            rates_to_create = [
                r.model_dump() if hasattr(r, "model_dump") else r
                for r in custom_rates
            ]
        for rate in rates_to_create:
            if not isinstance(rate, dict):
                continue
            await repo.create(**rate)
