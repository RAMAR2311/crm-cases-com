"""Add zona to Cliente

Revision ID: 7a8f9e0d1c2b
Revises: 03acf7581d1b
Create Date: 2026-08-11 18:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7a8f9e0d1c2b'
down_revision = '03acf7581d1b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('clientes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('zona', sa.String(length=100), nullable=True))


def downgrade():
    with op.batch_alter_table('clientes', schema=None) as batch_op:
        batch_op.drop_column('zona')
