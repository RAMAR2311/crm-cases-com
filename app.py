import os
from flask import Flask, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect

# Importar la instancia de db desde models
from models import db, User

import socket

def is_postgres_available():
    try:
        sock = socket.create_connection(('127.0.0.1', 5432), timeout=0.3)
        sock.close()
        return True
    except Exception:
        return False

def get_database_uri():
    db_url = os.environ.get('DATABASE_URL')
    if db_url:
        return db_url
    
    if is_postgres_available():
        pg_url = 'postgresql://postgres:admin123@localhost:5432/crm_cases'
        try:
            from sqlalchemy import create_engine
            engine = create_engine(pg_url, connect_args={'connect_timeout': 1})
            conn = engine.connect()
            conn.close()
            engine.dispose()
            print("[BD] Conectado exitosamente a PostgreSQL.")
            return pg_url
        except Exception:
            pass

    instance_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
    os.makedirs(instance_dir, exist_ok=True)
    sqlite_db = 'sqlite:///' + os.path.join(instance_dir, 'crm_cases.db')
    print("[BD] PostgreSQL no disponible en localhost:5432. Usando SQLite local (instance/crm_cases.db).")
    return sqlite_db

from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import request

load_dotenv(override=True)

def obtener_serializer():
    secret = os.environ.get('SECRET_KEY', 'dev-key-super-secreta')
    return URLSafeTimedSerializer(secret, salt='server-payment-salt')

def generar_token_pago(anio, mes):
    s = obtener_serializer()
    return s.dumps({'anio': anio, 'mes': mes})

def verificar_token_pago(token):
    s = obtener_serializer()
    try:
        data = s.loads(token, max_age=86400 * 60) # Válido por 60 días
        return data.get('anio'), data.get('mes')
    except (BadSignature, SignatureExpired):
        return None, None

csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    
    # Configuración mediante variables de entorno (con fallback a PostgreSQL local o SQLite)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-super-secreta')
    app.config['SQLALCHEMY_DATABASE_URI'] = get_database_uri()
    
    # Parámetros del servidor/hosting
    app.config['VALOR_MENSUALIDAD_SERVIDOR'] = os.environ.get('VALOR_MENSUALIDAD_SERVIDOR', '100.000')
    app.config['DIA_VENCIMIENTO_SERVIDOR'] = int(os.environ.get('DIA_VENCIMIENTO_SERVIDOR', '15'))
    app.config['DIAS_GABELA'] = int(os.environ.get('DIAS_GABELA', '5'))
    app.config['NUMERO_WHATSAPP_PROVEEDOR'] = os.environ.get('NUMERO_WHATSAPP_PROVEEDOR', '573115643557')
    app.config['LLAVE_BREB'] = os.environ.get('LLAVE_BREB', '@QEI910')
    app.config['NUMERO_NEQUI'] = os.environ.get('NUMERO_NEQUI', '3505422186')
    app.config['ANIO_INICIO_COBRO_SERVIDOR'] = int(os.environ.get('ANIO_INICIO_COBRO_SERVIDOR', '2027'))
    
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')

    # Inicializar Extensiones
    db.init_app(app)
    Migrate(app, db)
    csrf.init_app(app)
    
    login_manager = LoginManager()
    login_manager.login_view = 'auth_bp.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Importar y Registrar Blueprints
    from routes.sales import sales_bp
    from routes.inventory import inventory_bp
    from routes.auth import auth_bp
    from routes.arqueo import arqueo_bp
    from routes.gastos import gastos_bp
    
    app.register_blueprint(sales_bp, url_prefix='/sales')
    app.register_blueprint(inventory_bp, url_prefix='/inventory')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(arqueo_bp, url_prefix='/arqueo')
    app.register_blueprint(gastos_bp, url_prefix='/gastos')
    
    # Registro de Blueprint Admin
    from routes.admin import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Registro de Blueprint Bodega
    from routes.bodega import bodega_bp
    app.register_blueprint(bodega_bp, url_prefix='/bodega')

    # Registro de Blueprint Celulares
    from routes.celulares import celulares_bp
    app.register_blueprint(celulares_bp, url_prefix='/celulares')

    @app.template_filter('cop')
    def cop_filter(value):
        if value is None:
            return "0"
        try:
            # Formateo a moneda colombiana (separador de miles con coma, como pidió el usuario)
            return "{:,.0f}".format(float(value))
        except (ValueError, TypeError):
            return value

    @app.context_processor
    def inject_pago_servidor():
        if not current_user or not current_user.is_authenticated or current_user.rol != 'admin':
            return {'pago_servidor': None, 'server_payment_info': None}
        
        try:
            from models import ServerPayment, obtener_hora_bogota
            import urllib.parse
            
            load_dotenv(override=True)
            
            ahora = obtener_hora_bogota()
            anio_actual = ahora.year
            mes_actual = ahora.month
            dia_actual = ahora.day
            
            nombres_meses = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
            nombre_mes = nombres_meses[mes_actual] if 1 <= mes_actual <= 12 else str(mes_actual)
            
            anio_inicio_cobro = int(os.environ.get('ANIO_INICIO_COBRO_SERVIDOR', app.config.get('ANIO_INICIO_COBRO_SERVIDOR', 2027)))
            es_exento = (anio_actual < anio_inicio_cobro)
            
            pago_registrado = ServerPayment.query.filter_by(anio=anio_actual, mes=mes_actual, estado='pagado').first()
            
            valor_mensual = os.environ.get('VALOR_MENSUALIDAD_SERVIDOR', app.config.get('VALOR_MENSUALIDAD_SERVIDOR', '100.000'))
            dia_vencimiento = int(os.environ.get('DIA_VENCIMIENTO_SERVIDOR', app.config.get('DIA_VENCIMIENTO_SERVIDOR', 15)))
            dias_gabela_config = int(os.environ.get('DIAS_GABELA', app.config.get('DIAS_GABELA', 5)))
            numero_nequi = os.environ.get('NUMERO_NEQUI', app.config.get('NUMERO_NEQUI', '3505422186'))
            llave_breb = os.environ.get('LLAVE_BREB', app.config.get('LLAVE_BREB', '@QEI910'))
            whatsapp_num = os.environ.get('NUMERO_WHATSAPP_PROVEEDOR', app.config.get('NUMERO_WHATSAPP_PROVEEDOR', '573115643557'))
            
            # Evaluación del estado según el día del mes y la BD (o exención por primer cliente)
            dias_restantes = 0
            dias_gabela = 0
            
            if es_exento or (pago_registrado and pago_registrado.estado == 'pagado'):
                estado = 'pagado'
                dias_restantes = 0
                dias_gabela = 0
            else:
                if 1 <= dia_actual <= 6:
                    estado = 'al_dia'
                    dias_restantes = 15 - dia_actual
                elif 7 <= dia_actual <= 14:
                    estado = 'preventivo'
                    dias_restantes = 15 - dia_actual
                elif dia_actual == 15:
                    estado = 'hoy'
                    dias_restantes = 0
                elif 16 <= dia_actual <= 20:
                    estado = 'gabela'
                    dias_restantes = 0
                    dias_gabela = 20 - dia_actual + 1
                else:
                    estado = 'vencido'
                    dias_restantes = 0
                    dias_gabela = 0
            
            token = generar_token_pago(anio_actual, mes_actual)
            url_confirmacion = request.url_root.rstrip('/') + url_for('confirmar_pago_servidor_app', token=token)
            
            mensaje_wa = (
                f"Hola, adjunto el comprobante de pago de la mensualidad del servidor Zenic (${valor_mensual} COP) "
                f"para {nombre_mes} {anio_actual}.\n\n"
                f"Para confirmar mi pago en el sistema con 1 solo clic, toca aquí:\n"
                f"{url_confirmacion}"
            )
            whatsapp_url = f"https://wa.me/{whatsapp_num}?text={urllib.parse.quote(mensaje_wa)}"
            
            pago_servidor = {
                'estado': estado,
                'mes_nombre': nombre_mes,
                'anio': anio_actual,
                'monto': valor_mensual,
                'dias_restantes': dias_restantes,
                'dias_gabela': dias_gabela,
                'whatsapp_url': whatsapp_url,
                'nu_llave': llave_breb,
                'nequi_num': numero_nequi,
                'es_exento': es_exento,
                'anio_inicio_cobro': anio_inicio_cobro,
                'url_confirmacion': url_confirmacion
            }
            
            server_payment_info = {
                'esta_pagado': (estado == 'pagado' or es_exento),
                'es_exento': es_exento,
                'anio_inicio_cobro': anio_inicio_cobro,
                'mostrar_alerta': (estado in ['preventivo', 'hoy', 'gabela', 'vencido'] and not es_exento),
                'es_vencido': (estado in ['gabela', 'vencido'] and not es_exento),
                'es_gabela_superada': (estado == 'vencido' and not es_exento),
                'dias_restantes': dias_restantes,
                'valor_mensual': valor_mensual,
                'dia_vencimiento': dia_vencimiento,
                'dias_gabela': dias_gabela if estado == 'gabela' else dias_gabela_config,
                'numero_nequi': numero_nequi,
                'llave_breb': llave_breb,
                'whatsapp_link': whatsapp_url,
                'url_confirmacion': url_confirmacion,
                'anio_actual': anio_actual,
                'mes_actual': mes_actual,
                'nombre_mes': nombre_mes,
                'estado': estado
            }
            
            return {
                'pago_servidor': pago_servidor,
                'server_payment_info': server_payment_info
            }
        except Exception as e:
            return {'pago_servidor': None, 'server_payment_info': None}

    @app.route('/servidor/confirmar-pago', methods=['GET', 'POST'])
    @csrf.exempt
    def confirmar_pago_servidor_app():
        from models import ServerPayment, db, obtener_hora_bogota
        
        token = request.args.get('token') or request.form.get('token')
        if not token:
            return "<h2 style='color:red; font-family:sans-serif; text-align:center; margin-top:50px;'>Parámetro token es requerido.</h2>", 400

        anio, mes = verificar_token_pago(token)
        if not anio or not mes:
            return "<h2 style='color:red; font-family:sans-serif; text-align:center; margin-top:50px;'>El enlace de confirmación es inválido o ha expirado.</h2>", 403

        nombres_meses = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        nombre_mes = nombres_meses[mes] if 1 <= mes <= 12 else str(mes)

        pago = ServerPayment.query.filter_by(anio=anio, mes=mes).first()
        
        # 1. Si ya se pagó previamente, mostrar vista limpia de éxito
        if pago and pago.estado == 'pagado':
            return f"""
            <!DOCTYPE html>
            <html lang="es">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>¡Pago Ya Confirmado! - Servidor Zenic</title>
                <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
                <style>
                    body {{ background-color: #f4f6f8; font-family: 'Segoe UI', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
                    .card-success {{ background: #fff; border: 3px solid #100F0D; border-radius: 1.25rem; box-shadow: 6px 6px 0px #100F0D; padding: 2.5rem; text-align: center; max-width: 450px; width: 90%; }}
                </style>
            </head>
            <body>
                <div class="card-success">
                    <div class="mb-3 text-success"><i class="fa-solid fa-circle-check fa-4x"></i></div>
                    <h2 class="fw-bold text-dark mb-2">¡Pago Ya Confirmado!</h2>
                    <p class="text-secondary fs-5 mb-4">La mensualidad del <strong>Servidor Zenic</strong> para <strong>{nombre_mes} {anio}</strong> ya se encuentra registrada como pagada.</p>
                    <div class="alert alert-success border-2 border-dark rounded-3 py-2 fw-semibold mb-4">✅ Alerta de pago desactivada en el sistema.</div>
                    <a href="/" class="btn btn-dark btn-lg w-100 fw-bold border-2 shadow-sm">Ir a la Aplicación</a>
                </div>
            </body>
            </html>
            """

        error_msg = ""
        pin_esperado = os.environ.get('PIN_CONFIRMACION_SERVIDOR', app.config.get('PIN_CONFIRMACION_SERVIDOR', '9876'))

        # 2. Procesar el formulario cuando se envía el PIN (POST)
        if request.method == 'POST':
            pin_ingresado = request.form.get('pin', '').strip()
            if pin_ingresado == pin_esperado:
                if not pago:
                    pago = ServerPayment(
                        anio=anio,
                        mes=mes,
                        estado='pagado',
                        fecha_pago=obtener_hora_bogota(),
                        observacion='Autorizado con PIN de Proveedor (Zenic)'
                    )
                    db.session.add(pago)
                else:
                    pago.estado = 'pagado'
                    pago.fecha_pago = obtener_hora_bogota()
                    pago.observacion = 'Autorizado con PIN de Proveedor (Zenic)'

                db.session.commit()

                return f"""
                <!DOCTYPE html>
                <html lang="es">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Pago Confirmado - Servidor Zenic</title>
                    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
                    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
                    <style>
                        body {{ background-color: #f4f6f8; font-family: 'Segoe UI', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
                        .card-success {{ background: #fff; border: 3px solid #100F0D; border-radius: 1.25rem; box-shadow: 6px 6px 0px #100F0D; padding: 2.5rem; text-align: center; max-width: 450px; width: 90%; }}
                    </style>
                </head>
                <body>
                    <div class="card-success">
                        <div class="mb-3 text-success"><i class="fa-solid fa-circle-check fa-4x"></i></div>
                        <h2 class="fw-bold text-dark mb-2">¡Pago Confirmado!</h2>
                        <p class="text-secondary fs-5 mb-4">La mensualidad del <strong>Servidor Zenic</strong> para <strong>{nombre_mes} {anio}</strong> ha sido verificada y marcada como pagada con éxito.</p>
                        <div class="alert alert-success border-2 border-dark rounded-3 py-2 fw-semibold mb-4">✅ Alerta desactivada automáticamente en la aplicación</div>
                        <a href="/" class="btn btn-dark btn-lg w-100 fw-bold border-2 shadow-sm">Ir a la Aplicación</a>
                    </div>
                </body>
                </html>
                """
            else:
                error_msg = "🚨 PIN de confirmación incorrecto. Inténtalo nuevamente."

        # 3. Mostrar pantalla de solicitud de PIN (GET o POST con PIN incorrecto)
        return f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Autorizar Pago - Servidor Zenic</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
            <style>
                body {{ background-color: #f4f6f8; font-family: 'Segoe UI', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
                .card-pin {{ background: #fff; border: 3px solid #100F0D; border-radius: 1.25rem; box-shadow: 6px 6px 0px #100F0D; padding: 2.5rem; text-align: center; max-width: 440px; width: 90%; }}
            </style>
        </head>
        <body>
            <div class="card-pin">
                <div class="mb-3 text-warning"><i class="fa-solid fa-shield-halved fa-3x text-dark"></i></div>
                <h3 class="fw-bold text-dark mb-1">Confirmar Pago Servidor</h3>
                <p class="text-muted small mb-3">Mensualidad <strong>Servidor Zenic</strong> - <strong>{nombre_mes} {anio}</strong></p>
                
                {f'<div class="alert alert-danger border-2 border-dark rounded-3 py-2 fw-semibold mb-3 small">{error_msg}</div>' if error_msg else ''}

                <p class="text-secondary small mb-4">Ingresa el <strong>PIN Secreto del Proveedor</strong> para autorizar y registrar este pago en el sistema:</p>

                <form method="POST" action="">
                    <input type="hidden" name="token" value="{token}">
                    <div class="mb-4">
                        <input type="password" name="pin" class="form-control form-control-lg text-center fw-bold border-2 border-dark rounded-3" placeholder="••••" maxlength="10" required autofocus autocomplete="off" style="letter-spacing: 4px; font-size: 1.5rem;">
                    </div>
                    <button type="submit" class="btn btn-success btn-lg w-100 fw-bold border-2 border-dark shadow-sm">
                        <i class="fa-solid fa-check-double me-2"></i> Confirmar Pago
                    </button>
                </form>
            </div>
        </body>
        </html>
        """


    @app.route('/')
    def index():
        # Redirección de sesión y rol de usuario
        if not current_user.is_authenticated:
            return redirect(url_for('auth_bp.login'))
            
        if current_user.rol == 'admin':
            return redirect(url_for('admin_bp.dashboard'))
            
        if current_user.rol == 'bodega' or current_user.rol == 'vendedor_bodega':
            return redirect(url_for('bodega_bp.dashboard'))
            
        # Por defecto, Vendedores van directo a Cajas
        return redirect(url_for('sales_bp.procesar_venta'))

    @app.route('/sw.js')
    def service_worker():
        from flask import send_from_directory
        return send_from_directory('static', 'sw.js', mimetype='application/javascript')

    return app

if __name__ == '__main__':
    app = create_app()
    
    # ---------------- LÓGICA DE INICIALIZACIÓN ----------------
    with app.app_context():
        from models import db, User
        from werkzeug.security import generate_password_hash
        
        # Aseguramos que las tablas existan sin romper migraciones
        db.create_all()
        
        # Crear la carpeta de imágenes si no existe
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        
        # Verificamos e instanciamos al Administrador si no existe
        if not User.query.filter_by(email='admin@cases.com').first():
            master_admin = User(
                nombre='Administrador Principal',
                email='admin@cases.com',
                password_hash=generate_password_hash('Admin123'),
                rol='admin' # Rol dictaminado por los requerimientos
            )
            db.session.add(master_admin)
            db.session.commit()
            print("🚀 [INFO] Usuario maestro 'admin@cases.com' fue creado automáticamente.")
            
    app.run(debug=True)
