from datetime import datetime
from decimal import Decimal
import pytz
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_
from models import db, Product, StockAdjustment, ProviderInvoice
from decorators import admin_required
from utils import safe_int, safe_float

externos_bp = Blueprint('externos_bp', __name__)


def obtener_hora_bogota():
    return datetime.now(pytz.timezone('America/Bogota')).replace(tzinfo=None)


# ══════════════════════════════════════════════════════════════════════════
# 1.2 PRODUCTOS EXTERNOS - CELULARES (/externos/)
# ══════════════════════════════════════════════════════════════════════════

@externos_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        proveedor = request.form.get('proveedor', '').strip()
        imei = request.form.get('imei', '').strip() or None
        imei2 = request.form.get('imei2', '').strip() or None
        marca = request.form.get('marca', '').strip()
        modelo_celular = request.form.get('modelo_celular', '').strip()
        estado_celular = request.form.get('estado_celular', 'Nuevo').strip() or 'Nuevo'
        memoria = request.form.get('memoria', '').strip() or None
        color = request.form.get('color', '').strip() or None
        bateria = request.form.get('bateria', '').strip() or None
        observacion = request.form.get('observacion', '').strip() or None

        precio_costo = safe_float(request.form.get('precio_costo', '0').replace(',', '').replace('.', ''), 0.0)
        precio_minimo = safe_float(request.form.get('precio_minimo', '0').replace(',', '').replace('.', ''), precio_costo)
        precio_sugerido = safe_float(request.form.get('precio_sugerido', '0').replace(',', '').replace('.', ''), precio_minimo)

        if not proveedor:
            flash('El nombre del proveedor externo es obligatorio.', 'danger')
            return redirect(url_for('externos_bp.index'))

        if not marca or not modelo_celular:
            flash('Marca y Modelo son obligatorios para registrar un celular externo.', 'danger')
            return redirect(url_for('externos_bp.index'))

        if imei:
            from routes.celulares import archivar_imei_existente_si_vendido
            ok_imei, msg_imei = archivar_imei_existente_si_vendido(imei)
            if not ok_imei:
                flash(msg_imei, 'danger')
                return redirect(url_for('externos_bp.index'))

        ahora_str = obtener_hora_bogota().strftime('%Y%m%d%H%M%S')
        sku_ext = f"EXT-{ahora_str}"
        if Product.query.filter_by(sku=sku_ext).first():
            sku_ext = f"EXT-{ahora_str}-{Product.query.count() + 1}"[:50]

        partes_nom = [p for p in [marca, modelo_celular, memoria, color] if p]
        nombre_ext = " ".join(partes_nom) if partes_nom else f"Celular Externo {sku_ext}"

        nuevo = Product(
            nombre=nombre_ext,
            sku=sku_ext,
            tipo_inventario='externos',
            subtipo_externo='celular',
            cantidad_stock=1,
            precio_costo=precio_costo,
            precio_minimo=precio_minimo,
            precio_sugerido=precio_sugerido,
            imei=imei,
            imei2=imei2,
            marca=marca,
            modelo_celular=modelo_celular,
            estado_celular=estado_celular,
            memoria=memoria,
            color=color,
            bateria=bateria,
            proveedor=proveedor,
            observacion=observacion
        )

        try:
            db.session.add(nuevo)
            db.session.flush()
            db.session.add(StockAdjustment(
                product_id=nuevo.id,
                admin_id=current_user.id,
                tipo_movimiento='Ingreso Celular Externo (Consignación)',
                motivo=f"Proveedor: {proveedor} | SKU: {sku_ext}",
                stock_anterior=0,
                stock_nuevo=1
            ))
            db.session.commit()
            flash(f'Celular externo "{nombre_ext}" ({sku_ext}) del proveedor "{proveedor}" registrado con éxito.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error al registrar celular externo: {str(e)}', 'danger')

        return redirect(url_for('externos_bp.index'))

    q = request.args.get('q', '').strip()
    query = Product.query.filter(
        Product.tipo_inventario == 'externos',
        or_(Product.subtipo_externo == 'celular', Product.sku.ilike('EXT-%'))
    ).filter(~Product.sku.ilike('EXTACC-%'))

    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(
                Product.nombre.ilike(term),
                Product.sku.ilike(term),
                Product.imei.ilike(term),
                Product.proveedor.ilike(term),
                Product.marca.ilike(term),
                Product.modelo_celular.ilike(term)
            )
        )

    celulares_externos = query.order_by(Product.cantidad_stock.desc(), Product.fecha_creacion.desc()).all()
    disponibles_count = sum(1 for c in celulares_externos if (c.cantidad_stock or 0) > 0)
    costo_consignacion = sum(float(c.precio_costo or 0) * (c.cantidad_stock or 0) for c in celulares_externos if (c.cantidad_stock or 0) > 0)
    proveedores_activos = len(set(c.proveedor for c in celulares_externos if c.proveedor and (c.cantidad_stock or 0) > 0))
    deuda_pendiente_total = sum(float(inv.monto_costo or 0) for inv in ProviderInvoice.query.filter_by(estado='PENDIENTE').all())

    return render_template(
        'externos/celulares.html',
        celulares_externos=celulares_externos,
        disponibles_count=disponibles_count,
        costo_consignacion=costo_consignacion,
        proveedores_activos=proveedores_activos,
        deuda_pendiente_total=deuda_pendiente_total,
        q=q
    )


# ══════════════════════════════════════════════════════════════════════════
# 2.2 ACCESORIOS EXTERNOS (/externos/accesorios)
# ══════════════════════════════════════════════════════════════════════════

@externos_bp.route('/accesorios', methods=['GET', 'POST'])
@login_required
def accesorios():
    if request.method == 'POST':
        proveedor = request.form.get('proveedor', '').strip()
        nombre = request.form.get('nombre', '').strip()
        cantidad_stock = safe_int(request.form.get('cantidad_stock', '1'), 1)
        precio_costo = safe_float(request.form.get('precio_costo', '0').replace(',', '').replace('.', ''), 0.0)
        precio_minimo = safe_float(request.form.get('precio_minimo', '0').replace(',', '').replace('.', ''), precio_costo)
        precio_sugerido = safe_float(request.form.get('precio_sugerido', '0').replace(',', '').replace('.', ''), precio_minimo)
        observacion = request.form.get('observacion', '').strip() or None

        if not proveedor or not nombre:
            flash('El nombre del accesorio y el proveedor son obligatorios.', 'danger')
            return redirect(url_for('externos_bp.accesorios'))

        ahora_str = obtener_hora_bogota().strftime('%Y%m%d%H%M%S')
        sku_ext = f"EXTACC-{ahora_str}"
        if Product.query.filter_by(sku=sku_ext).first():
            sku_ext = f"EXTACC-{ahora_str}-{Product.query.count() + 1}"[:50]

        nuevo = Product(
            nombre=nombre,
            sku=sku_ext,
            tipo_inventario='externos',
            subtipo_externo='accesorio',
            cantidad_stock=max(1, cantidad_stock),
            precio_costo=precio_costo,
            precio_minimo=precio_minimo,
            precio_sugerido=precio_sugerido,
            proveedor=proveedor,
            observacion=observacion
        )

        try:
            db.session.add(nuevo)
            db.session.flush()
            db.session.add(StockAdjustment(
                product_id=nuevo.id,
                admin_id=current_user.id,
                tipo_movimiento='Ingreso Accesorio Externo (Consignación)',
                motivo=f"Proveedor: {proveedor} | SKU: {sku_ext}",
                stock_anterior=0,
                stock_nuevo=nuevo.cantidad_stock
            ))
            db.session.commit()
            flash(f'Accesorio externo "{nombre}" ({sku_ext}) del proveedor "{proveedor}" registrado con éxito.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error al registrar accesorio externo: {str(e)}', 'danger')

        return redirect(url_for('externos_bp.accesorios'))

    q = request.args.get('q', '').strip()
    query = Product.query.filter(
        Product.tipo_inventario == 'externos',
        or_(Product.subtipo_externo == 'accesorio', Product.sku.ilike('EXTACC-%'))
    )

    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(
                Product.nombre.ilike(term),
                Product.sku.ilike(term),
                Product.proveedor.ilike(term)
            )
        )

    accesorios_externos = query.order_by(Product.cantidad_stock.desc(), Product.fecha_creacion.desc()).all()
    unidades_disponibles = sum((a.cantidad_stock or 0) for a in accesorios_externos if (a.cantidad_stock or 0) > 0)
    costo_consignacion = sum(float(a.precio_costo or 0) * (a.cantidad_stock or 0) for a in accesorios_externos if (a.cantidad_stock or 0) > 0)

    return render_template(
        'externos/accesorios.html',
        accesorios_externos=accesorios_externos,
        unidades_disponibles=unidades_disponibles,
        costo_consignacion=costo_consignacion,
        q=q
    )


# ══════════════════════════════════════════════════════════════════════════
# ACCIÓN: ENVIAR A INVENTARIO PROPIO / NACIONALIZAR (INDIVIDUAL Y MASIVA)
# ══════════════════════════════════════════════════════════════════════════

def _nacionalizar_producto(prod, admin_id):
    if prod.tipo_inventario != 'externos':
        return False

    es_accesorio = (prod.subtipo_externo == 'accesorio' or (prod.sku and prod.sku.startswith('EXTACC-')))
    if es_accesorio:
        prod.tipo_inventario = 'tienda'
        tipo_dest = 'Inventario Propio Accesorios (Tienda)'
    else:
        prod.tipo_inventario = 'celulares'
        if not prod.sku.endswith('-INV'):
            nuevo_sku = f"{prod.sku}-INV"[:50]
            if not Product.query.filter(Product.sku == nuevo_sku, Product.id != prod.id).first():
                prod.sku = nuevo_sku
        tipo_dest = 'Inventario Propio Celulares (-INV)'

    db.session.add(StockAdjustment(
        product_id=prod.id,
        admin_id=admin_id,
        tipo_movimiento='Nacionalización a Inventario Propio',
        motivo=f"Pasó de Externos ({prod.proveedor or 'S/P'}) a {tipo_dest}",
        stock_anterior=prod.cantidad_stock,
        stock_nuevo=prod.cantidad_stock
    ))
    return True


@externos_bp.route('/nacionalizar/<int:id>', methods=['POST'])
@login_required
@admin_required
def nacionalizar_individual(id):
    prod = Product.query.get_or_404(id)
    es_acc = (prod.subtipo_externo == 'accesorio' or (prod.sku and prod.sku.startswith('EXTACC-')))
    try:
        if _nacionalizar_producto(prod, current_user.id):
            db.session.commit()
            flash(f'✅ "{prod.nombre}" ({prod.sku}) fue enviado al Inventario Propio exitosamente.', 'success')
        else:
            flash('El producto ya pertenece al inventario propio.', 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al transferir al inventario propio: {str(e)}', 'danger')

    return redirect(url_for('externos_bp.accesorios' if es_acc else 'externos_bp.index'))


@externos_bp.route('/nacionalizar-masivo', methods=['POST'])
@login_required
@admin_required
def nacionalizar_masivo():
    ids = request.form.getlist('product_ids[]')
    origen = request.form.get('origen', 'celulares')
    if not ids:
        flash('Selecciona al menos un producto externo para transferir a inventario propio.', 'warning')
        return redirect(url_for('externos_bp.accesorios' if origen == 'accesorios' else 'externos_bp.index'))

    procesados = 0
    try:
        for pid in ids:
            prod = Product.query.get(safe_int(pid, 0))
            if prod and _nacionalizar_producto(prod, current_user.id):
                procesados += 1
        db.session.commit()
        flash(f'✅ {procesados} producto(s) transferidos al Inventario Propio exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error en la transferencia masiva: {str(e)}', 'danger')

    return redirect(url_for('externos_bp.accesorios' if origen == 'accesorios' else 'externos_bp.index'))


@externos_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_externo(id):
    prod = Product.query.get_or_404(id)
    es_acc = (prod.subtipo_externo == 'accesorio' or (prod.sku and prod.sku.startswith('EXTACC-')))
    if prod.detalles_venta:
        flash('No se puede eliminar un producto externo que ya fue facturado.', 'danger')
        return redirect(url_for('externos_bp.accesorios' if es_acc else 'externos_bp.index'))

    try:
        for aj in prod.ajustes_stock:
            db.session.delete(aj)
        db.session.delete(prod)
        db.session.commit()
        flash('Producto externo eliminado correctamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar: {str(e)}', 'danger')

    return redirect(url_for('externos_bp.accesorios' if es_acc else 'externos_bp.index'))


# ══════════════════════════════════════════════════════════════════════════
# API: CREAR PRODUCTO EXTERNO EN CALIENTE DESDE CAJA (/externos/api/nuevo_desde_caja)
# ══════════════════════════════════════════════════════════════════════════

@externos_bp.route('/api/nuevo_desde_caja', methods=['POST'])
@login_required
def api_nuevo_desde_caja():
    data = request.get_json() or {}
    subtipo = (data.get('subtipo') or 'accesorio').strip().lower() # 'celular' o 'accesorio'
    proveedor = (data.get('proveedor') or '').strip()
    nombre = (data.get('nombre') or '').strip()
    marca = (data.get('marca') or '').strip()
    modelo_celular = (data.get('modelo_celular') or '').strip()
    imei = (data.get('imei') or '').strip() or None
    color = (data.get('color') or '').strip() or None
    memoria = (data.get('memoria') or '').strip() or None
    cantidad = max(1, safe_int(data.get('cantidad'), 1))

    precio_costo = safe_float(data.get('precio_costo'), 0.0)
    precio_venta = safe_float(data.get('precio_venta') or data.get('precio_sugerido'), 0.0)
    precio_minimo = safe_float(data.get('precio_minimo'), precio_costo)

    if not proveedor:
        return jsonify({'error': 'El nombre del proveedor externo es obligatorio.'}), 400
    if precio_venta <= 0:
        return jsonify({'error': 'El precio de venta debe ser mayor a $0.'}), 400

    ahora_str = obtener_hora_bogota().strftime('%Y%m%d%H%M%S')
    if subtipo == 'celular':
        if not nombre and (marca or modelo_celular):
            nombre = " ".join([p for p in [marca, modelo_celular, memoria, color] if p])
        if not nombre:
            return jsonify({'error': 'Debes indicar Marca/Modelo o Nombre del celular externo.'}), 400
        if imei:
            from routes.celulares import archivar_imei_existente_si_vendido
            ok_imei, msg_imei = archivar_imei_existente_si_vendido(imei)
            if not ok_imei:
                return jsonify({'error': msg_imei}), 400
        sku_ext = f"EXT-{ahora_str}"
        cantidad = 1
    else:
        if not nombre:
            return jsonify({'error': 'El nombre del accesorio externo es obligatorio.'}), 400
        sku_ext = f"EXTACC-{ahora_str}"

    if Product.query.filter_by(sku=sku_ext).first():
        sku_ext = f"{sku_ext}-{Product.query.count() + 1}"[:50]

    try:
        nuevo = Product(
            nombre=nombre,
            sku=sku_ext,
            tipo_inventario='externos',
            subtipo_externo=subtipo,
            cantidad_stock=cantidad,
            precio_costo=precio_costo,
            precio_minimo=precio_minimo if precio_minimo > 0 else precio_costo,
            precio_sugerido=precio_venta,
            imei=imei if subtipo == 'celular' else None,
            marca=marca or None,
            modelo_celular=modelo_celular or None,
            color=color,
            memoria=memoria,
            proveedor=proveedor,
            observacion='Creado en caliente desde Caja POS'
        )
        db.session.add(nuevo)
        db.session.flush()

        db.session.add(StockAdjustment(
            product_id=nuevo.id,
            admin_id=current_user.id,
            tipo_movimiento='Alta Externo desde Caja POS',
            motivo=f"Proveedor: {proveedor} | SKU: {sku_ext}",
            stock_anterior=0,
            stock_nuevo=cantidad
        ))
        db.session.commit()

        return jsonify({
            'success': True,
            'product': {
                'id': nuevo.id,
                'sku': nuevo.sku,
                'nombre': nuevo.nombre,
                'tipo_inventario': nuevo.tipo_inventario,
                'subtipo_externo': nuevo.subtipo_externo,
                'proveedor': nuevo.proveedor,
                'imei': nuevo.imei,
                'cantidad_stock': nuevo.cantidad_stock,
                'cantidad': cantidad,
                'precio_costo': float(nuevo.precio_costo),
                'precio_minimo': float(nuevo.precio_minimo),
                'precio_limite': float(nuevo.precio_costo) if current_user.rol == 'admin' else float(nuevo.precio_minimo),
                'precio_sugerido': float(nuevo.precio_sugerido),
                'variantes': []
            }
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Error al crear producto externo: {str(e)}'}), 500


# ══════════════════════════════════════════════════════════════════════════
# CUENTAS POR PAGAR A PROVEEDORES EXTERNOS (/externos/cuentas_por_pagar)
# ══════════════════════════════════════════════════════════════════════════

@externos_bp.route('/cuentas_por_pagar', methods=['GET'])
@login_required
def cuentas_por_pagar():
    estado = request.args.get('estado', 'PENDIENTE').strip()
    query = ProviderInvoice.query
    if estado and estado != 'TODAS':
        query = query.filter_by(estado=estado)

    cuentas = query.order_by(ProviderInvoice.fecha_creacion.desc()).all()
    total_pendiente = sum(float(c.monto_costo or 0) for c in ProviderInvoice.query.filter_by(estado='PENDIENTE').all())
    total_pagado = sum(float(c.monto_costo or 0) for c in ProviderInvoice.query.filter_by(estado='PAGADO').all())

    return render_template(
        'externos/cuentas_por_pagar.html',
        cuentas=cuentas,
        estado=estado,
        total_pendiente=total_pendiente,
        total_pagado=total_pagado
    )


@externos_bp.route('/cuentas_por_pagar/pagar/<int:id>', methods=['POST'])
@login_required
@admin_required
def pagar_cuenta_proveedor(id):
    cuenta = ProviderInvoice.query.get_or_404(id)
    if cuenta.estado == 'PAGADO':
        flash('Esta cuenta ya estaba marcada como pagada.', 'info')
        return redirect(url_for('externos_bp.cuentas_por_pagar'))

    try:
        cuenta.estado = 'PAGADO'
        cuenta.fecha_pago = obtener_hora_bogota()
        db.session.commit()
        flash(f'✅ Pago al proveedor "{cuenta.proveedor}" por ${float(cuenta.monto_costo):,.0f} registrado exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al registrar el pago: {str(e)}', 'danger')

    return redirect(url_for('externos_bp.cuentas_por_pagar'))
