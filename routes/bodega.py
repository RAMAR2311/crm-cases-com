from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app, jsonify
from flask_login import login_required, current_user
from decorators import any_bodega_required
from models import db, Cliente, FacturaBodega, AbonoBodega, Product, StockAdjustment, FacturaBodegaDetalle
import os
from werkzeug.utils import secure_filename

bodega_bp = Blueprint('bodega_bp', __name__)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@bodega_bp.route('/dashboard')
@login_required
@any_bodega_required
def dashboard():
    if current_user.rol == 'vendedor_bodega':
        total_clientes = Cliente.query.filter_by(creado_por_id=current_user.id).count()
        facturas_recientes = FacturaBodega.query.filter_by(usuario_id=current_user.id).order_by(FacturaBodega.fecha_subida.desc()).limit(10).all()
    else:
        # Rol 'bodega' o 'admin' ve todo
        total_clientes = Cliente.query.count()
        facturas_recientes = FacturaBodega.query.order_by(FacturaBodega.fecha_subida.desc()).limit(10).all()
    
    return render_template('bodega/dashboard.html', clientes_count=total_clientes, facturas=facturas_recientes)

@bodega_bp.route('/clientes/nuevo', methods=['GET', 'POST'])
@login_required
@any_bodega_required
def nuevo_cliente():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        documento = request.form.get('documento')
        telefono = request.form.get('telefono')
        email = request.form.get('email')
        direccion = request.form.get('direccion')

        if not nombre or not documento or not telefono:
            flash('Por favor completa los campos obligatorios: Nombre, Documento y Teléfono.', 'danger')
            return redirect(url_for('bodega_bp.nuevo_cliente'))

        if Cliente.query.filter_by(documento_o_nit=documento.strip()).first():
            flash('Ya existe un cliente registrado con ese Documento/NIT.', 'warning')
            return redirect(url_for('bodega_bp.nuevo_cliente'))

        nuevo = Cliente(
            nombre_o_razon_social=nombre.strip(),
            documento_o_nit=documento.strip(),
            telefono=telefono.strip(),
            email=email.strip() if email else None,
            direccion=direccion.strip() if direccion else None,
            creado_por_id=current_user.id
        )
        try:
            db.session.add(nuevo)
            db.session.commit()
            flash(f'Cliente {nombre} registrado exitosamente.', 'success')
            return redirect(url_for('bodega_bp.dashboard'))
        except Exception as e:
            db.session.rollback()
            flash('Error al intentar registrar el cliente.', 'danger')

    return render_template('bodega/cliente_nuevo.html')

@bodega_bp.route('/clientes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@any_bodega_required
def editar_cliente(id):
    cliente = Cliente.query.get_or_404(id)
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        documento = request.form.get('documento')
        telefono = request.form.get('telefono')
        email = request.form.get('email')
        direccion = request.form.get('direccion')

        if not nombre or not documento or not telefono:
            flash('Por favor completa los campos obligatorios: Nombre, Documento y Teléfono.', 'danger')
            return redirect(url_for('bodega_bp.editar_cliente', id=id))

        cliente_existente = Cliente.query.filter_by(documento_o_nit=documento.strip()).first()
        if cliente_existente and cliente_existente.id != id:
            flash('Ya existe otro cliente registrado con ese Documento/NIT.', 'warning')
            return redirect(url_for('bodega_bp.editar_cliente', id=id))

        cliente.nombre_o_razon_social = nombre.strip()
        cliente.documento_o_nit = documento.strip()
        cliente.telefono = telefono.strip()
        cliente.email = email.strip() if email else None
        cliente.direccion = direccion.strip() if direccion else None

        try:
            db.session.commit()
            flash(f'Cliente {nombre} actualizado exitosamente.', 'success')
            return redirect(url_for('bodega_bp.clientes'))
        except Exception as e:
            db.session.rollback()
            flash('Error al intentar actualizar el cliente.', 'danger')

    return render_template('bodega/cliente_editar.html', cliente=cliente)

@bodega_bp.route('/facturas/nueva', methods=['GET', 'POST'])
@login_required
@any_bodega_required
def nueva_factura():
    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id')
        num_factura = request.form.get('numero_factura')
        monto_total = request.form.get('monto_total', 0.0)
        fecha_factura_str = request.form.get('fecha_factura')
        
        # Arrays of products and quantities
        productos_ids = request.form.getlist('producto_id[]')
        variantes_ids = request.form.getlist('variant_id[]')
        cantidades = request.form.getlist('cantidad[]')
        precios_unitarios = request.form.getlist('precio_unitario[]')
        
        if not productos_ids or not cantidades or not precios_unitarios:
            flash('Debes agregar al menos un producto a la factura.', 'danger')
            return redirect(url_for('bodega_bp.nueva_factura'))

        if len(productos_ids) != len(cantidades) or len(productos_ids) != len(precios_unitarios):
            flash('Error en los datos de los productos enviados.', 'danger')
            return redirect(url_for('bodega_bp.nueva_factura'))

        archivo_ruta_bd = None
        archivo = request.files.get('archivo_factura')
        if archivo and archivo.filename != '':
            if allowed_file(archivo.filename):
                filename = secure_filename(archivo.filename)
                unique_filename = f"fact_{cliente_id}_{num_factura}_{filename}"
                upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'facturas')
                
                os.makedirs(upload_path, exist_ok=True)
                file_path = os.path.join(upload_path, unique_filename)
                archivo.save(file_path)
                archivo_ruta_bd = f"uploads/facturas/{unique_filename}"
            else:
                flash('Tipo de archivo no permitido. Solo se permiten PDF e imágenes.', 'danger')
                return redirect(url_for('bodega_bp.nueva_factura'))

        from datetime import datetime
        fecha_obj = None
        if fecha_factura_str:
            try:
                # El formato de type="date" en HTML5 es YYYY-MM-DD
                fecha_obj = datetime.strptime(fecha_factura_str, '%Y-%m-%d')
            except ValueError:
                pass

        modalidad_pago = request.form.get('modalidad_pago', 'credito')
        
        try:
            nueva_fact = FacturaBodega(
                cliente_id=cliente_id,
                usuario_id=current_user.id,
                numero_factura=num_factura,
                monto_total=float(monto_total),
                archivo_ruta=archivo_ruta_bd,
                estado='Pagado' if modalidad_pago == 'contado' else 'Pendiente'
            )
            if fecha_obj:
                nueva_fact.fecha_subida = fecha_obj
                
            db.session.add(nueva_fact)
            db.session.flush() # Para obtener el ID de nueva_fact

            # Si es de contado, registrar el pago completo automáticamente
            if modalidad_pago == 'contado':
                abono = AbonoBodega(
                    factura_id=nueva_fact.id,
                    usuario_id=current_user.id,
                    monto=float(monto_total),
                    metodo_pago='efectivo',
                    observacion='Pago automático: Factura de Contado'
                )
                if fecha_obj:
                    abono.fecha_abono = fecha_obj
                db.session.add(abono)
                
            # Procesar productos y descontar el stock
            for i in range(len(productos_ids)):
                p_id = productos_ids[i]
                cant = int(cantidades[i])
                precio_uni = float(precios_unitarios[i])
                v_id_str = variantes_ids[i] if len(variantes_ids) > i else ""
                variant_id = int(v_id_str) if v_id_str.strip() else None

                producto = Product.query.get(p_id)
                variante = None
                
                if not producto:
                    db.session.rollback()
                    flash('Producto no encontrado.', 'danger')
                    return redirect(url_for('bodega_bp.nueva_factura'))

                if variant_id:
                    from models import ProductVariant
                    variante = ProductVariant.query.get(variant_id)
                    if not variante or variante.product_id != producto.id:
                        db.session.rollback()
                        flash(f'La subcategoría seleccionada no pertenece al producto {producto.nombre}.', 'danger')
                        return redirect(url_for('bodega_bp.nueva_factura'))
                    if variante.cantidad_stock < cant:
                        db.session.rollback()
                        flash(f'No hay stock suficiente para la subcategoría: {variante.nombre_variante}. Stock actual: {variante.cantidad_stock}', 'danger')
                        return redirect(url_for('bodega_bp.nueva_factura'))
                else:
                    if producto.cantidad_stock < cant:
                        db.session.rollback()
                        flash(f'No hay stock suficiente para el producto: {producto.nombre}. Stock actual: {producto.cantidad_stock}', 'danger')
                        return redirect(url_for('bodega_bp.nueva_factura'))
                
                # 1. Crear el Detalle
                detalle = FacturaBodegaDetalle(
                    factura_id=nueva_fact.id,
                    producto_id=producto.id,
                    variant_id=variant_id,
                    cantidad=cant,
                    precio_venta=precio_uni
                )
                db.session.add(detalle)
                
                # 2. Descontar Stock y Registrar Historial de Ajuste
                if variante:
                    stock_anterior = variante.cantidad_stock
                    variante.cantidad_stock -= cant
                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Salida de subcategoría {variante.nombre_variante} por Factura Bodega #{num_factura}",
                        stock_anterior=stock_anterior,
                        stock_nuevo=variante.cantidad_stock
                    )
                else:
                    stock_anterior = producto.cantidad_stock
                    producto.cantidad_stock -= cant
                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Salida por Factura Bodega #{num_factura}",
                        stock_anterior=stock_anterior,
                        stock_nuevo=producto.cantidad_stock
                    )
                db.session.add(ajuste)

            db.session.commit()
            flash('Factura guardada y stock de inventario descontado correctamente.', 'success')
            return redirect(url_for('bodega_bp.dashboard'))
            
        except Exception as e:
            db.session.rollback()
            flash('Ocurrió un error en la base de datos al guardar la factura o afectar el stock.', 'danger')

    clientes = Cliente.query.order_by(Cliente.nombre_o_razon_social).all()
    # Enviamos los productos disponibles a la vista, restringidos al inventario de bodega
    productos_disp = Product.query.filter_by(tipo_inventario='bodega').filter(Product.cantidad_stock > 0).order_by(Product.nombre).all()
    return render_template('bodega/factura_nueva.html', clientes=clientes, productos=productos_disp)

@bodega_bp.route('/api/producto/<path:sku>', methods=['GET'])
@login_required
@any_bodega_required
def api_buscar_producto_bodega(sku):
    producto = Product.query.filter_by(sku=sku, tipo_inventario='bodega').first()
    
    if not producto:
        return jsonify({'error': 'Código SKU no encontrado en bodega'}), 404
        
    return jsonify({
        'id': producto.id,
        'nombre': producto.nombre,
        'sku': producto.sku,
        'cantidad_stock': producto.total_stock,
        'precio_sugerido': float(producto.precio_sugerido),
        'precio_minimo': float(producto.precio_minimo),
        'precio_costo': float(producto.precio_costo),
        'variantes': [{"id": v.id, "nombre": v.nombre_variante, "stock": v.cantidad_stock, "precio_minimo": float(v.precio_minimo or producto.precio_minimo), "precio_limite": float(v.precio_costo or producto.precio_costo), "precio_sugerido": float(v.precio_sugerido or producto.precio_sugerido)} for v in producto.variantes]
    })

@bodega_bp.route('/clientes')
@login_required
@any_bodega_required
def clientes():
    if current_user.rol == 'vendedor_bodega':
        # Vendedor de bodega solo ve sus propios clientes en el listado de resumen
        lista_clientes = Cliente.query.filter_by(creado_por_id=current_user.id).order_by(Cliente.nombre_o_razon_social).all()
    else:
        # Bodega ve todo
        lista_clientes = Cliente.query.order_by(Cliente.nombre_o_razon_social).all()
    return render_template('bodega/clientes.html', clientes=lista_clientes)

@bodega_bp.route('/clientes/<int:id>')
@login_required
@any_bodega_required
def cliente_detalle(id):
    cliente = Cliente.query.get_or_404(id)
    return render_template('bodega/cliente_detalle.html', cliente=cliente)

@bodega_bp.route('/facturas/<int:factura_id>/abono', methods=['POST'])
@login_required
@any_bodega_required
def nuevo_abono(factura_id):
    factura = FacturaBodega.query.get_or_404(factura_id)
    monto_abono = float(request.form.get('monto_abono', 0.0))
    metodo_pago = request.form.get('metodo_pago', 'efectivo')
    observacion = request.form.get('observacion', '')

    if monto_abono <= 0:
        flash('El monto del abono debe ser mayor a cero.', 'danger')
        return redirect(url_for('bodega_bp.cliente_detalle', id=factura.cliente_id))

    if monto_abono > factura.saldo_pendiente:
        flash(f'El monto supera el saldo pendiente (${factura.saldo_pendiente}).', 'danger')
        return redirect(url_for('bodega_bp.cliente_detalle', id=factura.cliente_id))

    abono = AbonoBodega(
        factura_id=factura.id,
        usuario_id=current_user.id,
        monto=monto_abono,
        metodo_pago=metodo_pago,
        observacion=observacion
    )
    
    try:
        db.session.add(abono)
        db.session.commit()
        
        # Validar si el saldo quedó en cero
        if factura.saldo_pendiente <= 0:
            factura.estado = 'Pagado'
        else:
            factura.estado = 'Parcial'
        db.session.commit()

        flash(f'Abono de ${monto_abono} registrado correctamente a la factura #{factura.numero_factura}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Hubo un error al registrar el abono.', 'danger')

    return redirect(url_for('bodega_bp.cliente_detalle', id=factura.cliente_id))

@bodega_bp.route('/clientes/<int:id>/eliminar', methods=['POST'])
@login_required
@any_bodega_required
def eliminar_cliente(id):
    cliente = Cliente.query.get_or_404(id)

    # Seguridad: No permitir eliminar clientes que tengan deuda pendiente
    if cliente.deuda_total > 0:
        flash(f'No se puede eliminar a "{cliente.nombre_o_razon_social}" porque tiene una deuda pendiente de ${cliente.deuda_total}.', 'danger')
        return redirect(url_for('bodega_bp.clientes'))

    nombre = cliente.nombre_o_razon_social
    try:
        # Eliminar facturas asociadas (y sus abonos/detalles por cascade)
        for factura in cliente.facturas:
            db.session.delete(factura)
        db.session.delete(cliente)
        db.session.commit()
        flash(f'Cliente "{nombre}" eliminado exitosamente del directorio.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error al intentar eliminar el cliente.', 'danger')

    return redirect(url_for('bodega_bp.clientes'))
