"""Add workflow_type to workflows

Revision ID: a1b2c3d4e5f6
Revises: 2c1f4b3f14e1
Create Date: 2026-09-10 19:02:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '2c1f4b3f14e1'
branch_labels = None
depends_on = None


def upgrade():
    # Add workflow_type column with default 'standard'
    # Existing rows will automatically get 'standard' as their value
    op.add_column(
        'workflows',
        sa.Column(
            'workflow_type',
            sa.String(length=20),
            nullable=False,
            server_default='standard'
        )
    )


def downgrade():
    op.drop_column('workflows', 'workflow_type')
