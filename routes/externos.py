import os
from datetime import datetime
from decimal import Decimal
import pytz
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_
from models import db, Product, StockAdjustment, ProviderInvoice, ProviderPayment
from decorators import admin_required
from utils import safe_int, safe_float

externos_bp = Blueprint('externos_bp', __name__)


def obtener_hora_bogota():
    return datetime.now(pytz.timezone('America/Bogota')).replace(tzinfo=None)


def obtener_proveedores_registrados():
    """Retorna la lista ordenada de todos los proveedores externos registrados en productos o cuentas por pagar."""
    provs = set()
    try:
        for (p_nom,) in db.session.query(Product.proveedor).filter(Product.proveedor.isnot(None)).distinct().all():
            if p_nom and p_nom.strip() and not p_nom.strip().lower().startswith('retoma'):
                provs.add(p_nom.strip())
        for (i_nom,) in db.session.query(ProviderInvoice.proveedor).filter(ProviderInvoice.proveedor.isnot(None)).distinct().all():
            if i_nom and i_nom.strip() and not i_nom.strip().lower().startswith('retoma'):
                provs.add(i_nom.strip())
    except Exception:
        pass
    return sorted(provs, key=lambda x: x.lower())


def _resolver_proveedor_form(form):
    sel = (form.get('proveedor_select') or '').strip()
    nuevo = (form.get('proveedor_nuevo') or '').strip()
    directo = (form.get('proveedor') or '').strip()
    if sel and sel != '__nuevo__':
        return sel
    if nuevo:
        return nuevo
    return directo


# ══════════════════════════════════════════════════════════════════════════
# 1.2 PRODUCTOS EXTERNOS - CELULARES (/externos/)
# ══════════════════════════════════════════════════════════════════════════

@externos_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        proveedor = _resolver_proveedor_form(request.form)
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
            flash('Debes seleccionar un proveedor existente o escribir el nombre del nuevo proveedor.', 'danger')
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

        ahora_obj = obtener_hora_bogota()
        ahora_str = ahora_obj.strftime('%Y%m%d%H%M%S')
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
                motivo=f"Proveedor: {proveedor} | SKU: {sku_ext}" + (f" | IMEI 1: {imei}" if imei else "") + (f" | IMEI 2: {imei2}" if imei2 else ""),
                stock_anterior=0,
                stock_nuevo=1
            ))
            # Registrar ingreso de deuda con el proveedor en Cuentas Externos para control de pago
            if precio_costo > 0:
                imei_info = f" | IMEI: {imei}" + (f" / {imei2}" if imei2 else "") if imei else ""
                db.session.add(ProviderInvoice(
                    proveedor=proveedor,
                    product_id=nuevo.id,
                    sale_id=None,
                    usuario_id=current_user.id,
                    concepto=f"Ingreso Cel Ext.: {nombre_ext} ({sku_ext}){imei_info}",
                    monto_costo=Decimal(str(precio_costo)),
                    estado='PENDIENTE',
                    fecha_creacion=ahora_obj
                ))
            db.session.commit()
            flash(f'Celular externo "{nombre_ext}" ({sku_ext}) del proveedor "{proveedor}" registrado con su cuenta por pagar.', 'success')
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
                Product.imei2.ilike(term),
                Product.proveedor.ilike(term),
                Product.marca.ilike(term),
                Product.modelo_celular.ilike(term)
            )
        )

    celulares_externos = query.order_by(Product.fecha_creacion.desc()).all()
    disponibles_count = len(celulares_externos)
    costo_consignacion = sum(float(c.precio_costo or 0) for c in celulares_externos)
    proveedores_activos = len(set(c.proveedor for c in celulares_externos if c.proveedor))

    # Mapear facturas de proveedor y ventas asociadas a cada celular externo
    facturas_map = {}
    for inv in ProviderInvoice.query.all():
        if inv.product_id:
            facturas_map[inv.product_id] = inv

    deuda_pendiente_total = sum(float(inv.monto_costo or 0) for inv in ProviderInvoice.query.filter_by(estado='PENDIENTE').all())
    proveedores_registrados = obtener_proveedores_registrados()

    return render_template(
        'externos/celulares.html',
        celulares_externos=celulares_externos,
        facturas_map=facturas_map,
        disponibles_count=disponibles_count,
        costo_consignacion=costo_consignacion,
        proveedores_activos=proveedores_activos,
        deuda_pendiente_total=deuda_pendiente_total,
        proveedores_registrados=proveedores_registrados,
        q=q
    )


# ══════════════════════════════════════════════════════════════════════════
# 2.2 ACCESORIOS EXTERNOS (/externos/accesorios)
# ══════════════════════════════════════════════════════════════════════════

@externos_bp.route('/accesorios', methods=['GET', 'POST'])
@login_required
def accesorios():
    if request.method == 'POST':
        proveedor = _resolver_proveedor_form(request.form)
        nombre = request.form.get('nombre', '').strip()
        cantidad_stock = safe_int(request.form.get('cantidad_stock', '1'), 1)
        precio_costo = safe_float(request.form.get('precio_costo', '0').replace(',', '').replace('.', ''), 0.0)
        precio_minimo = safe_float(request.form.get('precio_minimo', '0').replace(',', '').replace('.', ''), precio_costo)
        precio_sugerido = safe_float(request.form.get('precio_sugerido', '0').replace(',', '').replace('.', ''), precio_minimo)
        observacion = request.form.get('observacion', '').strip() or None

        if not proveedor or not nombre:
            flash('El nombre del accesorio y el proveedor son obligatorios.', 'danger')
            return redirect(url_for('externos_bp.accesorios'))

        ahora_obj = obtener_hora_bogota()
        ahora_str = ahora_obj.strftime('%Y%m%d%H%M%S')
        sku_ext = f"EXTACC-{ahora_str}"
        if Product.query.filter_by(sku=sku_ext).first():
            sku_ext = f"EXTACC-{ahora_str}-{Product.query.count() + 1}"[:50]

        cant_final = max(1, cantidad_stock)
        nuevo = Product(
            nombre=nombre,
            sku=sku_ext,
            tipo_inventario='externos',
            subtipo_externo='accesorio',
            cantidad_stock=cant_final,
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
            if precio_costo > 0:
                db.session.add(ProviderInvoice(
                    proveedor=proveedor,
                    product_id=nuevo.id,
                    sale_id=None,
                    usuario_id=current_user.id,
                    concepto=f"Ingreso Acc Ext.: {cant_final}x {nombre} ({sku_ext})",
                    monto_costo=Decimal(str(precio_costo)) * cant_final,
                    estado='PENDIENTE',
                    fecha_creacion=ahora_obj
                ))
            db.session.commit()
            flash(f'Accesorio externo "{nombre}" ({sku_ext}) del proveedor "{proveedor}" registrado con su cuenta por pagar.', 'success')
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

    accesorios_externos = query.order_by(Product.fecha_creacion.desc()).all()
    unidades_disponibles = len(accesorios_externos)
    costo_consignacion = sum(float(a.precio_costo or 0) for a in accesorios_externos)

    facturas_map = {}
    for inv in ProviderInvoice.query.all():
        if inv.product_id:
            facturas_map[inv.product_id] = inv

    deuda_pendiente_total = sum(float(inv.monto_costo or 0) for inv in ProviderInvoice.query.filter_by(estado='PENDIENTE').all())
    proveedores_registrados = obtener_proveedores_registrados()

    return render_template(
        'externos/accesorios.html',
        accesorios_externos=accesorios_externos,
        facturas_map=facturas_map,
        unidades_disponibles=unidades_disponibles,
        costo_consignacion=costo_consignacion,
        deuda_pendiente_total=deuda_pendiente_total,
        proveedores_registrados=proveedores_registrados,
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
    imei2 = (data.get('imei2') or '').strip() or None
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
        imagen_auto_ext = None
        if subtipo == 'celular':
            from routes.celulares import obtener_imagen_por_referencia
            imagen_auto_ext = obtener_imagen_por_referencia(marca, modelo_celular, color)

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
            imei2=imei2 if subtipo == 'celular' else None,
            marca=marca or None,
            modelo_celular=modelo_celular or None,
            color=color,
            memoria=memoria,
            proveedor=proveedor,
            observacion='Creado en caliente desde Caja POS',
            imagen=imagen_auto_ext
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
        if precio_costo > 0:
            tipo_label = 'Cel Ext.' if subtipo == 'celular' else f'Acc Ext. ({cantidad}x)'
            db.session.add(ProviderInvoice(
                proveedor=proveedor,
                product_id=nuevo.id,
                sale_id=None,
                usuario_id=current_user.id,
                concepto=f"{tipo_label}: {nombre} ({sku_ext})",
                monto_costo=Decimal(str(precio_costo)) * cantidad,
                estado='PENDIENTE',
                fecha_creacion=obtener_hora_bogota()
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
                'imei2': nuevo.imei2,
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
# CUENTAS POR PAGAR Y ESTADO DE CUENTA DE PROVEEDORES EXTERNOS
# ══════════════════════════════════════════════════════════════════════════

def _recalcular_facturas_proveedor(proveedor_nombre):
    """
    Sincroniza el estado de las facturas de un proveedor según sus abonos directos
    y abonos generales a la cuenta del proveedor (FIFO sobre facturas más antiguas).
    """
    facturas = ProviderInvoice.query.filter_by(proveedor=proveedor_nombre).order_by(ProviderInvoice.fecha_creacion.asc()).all()
    abonos_generales = ProviderPayment.query.filter_by(proveedor=proveedor_nombre, invoice_id=None).order_by(ProviderPayment.fecha_abono.asc()).all()
    bolsa_general = sum(float(a.monto or 0) for a in abonos_generales)

    ahora = obtener_hora_bogota()
    for f in facturas:
        costo = float(f.monto_costo or 0)
        abonado_directo = sum(float(a.monto or 0) for a in f.abonos)
        # Si estaba marcada como PAGADO antes de existir la tabla de abonos y no tiene abonos, respetarla
        if f.estado == 'PAGADO' and not f.abonos and bolsa_general <= 0:
            continue

        restante = max(0.0, costo - abonado_directo)
        aplicado_general = min(restante, bolsa_general)
        bolsa_general -= aplicado_general
        nuevo_restante = max(0.0, restante - aplicado_general)

        if nuevo_restante <= 0.5:
            f.estado = 'PAGADO'
            if not f.fecha_pago:
                f.fecha_pago = ahora
        elif (abonado_directo + aplicado_general) > 0:
            f.estado = 'PARCIAL'
        else:
            f.estado = 'PENDIENTE'


@externos_bp.route('/cuentas_por_pagar', methods=['GET'])
@login_required
def cuentas_por_pagar():
    estado = request.args.get('estado', 'PENDIENTE').strip()
    proveedor_filtro = request.args.get('proveedor', '').strip()

    todas_cuentas = ProviderInvoice.query.order_by(ProviderInvoice.fecha_creacion.desc()).all()
    todos_abonos = ProviderPayment.query.order_by(ProviderPayment.fecha_abono.desc()).all()

    # Agrupar estado de cuenta por proveedor
    resumen_map = {}
    for p_reg in obtener_proveedores_registrados():
        resumen_map[p_reg] = {
            'proveedor': p_reg,
            'total_facturado': 0.0,
            'total_abonado': 0.0,
            'saldo_pendiente': 0.0,
            'facturas_count': 0,
            'facturas_pendientes': 0,
            'abonos_count': 0,
            'ultima_actividad': None
        }

    for c in todas_cuentas:
        p_nom = (c.proveedor or 'Sin Proveedor').strip()
        if p_nom not in resumen_map:
            resumen_map[p_nom] = {
                'proveedor': p_nom,
                'total_facturado': 0.0,
                'total_abonado': 0.0,
                'saldo_pendiente': 0.0,
                'facturas_count': 0,
                'facturas_pendientes': 0,
                'abonos_count': 0,
                'ultima_actividad': None
            }
        monto_f = float(c.monto_costo or 0)
        resumen_map[p_nom]['total_facturado'] += monto_f
        resumen_map[p_nom]['facturas_count'] += 1
        if c.estado != 'PAGADO':
            resumen_map[p_nom]['facturas_pendientes'] += 1
        # Si fue marcada como PAGADO antes sin registro en ProviderPayment, sumar ese pago histórico
        if c.estado == 'PAGADO' and not c.abonos:
            resumen_map[p_nom]['total_abonado'] += monto_f
        if c.fecha_creacion and (not resumen_map[p_nom]['ultima_actividad'] or c.fecha_creacion > resumen_map[p_nom]['ultima_actividad']):
            resumen_map[p_nom]['ultima_actividad'] = c.fecha_creacion

    for ab in todos_abonos:
        p_nom = (ab.proveedor or 'Sin Proveedor').strip()
        if p_nom not in resumen_map:
            resumen_map[p_nom] = {
                'proveedor': p_nom,
                'total_facturado': 0.0,
                'total_abonado': 0.0,
                'saldo_pendiente': 0.0,
                'facturas_count': 0,
                'facturas_pendientes': 0,
                'abonos_count': 0,
                'ultima_actividad': None
            }
        resumen_map[p_nom]['total_abonado'] += float(ab.monto or 0)
        resumen_map[p_nom]['abonos_count'] += 1
        if ab.fecha_abono and (not resumen_map[p_nom]['ultima_actividad'] or ab.fecha_abono > resumen_map[p_nom]['ultima_actividad']):
            resumen_map[p_nom]['ultima_actividad'] = ab.fecha_abono

    for p_nom, datos in resumen_map.items():
        datos['saldo_pendiente'] = max(0.0, datos['total_facturado'] - datos['total_abonado'])
        datos['pendiente'] = datos['saldo_pendiente']
        datos['pagado'] = datos['total_abonado']
        datos['items_pendientes'] = datos['facturas_pendientes']

    resumen_proveedores = sorted(resumen_map.values(), key=lambda x: (-x['saldo_pendiente'], -x['total_facturado'], x['proveedor'].lower()))

    total_pendiente = sum(rp['saldo_pendiente'] for rp in resumen_proveedores)
    total_pagado = sum(rp['total_abonado'] for rp in resumen_proveedores)

    query = ProviderInvoice.query
    if estado == 'PENDIENTE':
        query = query.filter(ProviderInvoice.estado.in_(['PENDIENTE', 'PARCIAL']))
    elif estado == 'PAGADO':
        query = query.filter_by(estado='PAGADO')
    if proveedor_filtro:
        query = query.filter_by(proveedor=proveedor_filtro)

    cuentas = query.order_by(ProviderInvoice.fecha_creacion.desc()).all()

    return render_template(
        'externos/cuentas_por_pagar.html',
        cuentas=cuentas,
        estado=estado,
        proveedor_filtro=proveedor_filtro,
        resumen_proveedores=resumen_proveedores,
        proveedores_registrados=obtener_proveedores_registrados(),
        total_pendiente=total_pendiente,
        total_pagado=total_pagado
    )


@externos_bp.route('/estado_cuenta/<path:proveedor>', methods=['GET'])
@login_required
def estado_cuenta_proveedor(proveedor):
    prov_clean = (proveedor or '').strip()
    facturas = ProviderInvoice.query.filter_by(proveedor=prov_clean).order_by(ProviderInvoice.fecha_creacion.desc()).all()
    abonos = ProviderPayment.query.filter_by(proveedor=prov_clean).order_by(ProviderPayment.fecha_abono.desc()).all()

    total_facturado = sum(float(f.monto_costo or 0) for f in facturas)
    # Sumar abonos registrados + facturas antiguas que se marcaron PAGADO sin abono asociado
    pagos_antiguos_sin_abono = sum(float(f.monto_costo or 0) for f in facturas if f.estado == 'PAGADO' and not f.abonos)
    total_abonado = sum(float(a.monto or 0) for a in abonos) + pagos_antiguos_sin_abono
    saldo_pendiente = max(0.0, total_facturado - total_abonado)

    facturas_pendientes = [f for f in facturas if f.estado != 'PAGADO']

    return render_template(
        'externos/estado_cuenta_proveedor.html',
        proveedor=prov_clean,
        facturas=facturas,
        facturas_pendientes=facturas_pendientes,
        abonos=abonos,
        total_facturado=total_facturado,
        total_abonado=total_abonado,
        saldo_pendiente=saldo_pendiente
    )


@externos_bp.route('/estado_cuenta/abonar', methods=['POST'])
@login_required
@admin_required
def registrar_abono_proveedor():
    proveedor = (request.form.get('proveedor') or '').strip()
    invoice_id_raw = (request.form.get('invoice_id') or '').strip()
    monto = safe_float((request.form.get('monto') or '0').replace(',', '').replace('.', ''), 0.0)
    metodo_pago = (request.form.get('metodo_pago') or 'transferencia').strip()
    observacion = (request.form.get('observacion') or '').strip() or None

    if not proveedor or monto <= 0:
        flash('Debes ingresar un monto de abono válido mayor a $0.', 'danger')
        return redirect(request.referrer or url_for('externos_bp.cuentas_por_pagar'))

    invoice_id = safe_int(invoice_id_raw, 0) if invoice_id_raw else None
    if invoice_id == 0:
        invoice_id = None

    comprobante_filename = None
    if 'comprobante' in request.files:
        file = request.files['comprobante']
        if file and file.filename != '':
            ext = os.path.splitext(secure_filename(file.filename))[1].lower() or '.jpg'
            comprobante_filename = f"abono_prov_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
            upload_folder = current_app.config['UPLOAD_FOLDER']
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, comprobante_filename))

    try:
        nuevo_abono = ProviderPayment(
            proveedor=proveedor,
            invoice_id=invoice_id,
            usuario_id=current_user.id,
            monto=Decimal(str(monto)),
            metodo_pago=metodo_pago,
            comprobante_imagen=comprobante_filename,
            observacion=observacion,
            fecha_abono=obtener_hora_bogota()
        )
        db.session.add(nuevo_abono)
        db.session.flush()

        _recalcular_facturas_proveedor(proveedor)
        db.session.commit()
        flash(f'✅ Abono de ${monto:,.0f} registrado exitosamente en el Estado de Cuenta de "{proveedor}".', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al registrar el abono: {str(e)}', 'danger')

    return redirect(url_for('externos_bp.estado_cuenta_proveedor', proveedor=proveedor))


@externos_bp.route('/cuentas_por_pagar/pagar/<int:id>', methods=['POST'])
@login_required
@admin_required
def pagar_cuenta_proveedor(id):
    cuenta = ProviderInvoice.query.get_or_404(id)
    if cuenta.estado == 'PAGADO':
        flash('Esta cuenta ya estaba marcada como pagada.', 'info')
        return redirect(request.referrer or url_for('externos_bp.cuentas_por_pagar'))

    saldo = cuenta.saldo_pendiente
    metodo_pago = (request.form.get('metodo_pago') or 'efectivo').strip()
    observacion = (request.form.get('observacion') or f'Pago total factura #{cuenta.id}').strip()

    comprobante_filename = None
    if 'comprobante' in request.files:
        file = request.files['comprobante']
        if file and file.filename != '':
            ext = os.path.splitext(secure_filename(file.filename))[1].lower() or '.jpg'
            comprobante_filename = f"pago_prov_{cuenta.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
            upload_folder = current_app.config['UPLOAD_FOLDER']
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, comprobante_filename))

    try:
        if saldo > 0:
            db.session.add(ProviderPayment(
                proveedor=cuenta.proveedor,
                invoice_id=cuenta.id,
                usuario_id=current_user.id,
                monto=Decimal(str(saldo)),
                metodo_pago=metodo_pago,
                comprobante_imagen=comprobante_filename,
                observacion=observacion,
                fecha_abono=obtener_hora_bogota()
            ))
        cuenta.estado = 'PAGADO'
        cuenta.fecha_pago = obtener_hora_bogota()
        db.session.commit()
        flash(f'✅ Pago al proveedor "{cuenta.proveedor}" por ${float(saldo or cuenta.monto_costo):,.0f} registrado en su Estado de Cuenta.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al registrar el pago: {str(e)}', 'danger')

    return redirect(request.referrer or url_for('externos_bp.cuentas_por_pagar'))
