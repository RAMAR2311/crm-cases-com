from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import db, Product, StockAdjustment
from decorators import admin_required

inventory_bp = Blueprint('inventory_bp', __name__)

@inventory_bp.route('/', methods=['GET'])
@login_required
@admin_required
def index():
    productos = Product.query.order_by(Product.nombre).all()
    return render_template('inventory/index.html', productos=productos)

@inventory_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@admin_required
def nuevo():
    if request.method == 'POST':
        # La instanciación agrupa todos los parámetros del nuevo producto
        nuevo_prod = Product(
            sku=request.form.get('sku').strip(),
            nombre=request.form.get('nombre').strip(),
            cantidad_stock=int(request.form.get('cantidad_stock', 0)),
            precio_costo=float(request.form.get('precio_costo', 0.0)),
            precio_minimo=float(request.form.get('precio_minimo', 0.0)),
            precio_sugerido=float(request.form.get('precio_sugerido', 0.0))
        )
        try:
            db.session.add(nuevo_prod)
            db.session.commit()
            flash('Producto creado exitosamente.', 'success')
            return redirect(url_for('inventory_bp.index'))
        except Exception as e:
            db.session.rollback()
            flash('Error al intentar guardar el producto en la base de datos.', 'danger')
            
    return render_template('inventory/form.html')

@inventory_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_producto(id):
    # get_or_404 protege la ruta en caso de que se envíe un ID inexistente en la URL
    producto = Product.query.get_or_404(id)
    
    if request.method == 'POST':
        stock_anterior = producto.cantidad_stock
        cantidad_stock_nueva = int(request.form.get('cantidad_stock', 0))
        
        # Se actualizan directamente las propiedades del objeto SQLAlchemy trackeado
        producto.sku = request.form.get('sku').strip()
        producto.nombre = request.form.get('nombre').strip()
        producto.cantidad_stock = cantidad_stock_nueva
        producto.precio_costo = float(request.form.get('precio_costo', 0.0))
        producto.precio_minimo = float(request.form.get('precio_minimo', 0.0))
        producto.precio_sugerido = float(request.form.get('precio_sugerido', 0.0))
        
        try:
            if stock_anterior != cantidad_stock_nueva:
                ajuste = StockAdjustment(
                    product_id=producto.id,
                    admin_id=current_user.id,
                    stock_anterior=stock_anterior,
                    stock_nuevo=cantidad_stock_nueva
                )
                db.session.add(ajuste)
                
            db.session.commit()
            flash('Producto actualizado correctamente en base de datos.', 'success')
            return redirect(url_for('inventory_bp.index'))
        except Exception as e:
            db.session.rollback()
            flash('Error en la base de datos al actualizar el producto.', 'danger')

    # El objeto producto se pasa a Jinja para auto-poblar (pre-llenar) el formulario en modo edición
    return render_template('inventory/form.html', producto=producto)

@inventory_bp.route('/historial-ajustes')
@login_required
@admin_required
def historial_ajustes():
    # joins implícitos a través de SQLAlchemy relationships se usan al acceder a las propiedades (ej. ajuste.producto.nombre),
    # o si se requiere optimización, se hace join explícito, pero iterar los proxies de ORM está bien para listas moderadas.
    ajustes = StockAdjustment.query.order_by(StockAdjustment.fecha_ajuste.desc()).all()
    return render_template('inventory/historial_ajustes.html', ajustes=ajustes)
