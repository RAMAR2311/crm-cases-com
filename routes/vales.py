import os
from datetime import datetime
from decimal import Decimal
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from models import db, User, Vale, Expense, obtener_hora_bogota
from decorators import admin_required
from utils import safe_int, safe_float

vales_bp = Blueprint('vales_bp', __name__)


@vales_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        vendedor_id = safe_int(request.form.get('vendedor_id'), 0)
        monto_raw = (request.form.get('monto') or '0').replace(',', '').replace('.', '').strip()
        monto = safe_float(monto_raw, 0.0)
        metodo_pago = (request.form.get('metodo_pago') or 'efectivo').strip().lower()
        observacion = (request.form.get('observacion') or '').strip() or None
        fecha_str = (request.form.get('fecha_vale') or '').strip()

        if current_user.rol != 'admin':
            vendedor_id = current_user.id

        if not vendedor_id or monto <= 0:
            flash('Debes seleccionar el vendedor y un valor del vale mayor a $0.', 'danger')
            return redirect(url_for('vales_bp.index'))

        vendedor_obj = User.query.get(vendedor_id)
        if not vendedor_obj:
            flash('El vendedor seleccionado no existe.', 'danger')
            return redirect(url_for('vales_bp.index'))

        ahora = obtener_hora_bogota()
        if fecha_str:
            try:
                fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d')
                fecha_obj = fecha_dt.replace(hour=ahora.hour, minute=ahora.minute, second=ahora.second)
            except ValueError:
                fecha_obj = ahora
        else:
            fecha_obj = ahora

        comprobante_filename = None
        if 'comprobante' in request.files:
            file = request.files['comprobante']
            if file and file.filename != '':
                ext = os.path.splitext(secure_filename(file.filename))[1].lower() or '.jpg'
                comprobante_filename = f"vale_{vendedor_id}_{ahora.strftime('%Y%m%d%H%M%S')}{ext}"
                upload_folder = current_app.config['UPLOAD_FOLDER']
                os.makedirs(upload_folder, exist_ok=True)
                file.save(os.path.join(upload_folder, comprobante_filename))

        try:
            # 1. Crear automáticamente el Gasto Diario en el día en que se tramitó el vale y con el mismo método de pago
            desc_gasto = f"Vale a {vendedor_obj.nombre}" + (f" · {observacion}" if observacion else "")
            gasto_vale = Expense(
                usuario_id=current_user.id,
                tipo_gasto='Gasto Diario',
                categoria='Vale / Adelanto Personal',
                descripcion=desc_gasto[:255],
                monto=Decimal(str(monto)),
                metodo_pago=metodo_pago,
                fecha_gasto=fecha_obj
            )
            db.session.add(gasto_vale)
            db.session.flush()

            # 2. Registrar el Vale vinculado al Gasto Diario generado
            nuevo_vale = Vale(
                vendedor_id=vendedor_obj.id,
                registrado_por_id=current_user.id,
                expense_id=gasto_vale.id,
                monto=Decimal(str(monto)),
                metodo_pago=metodo_pago,
                comprobante_imagen=comprobante_filename,
                observacion=observacion,
                fecha_vale=fecha_obj
            )
            db.session.add(nuevo_vale)
            db.session.commit()
            flash(
                f'✅ Vale por ${monto:,.0f} ({metodo_pago.capitalize()}) asociado a "{vendedor_obj.nombre}" registrado exitosamente. '
                f'Se generó automáticamente el Gasto Diario del día ({fecha_obj.strftime("%d/%m/%Y")}) por {metodo_pago.capitalize()}.',
                'success'
            )
        except Exception as e:
            db.session.rollback()
            flash(f'Error al registrar el vale: {str(e)}', 'danger')

        return redirect(url_for('vales_bp.index'))

    # Filtros de consulta
    vendedor_filtro = safe_int(request.args.get('vendedor_id'), 0)
    metodo_filtro = (request.args.get('metodo_pago') or '').strip().lower()
    fecha_inicio_str = (request.args.get('fecha_inicio') or '').strip()
    fecha_fin_str = (request.args.get('fecha_fin') or '').strip()

    # Lista de personal (Vendedores y Administradores activos de la tienda)
    vendedores_lista = User.query.filter(User.rol.in_(['vendedor', 'admin'])).order_by(User.nombre.asc()).all()

    query = Vale.query
    if current_user.rol != 'admin':
        query = query.filter_by(vendedor_id=current_user.id)
    elif vendedor_filtro > 0:
        query = query.filter_by(vendedor_id=vendedor_filtro)

    if metodo_filtro in ['efectivo', 'nequi', 'bancolombia', 'daviplata']:
        query = query.filter_by(metodo_pago=metodo_filtro)

    if fecha_inicio_str:
        try:
            fi_dt = datetime.strptime(fecha_inicio_str, '%Y-%m-%d')
            query = query.filter(Vale.fecha_vale >= fi_dt)
        except ValueError:
            pass

    if fecha_fin_str:
        try:
            ff_dt = datetime.strptime(fecha_fin_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            query = query.filter(Vale.fecha_vale <= ff_dt)
        except ValueError:
            pass

    vales = query.order_by(Vale.fecha_vale.desc()).all()

    # Totales y resumen por vendedor
    total_vales = sum(float(v.monto or 0) for v in vales)
    total_efectivo = sum(float(v.monto or 0) for v in vales if v.metodo_pago == 'efectivo')
    total_digital = sum(float(v.monto or 0) for v in vales if v.metodo_pago in ['nequi', 'bancolombia', 'daviplata', 'transferencia'])

    resumen_vendedores_map = {}
    todos_vales_base = Vale.query.all() if current_user.rol == 'admin' else vales
    for v in todos_vales_base:
        vid = v.vendedor_id
        v_nom = v.vendedor.nombre if v.vendedor else f'Usuario #{vid}'
        if vid not in resumen_vendedores_map:
            resumen_vendedores_map[vid] = {
                'vendedor_id': vid,
                'nombre': v_nom,
                'cantidad': 0,
                'total': 0.0
            }
        resumen_vendedores_map[vid]['cantidad'] += 1
        resumen_vendedores_map[vid]['total'] += float(v.monto or 0)

    resumen_vendedores = sorted(resumen_vendedores_map.values(), key=lambda x: (-x['total'], x['nombre'].lower()))
    hoy_str = obtener_hora_bogota().strftime('%Y-%m-%d')

    return render_template(
        'vales/index.html',
        vales=vales,
        vendedores_lista=vendedores_lista,
        resumen_vendedores=resumen_vendedores,
        vendedor_filtro=vendedor_filtro,
        metodo_filtro=metodo_filtro,
        fecha_inicio=fecha_inicio_str,
        fecha_fin=fecha_fin_str,
        total_vales=total_vales,
        total_efectivo=total_efectivo,
        total_digital=total_digital,
        hoy=hoy_str
    )


@vales_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_vale(id):
    vale = Vale.query.get_or_404(id)
    nom_vend = vale.vendedor.nombre if vale.vendedor else 'Vendedor'
    monto_v = float(vale.monto or 0)
    try:
        if vale.gasto_asociado:
            db.session.delete(vale.gasto_asociado)
        db.session.delete(vale)
        db.session.commit()
        flash(f'Vale #{id} de "{nom_vend}" (${monto_v:,.0f}) y su gasto asociado del día fueron eliminados correctamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar el vale: {str(e)}', 'danger')

    return redirect(url_for('vales_bp.index'))
