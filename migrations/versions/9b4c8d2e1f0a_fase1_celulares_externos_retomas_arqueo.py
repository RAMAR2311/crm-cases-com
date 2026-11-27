"""Fase 1: Modulo Celulares, Accesorios, Externos, Retomas, PriceApprovals y Arqueo

Revision ID: 9b4c8d2e1f0a
Revises: 7a8f9e0d1c2b
Create Date: 2026-11-26 18:18:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9b4c8d2e1f0a'
down_revision = '7a8f9e0d1c2b'
branch_labels = None
depends_on = None


def _table_exists(insp, table_name):
    return table_name in insp.get_table_names()


def _column_exists(insp, table_name, column_name):
    if not _table_exists(insp, table_name):
        return False
    return any(c['name'] == column_name for c in insp.get_columns(table_name))


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1. Nuevas columnas en products
    product_cols = [
        ('imei2', sa.Column('imei2', sa.String(length=50), nullable=True)),
        ('color', sa.Column('color', sa.String(length=50), nullable=True)),
        ('bateria', sa.Column('bateria', sa.String(length=50), nullable=True)),
        ('memoria', sa.Column('memoria', sa.String(length=50), nullable=True)),
        ('proveedor', sa.Column('proveedor', sa.String(length=150), nullable=True)),
        ('subtipo_externo', sa.Column('subtipo_externo', sa.String(length=50), nullable=True)),
    ]
    missing_prod_cols = [(name, col) for name, col in product_cols if not _column_exists(insp, 'products', name)]
    if missing_prod_cols:
        with op.batch_alter_table('products', schema=None) as batch_op:
            for name, col in missing_prod_cols:
                batch_op.add_column(col)
            if any(name == 'imei2' for name, _ in missing_prod_cols):
                batch_op.create_index(batch_op.f('ix_products_imei2'), ['imei2'], unique=False)
            if any(name == 'proveedor' for name, _ in missing_prod_cols):
                batch_op.create_index(batch_op.f('ix_products_proveedor'), ['proveedor'], unique=False)

    # 2. Nuevas columnas en sale_clients (MinTIC / SIC)
    client_cols = [
        ('direccion', sa.Column('direccion', sa.String(length=255), nullable=True)),
        ('email', sa.Column('email', sa.String(length=150), nullable=True)),
    ]
    missing_client_cols = [(name, col) for name, col in client_cols if not _column_exists(insp, 'sale_clients', name)]
    if missing_client_cols:
        with op.batch_alter_table('sale_clients', schema=None) as batch_op:
            for _, col in missing_client_cols:
                batch_op.add_column(col)

    # 3. Nueva columna monto_retoma en sales
    if not _column_exists(insp, 'sales', 'monto_retoma'):
        with op.batch_alter_table('sales', schema=None) as batch_op:
            batch_op.add_column(sa.Column('monto_retoma', sa.Numeric(precision=10, scale=2), server_default='0.00', nullable=False))

    # 4. Nueva columna motivo en stock_adjustments
    if not _column_exists(insp, 'stock_adjustments', 'motivo'):
        with op.batch_alter_table('stock_adjustments', schema=None) as batch_op:
            batch_op.add_column(sa.Column('motivo', sa.String(length=255), nullable=True))

    # 5. Nuevas columnas en arqueo_caja (efectivo_fisico_contado y diferencia_efectivo)
    arqueo_cols = [
        ('efectivo_fisico_contado', sa.Column('efectivo_fisico_contado', sa.Numeric(precision=10, scale=2), server_default='0.00', nullable=False)),
        ('diferencia_efectivo', sa.Column('diferencia_efectivo', sa.Numeric(precision=10, scale=2), server_default='0.00', nullable=False)),
    ]
    missing_arqueo_cols = [(name, col) for name, col in arqueo_cols if not _column_exists(insp, 'arqueo_caja', name)]
    if missing_arqueo_cols:
        with op.batch_alter_table('arqueo_caja', schema=None) as batch_op:
            for _, col in missing_arqueo_cols:
                batch_op.add_column(col)

    # 6. Nueva tabla retomas
    if not _table_exists(insp, 'retomas'):
        op.create_table(
            'retomas',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('sale_id', sa.Integer(), nullable=True),
            sa.Column('product_id_generado', sa.Integer(), nullable=True),
            sa.Column('usuario_recibe_id', sa.Integer(), nullable=False),
            sa.Column('usuario_aprueba_id', sa.Integer(), nullable=True),
            sa.Column('imei', sa.String(length=50), nullable=False),
            sa.Column('imei2', sa.String(length=50), nullable=True),
            sa.Column('marca', sa.String(length=100), nullable=False),
            sa.Column('modelo_celular', sa.String(length=100), nullable=False),
            sa.Column('color', sa.String(length=50), nullable=True),
            sa.Column('memoria', sa.String(length=50), nullable=True),
            sa.Column('bateria', sa.String(length=50), nullable=True),
            sa.Column('valor_recibido', sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column('costo_real', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('precio_minimo', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('precio_sugerido', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('cliente_nombre', sa.String(length=150), nullable=True),
            sa.Column('cliente_documento', sa.String(length=50), nullable=True),
            sa.Column('cliente_telefono', sa.String(length=50), nullable=True),
            sa.Column('observaciones', sa.Text(), nullable=True),
            sa.Column('estado', sa.String(length=30), nullable=False),
            sa.Column('fecha_recepcion', sa.DateTime(), nullable=True),
            sa.Column('fecha_aprobacion', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['product_id_generado'], ['products.id']),
            sa.ForeignKeyConstraint(['sale_id'], ['sales.id']),
            sa.ForeignKeyConstraint(['usuario_aprueba_id'], ['users.id']),
            sa.ForeignKeyConstraint(['usuario_recibe_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('retomas', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_retomas_imei'), ['imei'], unique=False)

    # 7. Nueva tabla price_approvals
    if not _table_exists(insp, 'price_approvals'):
        op.create_table(
            'price_approvals',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('product_id', sa.Integer(), nullable=False),
            sa.Column('variant_id', sa.Integer(), nullable=True),
            sa.Column('vendedor_id', sa.Integer(), nullable=False),
            sa.Column('admin_id', sa.Integer(), nullable=True),
            sa.Column('sale_id', sa.Integer(), nullable=True),
            sa.Column('precio_minimo_original', sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column('precio_solicitado', sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column('motivo', sa.String(length=255), nullable=True),
            sa.Column('estado', sa.String(length=30), nullable=False),
            sa.Column('fecha_solicitud', sa.DateTime(), nullable=True),
            sa.Column('fecha_resolucion', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['admin_id'], ['users.id']),
            sa.ForeignKeyConstraint(['product_id'], ['products.id']),
            sa.ForeignKeyConstraint(['sale_id'], ['sales.id']),
            sa.ForeignKeyConstraint(['variant_id'], ['product_variants.id']),
            sa.ForeignKeyConstraint(['vendedor_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id')
        )

    # 8. Nueva tabla provider_invoices
    if not _table_exists(insp, 'provider_invoices'):
        op.create_table(
            'provider_invoices',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('proveedor', sa.String(length=150), nullable=False),
            sa.Column('product_id', sa.Integer(), nullable=True),
            sa.Column('sale_id', sa.Integer(), nullable=True),
            sa.Column('usuario_id', sa.Integer(), nullable=False),
            sa.Column('concepto', sa.String(length=255), nullable=False),
            sa.Column('monto_costo', sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column('estado', sa.String(length=30), nullable=False),
            sa.Column('fecha_creacion', sa.DateTime(), nullable=True),
            sa.Column('fecha_pago', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['product_id'], ['products.id']),
            sa.ForeignKeyConstraint(['sale_id'], ['sales.id']),
            sa.ForeignKeyConstraint(['usuario_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('provider_invoices', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_provider_invoices_proveedor'), ['proveedor'], unique=False)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if _table_exists(insp, 'provider_invoices'):
        op.drop_table('provider_invoices')
    if _table_exists(insp, 'price_approvals'):
        op.drop_table('price_approvals')
    if _table_exists(insp, 'retomas'):
        op.drop_table('retomas')

    with op.batch_alter_table('arqueo_caja', schema=None) as batch_op:
        if _column_exists(insp, 'arqueo_caja', 'diferencia_efectivo'):
            batch_op.drop_column('diferencia_efectivo')
        if _column_exists(insp, 'arqueo_caja', 'efectivo_fisico_contado'):
            batch_op.drop_column('efectivo_fisico_contado')

    if _column_exists(insp, 'stock_adjustments', 'motivo'):
        with op.batch_alter_table('stock_adjustments', schema=None) as batch_op:
            batch_op.drop_column('motivo')

    if _column_exists(insp, 'sales', 'monto_retoma'):
        with op.batch_alter_table('sales', schema=None) as batch_op:
            batch_op.drop_column('monto_retoma')

    with op.batch_alter_table('sale_clients', schema=None) as batch_op:
        if _column_exists(insp, 'sale_clients', 'email'):
            batch_op.drop_column('email')
        if _column_exists(insp, 'sale_clients', 'direccion'):
            batch_op.drop_column('direccion')

    with op.batch_alter_table('products', schema=None) as batch_op:
        for col in ['subtipo_externo', 'proveedor', 'memoria', 'bateria', 'color', 'imei2']:
            if _column_exists(insp, 'products', col):
                batch_op.drop_column(col)
