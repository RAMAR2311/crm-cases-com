from flask import Blueprint, request, jsonify, flash, redirect, render_template, abort, url_for
from flask_login import login_required, current_user
from models import (
    db, Product, ProductVariant, Sale, SaleDetail, SalePayment,
    SaleClient, Expense, StockAdjustment, Retoma, PriceApproval,
    ProviderInvoice, obtener_hora_bogota
)
from decorators import admin_required
from decimal import Decimal
from datetime import datetime, timedelta
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

sales_bp = Blueprint('sales_bp', __name__)


def _ejecutar_procesamiento_venta(data):
    """
    Motor transaccional atómico compartido por /sales/nueva y /sales/procesar_venta.
    - Exclusivamente 4 métodos de pago: efectivo, nequi, bancolombia, daviplata.
    - Bloquea ventas debajo de precio_minimo con 400 Bad Request salvo que exista PriceApproval en estado 'aprobado'.
    - Descuenta stock, registra StockAdjustment, SaleDetail, SalePayment, Retoma en CUARENTENA y ProviderInvoice para externos.
    """
    items = data.get('items', [])
    pagos_data = data.get('pagos', [])
    metodo_pago_legacy = data.get('metodo_pago', 'efectivo')
    retoma_data = data.get('retoma')

    if not items:
        return jsonify({'error': 'No se enviaron productos para la venta'}), 400

    if not pagos_data:
        pagos_data = [{'metodo_pago': metodo_pago_legacy, 'monto': None}]

    try:
        if len(pagos_data) == 1:
            metodo_pago_principal = pagos_data[0].get('metodo_pago', 'efectivo')
        else:
            metodo_pago_principal = 'mixto'

        fecha_venta_str = data.get('fecha_venta')
        fecha_venta_obj = obtener_hora_bogota()
        if fecha_venta_str:
            try:
                fecha_seleccionada = datetime.strptime(fecha_venta_str, '%Y-%m-%d').date()
                if fecha_seleccionada != fecha_venta_obj.date():
                    fecha_venta_obj = datetime.combine(fecha_seleccionada, fecha_venta_obj.time())
            except ValueError:
                pass

        tipo_venta_detectado = None
        for item in items:
            es_manual = item.get('es_manual', False)
            es_obsequio_item = item.get('es_obsequio', False)

            if es_manual:
                tipo_item = 'general'
            else:
                prod_id = item.get('product_id')
                producto_check = Product.query.get(prod_id)
                if not producto_check:
                    return jsonify({'error': f"El producto con ID {prod_id} no existe."}), 400
                if producto_check.tipo_inventario == 'bodega':
                    return jsonify({'error': 'Los productos de Bodega Mayorista no pueden facturarse en la Caja de Tienda.'}), 400
                if producto_check.tiene_maneo_pendiente:
                    return jsonify({'error': f"El producto '{producto_check.nombre}' se encuentra prestado en Maneo activo y no puede venderse."}), 400

                es_cel = (
                    producto_check.tipo_inventario == 'celulares'
                    or (producto_check.tipo_inventario == 'externos' and producto_check.subtipo_externo == 'celular')
                )
                tipo_item = 'celulares' if es_cel else 'general'

            if not es_obsequio_item:
                if tipo_venta_detectado is None:
                    tipo_venta_detectado = tipo_item
                elif tipo_venta_detectado != tipo_item:
                    return jsonify({'error': 'No se pueden mezclar celulares con accesorios en la misma venta. Por favor, realice transacciones separadas para no descuadrar los arqueos. (Nota: Si es un obsequio, primero márcalo como regalo)'}), 400

        tipo_venta_detectado = tipo_venta_detectado or 'general'

        # Validar Retoma si viene en el payload
        monto_retoma_dec = Decimal('0.00')
        if retoma_data and isinstance(retoma_data, dict):
            valor_ret = Decimal(str(retoma_data.get('valor_recibido', '0') or '0'))
            imei_ret = str(retoma_data.get('imei', '') or '').strip()
            marca_ret = str(retoma_data.get('marca', '') or '').strip()
            modelo_ret = str(retoma_data.get('modelo_celular', '') or '').strip()
            if valor_ret > 0:
                if not imei_ret or not marca_ret or not modelo_ret:
                    return jsonify({'error': 'Para aplicar una Retoma debes completar IMEI, Marca y Modelo del equipo recibido.'}), 400
                monto_retoma_dec = valor_ret

        nueva_venta = Sale(
            vendedor_id=current_user.id,
            monto_total=Decimal('0.00'),
            monto_retoma=monto_retoma_dec,
            metodo_pago=metodo_pago_principal,
            fecha_venta=fecha_venta_obj,
            tipo_venta=tipo_venta_detectado
        )
        db.session.add(nueva_venta)
        db.session.flush()

        subtotal_bruto = Decimal('0.00')
        aprobaciones_usadas = []

        for item in items:
            product_id = item.get('product_id')
            variant_id = item.get('variant_id')
            cantidad_vendida = int(item.get('cantidad', 0))
            precio_venta_final = Decimal(str(item.get('precio_final', '0.00')))
            es_manual = item.get('es_manual', False)
            es_obsequio = bool(item.get('es_obsequio', False))

            if cantidad_vendida <= 0:
                raise ValueError("La cantidad vendida debe ser mayor a 0.")

            if es_manual:
                nombre_manual = item.get('nombre_manual', 'Producto Externo')
                precio_costo_manual = Decimal(str(item.get('precio_costo', '0.00')))
                proveedor_manual = (item.get('proveedor') or 'Local Externo').strip()

                detalle = SaleDetail(
                    sale_id=nueva_venta.id,
                    product_id=None,
                    variant_id=None,
                    cantidad_vendida=cantidad_vendida,
                    precio_venta_final=Decimal('0.00') if es_obsequio else precio_venta_final,
                    nombre_manual=nombre_manual,
                    precio_costo_manual=precio_costo_manual
                )
                db.session.add(detalle)
                if not es_obsequio:
                    subtotal_bruto += (precio_venta_final * cantidad_vendida)

                if precio_costo_manual > 0:
                    gasto_externo = Expense(
                        usuario_id=current_user.id,
                        tipo_gasto='Gasto Diario',
                        categoria='Pago Prod. Externo',
                        descripcion=f"Pago por producto manual prestado: {nombre_manual}",
                        monto=(precio_costo_manual * cantidad_vendida),
                        fecha_gasto=fecha_venta_obj
                    )
                    db.session.add(gasto_externo)

                    db.session.add(ProviderInvoice(
                        proveedor=proveedor_manual,
                        product_id=None,
                        sale_id=nueva_venta.id,
                        usuario_id=current_user.id,
                        concepto=f"{cantidad_vendida}x {nombre_manual} (Ticket #{nueva_venta.id})",
                        monto_costo=(precio_costo_manual * cantidad_vendida),
                        estado='PENDIENTE',
                        fecha_creacion=fecha_venta_obj
                    ))
            else:
                producto = Product.query.with_for_update().get(product_id)
                if not producto:
                    raise ValueError(f"El producto con ID {product_id} no existe.")

                if variant_id:
                    variante = ProductVariant.query.with_for_update().get(variant_id)
                    if not variante:
                        raise ValueError(f"La variante con ID {variant_id} no existe.")
                    if cantidad_vendida > variante.cantidad_stock:
                        raise ValueError(f"Stock insuficiente para la variante '{variante.nombre_variante}' de '{producto.nombre}'. Solicitado: {cantidad_vendida}, Disponible: {variante.cantidad_stock}.")

                    stock_anterior = variante.cantidad_stock
                    variante.cantidad_stock -= cantidad_vendida
                    if producto.cantidad_stock >= cantidad_vendida:
                        producto.cantidad_stock -= cantidad_vendida
                    precio_minimo_item = Decimal(str(variante.precio_minimo if variante.precio_minimo is not None else producto.precio_minimo))
                    precio_costo_item = Decimal(str(variante.precio_costo if variante.precio_costo is not None else producto.precio_costo))

                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Venta Tienda (Subcat: {variante.nombre_variante}) #{nueva_venta.id}",
                        motivo=f"Ticket #{nueva_venta.id}",
                        stock_anterior=stock_anterior,
                        stock_nuevo=variante.cantidad_stock
                    )
                    db.session.add(ajuste)
                else:
                    if cantidad_vendida > producto.cantidad_stock:
                        raise ValueError(f"Stock insuficiente para el producto '{producto.nombre}'. Solicitado: {cantidad_vendida}, Disponible: {producto.cantidad_stock}.")

                    stock_anterior = producto.cantidad_stock
                    producto.cantidad_stock -= cantidad_vendida
                    precio_minimo_item = Decimal(str(producto.precio_minimo or 0))
                    precio_costo_item = Decimal(str(producto.precio_costo or 0))

                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Venta Tienda #{nueva_venta.id}",
                        motivo=f"Ticket #{nueva_venta.id}" + (f" | IMEI: {producto.imei}" if producto.imei else ""),
                        stock_anterior=stock_anterior,
                        stock_nuevo=producto.cantidad_stock
                    )
                    db.session.add(ajuste)

                # Control estricto de Precio Mínimo mediante PriceApproval por cada Vendedor
                if not es_obsequio and precio_venta_final < precio_minimo_item:
                    # Si el usuario actual es el Administrador, puede facturar directamente o usar su aprobación
                    if current_user.rol == 'admin':
                        q_app = PriceApproval.query.filter_by(
                            product_id=producto.id,
                            estado='aprobado'
                        )
                        if variant_id:
                            q_app = q_app.filter_by(variant_id=variant_id)
                        aprobacion = q_app.order_by(PriceApproval.id.desc()).first()
                        if aprobacion:
                            aprobaciones_usadas.append(aprobacion)
                    else:
                        # Verificar primero si el vendedor actual tiene una solicitud PENDIENTE sin resolver
                        q_pend = PriceApproval.query.filter_by(
                            product_id=producto.id,
                            vendedor_id=current_user.id,
                            estado='pendiente'
                        )
                        if variant_id:
                            q_pend = q_pend.filter_by(variant_id=variant_id)
                        pendiente_activa = q_pend.order_by(PriceApproval.id.desc()).first()
                        if pendiente_activa:
                            raise ValueError(
                                f"⏳ AUTORIZACIÓN PENDIENTE: Tienes la solicitud #{pendiente_activa.id} para '{producto.nombre}' "
                                f"por ${Decimal(str(pendiente_activa.precio_solicitado)):,.0f} esperando respuesta del Administrador. "
                                f"No puedes facturar hasta que el Administrador la apruebe o la rechace."
                            )

                        # Buscar aprobación otorgada específicamente a este vendedor
                        q_app = PriceApproval.query.filter_by(
                            product_id=producto.id,
                            vendedor_id=current_user.id,
                            estado='aprobado'
                        )
                        if variant_id:
                            q_app = q_app.filter_by(variant_id=variant_id)
                        aprobacion = q_app.order_by(PriceApproval.id.desc()).first()

                        if not aprobacion or Decimal(str(aprobacion.precio_solicitado)) > precio_venta_final:
                            raise ValueError(
                                f"BLOQUEO DE PRECIO MÍNIMO: El precio digitado (${precio_venta_final:,.0f}) para '{producto.nombre}' "
                                f"es inferior al Precio Mínimo (${precio_minimo_item:,.0f}). Debes solicitar Autorización al Administrador y esperar su aprobación."
                            )
                        aprobaciones_usadas.append(aprobacion)

                detalle = SaleDetail(
                    sale_id=nueva_venta.id,
                    product_id=producto.id,
                    variant_id=variant_id,
                    cantidad_vendida=cantidad_vendida,
                    precio_venta_final=Decimal('0.00') if es_obsequio else precio_venta_final
                )
                db.session.add(detalle)

                if not es_obsequio:
                    subtotal_bruto += (precio_venta_final * cantidad_vendida)

                # Si es un Producto Externo (tipo_inventario == 'externos'), vincular o generar ProviderInvoice
                if producto.tipo_inventario == 'externos' and precio_costo_item > 0:
                    prov_nombre = (producto.proveedor or 'Proveedor Externo').strip()
                    factura_previa = ProviderInvoice.query.filter_by(
                        product_id=producto.id,
                        sale_id=None
                    ).order_by(ProviderInvoice.id.desc()).first()
                    if factura_previa:
                        factura_previa.sale_id = nueva_venta.id
                        if f"Ticket #{nueva_venta.id}" not in (factura_previa.concepto or ''):
                            factura_previa.concepto = f"{factura_previa.concepto} - Ticket #{nueva_venta.id}"[:255]
                    else:
                        db.session.add(ProviderInvoice(
                            proveedor=prov_nombre,
                            product_id=producto.id,
                            sale_id=nueva_venta.id,
                            usuario_id=current_user.id,
                            concepto=f"{cantidad_vendida}x {producto.nombre} ({producto.sku}) - Ticket #{nueva_venta.id}",
                            monto_costo=(precio_costo_item * cantidad_vendida),
                            estado='PENDIENTE',
                            fecha_creacion=fecha_venta_obj
                        ))

        if monto_retoma_dec > subtotal_bruto:
            raise ValueError(f"El valor de la retoma (${monto_retoma_dec:,.0f}) no puede superar el subtotal de la venta (${subtotal_bruto:,.0f}).")

        monto_total_neto = subtotal_bruto - monto_retoma_dec
        nueva_venta.monto_total = monto_total_neto

        # Marcar aprobaciones de precio utilizadas como 'completada'
        for ap in aprobaciones_usadas:
            ap.estado = 'completada'
            ap.sale_id = nueva_venta.id
            ap.fecha_resolucion = obtener_hora_bogota()

        # Registrar Retoma en estado CUARENTENA si aplica
        cliente_data = data.get('cliente')
        if monto_retoma_dec > 0 and retoma_data:
            nueva_retoma = Retoma(
                sale_id=nueva_venta.id,
                usuario_recibe_id=current_user.id,
                imei=str(retoma_data.get('imei')).strip(),
                imei2=str(retoma_data.get('imei2') or '').strip() or None,
                marca=str(retoma_data.get('marca')).strip(),
                modelo_celular=str(retoma_data.get('modelo_celular')).strip(),
                color=str(retoma_data.get('color') or '').strip() or None,
                memoria=str(retoma_data.get('memoria') or '').strip() or None,
                bateria=str(retoma_data.get('bateria') or '').strip() or None,
                valor_recibido=monto_retoma_dec,
                cliente_nombre=(cliente_data.get('nombre') if isinstance(cliente_data, dict) else retoma_data.get('cliente_nombre')),
                cliente_documento=(cliente_data.get('documento') if isinstance(cliente_data, dict) else retoma_data.get('cliente_documento')),
                cliente_telefono=(cliente_data.get('telefono') if isinstance(cliente_data, dict) else retoma_data.get('cliente_telefono')),
                observaciones=str(retoma_data.get('observaciones') or f"Recibido en parte de pago Ticket #{nueva_venta.id}").strip(),
                estado='CUARENTENA',
                fecha_recepcion=fecha_venta_obj
            )
            db.session.add(nueva_retoma)

        # Registrar los pagos mixtos en sale_payments
        total_pagos = Decimal('0.00')
        if monto_total_neto > 0:
            for pago_info in pagos_data:
                metodo = pago_info.get('metodo_pago', 'efectivo')
                monto_pago = pago_info.get('monto')

                if monto_pago is None:
                    monto_pago = monto_total_neto
                else:
                    monto_pago = Decimal(str(monto_pago))

                if monto_pago <= 0:
                    raise ValueError(f"El monto del pago por '{metodo}' debe ser mayor a 0.")

                pago = SalePayment(
                    sale_id=nueva_venta.id,
                    metodo_pago=metodo,
                    monto=monto_pago
                )
                db.session.add(pago)
                total_pagos += monto_pago

            if abs(total_pagos - monto_total_neto) > Decimal('0.01'):
                raise ValueError(f"La suma de los pagos (${total_pagos:,.0f}) no coincide con el total a pagar tras retoma (${monto_total_neto:,.0f}).")

        # Guardar datos del cliente (Obligatorio en ventas de celulares: Nombre, Dirección, Celular y Cédula)
        if tipo_venta_detectado == 'celulares':
            if not cliente_data or not isinstance(cliente_data, dict):
                raise ValueError("Para facturar un celular es obligatorio registrar los datos del cliente (Nombre, Cédula, Celular y Dirección).")
            nombre_cli = (cliente_data.get('nombre') or '').strip()
            doc_cli = (cliente_data.get('documento') or '').strip()
            tel_cli = (cliente_data.get('telefono') or '').strip()
            dir_cli = (cliente_data.get('direccion') or '').strip()
            if not nombre_cli or not doc_cli or not tel_cli or not dir_cli:
                raise ValueError("En ventas de celulares es obligatorio completar: Nombre, Cédula, Celular y Dirección del comprador.")

        if cliente_data and isinstance(cliente_data, dict):
            nombre_cli = (cliente_data.get('nombre') or '').strip()
            doc_cli = (cliente_data.get('documento') or '').strip()
            tel_cli = (cliente_data.get('telefono') or '').strip()
            if nombre_cli and doc_cli:
                cliente = SaleClient(
                    sale_id=nueva_venta.id,
                    nombre=nombre_cli,
                    documento=doc_cli,
                    telefono=tel_cli or 'N/A',
                    direccion=(cliente_data.get('direccion') or '').strip() or None,
                    email=(cliente_data.get('email') or '').strip() or None
                )
                db.session.add(cliente)

        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Venta registrada e inventario descontado con éxito.',
            'sale_id': nueva_venta.id,
            'total': str(monto_total_neto),
            'monto_retoma': str(monto_retoma_dec)
        }), 201

    except ValueError as val_err:
        db.session.rollback()
        return jsonify({'error': str(val_err)}), 400

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Ocurrió un error interno al procesar la venta: {str(e)}'}), 500


@sales_bp.route('/nueva', methods=['GET', 'POST'])
@login_required
def procesar_venta():
    if request.method == 'GET':
        return redirect(url_for('sales_bp.caja_visual'))
    return _ejecutar_procesamiento_venta(request.get_json() or {})


@sales_bp.route('/procesar_venta', methods=['POST'])
@login_required
def procesar_venta_endpoint():
    return _ejecutar_procesamiento_venta(request.get_json() or {})


# ══════════════════════════════════════════════════════════════════════════
# API DE BÚSQUEDA PARA CAJA POS (SKU, CÓDIGO DE BARRAS O IMEI)
# ══════════════════════════════════════════════════════════════════════════

@sales_bp.route('/api/producto/<path:sku>', methods=['GET'])
@login_required
def api_buscar_producto(sku):
    sku_clean = sku.strip()
    producto = Product.query.filter(
        or_(Product.sku == sku_clean, Product.imei == sku_clean),
        Product.tipo_inventario.in_(['tienda', 'celulares', 'externos'])
    ).first()
    auto_select_variant = None

    if not producto:
        variante = ProductVariant.query.join(Product).filter(
            Product.tipo_inventario == 'celulares',
            ProductVariant.nombre_variante.like(f"%{sku_clean}%")
        ).first()

        if variante:
            producto = variante.producto
            auto_select_variant = variante.id
        else:
            return jsonify({'error': 'Código SKU o IMEI no encontrado en el sistema'}), 404

    if producto.tiene_maneo_pendiente:
        return jsonify({'error': f'El equipo "{producto.nombre}" está prestado actualmente en Maneo y no puede venderse.'}), 400

    return jsonify({
        'id': producto.id,
        'nombre': producto.nombre,
        'sku': producto.sku,
        'imei': producto.imei,
        'tipo_inventario': producto.tipo_inventario,
        'subtipo_externo': producto.subtipo_externo,
        'proveedor': producto.proveedor,
        'cantidad_stock': producto.total_stock,
        'precio_minimo': float(producto.precio_minimo),
        'precio_limite': float(producto.precio_minimo),
        'precio_sugerido': float(producto.precio_sugerido),
        'variantes': [
            {
                "id": v.id,
                "nombre": v.nombre_variante,
                "stock": v.cantidad_stock,
                "precio_minimo": float(v.precio_minimo or producto.precio_minimo),
                "precio_limite": float(v.precio_minimo or producto.precio_minimo),
                "precio_sugerido": float(v.precio_sugerido or producto.precio_sugerido)
            }
            for v in producto.variantes
        ],
        'auto_select_variant': auto_select_variant
    })


# ══════════════════════════════════════════════════════════════════════════
# AUTORIZACIONES DE PRECIO MÍNIMO (PriceApproval) EN TIEMPO REAL
# ══════════════════════════════════════════════════════════════════════════

@sales_bp.route('/api/solicitar_precio', methods=['POST'])
@login_required
def api_solicitar_precio():
    data = request.get_json() or {}
    product_id = data.get('product_id')
    variant_id = data.get('variant_id') or None
    precio_solicitado = Decimal(str(data.get('precio_solicitado', '0') or '0'))
    motivo = (data.get('motivo') or 'Descuento especial solicitado en Caja').strip()

    prod = Product.query.get(product_id)
    if not prod:
        return jsonify({'error': 'Producto no encontrado.'}), 404

    precio_min = prod.precio_minimo
    if variant_id:
        var = ProductVariant.query.get(variant_id)
        if var and var.precio_minimo is not None:
            precio_min = var.precio_minimo

    if precio_solicitado <= 0:
        return jsonify({'error': 'El precio solicitado debe ser mayor a $0.'}), 400

    # Si ya existe una solicitud pendiente de este mismo vendedor para este mismo producto, actualizarla en vez de duplicarla
    existente_pend = PriceApproval.query.filter_by(
        product_id=prod.id,
        variant_id=variant_id,
        vendedor_id=current_user.id,
        estado='pendiente'
    ).first()

    # Solo el Admin auto-aprueba; un Vendedor SIEMPRE queda en 'pendiente' esperando al Admin
    auto_aprobar = (current_user.rol == 'admin')

    if existente_pend and not auto_aprobar:
        existente_pend.precio_solicitado = precio_solicitado
        existente_pend.motivo = motivo
        existente_pend.fecha_solicitud = obtener_hora_bogota()
        db.session.commit()
        aprobacion = existente_pend
    else:
        aprobacion = PriceApproval(
            product_id=prod.id,
            variant_id=variant_id,
            vendedor_id=current_user.id,
            admin_id=current_user.id if auto_aprobar else None,
            precio_minimo_original=precio_min,
            precio_solicitado=precio_solicitado,
            motivo=motivo,
            estado='aprobado' if auto_aprobar else 'pendiente',
            fecha_resolucion=obtener_hora_bogota() if auto_aprobar else None
        )
        db.session.add(aprobacion)
        db.session.commit()

    return jsonify({
        'success': True,
        'approval_id': aprobacion.id,
        'estado': aprobacion.estado,
        'precio_solicitado': float(aprobacion.precio_solicitado),
        'precio_minimo': float(precio_min or 0),
        'mensaje': (
            f'✅ Precio especial de ${float(precio_solicitado):,.0f} aplicado por Administrador.'
            if auto_aprobar else
            f'⏳ Solicitud #{aprobacion.id} enviada al Administrador. Tu facturación queda en espera hasta que el Administrador apruebe o rechace el precio.'
        )
    }), 201


@sales_bp.route('/api/aprobaciones/pendientes_admin', methods=['GET'])
@login_required
def api_aprobaciones_pendientes_admin():
    """Devuelve al Administrador todas las solicitudes de precio pendientes en tiempo real para la barra persistente."""
    if current_user.rol != 'admin':
        return jsonify({'count': 0, 'items': []}), 200

    pendientes = PriceApproval.query.filter_by(estado='pendiente').order_by(PriceApproval.fecha_solicitud.asc()).all()
    items = []
    for ap in pendientes:
        nom_prod = ap.producto.nombre if ap.producto else 'Producto'
        if ap.variante:
            nom_prod = f"{nom_prod} ({ap.variante.nombre_variante})"
        items.append({
            'id': ap.id,
            'product_id': ap.product_id,
            'variant_id': ap.variant_id,
            'producto_nombre': nom_prod,
            'sku': ap.producto.sku if ap.producto else '',
            'imei': ap.producto.imei if (ap.producto and ap.producto.imei) else '',
            'vendedor_id': ap.vendedor_id,
            'vendedor_nombre': ap.vendedor.nombre if ap.vendedor else 'Vendedor',
            'precio_minimo': float(ap.precio_minimo_original or 0),
            'precio_solicitado': float(ap.precio_solicitado or 0),
            'motivo': ap.motivo or 'Solicitud de descuento en Caja',
            'hora': ap.fecha_solicitud.strftime('%I:%M %p') if ap.fecha_solicitud else ''
        })
    return jsonify({'count': len(items), 'items': items}), 200


@sales_bp.route('/api/aprobaciones/resolver_ajax/<int:id>', methods=['POST'])
@login_required
@admin_required
def api_resolver_aprobacion_ajax(id):
    """Permite al Administrador aprobar o rechazar desde la notificación flotante sin cambiar de pantalla."""
    ap = PriceApproval.query.get_or_404(id)
    data = request.get_json() or {}
    accion = (data.get('accion') or request.form.get('accion') or 'aprobar').strip().lower()
    precio_autorizado_raw = data.get('precio_autorizado') or request.form.get('precio_autorizado')

    if accion == 'aprobar':
        ap.estado = 'aprobado'
        if precio_autorizado_raw:
            try:
                nuevo_p = Decimal(str(precio_autorizado_raw).replace(',', '').replace('.', ''))
                if nuevo_p > 0:
                    ap.precio_solicitado = nuevo_p
            except Exception:
                pass
    else:
        ap.estado = 'rechazado'

    ap.admin_id = current_user.id
    ap.fecha_resolucion = obtener_hora_bogota()
    db.session.commit()

    return jsonify({
        'success': True,
        'id': ap.id,
        'estado': ap.estado,
        'precio_autorizado': float(ap.precio_solicitado or 0),
        'precio_minimo': float(ap.precio_minimo_original or 0)
    }), 200


@sales_bp.route('/api/aprobaciones/estado_vendedor', methods=['GET'])
@login_required
def api_estado_aprobaciones_vendedor():
    """Permite a la Caja del vendedor que solicitó la autorización consultar en vivo si el Admin ya aprobó o rechazó."""
    ids_raw = (request.args.get('ids') or '').strip()
    ids_list = [int(x) for x in ids_raw.split(',') if x.strip().isdigit()]

    query = PriceApproval.query.filter_by(vendedor_id=current_user.id)
    if ids_list:
        aprobaciones = query.filter(PriceApproval.id.in_(ids_list)).all()
    else:
        aprobaciones = query.filter_by(estado='pendiente').all()

    resultado = []
    for ap in aprobaciones:
        resultado.append({
            'id': ap.id,
            'product_id': ap.product_id,
            'variant_id': ap.variant_id,
            'estado': ap.estado, # 'pendiente', 'aprobado', 'rechazado', 'completada'
            'precio_solicitado': float(ap.precio_solicitado or 0),
            'precio_minimo': float(ap.precio_minimo_original or 0),
            'producto_nombre': ap.producto.nombre if ap.producto else 'Producto'
        })
    tiene_pendientes = any(r['estado'] == 'pendiente' for r in resultado)
    return jsonify({
        'tiene_pendientes': tiene_pendientes,
        'aprobaciones': resultado
    }), 200


@sales_bp.route('/aprobaciones_precio', methods=['GET'])
@login_required
def aprobaciones_precio():
    aprobaciones = PriceApproval.query.order_by(PriceApproval.fecha_solicitud.desc()).limit(100).all()
    return render_template('sales/aprobaciones_precio.html', aprobaciones=aprobaciones)


@sales_bp.route('/aprobaciones_precio/resolver/<int:id>', methods=['POST'])
@login_required
@admin_required
def resolver_aprobacion_precio(id):
    ap = PriceApproval.query.get_or_404(id)
    accion = request.form.get('accion', 'aprobar')
    ap.estado = 'aprobado' if accion == 'aprobar' else 'rechazado'
    ap.admin_id = current_user.id
    ap.fecha_resolucion = obtener_hora_bogota()
    db.session.commit()
    flash(f'Solicitud de precio #{ap.id} marcada como {ap.estado.upper()}.', 'success')
    return redirect(request.referrer or url_for('sales_bp.aprobaciones_precio'))


# ══════════════════════════════════════════════════════════════════════════
# TICKET, HISTORIAL, VENTAS DE HOY Y CAJA RÁPIDA VISUAL
# ══════════════════════════════════════════════════════════════════════════

@sales_bp.route('/recibo/<int:sale_id>', methods=['GET'])
@login_required
def imprimir_ticket(sale_id):
    venta = Sale.query.get_or_404(sale_id)
    return render_template('sales/ticket.html', venta=venta)


@sales_bp.route('/historial', methods=['GET'])
@login_required
@admin_required
def historial():
    hoy_bogota = obtener_hora_bogota().strftime('%Y-%m-%d')
    fecha_inicio = request.args.get('fecha_inicio', hoy_bogota)
    fecha_fin = request.args.get('fecha_fin', hoy_bogota)

    query = Sale.query.options(joinedload(Sale.vendedor))

    if fecha_inicio:
        inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
        query = query.filter(Sale.fecha_venta >= inicio_dt)

    if fecha_fin:
        fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d')
        query = query.filter(Sale.fecha_venta < fin_dt + timedelta(days=1))

    ventas = query.order_by(Sale.fecha_venta.desc()).all()

    total_efectivo = Decimal('0')
    total_nequi = Decimal('0')
    total_bancolombia = Decimal('0')
    total_daviplata = Decimal('0')
    total_transferencia_legacy = Decimal('0')
    total_mixto = 0

    for v in ventas:
        if v.pagos:
            for pago in v.pagos:
                if pago.metodo_pago == 'efectivo':
                    total_efectivo += pago.monto
                elif pago.metodo_pago == 'nequi':
                    total_nequi += pago.monto
                elif pago.metodo_pago == 'bancolombia':
                    total_bancolombia += pago.monto
                elif pago.metodo_pago == 'daviplata':
                    total_daviplata += pago.monto
                elif pago.metodo_pago == 'transferencia':
                    total_transferencia_legacy += pago.monto
            if len(v.pagos) > 1:
                total_mixto += 1
        else:
            if v.metodo_pago == 'efectivo':
                total_efectivo += v.monto_total
            elif v.metodo_pago == 'nequi':
                total_nequi += v.monto_total
            elif v.metodo_pago == 'bancolombia':
                total_bancolombia += v.monto_total
            elif v.metodo_pago == 'daviplata':
                total_daviplata += v.monto_total
            elif v.metodo_pago == 'transferencia':
                total_transferencia_legacy += v.monto_total

    total_general = total_efectivo + total_nequi + total_bancolombia + total_daviplata + total_transferencia_legacy

    return render_template(
        'sales/historial.html',
        ventas=ventas,
        total_general=total_general,
        total_efectivo=total_efectivo,
        total_nequi=total_nequi,
        total_bancolombia=total_bancolombia,
        total_daviplata=total_daviplata,
        total_transferencia_legacy=total_transferencia_legacy,
        total_mixto=total_mixto,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin
    )


@sales_bp.route('/ventas_hoy', methods=['GET'])
@login_required
def ventas_hoy():
    # Día exacto en Hora Colombia (UTC-5: 00:00:00 a 23:59:59)
    hoy_bogota = obtener_hora_bogota().date()
    inicio_dt = datetime.combine(hoy_bogota, datetime.min.time())
    fin_dt = datetime.combine(hoy_bogota, datetime.max.time())

    query = Sale.query.options(
        joinedload(Sale.vendedor),
        joinedload(Sale.pagos),
        joinedload(Sale.detalles).joinedload(SaleDetail.producto)
    ).filter(
        Sale.fecha_venta >= inicio_dt,
        Sale.fecha_venta <= fin_dt
    )

    # Seguridad por rol: admin ve todas las ventas del día; vendedor solo ve las suyas
    if current_user.rol != 'admin':
        query = query.filter(Sale.vendedor_id == current_user.id)

    ventas = query.order_by(Sale.fecha_venta.desc()).all()

    total_efectivo = Decimal('0')
    total_transferencias = Decimal('0')
    total_nequi = Decimal('0')
    total_bancolombia = Decimal('0')
    total_daviplata = Decimal('0')
    total_mixto = 0

    for v in ventas:
        if v.pagos:
            for pago in v.pagos:
                metodo = (pago.metodo_pago or '').lower()
                monto = pago.monto or Decimal('0')
                if metodo == 'efectivo':
                    total_efectivo += monto
                else:
                    total_transferencias += monto
                    if metodo == 'nequi':
                        total_nequi += monto
                    elif metodo == 'bancolombia':
                        total_bancolombia += monto
                    elif metodo == 'daviplata':
                        total_daviplata += monto
            if len(v.pagos) > 1:
                total_mixto += 1
        else:
            metodo = (v.metodo_pago or '').lower()
            monto = v.monto_total or Decimal('0')
            if metodo == 'efectivo':
                total_efectivo += monto
            else:
                total_transferencias += monto
                if metodo == 'nequi':
                    total_nequi += monto
                elif metodo == 'bancolombia':
                    total_bancolombia += monto
                elif metodo == 'daviplata':
                    total_daviplata += monto

    total_general = total_efectivo + total_transferencias

    return render_template(
        'sales/ventas_hoy.html',
        ventas=ventas,
        total_efectivo=total_efectivo,
        total_transferencias=total_transferencias,
        total_nequi=total_nequi,
        total_bancolombia=total_bancolombia,
        total_daviplata=total_daviplata,
        total_general=total_general,
        total_mixto=total_mixto,
        hoy=hoy_bogota.strftime('%Y-%m-%d')
    )


@sales_bp.route('/eliminar/<int:sale_id>', methods=['POST'])
@login_required
@admin_required
def eliminar_venta(sale_id):
    venta = Sale.query.get_or_404(sale_id)

    try:
        for detalle in venta.detalles:
            if detalle.variant_id:
                variante = ProductVariant.query.with_for_update().get(detalle.variant_id)
                if variante:
                    stock_anterior = variante.cantidad_stock
                    variante.cantidad_stock += detalle.cantidad_vendida

                    ajuste = StockAdjustment(
                        product_id=detalle.product_id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Anulación Venta #{venta.id} (Subcat: {variante.nombre_variante})",
                        stock_anterior=stock_anterior,
                        stock_nuevo=variante.cantidad_stock
                    )
                    db.session.add(ajuste)

                producto = Product.query.with_for_update().get(detalle.product_id)
                if producto:
                    producto.cantidad_stock += detalle.cantidad_vendida
            elif detalle.product_id:
                producto = Product.query.with_for_update().get(detalle.product_id)
                if producto:
                    stock_anterior = producto.cantidad_stock
                    producto.cantidad_stock += detalle.cantidad_vendida

                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Anulación Venta #{venta.id}",
                        stock_anterior=stock_anterior,
                        stock_nuevo=producto.cantidad_stock
                    )
                    db.session.add(ajuste)

        db.session.delete(venta)
        db.session.commit()
        flash('Venta anulada y stock devuelto exitosamente.', 'success')

    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al anular la venta.', 'danger')

    return redirect(url_for('sales_bp.historial'))


@sales_bp.route('/catalogo', methods=['GET'])
@login_required
def catalogo():
    query_str = request.args.get('q', '').strip()
    categoria = request.args.get('categoria', 'todos').strip().lower() # 'todos', 'accesorios', 'celulares'

    base_query = Product.query.filter(Product.tipo_inventario.in_(['tienda', 'celulares', 'externos']))

    if query_str:
        search_term = f"%{query_str}%"
        base_query = base_query.filter(
            or_(
                Product.sku.ilike(search_term),
                Product.nombre.ilike(search_term),
                Product.imei.ilike(search_term),
                Product.marca.ilike(search_term),
                Product.modelo_celular.ilike(search_term)
            )
        )

    todos_raw = base_query.order_by(Product.nombre.asc()).all()
    # Excluir registros históricos de IMEI archivados (-OLD-)
    todos_limpios = [
        p for p in todos_raw
        if '-OLD-' not in (p.sku or '') and '-OLD-' not in (p.imei or '')
    ]

    accesorios_list = [
        p for p in todos_limpios
        if p.tipo_inventario == 'tienda' or (p.tipo_inventario == 'externos' and p.subtipo_externo != 'celular')
    ]
    celulares_list = [
        p for p in todos_limpios
        if p.tipo_inventario == 'celulares' or (p.tipo_inventario == 'externos' and p.subtipo_externo == 'celular')
    ]

    if categoria == 'accesorios':
        productos = accesorios_list
    elif categoria == 'celulares':
        productos = celulares_list
    else:
        categoria = 'todos'
        productos = todos_limpios

    return render_template(
        'sales/catalogo.html',
        productos=productos,
        q=query_str,
        categoria=categoria,
        total_todos=len(todos_limpios),
        total_accesorios=len(accesorios_list),
        total_celulares=len(celulares_list)
    )


@sales_bp.route('/caja_visual', methods=['GET'])
@login_required
def caja_visual():
    from routes.externos import obtener_proveedores_registrados
    hoy_bogota = obtener_hora_bogota()
    todos = Product.query.filter(
        Product.tipo_inventario.in_(['tienda', 'celulares', 'externos'])
    ).order_by(Product.nombre.asc()).all()
    # Excluir equipos actualmente prestados en Maneo pendiente y registros históricos archivados (-OLD-)
    productos = [
        p for p in todos
        if not p.tiene_maneo_pendiente
        and '-OLD-' not in (p.sku or '')
        and '-OLD-' not in (p.imei or '')
        and (p.tipo_inventario != 'celulares' or (p.cantidad_stock or 0) > 0)
    ]
    return render_template(
        'sales/caja_visual.html',
        productos=productos,
        proveedores_registrados=obtener_proveedores_registrados(),
        hoy=hoy_bogota.strftime('%Y-%m-%d')
    )
