import os
import re
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
    estado_filtro = request.args.get('estado', '').strip() # 'disponibles', 'vendidos', 'maneo', 'todos'

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

    # KPIs en tiempo real sobre el stock activo de celulares
    stock_activo = 0
    costo_total_invertido = Decimal('0.00')
    valor_proyectado_venta = Decimal('0.00')
    equipos_en_maneo = 0

    for c in todos_celulares:
        if c.tiene_maneo_pendiente:
            equipos_en_maneo += 1
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

    celulares = query.order_by(Product.cantidad_stock.desc(), Product.fecha_creacion.desc()).all()

    if estado_filtro == 'disponibles':
        celulares = [c for c in celulares if c.total_stock > 0]
    elif estado_filtro == 'vendidos':
        celulares = [c for c in celulares if c.total_stock <= 0 and not c.tiene_maneo_pendiente]
    elif estado_filtro == 'maneo':
        celulares = [c for c in celulares if c.tiene_maneo_pendiente]

    return render_template(
        'celulares/inventario.html',
        celulares=celulares,
        stock_activo=stock_activo,
        costo_total_invertido=costo_total_invertido,
        valor_proyectado_venta=valor_proyectado_venta,
        equipos_en_maneo=equipos_en_maneo,
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


@celulares_bp.route('/plantilla-excel')
@login_required
@admin_required
def descargar_plantilla_celulares():
    cols = [
        'imei', 'imei2', 'marca', 'modelo_celular', 'estado_celular',
        'memoria', 'color', 'bateria', 'proveedor',
        'precio_costo', 'precio_minimo', 'precio_sugerido', 'observacion'
    ]
    df = pd.DataFrame(columns=cols)
    df.loc[0] = [
        '358901234567890', '', 'Apple', 'iPhone 15 Pro', 'Nuevo',
        '256GB', 'Titanio Natural', '100%', 'Importadora Mobile',
        3800000, 4100000, 4350000, 'Caja sellada'
    ]
    df.loc[1] = [
        '359988776655443', '359988776655444', 'Samsung', 'Galaxy S23 Ultra', 'Usado',
        '256GB / 12GB', 'Negro', '93%', 'Proveedor Local',
        2600000, 2900000, 3150000, 'Impecable con cargador'
    ]
    output = BytesIO()
    df.to_excel(output, index=False, engine='openpyxl')
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
                observacion=obs
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
# 1.3 RETOMAS / CUARENTENA (/retomas/cuarentena y /retomas/aprobar/<id>)
# ══════════════════════════════════════════════════════════════════════════

@retomas_bp.route('/cuarentena', methods=['GET', 'POST'])
@login_required
def cuarentena():
    if request.method == 'POST':
        # Registro manual de una retoma directamente en cuarentena (opcional además de Caja)
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
            flash('IMEI, Marca y Modelo son obligatorios para registrar una retoma.', 'danger')
            return redirect(url_for('retomas_bp.cuarentena'))

        # Verificar que no haya otra retoma en CUARENTENA con el mismo IMEI
        en_cuarentena = Retoma.query.filter_by(imei=imei, estado='CUARENTENA').first()
        if en_cuarentena:
            flash(f'El IMEI {imei} ya se encuentra actualmente en Cuarentena de Retomas.', 'warning')
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
            flash(f'Equipo {marca} {modelo_celular} (IMEI: {imei}) ingresado a Cuarentena.', 'success')
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
    total_aprobadas = Retoma.query.filter_by(estado='APROBADA').count()

    return render_template(
        'celulares/retomas_cuarentena.html',
        retomas=retomas,
        estado_filtro=estado_filtro,
        total_cuarentena=total_cuarentena,
        valor_cuarentena=valor_cuarentena,
        total_aprobadas=total_aprobadas
    )


@retomas_bp.route('/aprobar/<int:id>', methods=['POST'])
@login_required
@admin_required
def aprobar_retoma(id):
    retoma = Retoma.query.get_or_404(id)
    if retoma.estado != 'CUARENTENA':
        flash('Esta retoma ya fue procesada anteriormente.', 'warning')
        return redirect(url_for('retomas_bp.cuarentena'))

    costo_real = safe_float(request.form.get('costo_real', '').replace(',', '').replace('.', ''), float(retoma.valor_recibido or 0))
    precio_minimo = safe_float(request.form.get('precio_minimo', '').replace(',', '').replace('.', ''), costo_real)
    precio_sugerido = safe_float(request.form.get('precio_sugerido', '').replace(',', '').replace('.', ''), precio_minimo)
    observaciones = request.form.get('observaciones', '').strip() or retoma.observaciones

    if precio_sugerido <= 0 or costo_real < 0:
        flash('Debes definir un costo real y un precio sugerido válidos para aprobar la retoma.', 'danger')
        return redirect(url_for('retomas_bp.cuarentena'))

    try:
        # Reingreso de IMEI: Si fue vendido antes en la tienda, archivar el registro viejo como IMEI-OLD-X
        ok_imei, info_old = archivar_imei_existente_si_vendido(retoma.imei)
        if not ok_imei:
            flash(info_old, 'danger')
            return redirect(url_for('retomas_bp.cuarentena'))

        partes_nombre = [p for p in [retoma.marca, retoma.modelo_celular, retoma.memoria, retoma.color] if p]
        nombre_equipo = " ".join(partes_nombre) if partes_nombre else f"Celular Retoma {retoma.imei}"
        sku_retoma = f"RET-{retoma.imei}"[:50]
        if Product.query.filter_by(sku=sku_retoma).first():
            sku_retoma = f"RET-{datetime.now().strftime('%Y%m%d%H%M%S')}-{retoma.id}"[:50]

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
            proveedor=f"Retoma Cliente: {retoma.cliente_nombre or 'Mostrador'} ({retoma.cliente_documento or 'S/D'})",
            observacion=observaciones
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

        motivo_kardex = f"Retoma Aprobada #{retoma.id} | IMEI: {retoma.imei}"
        if info_old:
            motivo_kardex += f" (Reingreso histórico archivado como {info_old})"

        db.session.add(StockAdjustment(
            product_id=nuevo_producto.id,
            admin_id=current_user.id,
            tipo_movimiento='Alta por Retoma Aprobada',
            motivo=motivo_kardex,
            stock_anterior=0,
            stock_nuevo=1
        ))

        db.session.commit()
        msg = f'✅ Retoma aprobada. El equipo "{nombre_equipo}" (IMEI: {retoma.imei}) ingresó al inventario de Celulares como Usado.'
        if info_old:
            msg += f' (El registro anterior vendido de este IMEI fue archivado como {info_old} conservando su trazabilidad).'
        flash(msg, 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error al aprobar la retoma: {str(e)}', 'danger')

    return redirect(url_for('retomas_bp.cuarentena'))


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
            'estado_celular': prod.estado_celular if (prod and prod.estado_celular) else 'Nuevo',
            'imei': imei_limpio,
            'imei_raw': imei_equipo,
            'precio_venta': detalle.precio_venta_final,
            'vendedor': venta.vendedor.nombre if venta.vendedor else 'Vendedor',
            'en_garantia': en_garantia,
            'dias_restantes_garantia': dias_restantes_garantia
        })

    clientes_agrupados = list(directorio_map.values())
    return render_template('celulares/clientes.html', clientes_agrupados=clientes_agrupados)


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
        # Buscar en Product tanto el IMEI exacto como sus versiones archivadas IMEI-OLD-X o variantes
        equipos_encontrados = Product.query.filter(
            or_(
                Product.imei == imei_query,
                Product.imei.ilike(f"{imei_query}-OLD-%"),
                Product.imei.ilike(f"%{imei_query}%"),
                Product.imei2 == imei_query
            )
        ).order_by(Product.fecha_creacion.asc()).all()

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

        # 6. Eventos de Retomas / Reingresos (Retoma)
        retomas_encontradas = Retoma.query.filter(
            or_(
                Retoma.imei == imei_query,
                Retoma.imei.ilike(f"%{imei_query}%"),
                Retoma.imei2 == imei_query
            )
        ).order_by(Retoma.fecha_recepcion.asc()).all()

        for r in retomas_encontradas:
            eventos.append({
                'fecha': r.fecha_recepcion or obtener_hora_bogota(),
                'tipo': 'RETOMA / CUARENTENA',
                'icono': 'fa-rotate-left',
                'color': 'danger',
                'titulo': f"Recibido en Retoma ({r.marca} {r.modelo_celular}) - Estado: {r.estado}",
                'detalle': (
                    f"Valor descontado en caja: ${float(r.valor_recibido or 0):,.0f} | "
                    f"Recibido por: {r.usuario_recibe.nombre if r.usuario_recibe else 'Cajero'} | "
                    f"Entregado por cliente: {r.cliente_nombre or 'N/A'} ({r.cliente_documento or 'S/D'})"
                    + (f" | Ticket asociado: #{r.sale_id}" if r.sale_id else "")
                )
            })
            if r.estado == 'APROBADA' and r.fecha_aprobacion:
                eventos.append({
                    'fecha': r.fecha_aprobacion,
                    'tipo': 'RETOMA APROBADA (REINGRESO)',
                    'icono': 'fa-circle-check',
                    'color': 'success',
                    'titulo': f"Retoma Aprobada e Incorporada a Stock Usado",
                    'detalle': (
                        f"Aprobada por: {r.usuario_aprueba.nombre if r.usuario_aprueba else 'Admin'} | "
                        f"Costo Real asignado: ${float(r.costo_real or 0):,.0f} | "
                        f"Precio Sugerido: ${float(r.precio_sugerido or 0):,.0f}"
                    )
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
