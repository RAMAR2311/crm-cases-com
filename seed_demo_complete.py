import os
import sys
from datetime import timedelta
from decimal import Decimal
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import (
    db, User, Product, ProductVariant, Sale, SaleDetail, SalePayment,
    SaleClient, StockAdjustment, Maneo, Retoma, PriceApproval,
    ProviderInvoice, Expense, obtener_hora_bogota
)


def seed_all_modules():
    app = create_app()
    with app.app_context():
        ahora = obtener_hora_bogota()
        print("═════════════════════════════════════════════════════════════════")
        print(" INICIANDO CARGA DE EJEMPLOS EN TODOS LOS MÓDULOS (CASES.COM)")
        print(" (Respetando al 100% los datos existentes de Bodega)")
        print("═════════════════════════════════════════════════════════════════")

        # 0. Usuarios base (Admin y Vendedor de Tienda)
        admin = User.query.filter_by(email='admin@cases.com').first()
        if not admin:
            admin = User(
                nombre='Administrador Principal',
                email='admin@cases.com',
                telefono='3001112233',
                password_hash=generate_password_hash('Admin123'),
                rol='admin'
            )
            db.session.add(admin)

        vendedor = User.query.filter_by(email='vendedor@cases.com').first()
        if not vendedor:
            vendedor = User(
                nombre='Camila Vendedora Tienda',
                email='vendedor@cases.com',
                telefono='3104445566',
                password_hash=generate_password_hash('Vendedor123'),
                rol='vendedor'
            )
            db.session.add(vendedor)
        db.session.commit()

        # Helper para crear producto si no existe su SKU
        def get_or_create_product(sku, **kwargs):
            p = Product.query.filter_by(sku=sku).first()
            if not p:
                p = Product(sku=sku, **kwargs)
                db.session.add(p)
                db.session.flush()
            return p

        # ══════════════════════════════════════════════════════════════════
        # 1. MÓDULO CELULARES (tipo_inventario = 'celulares')
        # ══════════════════════════════════════════════════════════════════
        print("[1/7] Sembrando Inventario de Celulares por IMEI...")

        cel1 = get_or_create_product(
            sku='CEL-358901234567890',
            nombre='Apple iPhone 15 Pro Max 256GB Titanio Natural',
            tipo_inventario='celulares',
            cantidad_stock=1,
            precio_costo=Decimal('4150000.00'),
            precio_minimo=Decimal('4550000.00'),
            precio_sugerido=Decimal('4850000.00'),
            imei='358901234567890',
            imei2='358901234567891',
            marca='Apple',
            modelo_celular='iPhone 15 Pro Max',
            estado_celular='Nuevo',
            color='Titanio Natural',
            bateria='100%',
            memoria='256GB',
            proveedor='Apple Distribución Mayorista',
            observacion='Sellado en caja original, garantía 1 año'
        )

        cel2 = get_or_create_product(
            sku='CEL-357766554433221',
            nombre='Samsung Galaxy S24 Ultra 512GB Gris Titanio',
            tipo_inventario='celulares',
            cantidad_stock=1,
            precio_costo=Decimal('3900000.00'),
            precio_minimo=Decimal('4300000.00'),
            precio_sugerido=Decimal('4600000.00'),
            imei='357766554433221',
            marca='Samsung',
            modelo_celular='Galaxy S24 Ultra',
            estado_celular='Nuevo',
            color='Gris Titanio',
            bateria='100%',
            memoria='512GB / 12GB',
            proveedor='Samsung Partner Colombia',
            observacion='Nuevo homologado con S-Pen'
        )

        cel3 = get_or_create_product(
            sku='CEL-354433221100998',
            nombre='Apple iPhone 13 Pro 128GB Azul Sierra',
            tipo_inventario='celulares',
            cantidad_stock=1,
            precio_costo=Decimal('2100000.00'),
            precio_minimo=Decimal('2450000.00'),
            precio_sugerido=Decimal('2650000.00'),
            imei='354433221100998',
            marca='Apple',
            modelo_celular='iPhone 13 Pro',
            estado_celular='Usado',
            color='Azul Sierra',
            bateria='91%',
            memoria='128GB',
            proveedor='Retoma Cliente Verificada',
            observacion='Seminuevo estado 10/10 con cable USB-C a Lightning'
        )

        # Celular 4: Prestado en MANEO PENDIENTE (para ver alerta visual en inventario)
        cel_maneo = get_or_create_product(
            sku='CEL-352211009988776',
            nombre='Apple iPhone 14 Pro 256GB Morado Oscuro',
            tipo_inventario='celulares',
            cantidad_stock=0, # Descontado mientras está en maneo pendiente
            precio_costo=Decimal('3100000.00'),
            precio_minimo=Decimal('3450000.00'),
            precio_sugerido=Decimal('3690000.00'),
            imei='352211009988776',
            marca='Apple',
            modelo_celular='iPhone 14 Pro',
            estado_celular='Usado',
            color='Morado Oscuro',
            bateria='94%',
            memoria='256GB',
            proveedor='Importadora Mobile',
            observacion='Prestado en vitrina de local vecino'
        )

        if not Maneo.query.filter_by(product_id=cel_maneo.id, estado='PENDIENTE').first():
            db.session.add(Maneo(
                product_id=cel_maneo.id,
                local_vecino='Local 204 - TechCell Alex',
                cantidad=1,
                estado='PENDIENTE',
                fecha_prestamo=ahora - timedelta(hours=3)
            ))

        # Celular 5: Ya vendido hoy (con ciclo previo archivado -OLD-1 para probar Trazabilidad IMEI completa)
        cel_old_cycle = get_or_create_product(
            sku='CEL-359900112233445-OLD-1',
            nombre='Apple iPhone 14 128GB Medianoche',
            tipo_inventario='celulares',
            cantidad_stock=0,
            precio_costo=Decimal('2400000.00'),
            precio_minimo=Decimal('2700000.00'),
            precio_sugerido=Decimal('2900000.00'),
            imei='359900112233445-OLD-1',
            marca='Apple',
            modelo_celular='iPhone 14',
            estado_celular='Nuevo',
            color='Medianoche',
            bateria='100%',
            memoria='128GB',
            proveedor='Apple Distribución Mayorista',
            observacion='Primer ingreso cuando era nuevo',
            fecha_creacion=ahora - timedelta(days=45)
        )

        cel_vendido_hoy = get_or_create_product(
            sku='CEL-359900112233445',
            nombre='Apple iPhone 14 128GB Medianoche',
            tipo_inventario='celulares',
            cantidad_stock=0, # Vendido hoy
            precio_costo=Decimal('2150000.00'),
            precio_minimo=Decimal('2450000.00'),
            precio_sugerido=Decimal('2600000.00'),
            imei='359900112233445',
            marca='Apple',
            modelo_celular='iPhone 14',
            estado_celular='Usado',
            color='Medianoche',
            bateria='95%',
            memoria='128GB',
            proveedor='Retoma Aprobada #1',
            observacion='Reingreso por retoma, vendido nuevamente hoy',
            fecha_creacion=ahora - timedelta(days=2)
        )

        # ══════════════════════════════════════════════════════════════════
        # 2. MÓDULO ACCESORIOS (tipo_inventario = 'tienda') CON VARIANTES
        # ══════════════════════════════════════════════════════════════════
        print("[2/7] Sembrando Inventario de Accesorios de Tienda y Subcategorías...")

        acc1 = get_or_create_product(
            sku='ACC-CASE-15PM',
            nombre='Funda Silicone Case MagSafe iPhone 15 Pro Max',
            tipo_inventario='tienda',
            cantidad_stock=0,
            precio_costo=Decimal('22000.00'),
            precio_minimo=Decimal('40000.00'),
            precio_sugerido=Decimal('55000.00'),
            observacion='Estuche silicona gamuza interna con animación MagSafe'
        )
        if not acc1.variantes:
            db.session.add_all([
                ProductVariant(product_id=acc1.id, nombre_variante='Negro Mate', cantidad_stock=18, precio_costo=22000, precio_minimo=40000, precio_sugerido=55000),
                ProductVariant(product_id=acc1.id, nombre_variante='Rosa Pastel (Cases Edition)', cantidad_stock=14, precio_costo=24000, precio_minimo=45000, precio_sugerido=65000),
                ProductVariant(product_id=acc1.id, nombre_variante='Azul Tormenta (Stock Bajo)', cantidad_stock=4, precio_costo=22000, precio_minimo=40000, precio_sugerido=55000),
            ])

        acc2 = get_or_create_product(
            sku='ACC-CARG-20W',
            nombre='Adaptador de Corriente USB-C 20W Carga Rápida Original',
            tipo_inventario='tienda',
            cantidad_stock=25,
            precio_costo=Decimal('35000.00'),
            precio_minimo=Decimal('65000.00'),
            precio_sugerido=Decimal('85000.00'),
            observacion='Cubo carga rápida 20W con serial verificable'
        )

        acc3 = get_or_create_product(
            sku='ACC-VID-9D',
            nombre='Vidrio Templado Cerámico Antiespía 9D',
            tipo_inventario='tienda',
            cantidad_stock=0,
            precio_costo=Decimal('6000.00'),
            precio_minimo=Decimal('18000.00'),
            precio_sugerido=Decimal('25000.00'),
            observacion='Privacidad 180 grados bordes reforzados'
        )
        if not acc3.variantes:
            db.session.add_all([
                ProductVariant(product_id=acc3.id, nombre_variante='iPhone 13 / 14 / 15', cantidad_stock=30, precio_costo=6000, precio_minimo=18000, precio_sugerido=25000),
                ProductVariant(product_id=acc3.id, nombre_variante='iPhone 15 Pro Max', cantidad_stock=22, precio_costo=7000, precio_minimo=20000, precio_sugerido=30000),
            ])

        acc4 = get_or_create_product(
            sku='ACC-AIRP-PRO2',
            nombre='AirPods Pro (2da Generación) con Estuche MagSafe USB-C',
            tipo_inventario='tienda',
            cantidad_stock=6,
            precio_costo=Decimal('95000.00'),
            precio_minimo=Decimal('150000.00'),
            precio_sugerido=Decimal('190000.00'),
            observacion='Cancelación activa de ruido OEM AAA+'
        )

        # ══════════════════════════════════════════════════════════════════
        # 3. PRODUCTOS EXTERNOS (tipo_inventario = 'externos')
        # ══════════════════════════════════════════════════════════════════
        print("[3/7] Sembrando Celulares Externos y Accesorios Externos en Consignación...")

        ext_cel1 = get_or_create_product(
            sku='EXT-20261126101500',
            nombre='Apple iPhone 15 128GB Rosa',
            tipo_inventario='externos',
            subtipo_externo='celular',
            cantidad_stock=1,
            precio_costo=Decimal('2950000.00'),
            precio_minimo=Decimal('3250000.00'),
            precio_sugerido=Decimal('3450000.00'),
            imei='356677889900112',
            marca='Apple',
            modelo_celular='iPhone 15',
            estado_celular='Nuevo',
            color='Rosa',
            bateria='100%',
            memoria='128GB',
            proveedor='Bodega Celulares San Andresito - Mauricio',
            observacion='En consignación para venta o nacionalización'
        )

        ext_cel2 = get_or_create_product(
            sku='EXT-20261126113000',
            nombre='Xiaomi Redmi Note 13 Pro+ 5G 256GB Negro',
            tipo_inventario='externos',
            subtipo_externo='celular',
            cantidad_stock=1,
            precio_costo=Decimal('1350000.00'),
            precio_minimo=Decimal('1550000.00'),
            precio_sugerido=Decimal('1690000.00'),
            imei='861122334455667',
            marca='Xiaomi',
            modelo_celular='Redmi Note 13 Pro+ 5G',
            estado_celular='Nuevo',
            color='Negro Estelar',
            bateria='100%',
            memoria='256GB / 12GB',
            proveedor='Distribuidora Oriental Mobile',
            observacion='Equipo externo disponible en vitrina'
        )

        ext_acc1 = get_or_create_product(
            sku='EXTACC-20261126120000',
            nombre='PowerBank Baseus MagSafe 10.000mAh 20W',
            tipo_inventario='externos',
            subtipo_externo='accesorio',
            cantidad_stock=5,
            precio_costo=Decimal('85000.00'),
            precio_minimo=Decimal('125000.00'),
            precio_sugerido=Decimal('149000.00'),
            proveedor='Accesorios Premium Local 118',
            observacion='Batería magnética inalámbrica en consignación'
        )

        ext_acc_vendido = get_or_create_product(
            sku='EXTACC-20261126094500',
            nombre='Smartwatch Apple Watch Ultra Strap Titanium + Case',
            tipo_inventario='externos',
            subtipo_externo='accesorio',
            cantidad_stock=3,
            precio_costo=Decimal('65000.00'),
            precio_minimo=Decimal('95000.00'),
            precio_sugerido=Decimal('120000.00'),
            proveedor='Accesorios Premium Local 118',
            observacion='Pulsera de titanio en consignación'
        )

        db.session.commit()

        # ══════════════════════════════════════════════════════════════════
        # 4. RETOMAS EN CUARENTENA (/retomas/cuarentena)
        # ══════════════════════════════════════════════════════════════════
        print("[4/7] Sembrando Retomas en estado CUARENTENA y APROBADA...")

        if not Retoma.query.filter_by(imei='351234567890123').first():
            db.session.add(Retoma(
                usuario_recibe_id=vendedor.id,
                imei='351234567890123',
                marca='Apple',
                modelo_celular='iPhone 12 Pro',
                color='Grafito',
                memoria='128GB',
                bateria='86%',
                valor_recibido=Decimal('1350000.00'),
                cliente_nombre='Andrés Felipe Restrepo',
                cliente_documento='1020765432',
                cliente_telefono='3158889900',
                observaciones='Recibido en parte de pago. Pantalla intacta, revisar limpieza de auricular antes de pasar a stock.',
                estado='CUARENTENA',
                fecha_recepcion=ahora - timedelta(hours=2)
            ))

        if not Retoma.query.filter_by(imei='359876543210987').first():
            db.session.add(Retoma(
                usuario_recibe_id=admin.id,
                imei='359876543210987',
                marca='Samsung',
                modelo_celular='Galaxy S22',
                color='Verde Oscuro',
                memoria='256GB',
                bateria='90%',
                valor_recibido=Decimal('1100000.00'),
                cliente_nombre='Valentina Morales',
                cliente_documento='1018445566',
                cliente_telefono='3207771122',
                observaciones='Entregado con caja original. Pendiente definir precio sugerido por Admin.',
                estado='CUARENTENA',
                fecha_recepcion=ahora - timedelta(hours=1)
            ))

        if not Retoma.query.filter_by(imei='359900112233445', estado='APROBADA').first():
            db.session.add(Retoma(
                usuario_recibe_id=vendedor.id,
                usuario_aprueba_id=admin.id,
                product_id_generado=cel_vendido_hoy.id,
                imei='359900112233445',
                marca='Apple',
                modelo_celular='iPhone 14',
                color='Medianoche',
                memoria='128GB',
                bateria='95%',
                valor_recibido=Decimal('2100000.00'),
                costo_real=Decimal('2150000.00'),
                precio_minimo=Decimal('2450000.00'),
                precio_sugerido=Decimal('2600000.00'),
                cliente_nombre='Santiago Gómez',
                cliente_documento='80112233',
                cliente_telefono='3112223344',
                observaciones='Reingreso de IMEI vendido hace 45 días. Archivado ciclo previo como 359900112233445-OLD-1.',
                estado='APROBADA',
                fecha_recepcion=ahora - timedelta(days=3),
                fecha_aprobacion=ahora - timedelta(days=2)
            ))

        # ══════════════════════════════════════════════════════════════════
        # 5. AUTORIZACIONES DE PRECIO MÍNIMO (PriceApproval)
        # ══════════════════════════════════════════════════════════════════
        print("[5/7] Sembrando Solicitudes de Autorización de Precio Mínimo...")

        if PriceApproval.query.count() == 0:
            # 1 Pendiente de aprobar por el Admin
            db.session.add(PriceApproval(
                product_id=cel1.id,
                vendedor_id=vendedor.id,
                precio_minimo_original=Decimal('4550000.00'),
                precio_solicitado=Decimal('4480000.00'),
                motivo='Cliente paga de contado en efectivo y compra combo de accesorios',
                estado='pendiente',
                fecha_solicitud=ahora - timedelta(minutes=25)
            ))
            # 1 Ya Aprobada lista para usar en Caja con el Samsung S24 Ultra
            db.session.add(PriceApproval(
                product_id=cel2.id,
                vendedor_id=vendedor.id,
                admin_id=admin.id,
                precio_minimo_original=Decimal('4300000.00'),
                precio_solicitado=Decimal('4200000.00'),
                motivo='Cliente frecuente corporativo autorizado por Gerencia',
                estado='aprobado',
                fecha_solicitud=ahora - timedelta(minutes=50),
                fecha_resolucion=ahora - timedelta(minutes=40)
            ))

        # ══════════════════════════════════════════════════════════════════
        # 6. VENTAS DE HOY, CLIENTES MINTIC/SIC Y CUENTAS POR PAGAR EXTERNOS
        # ══════════════════════════════════════════════════════════════════
        print("[6/7] Sembrando Ventas de Hoy (Efectivo, Nequi, Bancolombia, Daviplata y Pago Mixto)...")

        hoy_inicio = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
        ventas_hoy_existentes = Sale.query.filter(Sale.fecha_venta >= hoy_inicio).count()

        if ventas_hoy_existentes == 0:
            # VENTA 1: Venta de Celular (Hoy) con Pago Mixto (Efectivo + Bancolombia) + Cliente MinTIC/SIC
            v1 = Sale(
                vendedor_id=admin.id,
                fecha_venta=ahora - timedelta(hours=2, minutes=15),
                monto_total=Decimal('2600000.00'),
                monto_retoma=Decimal('0.00'),
                metodo_pago='mixto',
                tipo_venta='celulares'
            )
            db.session.add(v1)
            db.session.flush()

            db.session.add(SaleDetail(
                sale_id=v1.id,
                product_id=cel_vendido_hoy.id,
                cantidad_vendida=1,
                precio_venta_final=Decimal('2600000.00')
            ))
            db.session.add_all([
                SalePayment(sale_id=v1.id, metodo_pago='efectivo', monto=Decimal('1600000.00')),
                SalePayment(sale_id=v1.id, metodo_pago='bancolombia', monto=Decimal('1000000.00')),
            ])
            db.session.add(SaleClient(
                sale_id=v1.id,
                nombre='Laura Sofía Mendoza',
                documento='1032456789',
                telefono='3145678901',
                direccion='Cra 15 # 85-32 Of. 402, Bogotá',
                email='laura.mendoza@empresa.co'
            ))

            # VENTA 2: Venta de Celular Nuevo hoy (iPhone 13 Pro) con Retoma aplicada + Nequi
            v_cel2 = Sale(
                vendedor_id=vendedor.id,
                fecha_venta=ahora - timedelta(hours=1, minutes=50),
                monto_total=Decimal('1300000.00'),
                monto_retoma=Decimal('1350000.00'),
                metodo_pago='nequi',
                tipo_venta='celulares'
            )
            db.session.add(v_cel2)
            db.session.flush()

            db.session.add(SaleDetail(
                sale_id=v_cel2.id,
                product_id=cel3.id,
                cantidad_vendida=1,
                precio_venta_final=Decimal('2650000.00')
            ))
            db.session.add(SalePayment(
                sale_id=v_cel2.id,
                metodo_pago='nequi',
                monto=Decimal('1300000.00')
            ))
            db.session.add(SaleClient(
                sale_id=v_cel2.id,
                nombre='Andrés Felipe Restrepo',
                documento='1020765432',
                telefono='3158889900',
                direccion='Av. Suba # 115-50, Bogotá',
                email='andres.restrepo@correo.com'
            ))

            # VENTA 3: Venta de Accesorios Propios en Efectivo (Alimentará el Arqueo General)
            var_rosa = ProductVariant.query.filter_by(product_id=acc1.id, nombre_variante='Rosa Pastel (Cases Edition)').first()
            v2 = Sale(
                vendedor_id=vendedor.id,
                fecha_venta=ahora - timedelta(hours=1, minutes=20),
                monto_total=Decimal('150000.00'),
                monto_retoma=Decimal('0.00'),
                metodo_pago='efectivo',
                tipo_venta='general'
            )
            db.session.add(v2)
            db.session.flush()

            db.session.add_all([
                SaleDetail(sale_id=v2.id, product_id=acc1.id, variant_id=var_rosa.id if var_rosa else None, cantidad_vendida=1, precio_venta_final=Decimal('65000.00')),
                SaleDetail(sale_id=v2.id, product_id=acc2.id, variant_id=None, cantidad_vendida=1, precio_venta_final=Decimal('85000.00')),
                SalePayment(sale_id=v2.id, metodo_pago='efectivo', monto=Decimal('150000.00'))
            ])

            # VENTA 4: Venta de Accesorio Externo + AirPods con Pago Mixto (Nequi + Daviplata) -> Genera ProviderInvoice
            v3 = Sale(
                vendedor_id=admin.id,
                fecha_venta=ahora - timedelta(minutes=35),
                monto_total=Decimal('310000.00'),
                monto_retoma=Decimal('0.00'),
                metodo_pago='mixto',
                tipo_venta='general'
            )
            db.session.add(v3)
            db.session.flush()

            db.session.add_all([
                SaleDetail(sale_id=v3.id, product_id=ext_acc_vendido.id, cantidad_vendida=1, precio_venta_final=Decimal('120000.00')),
                SaleDetail(sale_id=v3.id, product_id=acc4.id, cantidad_vendida=1, precio_venta_final=Decimal('190000.00')),
                SalePayment(sale_id=v3.id, metodo_pago='nequi', monto=Decimal('200000.00')),
                SalePayment(sale_id=v3.id, metodo_pago='daviplata', monto=Decimal('110000.00'))
            ])

            # Cuenta por pagar automática generada por la venta del accesorio externo
            db.session.add(ProviderInvoice(
                proveedor=ext_acc_vendido.proveedor,
                product_id=ext_acc_vendido.id,
                sale_id=v3.id,
                usuario_id=admin.id,
                concepto=f"1x {ext_acc_vendido.nombre} ({ext_acc_vendido.sku}) - Ticket #{v3.id}",
                monto_costo=Decimal('65000.00'),
                estado='PENDIENTE',
                fecha_creacion=ahora - timedelta(minutes=35)
            ))

        # ══════════════════════════════════════════════════════════════════
        # 7. KARDEX / AJUSTES Y GASTOS OPERATIVOS DEL DÍA (PARA ARQUEO)
        # ══════════════════════════════════════════════════════════════════
        print("[7/7] Sembrando Movimientos de Kardex y Gastos del Día...")

        if StockAdjustment.query.filter_by(product_id=acc2.id).count() == 0:
            db.session.add_all([
                StockAdjustment(
                    product_id=acc2.id,
                    admin_id=admin.id,
                    tipo_movimiento='Creación Inicial',
                    motivo='Stock inicial cargadores 20W',
                    stock_anterior=0,
                    stock_nuevo=27,
                    fecha_ajuste=ahora - timedelta(days=5)
                ),
                StockAdjustment(
                    product_id=acc2.id,
                    admin_id=admin.id,
                    tipo_movimiento='Muestra / Exhibición',
                    motivo='1 unidad abierta para pruebas de carga en mostrador',
                    stock_anterior=27,
                    stock_nuevo=26,
                    fecha_ajuste=ahora - timedelta(days=2)
                ),
                StockAdjustment(
                    product_id=cel_old_cycle.id,
                    admin_id=admin.id,
                    tipo_movimiento='Ingreso Celular Individual',
                    motivo='IMEI: 359900112233445 (Primer ingreso Nuevo)',
                    stock_anterior=0,
                    stock_nuevo=1,
                    fecha_ajuste=ahora - timedelta(days=45)
                ),
                StockAdjustment(
                    product_id=cel_vendido_hoy.id,
                    admin_id=admin.id,
                    tipo_movimiento='Alta por Retoma Aprobada',
                    motivo='Reingreso por Retoma Aprobada (ciclo previo archivado como 359900112233445-OLD-1)',
                    stock_anterior=0,
                    stock_nuevo=1,
                    fecha_ajuste=ahora - timedelta(days=2)
                )
            ])

        if Expense.query.count() == 0:
            db.session.add(Expense(
                usuario_id=admin.id,
                tipo_gasto='Gasto Diario',
                categoria='Insumos y Bolsas',
                descripcion='Compra de bolsas de seguridad y alcohol isopropílico para limpieza de pantallas',
                monto=Decimal('25000.00'),
                metodo_pago='efectivo',
                fecha_gasto=ahora - timedelta(hours=1)
            ))

        db.session.commit()
        print("═════════════════════════════════════════════════════════════════")
        print(" ✅ ¡EJEMPLOS CREADOS EXITOSAMENTE EN TODOS LOS MÓDULOS!")
        print("═════════════════════════════════════════════════════════════════")


if __name__ == '__main__':
    seed_all_modules()
