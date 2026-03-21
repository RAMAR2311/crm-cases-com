from flask import Blueprint, render_template, abort, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Product, Sale, User
from sqlalchemy.sql import func
from werkzeug.security import generate_password_hash

admin_bp = Blueprint('admin_bp', __name__)

@admin_bp.route('/vendedores', methods=['GET', 'POST'])
@login_required
def vendedores():
    # 1. Regla Robusta: Nadie excepto Administradores cruza esta puerta
    if current_user.rol != 'admin':
        abort(403)
        
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('email')
        telefono = request.form.get('telefono')
        password = request.form.get('password')
        
        # Validar Duplicidad
        if User.query.filter_by(email=email).first():
            flash('Acción Denegada: Ese correo ya le pertenece a otro vendedor.', 'danger')
        else:
            # Hash Seguro de contraseñas de vendedores
            nuevo_vendedor = User(
                nombre=nombre.strip(),
                email=email.strip(),
                telefono=telefono.strip() if telefono else None,
                password_hash=generate_password_hash(password),
                rol='vendedor'
            )
            db.session.add(nuevo_vendedor)
            db.session.commit()
            flash(f"¡Vendedor '{nombre}' registrado y autorizado para Cajas!", "success")
            
        return redirect(url_for('admin_bp.vendedores'))
        
    # GET: Pasar lista de la fuerza de ventas registrada a la plantilla
    lista_vendedores = User.query.filter_by(rol='vendedor').order_by(User.nombre).all()
    return render_template('admin/vendedores.html', vendedores=lista_vendedores)

@admin_bp.route('/dashboard')
@login_required # Seguridad Inicial
def dashboard():
    # REGLA ESTRICTA DE SEGURIDAD: Solo 'admin' puede pasar de aquí
    if current_user.rol != 'admin':
        abort(403) # Lanza un Error HTTP 403 Forbidden
        
    # Variables Analíticas para el Panel
    total_productos = Product.query.count()
    productos_bajo_stock = Product.query.filter(Product.cantidad_stock <= 10).count()
    
    # Cálculo sumatorio escalable en Base de Datos de los ingresos totales
    total_ventas = db.session.query(func.sum(Sale.monto_total)).scalar() or 0.0

    # Inyección a Jinja2
    return render_template('admin/dashboard.html', 
                           total_productos=total_productos,
                           productos_bajo_stock=productos_bajo_stock,
                           total_ventas=total_ventas)
