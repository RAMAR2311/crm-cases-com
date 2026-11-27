import os
import re
import json
from io import BytesIO
from datetime import datetime
from decimal import Decimal
import pytz
import pandas as pd
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, send_file
from flask_login import login_required, current_user
from sqlalchemy import or_
from models import (
    db, Product, ProductVariant, Sale, SaleDetail, SalePayment,
    SaleClient, ArqueoCaja, Maneo, Retoma, PriceApproval, StockAdjustment
)
from decorators import admin_required
from utils import safe_int, safe_float, safe_decimal

celulares_bp = Blueprint('celulares_bp', __name__)
retomas_bp = Blueprint('retomas_bp', __name__)


def obtener_hora_bogota():
    return datetime.now(pytz.timezone('America/Bogota')).replace(tzinfo=None)


def _ruta_json_imagenes_ref():
    upload_folder = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_folder, exist_ok=True)
    return os.path.join(upload_folder, 'referencias_imagenes.json')


def cargar_mapa_imagenes_referencia():
    ruta = _ruta_json_imagenes_ref()
    if not os.path.exists(ruta):
        return {}
    try:
        with open(ruta, 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def guardar_imagen_en_mapa_referencia(marca, modelo_celular, color, filename):
    mapa = cargar_mapa_imagenes_referencia()
    m_key = (marca or '').strip().lower()
    mod_key = (modelo_celular or '').strip().lower()
    col_key = (color or '').strip().lower()

    if col_key and col_key != 'todos':
        key = f"{m_key}|{mod_key}|{col_key}"
        label = f"{marca} {modelo_celular} ({color})"
    else:
        key = f"{m_key}|{mod_key}"
        label = f"{marca} {modelo_celular} (Todos los colores)"

    mapa[key] = {
        'marca': marca,
        'modelo': modelo_celular,
        'color': color if (col_key and col_key != 'todos') else 'Todos',
        'label': label,
        'imagen': filename,
        'fecha': obtener_hora_bogota().strftime('%d/%m/%Y %H:%M')
    }
    try:
        with open(_ruta_json_imagenes_ref(), 'w', encoding='utf-8') as f:
            json.dump(mapa, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def obtener_imagen_por_referencia(marca, modelo_celular, color=None):
    """
    Busca automáticamente la imagen configurada para una Referencia (y Color opcional).
    1. Primero busca coincidencia exacta Marca + Referencia + Color en el catálogo de imágenes por referencia.
    2. Luego busca coincidencia general Marca + Referencia.
    3. Como respaldo, busca si ya existe algún celular de esa misma Referencia en la BD con foto asignada.
    """
    m_key = (marca or '').strip().lower()
    mod_key = (modelo_celular or '').strip().lower()
    col_key = (color or '').strip().lower()
    if not mod_key:
        return None

    mapa = cargar_mapa_imagenes_referencia()
    if col_key:
        entry_col = mapa.get(f"{m_key}|{mod_key}|{col_key}")
        if entry_col and entry_col.get('imagen'):
            return entry_col['imagen']

    entry_ref = mapa.get(f"{m_key}|{mod_key}")
    if entry_ref and entry_ref.get('imagen'):
        return entry_ref['imagen']

    # Respaldo: buscar en base de datos otro equipo de la misma referencia que ya tenga foto
    try:
        if col_key:
            existente_col = Product.query.filter(
                Product.modelo_celular.ilike(modelo_celular.strip()),
                Product.color.ilike(color.strip()),
                Product.imagen.isnot(None),
                Product.imagen != ''
            ).first()
            if existente_col:
                return existente_col.imagen
        existente_ref = Product.query.filter(
            Product.modelo_celular.ilike(modelo_celular.strip()),
            Product.imagen.isnot(None),
            Product.imagen != ''
        ).first()
        if existente_ref:
            return existente_ref.imagen
    except Exception:
        pass

    return None


def archivar_imei_existente_si_vendido(imei_limpio):
    """
    Reingreso de IMEI:
    Si un IMEI vendido anteriormente (cantidad_stock <= 0) vuelve a ingresar por retoma o recompra,
    archiva el IMEI anterior como '<IMEI>-OLD-1', '<IMEI>-OLD-2', etc. para permitir el nuevo ingreso
    sin romper la restricción UNIQUE de Product.imei y manteniendo toda la trazabilidad histórica.
    Retorna (True, None) si se puede proceder, o (False, 'Mensaje de error') si el IMEI ya está activo en stock.
    """
    if not imei_limpio:
        return True, None

    existente = Product.query.filter_by(imei=imei_limpio).first()
    if not existente:
        return True, None

    if (existente.cantidad_stock or 0) > 0:
        return False, f"El IMEI {imei_limpio} ya se encuentra activo en el inventario ({existente.nombre})."

    # Buscar el siguiente sufijo -OLD-N disponible
    n = 1
    while Product.query.filter_by(imei=f"{imei_limpio}-OLD-{n}").first():
        n += 1

    imei_archivado = f"{imei_limpio}-OLD-{n}"
    sku_archivado = f"{existente.sku}-OLD-{n}"[:50]
    existente.imei = imei_archivado
    if not Product.query.filter_by(sku=sku_archivado).first():
        existente.sku = sku_archivado

    db.session.flush()
    return True, imei_archivado


# ══════════════════════════════════════════════════════════════════════════
# 1.1 INVENTARIO DE CELULARES (/celulares/inventario)
# ══════════════════════════════════════════════════════════════════════════

@celulares_bp.route('/inventario')
@login_required
def inventario():
    q = request.args.get('q', '').strip()
    # Por defecto mostramos SOLO los Activos ('disponibles') para no confundir con vendidos o maneo
    estado_filtro = request.args.get('estado', 'disponibles').strip() or 'disponibles'

    query = Product.query.filter_by(tipo_inventario='celulares')

    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(
                Product.nombre.ilike(term),
                Product.sku.ilike(term),
                Product.imei.ilike(term),
                Product.imei2.ilike(term),
                Product.marca.ilike(term),
                Product.modelo_celular.ilike(term),
                Product.proveedor.ilike(term)
            )
        )

    todos_celulares = Product.query.filter_by(tipo_inventario='celulares').all()

    # KPIs y contadores en tiempo real por pestaña
    stock_activo = 0
    costo_total_invertido = Decimal('0.00')
    valor_proyectado_venta = Decimal('0.00')
    equipos_en_maneo = 0
    count_activos = 0
    count_vendidos = 0

    for c in todos_celulares:
        en_maneo = c.tiene_maneo_pendiente
        if en_maneo:
            equipos_en_maneo += 1
        st_total = c.total_stock
        if st_total > 0 and not en_maneo:
            count_activos += 1
        elif st_total <= 0 and not en_maneo:
            count_vendidos += 1

        if c.variantes:
            for v in c.variantes:
                st = int(v.cantidad_stock or 0)
                if st > 0:
                    stock_activo += st
                    costo_total_invertido += Decimal(str(v.precio_costo or c.precio_costo or 0)) * st
                    valor_proyectado_venta += Decimal(str(v.precio_sugerido or c.precio_sugerido or 0)) * st
        else:
            st = int(c.cantidad_stock or 0)
            if st > 0:
                stock_activo += st
                costo_total_invertido += Decimal(str(c.precio_costo or 0)) * st
                valor_proyectado_venta += Decimal(str(c.precio_sugerido or 0)) * st

    count_soporte = Retoma.query.filter_by(estado='EN_ARREGLO').count()
    count_retomas = Retoma.query.filter_by(estado='CUARENTENA').count()

    celulares = query.order_by(Product.cantidad_stock.desc(), Product.fecha_creacion.desc()).all()

    if estado_filtro == 'disponibles':
        celulares = [c for c in celulares if c.total_stock > 0 and not c.tiene_maneo_pendiente]
    elif estado_filtro == 'vendidos':
        celulares = [c for c in celulares if c.total_stock <= 0 and not c.tiene_maneo_pendiente]
    elif estado_filtro == 'maneo':
        celulares = [c for c in celulares if c.tiene_maneo_pendiente]
    # Si estado_filtro == 'todos', muestra todos

    mapa_imagenes_ref = list(cargar_mapa_imagenes_referencia().values())

    return render_template(
        'celulares/inventario.html',
        celulares=celulares,
        stock_activo=stock_activo,
        costo_total_invertido=costo_total_invertido,
        valor_proyectado_venta=valor_proyectado_venta,
        equipos_en_maneo=equipos_en_maneo,
        count_activos=count_activos,
        count_vendidos=count_vendidos,
        count_soporte=count_soporte,
        count_retomas=count_retomas,
        imagenes_referencia=mapa_imagenes_ref,
        q=q,
        estado_filtro=estado_filtro
    )


@celulares_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def nuevo_celular():
    if request.method == 'POST':
        imei = request.form.get('imei', '').strip()
        imei2 = request.form.get('imei2', '').strip() or None
        marca = request.form.get('marca', '').strip()
        modelo_celular = request.form.get('modelo_celular', '').strip()
        estado_celular = request.form.get('estado_celular', 'Nuevo').strip() or 'Nuevo'
        color = request.form.get('color', '').strip() or None
        bateria = request.form.get('bateria', '').strip() or None
        memoria = request.form.get('memoria', '').strip() or None
        proveedor = request.form.get('proveedor', '').strip() or None
        observacion = request.form.get('observacion', '').strip() or None

        precio_costo_str = request.form.get('precio_costo', '0').replace(',', '').replace('.', '')
        precio_minimo_str = request.form.get('precio_minimo', '0').replace(',', '').replace('.', '')
        precio_sugerido_str = request.form.get('precio_sugerido', '0').replace(',', '').replace('.', '')

        if not imei:
            flash('El número de IMEI principal es obligatorio para registrar un celular.', 'danger')
            return render_template('celulares/form_celular.html', celular=None)

        # Validar duplicado de IMEI
        ok_imei, msg_imei = archivar_imei_existente_si_vendido(imei)
        if not ok_imei:
            flash(msg_imei, 'danger')
            return render_template('celulares/form_celular.html', celular=None)

        partes_nombre = [p for p in [marca, modelo_celular, memoria, color] if p]
        nombre_completo = " ".join(partes_nombre) if partes_nombre else f"Celular {imei}"
        sku_generado = f"CEL-{imei}"[:50]
        if Product.query.filter_by(sku=sku_generado).first():
            sku_generado = f"CEL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{imei[-4:]}"[:50]

        imagen_filename = None
        if 'imagen' in request.files:
            file = request.files['imagen']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
                upload_folder = current_app.config['UPLOAD_FOLDER']
                os.makedirs(upload_folder, exist_ok=True)
                file.save(os.path.join(upload_folder, filename))
                imagen_filename = filename

        # Si no se subió foto manual, asignar automáticamente la imagen configurada para esa Referencia
        if not imagen_filename:
            imagen_filename = obtener_imagen_por_referencia(marca, modelo_celular, color)

        nuevo = Product(
            nombre=nombre_completo,
            sku=sku_generado,
            tipo_inventario='celulares',
            cantidad_stock=1,
            precio_costo=safe_float(precio_costo_str, 0.0),
            precio_minimo=safe_float(precio_minimo_str, 0.0),
            precio_sugerido=safe_float(precio_sugerido_str, 0.0),
            imei=imei,
            imei2=imei2,
            marca=marca,
            modelo_celular=modelo_celular,
            estado_celular=estado_celular,
            color=color,
            bateria=bateria,
            memoria=memoria,
            proveedor=proveedor,
            observacion=observacion,
            imagen=imagen_filename
        )

        try:
            db.session.add(nuevo)
            db.session.flush()

            ajuste = StockAdjustment(
                product_id=nuevo.id,
                admin_id=current_user.id,
                tipo_movimiento='Ingreso Celular Individual',
                motivo=f"IMEI: {imei} | Proveedor: {proveedor or 'N/A'}",
                stock_anterior=0,
                stock_nuevo=1
            )
            db.session.add(ajuste)
            db.session.commit()
            flash(f'Celular "{nombre_completo}" (IMEI: {imei}) registrado exitosamente.', 'success')
            return redirect(url_for('celulares_bp.inventario'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al registrar el celular: {str(e)}', 'danger')

    return render_template('celulares/form_celular.html', celular=None)


@celulares_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_celular(id):
    celular = Product.query.get_or_404(id)
    if celular.tipo_inventario != 'celulares':
        flash('El producto seleccionado no pertenece al inventario de celulares.', 'danger')
        return redirect(url_for('celulares_bp.inventario'))

    if request.method == 'POST':
        nuevo_imei = request.form.get('imei', '').strip()
        if nuevo_imei and nuevo_imei != celular.imei:
            otro = Product.query.filter(Product.imei == nuevo_imei, Product.id != celular.id).first()
            if otro:
                flash(f'El IMEI {nuevo_imei} ya está asignado a otro equipo ({otro.nombre}).', 'danger')
                return render_template('celulares/form_celular.html', celular=celular)
            celular.imei = nuevo_imei

        celular.imei2 = request.form.get('imei2', '').strip() or None
        celular.marca = request.form.get('marca', '').strip()
        celular.modelo_celular = request.form.get('modelo_celular', '').strip()
        celular.estado_celular = request.form.get('estado_celular', celular.estado_celular or 'Nuevo').strip()
        celular.color = request.form.get('color', '').strip() or None
        celular.bateria = request.form.get('bateria', '').strip() or None
        celular.memoria = request.form.get('memoria', '').strip() or None
        celular.proveedor = request.form.get('proveedor', '').strip() or None
        celular.observacion = request.form.get('observacion', '').strip() or None

        partes_nombre = [p for p in [celular.marca, celular.modelo_celular, celular.memoria, celular.color] if p]
        if partes_nombre:
            celular.nombre = " ".join(partes_nombre)

        precio_costo_str = request.form.get('precio_costo', '0').replace(',', '').replace('.', '')
        precio_minimo_str = request.form.get('precio_minimo', '0').replace(',', '').replace('.', '')
        precio_sugerido_str = request.form.get('precio_sugerido', '0').replace(',', '').replace('.', '')

        celular.precio_costo = safe_float(precio_costo_str, float(celular.precio_costo or 0))
        celular.precio_minimo = safe_float(precio_minimo_str, float(celular.precio_minimo or 0))
        celular.precio_sugerido = safe_float(precio_sugerido_str, float(celular.precio_sugerido or 0))

        if 'imagen' in request.files:
            file = request.files['imagen']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
                upload_folder = current_app.config['UPLOAD_FOLDER']
                os.makedirs(upload_folder, exist_ok=True)
                file.save(os.path.join(upload_folder, filename))
                celular.imagen = filename

        try:
            db.session.commit()
            flash(f'Equipo "{celular.nombre}" actualizado exitosamente.', 'success')
            return redirect(url_for('celulares_bp.inventario'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar: {str(e)}', 'danger')

    return render_template('celulares/form_celular.html', celular=celular)


@celulares_bp.route('/api/precios-actuales', methods=['GET'])
@login_required
def api_precios_actuales_referencia():
    marca = (request.args.get('marca') or '').strip()
    modelo = (request.args.get('modelo') or '').strip()
    memoria = (request.args.get('memoria') or '').strip()
    color = (request.args.get('color') or '').strip()
    estado = (request.args.get('estado') or 'Nuevo').strip()

    if not modelo:
        return jsonify({'found': False, 'count': 0})

    query = Product.query.filter(
        Product.tipo_inventario == 'celulares',
        Product.cantidad_stock > 0
    )
    if marca:
        query = query.filter(or_(Product.marca.ilike(marca), Product.nombre.ilike(f"%{marca}%")))

    query = query.filter(or_(Product.modelo_celular.ilike(modelo), Product.nombre.ilike(f"%{modelo}%")))

    if memoria and memoria.upper() != 'TODAS':
        query = query.filter(or_(Product.memoria.ilike(memoria), Product.nombre.ilike(f"%{memoria}%")))

    if color and color.upper() != 'TODOS':
        query = query.filter(or_(Product.color.ilike(color), Product.nombre.ilike(f"%{color}%")))

    if estado == 'Nuevo':
        query = query.filter(Product.estado_celular.ilike('Nuevo'))
    elif estado == 'Usado_Sin_Retoma':
        query = query.filter(
            Product.estado_celular.ilike('Usado'),
            ~Product.sku.ilike('RET-%'),
            or_(Product.proveedor.is_(None), ~Product.proveedor.ilike('%Retoma%'))
        )
    elif estado == 'Retomas_Sin_Arreglo':
        query = query.filter(
            or_(Product.sku.ilike('RET-%'), Product.proveedor.ilike('%Retoma%')),
            or_(Product.observacion.is_(None), ~Product.observacion.ilike('%[ARREGLADO%'))
        )
    elif estado == 'Retomas_Con_Arreglo':
        query = query.filter(
            or_(Product.sku.ilike('RET-%'), Product.proveedor.ilike('%Retoma%')),
            Product.observacion.ilike('%[ARREGLADO%')
        )
    elif estado == 'Retomas_Todas':
        query = query.filter(
            or_(Product.sku.ilike('RET-%'), Product.proveedor.ilike('%Retoma%'))
        )
    elif estado == 'Usado':
        query = query.filter(Product.estado_celular.ilike('Usado'))

    equipos = query.order_by(Product.id.desc()).all()
    if not equipos:
        return jsonify({'found': False, 'count': 0})

    primero = equipos[0]
    return jsonify({
        'found': True,
        'count': len(equipos),
        'ejemplo_nombre': primero.nombre,
        'precio_costo': int(float(primero.precio_costo or 0)),
        'precio_minimo': int(float(primero.precio_minimo or 0)),
        'precio_sugerido': int(float(primero.precio_sugerido or 0))
    })


@celulares_bp.route('/cambio-precio-masivo', methods=['POST'])
@login_required
@admin_required
def cambio_precio_masivo():
    marca = (request.form.get('marca') or '').strip()
    modelo = (request.form.get('modelo_celular') or '').strip()
    memoria = (request.form.get('memoria') or '').strip()
    color = (request.form.get('color') or '').strip()
    estado_filtro = (request.form.get('estado_celular') or 'Nuevo').strip()

    nuevo_costo_str = (request.form.get('nuevo_precio_costo') or '').replace(',', '').replace('.', '').strip()
    nuevo_min_str = (request.form.get('nuevo_precio_minimo') or '').replace(',', '').replace('.', '').strip()
    nuevo_sug_str = (request.form.get('nuevo_precio_sugerido') or '').replace(',', '').replace('.', '').strip()

    if not modelo:
        flash('Debes seleccionar la Referencia / Modelo para aplicar el cambio de precio masivo.', 'warning')
        return redirect(url_for('celulares_bp.inventario'))

    nuevo_sug = safe_float(nuevo_sug_str, 0.0) if nuevo_sug_str else 0.0
    nuevo_min = safe_float(nuevo_min_str, 0.0) if nuevo_min_str else 0.0
    nuevo_costo = safe_float(nuevo_costo_str, 0.0) if nuevo_costo_str else 0.0

    if nuevo_sug <= 0 and nuevo_min <= 0 and nuevo_costo <= 0:
        flash('Ingresa al menos un nuevo precio (Sugerido, Mínimo o Costo) mayor a $0.', 'danger')
        return redirect(url_for('celulares_bp.inventario'))

    query = Product.query.filter(
        Product.tipo_inventario == 'celulares',
        Product.cantidad_stock > 0
    )

    if marca:
        query = query.filter(
            or_(
                Product.marca.ilike(marca),
                Product.nombre.ilike(f"%{marca}%")
            )
        )

    query = query.filter(
        or_(
            Product.modelo_celular.ilike(modelo),
            Product.nombre.ilike(f"%{modelo}%")
        )
    )

    if memoria and memoria.upper() != 'TODAS':
        query = query.filter(
            or_(
                Product.memoria.ilike(memoria),
                Product.nombre.ilike(f"%{memoria}%")
            )
        )

    if color and color.upper() != 'TODOS':
        query = query.filter(
            or_(
                Product.color.ilike(color),
                Product.nombre.ilike(f"%{color}%")
            )
        )

    if estado_filtro == 'Nuevo':
        query = query.filter(Product.estado_celular.ilike('Nuevo'))
    elif estado_filtro == 'Usado_Sin_Retoma':
        query = query.filter(
            Product.estado_celular.ilike('Usado'),
            ~Product.sku.ilike('RET-%'),
            or_(Product.proveedor.is_(None), ~Product.proveedor.ilike('%Retoma%'))
        )
    elif estado_filtro == 'Retomas_Sin_Arreglo':
        query = query.filter(
            or_(Product.sku.ilike('RET-%'), Product.proveedor.ilike('%Retoma%')),
            or_(Product.observacion.is_(None), ~Product.observacion.ilike('%[ARREGLADO%'))
        )
    elif estado_filtro == 'Retomas_Con_Arreglo':
        query = query.filter(
            or_(Product.sku.ilike('RET-%'), Product.proveedor.ilike('%Retoma%')),
            Product.observacion.ilike('%[ARREGLADO%')
        )
    elif estado_filtro == 'Retomas_Todas':
        query = query.filter(
            or_(Product.sku.ilike('RET-%'), Product.proveedor.ilike('%Retoma%'))
        )
    elif estado_filtro == 'Usado':
        query = query.filter(Product.estado_celular.ilike('Usado'))

    equipos = query.all()
    if not equipos:
        cap_txt = f" ({memoria})" if (memoria and memoria.upper() != 'TODAS') else ""
        col_txt = f" [{color}]" if (color and color.upper() != 'TODOS') else ""
        flash(f'No se encontraron equipos activos en stock para "{marca} {modelo}{cap_txt}{col_txt}".', 'warning')
        return redirect(url_for('celulares_bp.inventario'))

    try:
        for eq in equipos:
            if nuevo_costo > 0:
                eq.precio_costo = nuevo_costo
            if nuevo_min > 0:
                eq.precio_minimo = nuevo_min
            if nuevo_sug > 0:
                eq.precio_sugerido = nuevo_sug
        db.session.commit()
        cap_txt = f" · {memoria}" if (memoria and memoria.upper() != 'TODAS') else ""
        col_txt = f" · Color: {color}" if (color and color.upper() != 'TODOS') else ""
        flash(f'✅ Cambio de precio masivo aplicado a {len(equipos)} equipo(s) de "{marca} {modelo}{cap_txt}{col_txt}".', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al actualizar precios masivamente: {str(e)}', 'danger')

    return redirect(url_for('celulares_bp.inventario'))


@celulares_bp.route('/imagen-por-referencia', methods=['POST'])
@login_required
@admin_required
def asignar_imagen_por_referencia():
    marca = (request.form.get('marca') or '').strip()
    modelo = (request.form.get('modelo_celular') or '').strip()
    color = (request.form.get('color') or '').strip()

    if not modelo:
        flash('Debes seleccionar la Referencia / Modelo para asignar la imagen.', 'warning')
        return redirect(url_for('celulares_bp.inventario'))

    if 'imagen' not in request.files or not request.files['imagen'].filename:
        flash('Debes seleccionar un archivo de imagen.', 'danger')
        return redirect(url_for('celulares_bp.inventario'))

    file = request.files['imagen']
    filename = secure_filename(file.filename)
    filename = f"ref_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
    upload_folder = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_folder, exist_ok=True)
    file.save(os.path.join(upload_folder, filename))

    guardar_imagen_en_mapa_referencia(marca, modelo, color, filename)

    query = Product.query.filter(
        Product.tipo_inventario.in_(['celulares', 'externos']),
        or_(
            Product.modelo_celular.ilike(modelo),
            Product.nombre.ilike(f"%{modelo}%")
        )
    )
    if marca:
        query = query.filter(
            or_(
                Product.marca.ilike(marca),
                Product.nombre.ilike(f"%{marca}%")
            )
        )
    if color and color.upper() != 'TODOS':
        query = query.filter(
            or_(
                Product.color.ilike(color),
                Product.nombre.ilike(f"%{color}%")
            )
        )

    equipos_existentes = query.all()
    for eq in equipos_existentes:
        eq.imagen = filename

    db.session.commit()
    col_txt = f" ({color})" if (color and color.upper() != 'TODOS') else " (Todos los colores)"
    flash(
        f'✅ Imagen configurada para "{marca} {modelo}{col_txt}". '
        f'Se actualizó en {len(equipos_existentes)} equipo(s) actual(es) y se asignará automáticamente a los próximos que ingresen.',
        'success'
    )
    return redirect(url_for('celulares_bp.inventario'))


@celulares_bp.route('/plantilla-excel')
@login_required
@admin_required
def descargar_plantilla_celulares():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Carga_Celulares_IMEI"

    headers = [
        'imei', 'imei2', 'marca', 'modelo_celular', 'estado_celular',
        'memoria', 'color', 'bateria', 'proveedor',
        'precio_costo', 'precio_minimo', 'precio_sugerido', 'observacion'
    ]
    ws.append(headers)

    # Estilizar encabezados
    header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=10)
    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Filas de ejemplo con formato estandarizado
    ejemplos = [
        [
            '358901234567890', '', 'Apple', 'iPhone 17 Pro Max', 'Nuevo',
            '256GB', 'Naranja', '100%', 'Apple Distribución Mayorista',
            5200000, 5600000, 5950000, 'Caja sellada garantía 1 año'
        ],
        [
            '359988776655443', '359988776655444', 'Samsung', 'Galaxy S25 Ultra', 'Usado',
            '12GB / 256GB', 'Negro', '95%', 'Samsung Partner Colombia',
            3800000, 4150000, 4450000, 'Impecable con caja'
        ]
    ]
    for row_data in ejemplos:
        ws.append(row_data)

    # Ajustar anchos de columna
    col_widths = [18, 18, 14, 24, 15, 16, 22, 10, 26, 15, 15, 16, 28]
    for i, w in enumerate(col_widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w

    # Crear hoja de catálogo oficial para alimentar los desplegables de Excel
    ws_cat = wb.create_sheet(title="Catalogo_Oficial")
    ws_cat.append(["MARCAS", "REFERENCIAS_OFICIALES", "CAPACIDADES_OFICIALES", "COLORES_OFICIALES", "ESTADOS"])

    marcas_oficiales = ["Apple", "Samsung"]
    referencias_oficiales = [
        # Apple (hasta 18 Pro Max)
        "iPhone 18 Pro Max", "iPhone 18 Pro", "iPhone 18 Air", "iPhone 18 Plus", "iPhone 18",
        "iPhone 17 Pro Max", "iPhone 17 Pro", "iPhone 17 Air", "iPhone 17 Plus", "iPhone 17",
        "iPhone 16 Pro Max", "iPhone 16 Pro", "iPhone 16 Plus", "iPhone 16", "iPhone 16e",
        "iPhone 15 Pro Max", "iPhone 15 Pro", "iPhone 15 Plus", "iPhone 15",
        "iPhone 14 Pro Max", "iPhone 14 Pro", "iPhone 14 Plus", "iPhone 14",
        "iPhone 13 Pro Max", "iPhone 13 Pro", "iPhone 13", "iPhone 13 Mini",
        "iPhone 12 Pro Max", "iPhone 12 Pro", "iPhone 12", "iPhone 12 Mini",
        "iPhone 11 Pro Max", "iPhone 11 Pro", "iPhone 11", "iPhone SE (3ra Gen)",
        "iPad Pro", "iPad Air", "iPad (10ma / 9na Gen)",
        "Apple Watch Ultra 2 / Ultra", "Apple Watch Series 10 / 9 / SE",
        # Samsung
        "Galaxy S26 Ultra", "Galaxy S26+", "Galaxy S26",
        "Galaxy S25 Ultra", "Galaxy S25+", "Galaxy S25", "Galaxy S25 Edge",
        "Galaxy S24 Ultra", "Galaxy S24+", "Galaxy S24", "Galaxy S24 FE",
        "Galaxy S23 Ultra", "Galaxy S23+", "Galaxy S23", "Galaxy S23 FE",
        "Galaxy S22 Ultra", "Galaxy S22+ / S22",
        "Galaxy Z Fold 7", "Galaxy Z Fold 6", "Galaxy Z Fold 5",
        "Galaxy Z Flip 7", "Galaxy Z Flip 6", "Galaxy Z Flip 5",
        "Galaxy A56 5G", "Galaxy A55 5G", "Galaxy A54 5G",
        "Galaxy A36 5G", "Galaxy A35 5G", "Galaxy A34 5G",
        "Galaxy A26 5G", "Galaxy A25 5G", "Galaxy A16 5G / A16", "Galaxy A15 5G / A15", "Galaxy A06", "Galaxy A05s / A05"
    ]
    capacidades_oficiales = [
        "64GB", "128GB", "256GB", "512GB", "1TB", "2TB",
        "4GB / 64GB", "4GB / 128GB", "6GB / 128GB",
        "8GB / 128GB", "8GB / 256GB", "12GB / 128GB", "12GB / 256GB", "12GB / 512GB", "12GB / 1TB", "16GB / 1TB",
        "40mm", "41mm", "42mm", "44mm", "45mm", "46mm", "49mm GPS + Cellular"
    ]
    colores_oficiales = [
        "Negro", "Blanco", "Plata", "Natural", "Desierto", "Azul", "Naranja",
        "Oro", "Grafito", "Gris", "Morado", "Rosa", "Verde", "Amarillo", "Rojo",
        "Lavanda", "Menta", "Crema", "Violeta", "Vino", "Bronce", "Azul Oscuro", "Azul Claro"
    ]
    estados_oficiales = ["Nuevo", "Usado"]

    max_len = max(len(marcas_oficiales), len(referencias_oficiales), len(capacidades_oficiales), len(colores_oficiales), len(estados_oficiales))
    for i in range(max_len):
        ws_cat.append([
            marcas_oficiales[i] if i < len(marcas_oficiales) else None,
            referencias_oficiales[i] if i < len(referencias_oficiales) else None,
            capacidades_oficiales[i] if i < len(capacidades_oficiales) else None,
            colores_oficiales[i] if i < len(colores_oficiales) else None,
            estados_oficiales[i] if i < len(estados_oficiales) else None,
        ])

    # Validaciones Desplegables (DataValidation) en las filas 2 a 500
    dv_marca = DataValidation(type="list", formula1=f"='Catalogo_Oficial'!$A$2:$A${len(marcas_oficiales)+1}", allow_blank=True)
    dv_modelo = DataValidation(type="list", formula1=f"='Catalogo_Oficial'!$B$2:$B${len(referencias_oficiales)+1}", allow_blank=True)
    dv_estado = DataValidation(type="list", formula1=f"='Catalogo_Oficial'!$E$2:$E${len(estados_oficiales)+1}", allow_blank=True)
    dv_memoria = DataValidation(type="list", formula1=f"='Catalogo_Oficial'!$C$2:$C${len(capacidades_oficiales)+1}", allow_blank=True)
    dv_color = DataValidation(type="list", formula1=f"='Catalogo_Oficial'!$D$2:$D${len(colores_oficiales)+1}", allow_blank=True)

    ws.add_data_validation(dv_marca)
    ws.add_data_validation(dv_modelo)
    ws.add_data_validation(dv_estado)
    ws.add_data_validation(dv_memoria)
    ws.add_data_validation(dv_color)

    dv_marca.add("C2:C500")
    dv_modelo.add("D2:D500")
    dv_estado.add("E2:E500")
    dv_memoria.add("F2:F500")
    dv_color.add("G2:G500")

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output, download_name="plantilla_celulares_imei.xlsx", as_attachment=True)


@celulares_bp.route('/importar-excel', methods=['POST'])
@login_required
@admin_required
def importar_celulares_excel():
    if 'archivo' not in request.files:
        flash('No se seleccionó ningún archivo Excel.', 'danger')
        return redirect(url_for('celulares_bp.inventario'))

    archivo = request.files['archivo']
    if not archivo or archivo.filename == '':
        flash('Debes seleccionar un archivo válido.', 'danger')
        return redirect(url_for('celulares_bp.inventario'))

    if not (archivo.filename.endswith('.xlsx') or archivo.filename.endswith('.csv')):
        flash('Formato inválido. Sube un archivo .xlsx o .csv.', 'warning')
        return redirect(url_for('celulares_bp.inventario'))

    try:
        if archivo.filename.endswith('.csv'):
            df = pd.read_csv(archivo, dtype=str)
        else:
            df = pd.read_excel(archivo, dtype=str)

        df.columns = [str(c).strip().lower() for c in df.columns]
        required = ['imei', 'marca', 'modelo_celular', 'precio_costo', 'precio_minimo', 'precio_sugerido']
        missing = [c for c in required if c not in df.columns]
        if missing:
            flash(f"Faltan columnas obligatorias en el Excel: {', '.join(missing)}", 'danger')
            return redirect(url_for('celulares_bp.inventario'))

        creados = 0
        omitidos_duplicados = []
        imeis_en_archivo = set()

        for idx, row in df.iterrows():
            imei_val = str(row.get('imei', '') or '').strip()
            if not imei_val or imei_val.lower() == 'nan':
                continue
            if imei_val.endswith('.0'):
                imei_val = imei_val[:-2]

            if imei_val in imeis_en_archivo:
                omitidos_duplicados.append(f"{imei_val} (repetido en el archivo)")
                continue
            imeis_en_archivo.add(imei_val)

            ok_imei, msg_imei = archivar_imei_existente_si_vendido(imei_val)
            if not ok_imei:
                omitidos_duplicados.append(imei_val)
                continue

            def clean_str(key, default=''):
                v = str(row.get(key, '') or '').strip()
                return default if (not v or v.lower() == 'nan') else v

            marca = clean_str('marca', 'Genérico')
            modelo = clean_str('modelo_celular', 'Smartphone')
            estado = clean_str('estado_celular', 'Nuevo').capitalize()
            if estado not in ['Nuevo', 'Usado']:
                estado = 'Nuevo'
            memoria = clean_str('memoria') or None
            color = clean_str('color') or None
            bateria = clean_str('bateria') or None
            proveedor = clean_str('proveedor') or None
            imei2 = clean_str('imei2') or None
            if imei2 and imei2.endswith('.0'):
                imei2 = imei2[:-2]
            obs = clean_str('observacion') or None

            costo = safe_float(clean_str('precio_costo', '0').replace(',', ''), 0.0)
            minimo = safe_float(clean_str('precio_minimo', '0').replace(',', ''), 0.0)
            sugerido = safe_float(clean_str('precio_sugerido', '0').replace(',', ''), 0.0)

            partes_nombre = [p for p in [marca, modelo, memoria, color] if p]
            nombre_completo = " ".join(partes_nombre)
            sku_gen = f"CEL-{imei_val}"[:50]
            if Product.query.filter_by(sku=sku_gen).first():
                sku_gen = f"CEL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{idx}"[:50]

            imagen_auto = obtener_imagen_por_referencia(marca, modelo, color)

            nuevo = Product(
                nombre=nombre_completo,
                sku=sku_gen,
                tipo_inventario='celulares',
                cantidad_stock=1,
                precio_costo=costo,
                precio_minimo=minimo,
                precio_sugerido=sugerido,
                imei=imei_val,
                imei2=imei2,
                marca=marca,
                modelo_celular=modelo,
                estado_celular=estado,
                memoria=memoria,
                color=color,
                bateria=bateria,
                proveedor=proveedor,
                observacion=obs,
                imagen=imagen_auto
            )
            db.session.add(nuevo)
            db.session.flush()

            db.session.add(StockAdjustment(
                product_id=nuevo.id,
                admin_id=current_user.id,
                tipo_movimiento='Carga Masiva Excel (Celulares)',
                motivo=f"IMEI: {imei_val} | Proveedor: {proveedor or 'N/A'}",
                stock_anterior=0,
                stock_nuevo=1
            ))
            creados += 1

        db.session.commit()
        msg = f'Carga masiva completada: {creados} celulares registrados.'
        if omitidos_duplicados:
            msg += f' Se omitieron {len(omitidos_duplicados)} IMEIs duplicados activos: {", ".join(omitidos_duplicados[:5])}.'
            flash(msg, 'warning')
        else:
            flash(msg, 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error procesando archivo Excel: {str(e)}', 'danger')

    return redirect(url_for('celulares_bp.inventario'))


@celulares_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_celular(id):
    celular = Product.query.get_or_404(id)
    if celular.tipo_inventario != 'celulares':
        flash('El registro no corresponde a un celular.', 'danger')
        return redirect(url_for('celulares_bp.inventario'))

    if celular.detalles_venta:
        flash('Acción bloqueada: No se puede eliminar este celular porque ya tiene historial de venta registrado.', 'danger')
        return redirect(url_for('celulares_bp.inventario'))

    if celular.maneos:
        flash('Acción bloqueada: El equipo tiene registros en Maneos (Préstamos).', 'warning')
        return redirect(url_for('celulares_bp.inventario'))

    try:
        for aj in celular.ajustes_stock:
            db.session.delete(aj)
        db.session.delete(celular)
        db.session.commit()
        flash('Celular eliminado del inventario exitosamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar el celular: {str(e)}', 'danger')

    return redirect(url_for('celulares_bp.inventario'))


# Compatibilidad con rutas heredadas de variantes de IMEI si existían modelos agrupados
@celulares_bp.route('/gestionar_imeis/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def gestionar_imeis(id):
    return redirect(url_for('celulares_bp.editar_celular', id=id))


# ══════════════════════════════════════════════════════════════════════════
# 1.3 RETOMAS & ARREGLOS (/retomas/cuarentena, /retomas/arreglos, /retomas/aprobar/<id>)
# ══════════════════════════════════════════════════════════════════════════

@retomas_bp.route('/cuarentena', methods=['GET', 'POST'])
@login_required
def cuarentena():
    if request.method == 'POST':
        # Registro manual de una retoma (además de Caja)
        imei = request.form.get('imei', '').strip()
        imei2 = request.form.get('imei2', '').strip() or None
        marca = request.form.get('marca', '').strip()
        modelo_celular = request.form.get('modelo_celular', '').strip()
        color = request.form.get('color', '').strip() or None
        memoria = request.form.get('memoria', '').strip() or None
        bateria = request.form.get('bateria', '').strip() or None
        valor_recibido = safe_float(request.form.get('valor_recibido', '0').replace(',', '').replace('.', ''), 0.0)
        cliente_nombre = request.form.get('cliente_nombre', '').strip() or None
        cliente_documento = request.form.get('cliente_documento', '').strip() or None
        cliente_telefono = request.form.get('cliente_telefono', '').strip() or None
        observaciones = request.form.get('observaciones', '').strip() or None

        if not imei or not marca or not modelo_celular:
            flash('IMEI 1, Marca y Referencia son obligatorios para registrar una retoma.', 'danger')
            return redirect(url_for('retomas_bp.cuarentena'))

        # Verificar que no haya otra retoma pendiente o en arreglo con el mismo IMEI
        pendiente_existente = Retoma.query.filter(
            Retoma.imei == imei,
            Retoma.estado.in_(['CUARENTENA', 'EN_ARREGLO'])
        ).first()
        if pendiente_existente:
            flash(f'El IMEI {imei} ya se encuentra registrado en Retomas / Arreglos pendientes.', 'warning')
            return redirect(url_for('retomas_bp.cuarentena'))

        nueva_retoma = Retoma(
            usuario_recibe_id=current_user.id,
            imei=imei,
            imei2=imei2,
            marca=marca,
            modelo_celular=modelo_celular,
            color=color,
            memoria=memoria,
            bateria=bateria,
            valor_recibido=valor_recibido,
            cliente_nombre=cliente_nombre,
            cliente_documento=cliente_documento,
            cliente_telefono=cliente_telefono,
            observaciones=observaciones,
            estado='CUARENTENA'
        )
        try:
            db.session.add(nueva_retoma)
            db.session.commit()
            flash(f'Retoma {marca} {modelo_celular} (IMEI 1: {imei}) registrada exitosamente.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error al registrar retoma: {str(e)}', 'danger')

        return redirect(url_for('retomas_bp.cuarentena'))

    estado_filtro = request.args.get('estado', 'CUARENTENA').strip()
    query = Retoma.query
    if estado_filtro and estado_filtro != 'TODAS':
        query = query.filter_by(estado=estado_filtro)

    retomas = query.order_by(Retoma.fecha_recepcion.desc()).all()
    total_cuarentena = Retoma.query.filter_by(estado='CUARENTENA').count()
    valor_cuarentena = sum(float(r.valor_recibido or 0) for r in Retoma.query.filter_by(estado='CUARENTENA').all())
    total_arreglos = Retoma.query.filter_by(estado='EN_ARREGLO').count()
    total_aprobadas = Retoma.query.filter_by(estado='APROBADA').count()

    return render_template(
        'celulares/retomas_cuarentena.html',
        retomas=retomas,
        estado_filtro=estado_filtro,
        total_cuarentena=total_cuarentena,
        valor_cuarentena=valor_cuarentena,
        total_arreglos=total_arreglos,
        total_aprobadas=total_aprobadas
    )


@retomas_bp.route('/enviar-arreglo/<int:id>', methods=['POST'])
@login_required
def enviar_a_arreglo(id):
    retoma = Retoma.query.get_or_404(id)
    if retoma.estado not in ['CUARENTENA', 'EN_ARREGLO']:
        flash('Esta retoma ya fue ingresada al inventario.', 'warning')
        return redirect(url_for('retomas_bp.cuarentena'))

    motivo_arreglo = (request.form.get('observacion_arreglo') or '').strip()
    if not motivo_arreglo:
        flash('Debes escribir la observación o detalle de lo que se va a mandar a arreglar.', 'warning')
        return redirect(url_for('retomas_bp.cuarentena'))

    retoma.observaciones = motivo_arreglo
    retoma.estado = 'EN_ARREGLO'
    try:
        db.session.commit()
        flash(f'🔧 Equipo "{retoma.marca} {retoma.modelo_celular}" (IMEI: {retoma.imei}) enviado al módulo de Arreglos.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al enviar a arreglos: {str(e)}', 'danger')

    return redirect(url_for('retomas_bp.arreglos'))


@retomas_bp.route('/arreglos', methods=['GET'])
@login_required
def arreglos():
    equipos_arreglo = Retoma.query.filter_by(estado='EN_ARREGLO').order_by(Retoma.fecha_recepcion.desc()).all()
    total_en_arreglo = len(equipos_arreglo)
    valor_base_arreglos = sum(float(r.valor_recibido or 0) for r in equipos_arreglo)
    total_pendientes_retoma = Retoma.query.filter_by(estado='CUARENTENA').count()

    return render_template(
        'celulares/arreglos.html',
        arreglos=equipos_arreglo,
        total_en_arreglo=total_en_arreglo,
        valor_base_arreglos=valor_base_arreglos,
        total_pendientes_retoma=total_pendientes_retoma
    )


@retomas_bp.route('/contrato/<int:id>', methods=['GET'])
@login_required
def contrato_compraventa(id):
    retoma = Retoma.query.get_or_404(id)
    venta = Sale.query.get(retoma.sale_id) if retoma.sale_id else None
    cliente_venta = venta.cliente if (venta and venta.cliente) else None

    nombre_vendedor_equipo = retoma.cliente_nombre or (cliente_venta.nombre if cliente_venta else '')
    documento_vendedor_equipo = retoma.cliente_documento or (cliente_venta.documento if cliente_venta else '')
    telefono_vendedor_equipo = retoma.cliente_telefono or (cliente_venta.telefono if cliente_venta else '')

    fecha_doc = retoma.fecha_recepcion or (venta.fecha_venta if venta else obtener_hora_bogota())
    meses_es = [
        '', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
        'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
    ]
    dia_firma = fecha_doc.day
    mes_firma = meses_es[fecha_doc.month] if 1 <= fecha_doc.month <= 12 else str(fecha_doc.month)
    anio_firma = fecha_doc.year

    return render_template(
        'celulares/contrato_retoma.html',
        retoma=retoma,
        venta=venta,
        nombre_vendedor_equipo=nombre_vendedor_equipo,
        documento_vendedor_equipo=documento_vendedor_equipo,
        telefono_vendedor_equipo=telefono_vendedor_equipo,
        dia_firma=dia_firma,
        mes_firma=mes_firma,
        anio_firma=anio_firma
    )


@retomas_bp.route('/contrato_por_venta/<int:sale_id>', methods=['GET'])
@login_required
def contrato_por_venta(sale_id):
    retoma = Retoma.query.filter_by(sale_id=sale_id).order_by(Retoma.id.desc()).first_or_404()
    return redirect(url_for('retomas_bp.contrato_compraventa', id=retoma.id))


@retomas_bp.route('/aprobar/<int:id>', methods=['POST'])
@login_required
@admin_required
def aprobar_retoma(id):
    retoma = Retoma.query.get_or_404(id)
    if retoma.estado not in ['CUARENTENA', 'EN_ARREGLO']:
        flash('Esta retoma ya fue ingresada a inventario anteriormente.', 'warning')
        return redirect(url_for('retomas_bp.cuarentena'))

    venia_de_arreglo = (retoma.estado == 'EN_ARREGLO') or (request.form.get('origen') == 'arreglos')
    valor_base = float(retoma.valor_recibido or 0)
    sobrecosto_arreglo = safe_float(
        (request.form.get('sobrecosto_arreglo') or '0').replace(',', '').replace('.', ''),
        0.0
    )

    # Si viene con sobrecosto de arreglo, sumamos valor_base + sobrecosto_arreglo; o tomamos costo_real si lo enviaron directo
    costo_real_form = (request.form.get('costo_real') or '').replace(',', '').replace('.', '').strip()
    if sobrecosto_arreglo > 0:
        costo_real = valor_base + sobrecosto_arreglo
    elif costo_real_form:
        costo_real = safe_float(costo_real_form, valor_base)
    else:
        costo_real = valor_base

    precio_minimo = safe_float(
        (request.form.get('precio_minimo') or '').replace(',', '').replace('.', ''),
        0.0
    )
    precio_sugerido = safe_float(
        (request.form.get('precio_sugerido') or '').replace(',', '').replace('.', ''),
        0.0
    )
    bateria_nueva = (request.form.get('bateria') or '').strip()
    if bateria_nueva:
        retoma.bateria = bateria_nueva

    obs_input = (request.form.get('observaciones') or '').strip() or (retoma.observaciones or '')
    if venia_de_arreglo:
        etiqueta_arr = f"[ARREGLADO +${int(sobrecosto_arreglo):,}]".replace(',', '.') if sobrecosto_arreglo > 0 else "[ARREGLADO]"
        if "[ARREGLADO" not in obs_input:
            observaciones = f"{etiqueta_arr} {obs_input}".strip()
        else:
            observaciones = obs_input
    else:
        observaciones = obs_input or "Retoma ingresada directamente a inventario"

    destino_retorno = url_for('retomas_bp.arreglos') if venia_de_arreglo else url_for('retomas_bp.cuarentena')

    if precio_sugerido <= 0 or precio_minimo <= 0:
        flash('Debes ingresar el Precio Mínimo y el Precio Sugerido de venta para ingresar el equipo al inventario.', 'danger')
        return redirect(destino_retorno)

    try:
        # Reingreso de IMEI: Si fue vendido antes en la tienda, archivar el registro viejo como IMEI-OLD-X
        ok_imei, info_old = archivar_imei_existente_si_vendido(retoma.imei)
        if not ok_imei:
            flash(info_old, 'danger')
            return redirect(destino_retorno)

        partes_nombre = [p for p in [retoma.marca, retoma.modelo_celular, retoma.memoria, retoma.color] if p]
        nombre_equipo = " ".join(partes_nombre) if partes_nombre else f"Celular Retoma {retoma.imei}"
        sku_retoma = f"RET-{retoma.imei}"[:50]
        if Product.query.filter_by(sku=sku_retoma).first():
            sku_retoma = f"RET-{datetime.now().strftime('%Y%m%d%H%M%S')}-{retoma.id}"[:50]

        imagen_auto_ret = obtener_imagen_por_referencia(retoma.marca, retoma.modelo_celular, retoma.color)
        tipo_origen_prov = "Retoma Arreglada" if venia_de_arreglo else "Retoma Cliente"

        nuevo_producto = Product(
            nombre=nombre_equipo,
            sku=sku_retoma,
            tipo_inventario='celulares',
            cantidad_stock=1,
            precio_costo=costo_real,
            precio_minimo=precio_minimo,
            precio_sugerido=precio_sugerido,
            imei=retoma.imei,
            imei2=retoma.imei2,
            marca=retoma.marca,
            modelo_celular=retoma.modelo_celular,
            estado_celular='Usado',
            color=retoma.color,
            memoria=retoma.memoria,
            bateria=retoma.bateria,
            proveedor=f"{tipo_origen_prov}: {retoma.cliente_nombre or 'Mostrador'} ({retoma.cliente_documento or 'S/D'})",
            observacion=observaciones,
            imagen=imagen_auto_ret
        )
        db.session.add(nuevo_producto)
        db.session.flush()

        retoma.costo_real = costo_real
        retoma.precio_minimo = precio_minimo
        retoma.precio_sugerido = precio_sugerido
        retoma.observaciones = observaciones
        retoma.estado = 'APROBADA'
        retoma.usuario_aprueba_id = current_user.id
        retoma.fecha_aprobacion = obtener_hora_bogota()
        retoma.product_id_generado = nuevo_producto.id

        motivo_kardex = f"Ingreso de Retoma #{retoma.id} | IMEI 1: {retoma.imei}"
        if sobrecosto_arreglo > 0:
            motivo_kardex += f" | Recibido: ${int(valor_base)} + Arreglo: ${int(sobrecosto_arreglo)} = Costo Total: ${int(costo_real)}"
        if info_old:
            motivo_kardex += f" (Reingreso histórico archivado como {info_old})"

        db.session.add(StockAdjustment(
            product_id=nuevo_producto.id,
            admin_id=current_user.id,
            tipo_movimiento='Alta por Retoma / Arreglo Terminado' if venia_de_arreglo else 'Alta por Retoma Directa',
            motivo=motivo_kardex,
            stock_anterior=0,
            stock_nuevo=1
        ))

        db.session.commit()
        msg = f'✅ Equipo "{nombre_equipo}" (IMEI 1: {retoma.imei}) ingresado al Inventario de Celulares con Costo ${int(costo_real):,}, Mínimo ${int(precio_minimo):,} y Sugerido ${int(precio_sugerido):,}.'.replace(',', '.')
        if info_old:
            msg += f' (El registro anterior vendido de este IMEI fue archivado como {info_old}).'
        flash(msg, 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error al ingresar el equipo a inventario: {str(e)}', 'danger')

    return redirect(destino_retorno)


# ══════════════════════════════════════════════════════════════════════════
# 1.4 CLIENTES CELULARES (/celulares/clientes)
# ══════════════════════════════════════════════════════════════════════════

@celulares_bp.route('/clientes')
@login_required
def clientes():
    # Consultamos todas las ventas que tengan cliente registrado o detalles de celulares
    registros = (
        db.session.query(SaleClient, Sale, SaleDetail, Product)
        .join(Sale, SaleClient.sale_id == Sale.id)
        .join(SaleDetail, Sale.id == SaleDetail.sale_id)
        .outerjoin(Product, SaleDetail.product_id == Product.id)
        .order_by(Sale.fecha_venta.desc())
        .all()
    )

    ahora = obtener_hora_bogota()
    directorio_map = {}

    for sc, venta, detalle, prod in registros:
        # Filtrar para incluir ventas de celulares (o productos externos tipo celular)
        es_cel = (
            venta.tipo_venta == 'celulares'
            or (prod and prod.tipo_inventario == 'celulares')
            or (prod and prod.tipo_inventario == 'externos' and prod.subtipo_externo == 'celular')
            or (prod and prod.imei)
        )
        if not es_cel:
            continue

        doc_key = (sc.documento or 'SIN-DOC').strip()
        if doc_key not in directorio_map:
            directorio_map[doc_key] = {
                'documento': doc_key,
                'nombre': sc.nombre,
                'telefono': sc.telefono,
                'direccion': sc.direccion or '',
                'email': sc.email or '',
                'total_compras': Decimal('0.00'),
                'equipos': []
            }
        else:
            # Actualizar dirección/correo si en una compra más reciente se completaron
            if sc.direccion and not directorio_map[doc_key]['direccion']:
                directorio_map[doc_key]['direccion'] = sc.direccion
            if sc.email and not directorio_map[doc_key]['email']:
                directorio_map[doc_key]['email'] = sc.email

        imei_equipo = prod.imei if (prod and prod.imei) else (detalle.variante.nombre_variante if detalle.variante else 'N/A')
        # Si fue archivado como -OLD-X, mostrar también el IMEI limpio original
        imei_limpio = re.sub(r'-OLD-\d+$', '', imei_equipo) if imei_equipo else 'N/A'

        dias_transcurridos = (ahora.date() - venta.fecha_venta.date()).days if venta.fecha_venta else 0
        dias_garantia = 90 if (prod and prod.estado_celular == 'Usado') else 365
        en_garantia = dias_transcurridos <= dias_garantia
        dias_restantes_garantia = max(0, dias_garantia - dias_transcurridos)

        directorio_map[doc_key]['total_compras'] += (detalle.precio_venta_final * detalle.cantidad_vendida)
        directorio_map[doc_key]['equipos'].append({
            'sale_id': venta.id,
            'fecha': venta.fecha_venta,
            'nombre_equipo': prod.nombre if prod else (detalle.nombre_manual or 'Celular'),
            'marca': prod.marca if prod else '',
            'modelo': prod.modelo_celular if prod else '',
            'memoria': prod.memoria if prod else '',
            'color': prod.color if prod else '',
            'estado_celular': prod.estado_celular if (prod and prod.estado_celular) else 'Nuevo',
            'imei': imei_limpio,
            'imei2': prod.imei2 if (prod and prod.imei2) else '',
            'imei_raw': imei_equipo,
            'precio_venta': detalle.precio_venta_final,
            'metodo_pago': venta.metodo_pago_display,
            'vendedor': venta.vendedor.nombre if venta.vendedor else 'Vendedor',
            'en_garantia': en_garantia,
            'dias_restantes_garantia': dias_restantes_garantia
        })

    clientes_agrupados = list(directorio_map.values())
    return render_template('celulares/clientes.html', clientes_agrupados=clientes_agrupados)


@celulares_bp.route('/api/cliente/<path:documento>', methods=['GET'])
@login_required
def api_buscar_cliente_por_documento(documento):
    """Permite autocompletar en Caja los datos de un cliente recurrente al digitar su Cédula/NIT."""
    doc_clean = (documento or '').strip()
    if not doc_clean:
        return jsonify({'found': False}), 200
    sc = SaleClient.query.filter_by(documento=doc_clean).order_by(SaleClient.id.desc()).first()
    if not sc:
        return jsonify({'found': False}), 200
    return jsonify({
        'found': True,
        'documento': sc.documento,
        'nombre': sc.nombre or '',
        'telefono': sc.telefono or '',
        'direccion': sc.direccion or '',
        'email': sc.email or ''
    }), 200


# ══════════════════════════════════════════════════════════════════════════
# 1.5 HISTORIAL CELULARES (/celulares/historial_ventas)
# (SIN switches de OK Contabilidad ni OK Inventario)
# ══════════════════════════════════════════════════════════════════════════

@celulares_bp.route('/historial_ventas')
@celulares_bp.route('/ventas/historial')
@login_required
def historial_ventas():
    ventas_celulares = (
        Sale.query
        .join(SaleDetail)
        .join(Product)
        .filter(
            or_(
                Product.tipo_inventario == 'celulares',
                (Product.tipo_inventario == 'externos') & (Product.subtipo_externo == 'celular')
            )
        )
        .order_by(Sale.fecha_venta.desc())
        .all()
    )

    datos_historial = []
    for v in ventas_celulares:
        for d in v.detalles:
            if not d.producto:
                continue
            if d.producto.tipo_inventario != 'celulares' and not (d.producto.tipo_inventario == 'externos' and d.producto.subtipo_externo == 'celular'):
                continue

            costo_u = Decimal(str(d.variante.precio_costo if (d.variante and d.variante.precio_costo is not None) else (d.producto.precio_costo or 0)))
            precio_v = Decimal(str(d.precio_venta_final or 0))
            utilidad = (precio_v - costo_u) * d.cantidad_vendida

            imei_str = d.producto.imei or (d.variante.nombre_variante if d.variante else 'N/A')
            imei_display = re.sub(r'-OLD-\d+$', '', imei_str) if imei_str else 'N/A'

            datos_historial.append({
                'id_venta': v.id,
                'retoma_id': v.retomas[0].id if v.retomas else None,
                'fecha': v.fecha_venta,
                'vendedor': v.vendedor.nombre if v.vendedor else 'Cajero',
                'cliente_nombre': v.cliente.nombre if v.cliente else 'Cliente Mostrador',
                'cliente_documento': v.cliente.documento if v.cliente else '',
                'celular': d.producto.nombre,
                'estado_celular': d.producto.estado_celular or 'Nuevo',
                'imei': imei_display,
                'precio_costo': costo_u,
                'precio_venta': precio_v,
                'utilidad': utilidad,
                'metodo_pago': v.metodo_pago_display
            })

    return render_template('celulares/historial_ventas.html', historial=datos_historial)


# ══════════════════════════════════════════════════════════════════════════
# 1.6 TRAZABILIDAD IMEI (/celulares/trazabilidad)
# ══════════════════════════════════════════════════════════════════════════

@celulares_bp.route('/trazabilidad', methods=['GET'])
@login_required
def trazabilidad():
    imei_query = request.args.get('imei', '').strip()
    eventos = []
    equipos_encontrados = []
    retomas_encontradas = []

    if imei_query:
        # Buscar en Product tanto el IMEI 1 como IMEI 2 (incluyendo versiones archivadas -OLD-X)
        equipos_encontrados = Product.query.filter(
            or_(
                Product.imei == imei_query,
                Product.imei.ilike(f"{imei_query}-OLD-%"),
                Product.imei.ilike(f"%{imei_query}%"),
                Product.imei2 == imei_query,
                Product.imei2.ilike(f"%{imei_query}%")
            )
        ).order_by(Product.fecha_creacion.asc()).all()

        # Si el usuario buscó por IMEI 2 o IMEI 1, recopilar todos los IMEIs hermanos de esos equipos para cruzar Retomas y Facturas
        imeis_relacionados = {imei_query}
        for eq in equipos_encontrados:
            if eq.imei:
                imeis_relacionados.add(re.sub(r'-OLD-\d+$', '', eq.imei))
                imeis_relacionados.add(eq.imei)
            if eq.imei2:
                imeis_relacionados.add(eq.imei2)

        # También buscar en ProductVariant por si hay registros antiguos agrupados
        variantes_imei = ProductVariant.query.filter(ProductVariant.nombre_variante.ilike(f"%{imei_query}%")).all()
        for var in variantes_imei:
            if var.producto and var.producto not in equipos_encontrados:
                equipos_encontrados.append(var.producto)

        product_ids = [p.id for p in equipos_encontrados]

        # 1. Eventos de Entrada / Creación en Product
        for p in equipos_encontrados:
            es_archivado = bool(p.imei and '-OLD-' in p.imei)
            eventos.append({
                'fecha': p.fecha_creacion or obtener_hora_bogota(),
                'tipo': 'INGRESO INVENTARIO',
                'icono': 'fa-box-open',
                'color': 'primary',
                'titulo': f"Ingreso al Sistema: {p.nombre} ({p.estado_celular or 'Nuevo'})",
                'detalle': (
                    f"SKU: {p.sku} | IMEI registrado: {p.imei or 'En variante'} | "
                    f"Proveedor/Origen: {p.proveedor or 'Inventario Propio'} | "
                    f"Costo: ${float(p.precio_costo or 0):,.0f} | Sugerido: ${float(p.precio_sugerido or 0):,.0f}"
                    + (" [Ciclo anterior archivado]" if es_archivado else "")
                )
            })

        # 2. Eventos de Ajustes de Kardex (StockAdjustment)
        if product_ids:
            ajustes = StockAdjustment.query.filter(StockAdjustment.product_id.in_(product_ids)).order_by(StockAdjustment.fecha_ajuste.asc()).all()
            for aj in ajustes:
                eventos.append({
                    'fecha': aj.fecha_ajuste or obtener_hora_bogota(),
                    'tipo': 'KARDEX / STOCK',
                    'icono': 'fa-sliders',
                    'color': 'secondary',
                    'titulo': f"Movimiento Kardex: {aj.tipo_movimiento}",
                    'detalle': f"Stock: {aj.stock_anterior} → {aj.stock_nuevo} | Responsable: {aj.admin.nombre if aj.admin else 'Sistema'}" + (f" | Motivo: {aj.motivo}" if aj.motivo else "")
                })

        # 3. Eventos de Maneos (Préstamos a locales vecinos)
        if product_ids:
            maneos_list = Maneo.query.filter(Maneo.product_id.in_(product_ids)).order_by(Maneo.fecha_prestamo.asc()).all()
            for m in maneos_list:
                eventos.append({
                    'fecha': m.fecha_prestamo or obtener_hora_bogota(),
                    'tipo': 'MANEO (PRÉSTAMO)',
                    'icono': 'fa-handshake',
                    'color': 'warning',
                    'titulo': f"Prestado en Maneo a: {m.local_vecino}",
                    'detalle': f"Estado actual: {m.estado} | Cantidad: {m.cantidad}" + (f" | Resuelto el: {m.fecha_resolucion.strftime('%d/%m/%Y %H:%M')}" if m.fecha_resolucion else " (Actualmente prestado)")
                })

        # 4. Eventos de Aprobaciones de Precio (PriceApproval)
        if product_ids:
            aprobaciones = PriceApproval.query.filter(PriceApproval.product_id.in_(product_ids)).order_by(PriceApproval.fecha_solicitud.asc()).all()
            for ap in aprobaciones:
                eventos.append({
                    'fecha': ap.fecha_solicitud or obtener_hora_bogota(),
                    'tipo': 'AUTORIZACIÓN PRECIO',
                    'icono': 'fa-shield-halved',
                    'color': 'info',
                    'titulo': f"Solicitud de Precio Especial (${float(ap.precio_solicitado):,.0f})",
                    'detalle': (
                        f"Precio Mínimo: ${float(ap.precio_minimo_original):,.0f} | "
                        f"Solicitado por: {ap.vendedor.nombre if ap.vendedor else 'Vendedor'} | "
                        f"Estado: {ap.estado.upper()}"
                        + (f" | Motivo: {ap.motivo}" if ap.motivo else "")
                    )
                })

        # 5. Eventos de Venta y Cliente (SaleDetail / Sale / SaleClient)
        if product_ids:
            ventas_det = SaleDetail.query.join(Sale).filter(SaleDetail.product_id.in_(product_ids)).order_by(Sale.fecha_venta.asc()).all()
            for vd in ventas_det:
                v = vd.venta
                cli_info = (
                    f"Cliente: {v.cliente.nombre} (CC: {v.cliente.documento} | Tel: {v.cliente.telefono})"
                    if v and v.cliente else "Cliente Mostrador"
                )
                eventos.append({
                    'fecha': v.fecha_venta if v else obtener_hora_bogota(),
                    'tipo': 'VENTA FACTURADA',
                    'icono': 'fa-cash-register',
                    'color': 'success',
                    'titulo': f"Vendido en Ticket #{v.id if v else 'N/A'} por ${float(vd.precio_venta_final):,.0f}",
                    'detalle': f"Vendedor: {v.vendedor.nombre if (v and v.vendedor) else 'Cajero'} | Pago: {v.metodo_pago_display if v else 'Efectivo'} | {cli_info}"
                })

        # 6. Eventos de Retomas / Arreglos / Reingresos (Retoma)
        condiciones_retoma = [
            Retoma.imei.ilike(f"%{imei_query}%"),
            Retoma.imei2.ilike(f"%{imei_query}%")
        ]
        for im_rel in imeis_relacionados:
            if im_rel:
                condiciones_retoma.append(Retoma.imei == im_rel)
                condiciones_retoma.append(Retoma.imei2 == im_rel)

        retomas_encontradas = Retoma.query.filter(
            or_(*condiciones_retoma)
        ).order_by(Retoma.fecha_recepcion.asc()).all()

        for r in retomas_encontradas:
            estado_lbl = 'EN ARREGLOS' if r.estado == 'EN_ARREGLO' else ('INGRESADA A INVENTARIO' if r.estado == 'APROBADA' else 'PENDIENTE POR DEFINIR')
            imei_dual_txt = f"IMEI 1: {r.imei}" + (f" | IMEI 2: {r.imei2}" if r.imei2 else "")
            eventos.append({
                'fecha': r.fecha_recepcion or obtener_hora_bogota(),
                'tipo': 'RETOMA RECIBIDA',
                'icono': 'fa-rotate-left',
                'color': 'warning',
                'titulo': f"Recibido en Retoma ({r.marca} {r.modelo_celular}) - {estado_lbl}",
                'detalle': (
                    f"{imei_dual_txt} | Valor recibido en caja: ${float(r.valor_recibido or 0):,.0f} | "
                    f"Recibido por: {r.usuario_recibe.nombre if r.usuario_recibe else 'Cajero'} | "
                    f"Cliente: {r.cliente_nombre or 'N/A'} ({r.cliente_documento or 'S/D'})"
                    + (f" | Ticket: #{r.sale_id}" if r.sale_id else "")
                    + (f" | Obs: {r.observaciones}" if r.observaciones else "")
                )
            })
            if r.estado == 'EN_ARREGLO' or (r.observaciones and '[ARREGLADO' in r.observaciones):
                eventos.append({
                    'fecha': r.fecha_aprobacion or r.fecha_recepcion or obtener_hora_bogota(),
                    'tipo': 'MÓDULO ARREGLOS / SOPORTE',
                    'icono': 'fa-screwdriver-wrench',
                    'color': 'danger',
                    'titulo': f"Paso por Taller / Arreglos ({r.marca} {r.modelo_celular})",
                    'detalle': f"Detalle del arreglo: {r.observaciones or 'Reparación general'} | Costo final con arreglo: ${float(r.costo_real or r.valor_recibido or 0):,.0f}"
                })
            if r.estado == 'APROBADA' and r.fecha_aprobacion:
                eventos.append({
                    'fecha': r.fecha_aprobacion,
                    'tipo': 'RETOMA INGRESADA A INVENTARIO',
                    'icono': 'fa-circle-check',
                    'color': 'success',
                    'titulo': f"Retoma Incorporada a Stock de Celulares",
                    'detalle': (
                        f"Ingresada por: {r.usuario_aprueba.nombre if r.usuario_aprueba else 'Admin'} | "
                        f"Costo Final: ${float(r.costo_real or 0):,.0f} | "
                        f"Mínimo: ${float(r.precio_minimo or 0):,.0f} | "
                        f"Sugerido: ${float(r.precio_sugerido or 0):,.0f}"
                    )
                })

        # 7. Eventos de Cuentas Externos y Abonos a Proveedor (ProviderInvoice / ProviderPayment)
        if product_ids:
            from models import ProviderInvoice, ProviderPayment
            facturas_prov = ProviderInvoice.query.filter(ProviderInvoice.product_id.in_(product_ids)).order_by(ProviderInvoice.fecha_creacion.asc()).all()
            for fp in facturas_prov:
                eventos.append({
                    'fecha': fp.fecha_creacion or obtener_hora_bogota(),
                    'tipo': 'CUENTA PROVEEDOR EXTERNO',
                    'icono': 'fa-file-invoice-dollar',
                    'color': 'dark',
                    'titulo': f"Cuenta por Pagar a Proveedor: {fp.proveedor} (${float(fp.monto_costo or 0):,.0f})",
                    'detalle': f"Concepto: {fp.concepto} | Estado de pago: {fp.estado}" + (f" | Pagado el: {fp.fecha_pago.strftime('%d/%m/%Y %H:%M')}" if fp.fecha_pago else "")
                })
                for ab in fp.abonos:
                    eventos.append({
                        'fecha': ab.fecha_abono or obtener_hora_bogota(),
                        'tipo': 'ABONO A PROVEEDOR EXTERNO',
                        'icono': 'fa-hand-holding-dollar',
                        'color': 'success',
                        'titulo': f"Abono registrado a {ab.proveedor} por ${float(ab.monto or 0):,.0f}",
                        'detalle': f"Método: {ab.metodo_pago.upper()} | Obs: {ab.observacion or 'Sin nota'}" + (" | Con foto de comprobante adjunta" if ab.comprobante_imagen else "")
                    })

        # Ordenar línea de tiempo cronológicamente
        eventos.sort(key=lambda x: x['fecha'] or datetime.min)

    return render_template(
        'celulares/trazabilidad.html',
        imei_query=imei_query,
        eventos=eventos,
        equipos_encontrados=equipos_encontrados,
        retomas_encontradas=retomas_encontradas
    )


# ══════════════════════════════════════════════════════════════════════════
# ARQUEO Y VENTA DIRECTA DE CELULARES (MANTENIDOS POR COMPATIBILIDAD)
# ══════════════════════════════════════════════════════════════════════════

@celulares_bp.route('/venta', methods=['GET', 'POST'])
@login_required
def venta():
    celulares_disponibles = Product.query.filter(
        Product.tipo_inventario == 'celulares',
        Product.cantidad_stock > 0
    ).all()

    if request.method == 'POST':
        celular_id = request.form.get('celular_id')
        precio_venta_final = safe_float(request.form.get('precio_venta_final'), 0.0)
        metodos_pago = request.form.getlist('metodo_pago[]')
        montos_pago = request.form.getlist('monto_pago[]')

        if not celular_id or not metodos_pago or not montos_pago:
            flash('Datos incompletos para la venta.', 'danger')
            return redirect(url_for('celulares_bp.venta'))

        celular = Product.query.get(celular_id)
        if not celular or celular.cantidad_stock < 1:
            flash('El celular seleccionado no está disponible en stock.', 'danger')
            return redirect(url_for('celulares_bp.venta'))

        if celular.tiene_maneo_pendiente:
            flash('Este celular se encuentra prestado en Maneo y no puede venderse hasta resolver el préstamo.', 'danger')
            return redirect(url_for('celulares_bp.venta'))

        if precio_venta_final < float(celular.precio_minimo) and current_user.rol != 'admin':
            flash(f'El precio de venta no puede ser menor al mínimo permitido (${celular.precio_minimo:,.2f}).', 'danger')
            return redirect(url_for('celulares_bp.venta'))

        nueva_venta = Sale(
            vendedor_id=current_user.id,
            monto_total=precio_venta_final,
            metodo_pago=metodos_pago[0] if len(metodos_pago) == 1 else 'mixto',
            tipo_venta='celulares'
        )
        db.session.add(nueva_venta)
        db.session.flush()

        suma_pagos = 0.0
        for mp, monto in zip(metodos_pago, montos_pago):
            monto_float = float(monto)
            if monto_float > 0:
                db.session.add(SalePayment(
                    sale_id=nueva_venta.id,
                    metodo_pago=mp,
                    monto=monto_float
                ))
                suma_pagos += monto_float

        if abs(suma_pagos - precio_venta_final) > 0.01:
            db.session.rollback()
            flash('La suma de los métodos de pago no coincide con el total de la venta.', 'danger')
            return redirect(url_for('celulares_bp.venta'))

        db.session.add(SaleDetail(
            sale_id=nueva_venta.id,
            product_id=celular.id,
            cantidad_vendida=1,
            precio_venta_final=precio_venta_final
        ))

        stock_ant = celular.cantidad_stock
        celular.cantidad_stock -= 1
        db.session.add(StockAdjustment(
            product_id=celular.id,
            admin_id=current_user.id,
            tipo_movimiento=f'Venta Celular #{nueva_venta.id}',
            motivo=f"IMEI: {celular.imei or 'N/A'}",
            stock_anterior=stock_ant,
            stock_nuevo=celular.cantidad_stock
        ))

        try:
            db.session.commit()
            flash('Venta de celular registrada exitosamente.', 'success')
            return redirect(url_for('celulares_bp.historial_ventas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al registrar la venta: {str(e)}', 'danger')

    return render_template('celulares/venta.html', celulares=celulares_disponibles)


@celulares_bp.route('/arqueo/nuevo', methods=['GET', 'POST'])
@login_required
def arqueo_nuevo():
    fecha_str = request.args.get('fecha', obtener_hora_bogota().strftime('%Y-%m-%d'))
    try:
        fecha_seleccionada = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except ValueError:
        fecha_seleccionada = obtener_hora_bogota().date()
        fecha_str = fecha_seleccionada.strftime('%Y-%m-%d')

    ventas_del_dia = Sale.query.filter(
        db.func.date(Sale.fecha_venta) == fecha_seleccionada,
        Sale.tipo_venta == 'celulares'
    ).all()

    total_efectivo = Decimal('0')
    total_transferencia = Decimal('0')
    for v in ventas_del_dia:
        if v.pagos:
            for pago in v.pagos:
                if pago.metodo_pago == 'efectivo':
                    total_efectivo += pago.monto
                else:
                    total_transferencia += pago.monto
        else:
            if v.metodo_pago == 'efectivo':
                total_efectivo += v.monto_total
            elif v.metodo_pago in ['transferencia', 'nequi', 'bancolombia', 'daviplata']:
                total_transferencia += v.monto_total

    arqueo_existente = ArqueoCaja.query.filter_by(fecha_arqueo=fecha_seleccionada, tipo_arqueo='celulares').first()

    if request.method == 'POST':
        if arqueo_existente:
            flash('Ya existe un arqueo de celulares para esta fecha.', 'warning')
            return redirect(url_for('celulares_bp.arqueo_reporte', fecha_inicio=fecha_str, fecha_fin=fecha_str))

        nuevo_arqueo = ArqueoCaja(
            vendedor_id=current_user.id,
            fecha_arqueo=fecha_seleccionada,
            tipo_arqueo='celulares',
            base_inicial=Decimal('0.00'),
            gastos_del_dia=Decimal('0.00'),
            total_efectivo_sistema=total_efectivo,
            total_transferencia_sistema=total_transferencia,
            total_unidades_ch=Decimal('0.00'),
            total_celulares=Decimal('0.00')
        )

        try:
            db.session.add(nuevo_arqueo)
            db.session.commit()
            flash('Arqueo de celulares guardado exitosamente.', 'success')
            return redirect(url_for('celulares_bp.arqueo_reporte', fecha_inicio=fecha_str, fecha_fin=fecha_str))
        except Exception:
            db.session.rollback()
            flash('Ocurrió un error al guardar el arqueo.', 'danger')

    return render_template(
        'celulares/arqueo/form.html',
        fecha=fecha_str,
        total_efectivo=total_efectivo,
        total_transferencia=total_transferencia,
        arqueo_existente=arqueo_existente
    )


@celulares_bp.route('/arqueo/reporte', methods=['GET'])
@login_required
def arqueo_reporte():
    fecha_inicio_str = request.args.get('fecha_inicio', obtener_hora_bogota().strftime('%Y-%m-%d'))
    fecha_fin_str = request.args.get('fecha_fin', obtener_hora_bogota().strftime('%Y-%m-%d'))

    try:
        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
    except ValueError:
        fecha_inicio = obtener_hora_bogota().date()
        fecha_fin = obtener_hora_bogota().date()

    if current_user.rol != 'admin':
        hoy = obtener_hora_bogota().date()
        fecha_inicio = hoy
        fecha_fin = hoy
        fecha_inicio_str = hoy.strftime('%Y-%m-%d')
        fecha_fin_str = hoy.strftime('%Y-%m-%d')

    arqueos = ArqueoCaja.query.filter(
        ArqueoCaja.fecha_arqueo >= fecha_inicio,
        ArqueoCaja.fecha_arqueo <= fecha_fin,
        ArqueoCaja.tipo_arqueo == 'celulares'
    ).order_by(ArqueoCaja.fecha_arqueo.desc()).all()

    resumen = {
        'total_efectivo': sum(a.total_efectivo_sistema for a in arqueos),
        'total_transferencia': sum(a.total_transferencia_sistema for a in arqueos)
    }
    resumen['total_recaudado_neto'] = resumen['total_efectivo'] + resumen['total_transferencia']

    ventas_periodo = Sale.query.filter(
        db.func.date(Sale.fecha_venta) >= fecha_inicio,
        db.func.date(Sale.fecha_venta) <= fecha_fin,
        Sale.tipo_venta == 'celulares'
    ).order_by(Sale.fecha_venta.asc()).all()

    fecha_generacion = obtener_hora_bogota().strftime('%Y-%m-%d %H:%M')

    return render_template(
        'celulares/arqueo/reporte.html',
        arqueos=arqueos,
        resumen=resumen,
        fecha_inicio=fecha_inicio_str,
        fecha_fin=fecha_fin_str,
        fecha_generacion=fecha_generacion,
        ventas_periodo=ventas_periodo
    )


@celulares_bp.route('/arqueo/anular/<int:id>', methods=['POST'])
@login_required
@admin_required
def anular_arqueo(id):
    arqueo = ArqueoCaja.query.get_or_404(id)
    fecha_str = arqueo.fecha_arqueo.strftime('%Y-%m-%d')
    try:
        db.session.delete(arqueo)
        db.session.commit()
        flash(f'El cierre de arqueo de celulares del día {fecha_str} fue anulado exitosamente.', 'success')
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al anular el cierre de arqueo de celulares.', 'danger')

    fecha_inicio = request.args.get('fecha_inicio', fecha_str)
    fecha_fin = request.args.get('fecha_fin', fecha_str)
    return redirect(url_for('celulares_bp.arqueo_reporte', fecha_inicio=fecha_inicio, fecha_fin=fecha_fin))
