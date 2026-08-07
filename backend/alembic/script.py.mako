"""Alembic migration script template.

This is the script that Alembic uses to generate new migration revisions.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic
revision = None  # will be set by alembic revision --autogenerate
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
