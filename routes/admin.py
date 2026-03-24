from flask import Blueprint, render_template, abort, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Product, Sale, User
from sqlalchemy.sql import func
from werkzeug.security import generate_password_hash
from decorators import admin_required

admin_bp = Blueprint('admin_bp', __name__)

@admin_bp.route('/vendedores', methods=['GET', 'POST'])
@login_required
@admin_required
def vendedores():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('email')
        telefono = request.form.get('telefono')
        password = request.form.get('password')
        
        # Se previene registrar vendedores con un mismo email para preservar la unicidad de las credenciales de acceso
        if User.query.filter_by(email=email).first():
            flash('Acción Denegada: Ese correo ya le pertenece a otro vendedor.', 'danger')
        else:
            try:
                # Se aplica un hash a la contraseña para evitar guardar texto plano, previniendo exposición en caso de brechas
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
            except Exception as e:
                db.session.rollback()
                flash('Ocurrió un error en la base de datos al intentar registrar al vendedor.', 'danger')
            
        return redirect(url_for('admin_bp.vendedores'))
        
    # Se pasa la lista para poblar la tabla HTML de gestión de personal
    lista_vendedores = User.query.filter_by(rol='vendedor').order_by(User.nombre).all()
    return render_template('admin/vendedores.html', vendedores=lista_vendedores)

@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    # Se obtienen métricas clave para que el administrador tenga un resumen rápido de las operaciones del negocio
    total_productos = Product.query.count()
    productos_bajo_stock = Product.query.filter(Product.cantidad_stock <= 10).count()
    
    # Se delega la suma al motor de base de datos para no saturar la memoria de la aplicación con registros a medida que crecen las ventas
    total_ventas = db.session.query(func.sum(Sale.monto_total)).scalar() or 0.0

    return render_template('admin/dashboard.html', 
                           total_productos=total_productos,
                           productos_bajo_stock=productos_bajo_stock,
                           total_ventas=total_ventas)
