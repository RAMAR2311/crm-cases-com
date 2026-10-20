import os
from datetime import datetime, timedelta
from decimal import Decimal
from app import create_app
from models import db, User, Product, ProductVariant, Cliente, FacturaBodega, FacturaBodegaDetalle, AbonoBodega, StockAdjustment, obtener_hora_bogota

def seed_bodega_completo():
    app = create_app()
    with app.app_context():
        print("[INFO] Iniciando carga de datos de ejemplo para el perfil de BODEGA...")

        # 1. Obtener o crear Usuario de Bodega
        bodega_user = User.query.filter_by(rol='bodega').first()
        if not bodega_user:
            from werkzeug.security import generate_password_hash
            bodega_user = User(
                nombre='Encargado de Bodega',
                email='bodega@cases.com',
                telefono='3001234567',
                password_hash=generate_password_hash('Bodega123'),
                rol='bodega'
            )
            db.session.add(bodega_user)
            db.session.commit()
            print("[INFO] Usuario 'bodega@cases.com' creado.")

        os.makedirs('static/uploads/facturas', exist_ok=True)
        ahora = obtener_hora_bogota()

        # 2. PRODUCTOS DE BODEGA (Por mayor, accesorios, celulares y variantes)
        productos_config = [
            {
                "sku": "BOD-I15PM-256",
                "nombre": "iPhone 15 Pro Max 256GB - Lote Bodega",
                "stock": 25,
                "costo": 4500000,
                "minimo": 4800000,
                "sugerido": 5200000,
                "observacion": "Garantía oficial Apple 1 año. Lote sellado.",
                "variantes": []
            },
            {
                "sku": "BOD-S24U-512",
                "nombre": "Samsung Galaxy S24 Ultra Titanium - Bodega",
                "stock": 18,
                "costo": 3800000,
                "minimo": 4100000,
                "sugerido": 4500000,
                "observacion": "Snapdragon 8 Gen 3, libre de operador.",
                "variantes": []
            },
            {
                "sku": "BOD-APP2",
                "nombre": "AirPods Pro 2nd Gen USB-C - Bodega",
                "stock": 50,
                "costo": 720000,
                "minimo": 850000,
                "sugerido": 960000,
                "observacion": "Caja sellada con estuche MagSafe USB-C.",
                "variantes": []
            },
            {
                "sku": "BOD-CHG-20W",
                "nombre": "Cargador Apple 20W Original (Pack x10 Unidades)",
                "stock": 80,
                "costo": 85000,
                "minimo": 115000,
                "sugerido": 140000,
                "observacion": "Pack mayorista de 10 unidades empaque individual.",
                "variantes": []
            },
            {
                "sku": "BOD-VID-9D",
                "nombre": "Vidrio Templado Privacy 9D Cerámica (Caja x50)",
                "stock": 120,
                "costo": 65000,
                "minimo": 95000,
                "sugerido": 130000,
                "observacion": "Vidrio cerámico antiespía indeformable de alta rotación.",
                "variantes": []
            },
            {
                "sku": "BOD-CAB-CC60",
                "nombre": "Cable Trenzado Tipo C a C 60W 1.2m (Caja x20)",
                "stock": 90,
                "costo": 48000,
                "minimo": 70000,
                "sugerido": 95000,
                "observacion": "Cables reforzados con nylon de alta durabilidad.",
                "variantes": []
            },
            {
                "sku": "BOD-CASE-MAG",
                "nombre": "Case Magnético Armor MagSafe (Pack x10)",
                "stock": 0, # Con variantes
                "costo": 40000,
                "minimo": 60000,
                "sugerido": 80000,
                "observacion": "Forros acrílicos con aro imantado fuerte para carga Qi.",
                "variantes": [
                    ("iPhone 13 / 14 Normal (Pack x10)", 35, 38000, 58000, 75000),
                    ("iPhone 15 Pro / Pro Max (Pack x10)", 45, 42000, 65000, 85000),
                    ("Samsung S24 Ultra (Pack x10)", 25, 40000, 62000, 80000)
                ]
            }
        ]

        for p_info in productos_config:
            p = Product.query.filter_by(sku=p_info["sku"]).first()
            if not p:
                p = Product(
                    sku=p_info["sku"],
                    nombre=p_info["nombre"],
                    tipo_inventario="bodega",
                    cantidad_stock=p_info["stock"],
                    precio_costo=Decimal(str(p_info["costo"])),
                    precio_minimo=Decimal(str(p_info["minimo"])),
                    precio_sugerido=Decimal(str(p_info["sugerido"])),
                    observacion=p_info["observacion"]
                )
                db.session.add(p)
                db.session.flush()

                # Registro de stock inicial en el historial
                if p_info["stock"] > 0:
                    adj = StockAdjustment(
                        product_id=p.id,
                        admin_id=bodega_user.id,
                        tipo_movimiento="Ingreso Inicial a Bodega",
                        stock_anterior=0,
                        stock_nuevo=p_info["stock"]
                    )
                    db.session.add(adj)

                # Crear variantes si las tiene
                for v_nombre, v_stock, v_costo, v_min, v_sug in p_info["variantes"]:
                    v = ProductVariant(
                        product_id=p.id,
                        nombre_variante=v_nombre,
                        cantidad_stock=v_stock,
                        precio_costo=Decimal(str(v_costo)),
                        precio_minimo=Decimal(str(v_min)),
                        precio_sugerido=Decimal(str(v_sug))
                    )
                    db.session.add(v)
                    db.session.flush()

                    adj_v = StockAdjustment(
                        product_id=p.id,
                        admin_id=bodega_user.id,
                        tipo_movimiento=f"Ingreso Subcategoría: {v_nombre}",
                        stock_anterior=0,
                        stock_nuevo=v_stock
                    )
                    db.session.add(adj_v)
            else:
                p.tipo_inventario = "bodega"
                if p_info["stock"] > 0:
                    p.cantidad_stock = p_info["stock"]

        db.session.commit()
        print("[INFO] Catalogo de productos de bodega y variantes registrado.")

        # 3. CLIENTES MAYORISTAS DE BODEGA
        clientes_data = [
            {
                "nombre_o_razon_social": "Distribuidora Tech Medellín SAS",
                "documento_o_nit": "900.888.777-1",
                "telefono": "6045551234",
                "email": "compras@techmedellin.com",
                "direccion": "C.C. Monterrey Local 123",
                "zona": "Centro"
            },
            {
                "nombre_o_razon_social": "Inversiones Marlo Cali",
                "documento_o_nit": "800.111.222-3",
                "telefono": "3159998877",
                "email": "marlo@casestech.com",
                "direccion": "Av. Pasoancho # 50-10",
                "zona": "Alrededores"
            },
            {
                "nombre_o_razon_social": "Accesorios San Andresito Bogotá",
                "documento_o_nit": "901.234.567-8",
                "telefono": "3108889900",
                "email": "ventas@sanandresito38.co",
                "direccion": "C.C. Puerto Príncipe Local 45",
                "zona": "San Andresito 38"
            },
            {
                "nombre_o_razon_social": "Cellular Shop Fontibón",
                "documento_o_nit": "1020304050-1",
                "telefono": "3124567890",
                "email": "shopfontibon@gmail.com",
                "direccion": "Cra 100 # 22-15",
                "zona": "Fontibón"
            },
            {
                "nombre_o_razon_social": "Comercializadora del Norte Barranquilla",
                "documento_o_nit": "900.555.444-2",
                "telefono": "3012223344",
                "email": "contacto@comercializadoranorte.com",
                "direccion": "Calle 72 # 45-20",
                "zona": "Norte"
            }
        ]

        clientes_creados = {}
        for c_data in clientes_data:
            cliente = Cliente.query.filter_by(documento_o_nit=c_data["documento_o_nit"]).first()
            if not cliente:
                cliente = Cliente(
                    creado_por_id=bodega_user.id,
                    fecha_registro=ahora - timedelta(days=15),
                    **c_data
                )
                db.session.add(cliente)
                db.session.flush()
            clientes_creados[c_data["documento_o_nit"]] = cliente

        db.session.commit()
        print("[INFO] Directorio de 5 clientes mayoristas registrado.")

        # 4. FACTURAS Y FLUJO DE ABONOS CON DIVERSOS ESTADOS
        
        # Factura 1: Distribuidora Tech Medellín -> Crédito con Abono Parcial (Estado: Parcial)
        c1 = clientes_creados["900.888.777-1"]
        if not FacturaBodega.query.filter_by(numero_factura="FB-2026-001").first():
            f1 = FacturaBodega(
                cliente_id=c1.id,
                usuario_id=bodega_user.id,
                numero_factura="FB-2026-001",
                monto_total=Decimal("11400000.00"),
                modalidad="credito",
                estado="Parcial",
                fecha_subida=ahora - timedelta(days=5)
            )
            db.session.add(f1)
            db.session.flush()

            prod_i15 = Product.query.filter_by(sku="BOD-I15PM-256").first()
            prod_chg = Product.query.filter_by(sku="BOD-CHG-20W").first()
            if prod_i15:
                db.session.add(FacturaBodegaDetalle(
                    factura_id=f1.id, producto_id=prod_i15.id, cantidad=2, precio_venta=Decimal("5100000.00")
                ))
            if prod_chg:
                db.session.add(FacturaBodegaDetalle(
                    factura_id=f1.id, producto_id=prod_chg.id, cantidad=10, precio_venta=Decimal("120000.00")
                ))

            # Abono registrado de 5,000,000 COP
            abono1 = AbonoBodega(
                cliente_id=c1.id,
                factura_id=f1.id,
                usuario_id=bodega_user.id,
                monto=Decimal("5000000.00"),
                metodo_pago="transferencia",
                observacion="Transferencia Bancolombia Ref #77821",
                fecha_abono=ahora - timedelta(days=3)
            )
            db.session.add(abono1)
            print("[INFO] Factura FB-2026-001 creada (Credito - Estado Parcial: Deuda $6.4M).")

        # Factura 2: Inversiones Marlo Cali -> Contado (Estado: Pagado)
        c2 = clientes_creados["800.111.222-3"]
        if not FacturaBodega.query.filter_by(numero_factura="FB-2026-002").first():
            f2 = FacturaBodega(
                cliente_id=c2.id,
                usuario_id=bodega_user.id,
                numero_factura="FB-2026-002",
                monto_total=Decimal("2850000.00"),
                modalidad="contado",
                estado="Pagado",
                fecha_subida=ahora - timedelta(days=4)
            )
            db.session.add(f2)
            db.session.flush()

            prod_app = Product.query.filter_by(sku="BOD-APP2").first()
            if prod_app:
                db.session.add(FacturaBodegaDetalle(
                    factura_id=f2.id, producto_id=prod_app.id, cantidad=3, precio_venta=Decimal("950000.00")
                ))

            abono2 = AbonoBodega(
                cliente_id=c2.id,
                factura_id=f2.id,
                usuario_id=bodega_user.id,
                monto=Decimal("2850000.00"),
                metodo_pago="nequi",
                observacion="Pago de contado via Nequi",
                fecha_abono=ahora - timedelta(days=4)
            )
            db.session.add(abono2)
            print("[INFO] Factura FB-2026-002 creada (Contado - Estado Pagado).")

        # Factura 3: Accesorios San Andresito Bogotá -> Crédito sin abonos (Estado: Pendiente)
        c3 = clientes_creados["901.234.567-8"]
        if not FacturaBodega.query.filter_by(numero_factura="FB-2026-003").first():
            f3 = FacturaBodega(
                cliente_id=c3.id,
                usuario_id=bodega_user.id,
                numero_factura="FB-2026-003",
                monto_total=Decimal("4350000.00"),
                modalidad="credito",
                estado="Pendiente",
                fecha_subida=ahora - timedelta(days=2)
            )
            db.session.add(f3)
            db.session.flush()

            prod_s24 = Product.query.filter_by(sku="BOD-S24U-512").first()
            if prod_s24:
                db.session.add(FacturaBodegaDetalle(
                    factura_id=f3.id, producto_id=prod_s24.id, cantidad=1, precio_venta=Decimal("4350000.00")
                ))
            print("[INFO] Factura FB-2026-003 creada (Credito - Estado Pendiente Total).")

        # Factura 4: Cellular Shop Fontibón -> Crédito 100% saldado con 2 abonos
        c4 = clientes_creados["1020304050-1"]
        if not FacturaBodega.query.filter_by(numero_factura="FB-2026-004").first():
            f4 = FacturaBodega(
                cliente_id=c4.id,
                usuario_id=bodega_user.id,
                numero_factura="FB-2026-004",
                monto_total=Decimal("1950000.00"),
                modalidad="credito",
                estado="Pagado",
                fecha_subida=ahora - timedelta(days=6)
            )
            db.session.add(f4)
            db.session.flush()

            prod_vid = Product.query.filter_by(sku="BOD-VID-9D").first()
            prod_cab = Product.query.filter_by(sku="BOD-CAB-CC60").first()
            if prod_vid:
                db.session.add(FacturaBodegaDetalle(
                    factura_id=f4.id, producto_id=prod_vid.id, cantidad=10, precio_venta=Decimal("115000.00")
                ))
            if prod_cab:
                db.session.add(FacturaBodegaDetalle(
                    factura_id=f4.id, producto_id=prod_cab.id, cantidad=10, precio_venta=Decimal("80000.00")
                ))

            # Abono 1
            db.session.add(AbonoBodega(
                cliente_id=c4.id,
                factura_id=f4.id,
                usuario_id=bodega_user.id,
                monto=Decimal("1000000.00"),
                metodo_pago="efectivo",
                observacion="Abono en efectivo en bodega",
                fecha_abono=ahora - timedelta(days=4)
            ))
            # Abono 2
            db.session.add(AbonoBodega(
                cliente_id=c4.id,
                factura_id=f4.id,
                usuario_id=bodega_user.id,
                monto=Decimal("950000.00"),
                metodo_pago="daviplata",
                observacion="Saldo final cancelado por Daviplata",
                fecha_abono=ahora - timedelta(days=1)
            ))
            print("[INFO] Factura FB-2026-004 creada (Credito Saldado al 100%).")

        # Factura 5: Comercializadora del Norte -> Factura reciente de cases con variantes
        c5 = clientes_creados["900.555.444-2"]
        if not FacturaBodega.query.filter_by(numero_factura="FB-2026-005").first():
            f5 = FacturaBodega(
                cliente_id=c5.id,
                usuario_id=bodega_user.id,
                numero_factura="FB-2026-005",
                monto_total=Decimal("2275000.00"),
                modalidad="credito",
                estado="Parcial",
                fecha_subida=ahora - timedelta(hours=6)
            )
            db.session.add(f5)
            db.session.flush()

            prod_case = Product.query.filter_by(sku="BOD-CASE-MAG").first()
            if prod_case and prod_case.variantes:
                var1 = prod_case.variantes[0]
                var2 = prod_case.variantes[1]
                db.session.add(FacturaBodegaDetalle(
                    factura_id=f5.id,
                    producto_id=prod_case.id,
                    variant_id=var1.id,
                    cantidad=15,
                    precio_venta=Decimal("70000.00")
                ))
                db.session.add(FacturaBodegaDetalle(
                    factura_id=f5.id,
                    producto_id=prod_case.id,
                    variant_id=var2.id,
                    cantidad=15,
                    precio_venta=Decimal("81666.67")
                ))

            # Abono inicial reciente
            db.session.add(AbonoBodega(
                cliente_id=c5.id,
                factura_id=f5.id,
                usuario_id=bodega_user.id,
                monto=Decimal("1000000.00"),
                metodo_pago="transferencia",
                observacion="Anticipo 50% de pedido Barranquilla",
                fecha_abono=ahora - timedelta(hours=4)
            ))
            print("[INFO] Factura FB-2026-005 creada (Con productos variantes y abono de hoy).")

        db.session.commit()
        print("\n[EXITO] SEED DE BODEGA COMPLETADO CON EXITO.")
        print("----------------------------------------------------")
        print(f"* Total Clientes: {Cliente.query.count()}")
        print(f"* Total Productos Bodega: {Product.query.filter_by(tipo_inventario='bodega').count()}")
        print(f"* Total Facturas Bodega: {FacturaBodega.query.count()}")
        print(f"* Total Abonos Registrados: {AbonoBodega.query.count()}")
        print("----------------------------------------------------")

if __name__ == '__main__':
    seed_bodega_completo()
