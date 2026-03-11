"""add tenant onboarding_completed_at

Revision ID: b1c2d3e4f5a6
Revises: e85a18fe57ca
Create Date: 2026-03-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "e85a18fe57ca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: mark tenants that already have at least one crew as onboarding complete
    op.execute("""
        UPDATE tenants t
        SET onboarding_completed_at = now()
        WHERE t.onboarding_completed_at IS NULL
        AND EXISTS (SELECT 1 FROM crews c WHERE c.tenant_id = t.id LIMIT 1)
    """)


def downgrade() -> None:
    op.drop_column("tenants", "onboarding_completed_at")
