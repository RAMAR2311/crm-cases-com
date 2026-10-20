"""
Script de Auditoría Previa y Test de Renderizado en Servidor (Dry-Run Completo).
Verifica que ninguna plantilla arroje TypeError, KeyError o UndefinedError,
incluso con valores None o registros incompletos.
"""
import sys
import os
from decimal import Decimal
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import create_app
from flask import render_template
from flask_login import login_user
from unittest.mock import MagicMock

def test_dry_run():
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    passed = 0
    failed = 0
    errors = []

    with app.test_request_context():
        # Mock Users
        user_bodega = MagicMock(is_authenticated=True, id=1, nombre="Bodega Master", rol="bodega")
        user_admin = MagicMock(is_authenticated=True, id=2, nombre="Admin Master", rol="admin")
        user_cajero = MagicMock(is_authenticated=True, id=3, nombre="Cajero 1", rol="vendedor")

        # 1. Error pages
        for err_code in [404, 500]:
            try:
                out = render_template(f'errors/{err_code}.html')
                assert f"{err_code}" in out
                print(f"  [OK] Render errors/{err_code}.html (Autónoma)")
                passed += 1
            except Exception as e:
                print(f"  [FAIL] errors/{err_code}.html: {e}")
                errors.append(f"errors/{err_code}.html: {e}")
                failed += 1

        # 2. Login
        try:
            user_anon = MagicMock(is_authenticated=False)
            login_user(user_anon)
            out = render_template('auth/login.html')
            assert "Acceso al Panel" in out
            print("  [OK] Render auth/login.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] auth/login.html: {e}")
            errors.append(f"auth/login.html: {e}")
            failed += 1

        # 3. Bodega Dashboard
        login_user(user_bodega)
        try:
            mock_f = MagicMock()
            mock_f.id = 99
            mock_f.numero_factura = "FAC-TEST-01"
            mock_f.cliente = None
            mock_f.fecha_subida = datetime.now()
            mock_f.estado = "Pendiente"
            mock_f.monto_total = Decimal('100000.00')
            mock_f.total_abonado = Decimal('0.00')
            mock_f.saldo_pendiente = Decimal('100000.00')
            mock_f.detalles = []
            mock_f.archivo_ruta = None

            mock_a = MagicMock()
            mock_a.cliente = None
            mock_a.monto = Decimal('50000.00')
            mock_a.fecha_abono = datetime.now()
            mock_a.metodo_pago = "efectivo"

            out = render_template('bodega/dashboard.html', clientes_count=10, facturas=[mock_f], abonos=[mock_a])
            assert "Área de Bodega" in out
            print("  [OK] Render bodega/dashboard.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] bodega/dashboard.html: {e}")
            errors.append(f"bodega/dashboard.html: {e}")
            failed += 1

        # 4. Bodega Factura Detalle
        mock_cliente = MagicMock(
            id=1,
            nombre_o_razon_social="Distribuidora Colombia",
            documento_o_nit="900123456-1",
            telefono="3001234567",
            direccion="Cra 10 # 20-30",
            zona="Centro"
        )
        mock_prod = MagicMock(nombre="Funda Uso Rudo", sku="ACC-001")
        mock_d1 = MagicMock(producto=mock_prod, variante=None, cantidad=5, precio_venta=Decimal('15000.00'))
        mock_d2 = MagicMock(producto=None, variante=None, cantidad=2, precio_venta=Decimal('8000.00'))
        mock_abono = MagicMock(fecha_abono=datetime.now(), metodo_pago="transferencia", observacion="Abono", monto=Decimal('45500.00'), usuario=MagicMock(nombre="Bodega"))

        mock_f.cliente = mock_cliente
        mock_f.detalles = [mock_d1, mock_d2]
        mock_f.abonos = [mock_abono]
        mock_f.usuario = MagicMock(nombre="Encargado Bodega")

        try:
            out = render_template('bodega/factura_detalle.html', f=mock_f)
            assert "btnDetalleEnviarWhatsApp" in out
            print("  [OK] Render bodega/factura_detalle.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] bodega/factura_detalle.html: {e}")
            errors.append(f"bodega/factura_detalle.html: {e}")
            failed += 1

        # 5. Bodega Facturas Lista
        try:
            mock_pag = MagicMock(pages=2, page=1, has_prev=False, has_next=True, prev_num=None, next_num=2)
            mock_pag.iter_pages.return_value = [1, 2]
            out = render_template(
                'bodega/facturas_lista.html',
                facturas=[mock_f],
                total_count=1,
                total_facturado=Decimal('100000.00'),
                total_abonado=Decimal('50000.00'),
                total_pendiente=Decimal('50000.00'),
                search="",
                estado="todos",
                modalidad="todas",
                paginacion=mock_pag
            )
            assert "btn-whatsapp-trigger" in out
            print("  [OK] Render bodega/facturas_lista.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] bodega/facturas_lista.html: {e}")
            errors.append(f"bodega/facturas_lista.html: {e}")
            failed += 1

        # 6. Bodega Caja Rápida
        try:
            mock_p1 = MagicMock(
                id=10, 
                nombre="Cargador Rápido", 
                sku="CARG-20W", 
                cantidad_stock=40,
                total_stock=40,
                precio_costo=Decimal('10000.00'),
                precio_sugerido=Decimal('25000.00'), 
                precio_minimo=Decimal('20000.00'), 
                rango_costos=None,
                rango_minimos=None,
                rango_precios=None,
                tipo_inventario="bodega", 
                imagen=None, 
                variantes=[]
            )
            out = render_template('bodega/caja_rapida.html', productos=[mock_p1], clientes=[mock_cliente])
            assert "Caja Rápida Visual" in out
            print("  [OK] Render bodega/caja_rapida.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] bodega/caja_rapida.html: {e}")
            errors.append(f"bodega/caja_rapida.html: {e}")
            failed += 1

        # 7. Bodega Factura Nueva
        try:
            out = render_template('bodega/factura_nueva.html', clientes=[mock_cliente], productos=[mock_p1], preselected_id=None)
            assert "Anexar Factura" in out
            print("  [OK] Render bodega/factura_nueva.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] bodega/factura_nueva.html: {e}")
            errors.append(f"bodega/factura_nueva.html: {e}")
            failed += 1

        # 8. Bodega Módulo Abonos
        try:
            out = render_template('bodega/modulo_abonos.html')
            assert "Registro Rápido de Abonos" in out
            print("  [OK] Render bodega/modulo_abonos.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] bodega/modulo_abonos.html: {e}")
            errors.append(f"bodega/modulo_abonos.html: {e}")
            failed += 1

        # 9. Bodega Cliente Detalle
        try:
            mock_c_full = MagicMock(
                id=1,
                nombre_o_razon_social="Cliente Mayorista 1",
                documento_o_nit="800111222",
                telefono="3110000000",
                zona="Sur",
                total_contado=Decimal('100000.00'),
                total_credito=Decimal('200000.00'),
                total_abonado=Decimal('50000.00'),
                deuda_total=Decimal('150000.00'),
                facturas=[mock_f],
                abonos=[mock_abono]
            )
            out = render_template('bodega/cliente_detalle.html', cliente=mock_c_full)
            assert "Saldo Deudor Actual" in out
            print("  [OK] Render bodega/cliente_detalle.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] bodega/cliente_detalle.html: {e}")
            errors.append(f"bodega/cliente_detalle.html: {e}")
            failed += 1

        # 10. Admin Dashboard
        login_user(user_admin)
        try:
            out = render_template('admin/dashboard.html', total_ventas=Decimal('2000000'), conteo_ventas=25, total_productos=80, productos_bajo_stock=3, maneos_activos=1, pago_servidor=None)
            assert "Dashboard Operativo" in out
            print("  [OK] Render admin/dashboard.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] admin/dashboard.html: {e}")
            errors.append(f"admin/dashboard.html: {e}")
            failed += 1

        # 11. Admin Maneos
        try:
            mock_maneo = MagicMock(
                id=1,
                sku="ACC-001",
                local_vecino="Vecino Local 5",
                cantidad=2,
                estado="PENDIENTE",
                fecha_prestamo=datetime.now(),
                fecha_resolucion=None,
                producto=mock_prod,
                variante=None
            )
            out = render_template('admin/maneos.html', maneos=[mock_maneo], productos=[mock_p1])
            assert "Maneos" in out
            print("  [OK] Render admin/maneos.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] admin/maneos.html: {e}")
            errors.append(f"admin/maneos.html: {e}")
            failed += 1

        # 12. Inventory Index
        try:
            out = render_template('inventory/index.html', productos=[mock_p1], total_productos=1, paginacion=mock_pag)
            assert "Inventario" in out
            print("  [OK] Render inventory/index.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] inventory/index.html: {e}")
            errors.append(f"inventory/index.html: {e}")
            failed += 1

        # 13. Gastos Index
        try:
            mock_gasto = MagicMock(
                id=1,
                fecha_gasto=datetime.now(),
                categoria="Transporte",
                descripcion="Moto flete",
                monto=Decimal('15000.00'),
                metodo_pago="efectivo",
                tipo_gasto="Gasto Diario",
                usuario=MagicMock(nombre="Admin")
            )
            out = render_template('gastos/index.html', gastos=[mock_gasto], total_gastos=Decimal('15000.00'), fecha_inicio="2026-10-01", fecha_fin="2026-10-20")
            assert "Gastos" in out
            print("  [OK] Render gastos/index.html")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] gastos/index.html: {e}")
            errors.append(f"gastos/index.html: {e}")
            failed += 1

        # 14. Torture Test: Factura con campos None
        try:
            mock_f_nulls = MagicMock(
                id=99,
                numero_factura="FAC-NULL",
                fecha_creacion=datetime.now(),
                cliente=None,
                total=None,
                subtotal=None,
                descuento=None,
                saldo_pendiente=None,
                estado="pendiente",
                modalidad="remision",
                es_borrador=False,
                detalles=[
                    MagicMock(producto=None, variante=None, cantidad=None, precio_venta=None)
                ],
                abonos=[],
                usuario=None,
                anotaciones=None
            )
            out_null = render_template('bodega/factura_detalle.html', f=mock_f_nulls)
            assert "FAC-NULL" in out_null
            print("  [OK] Torture Test: bodega/factura_detalle.html con relaciones None")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] Torture Test bodega/factura_detalle.html: {e}")
            errors.append(f"Torture Test bodega/factura_detalle.html: {e}")
            failed += 1

        # 15. Torture Test: Inventario con stock y precios None
        try:
            mock_p_nulls = MagicMock(
                id=99,
                sku="NULL-SKU",
                nombre="Producto Sin Datos",
                total_stock=None,
                cantidad_stock=None,
                precio_costo=None,
                precio_minimo=None,
                precio_sugerido=None,
                rango_costos=None,
                rango_minimos=None,
                rango_precios=None,
                variantes=[
                    MagicMock(nombre_variante="Variante Fantasma", cantidad_stock=None, precio_costo=None, precio_sugerido=None)
                ]
            )
            out_p_null = render_template('inventory/index.html', productos=[mock_p_nulls], total_productos=1, paginacion=mock_pag)
            assert "NULL-SKU" in out_p_null
            print("  [OK] Torture Test: inventory/index.html con stock y variantes None")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] Torture Test inventory/index.html: {e}")
            errors.append(f"Torture Test inventory/index.html: {e}")
            failed += 1

    print("\n" + "="*60)
    print(f"AUDITORIA COMPLETA: {passed} VISTAS PASARON, {failed} FALLARON.")
    print("="*60)
    if failed > 0:
        for err in errors:
            print(f" - {err}")
        sys.exit(1)
    else:
        print(">>> TODAS LAS VISTAS CLAVE FUNCIONAN CON ESTABILIDAD Y NULL-SAFETY.")
        sys.exit(0)

if __name__ == '__main__':
    test_dry_run()
