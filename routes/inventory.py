from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import db, Product

inventory_bp = Blueprint('inventory_bp', __name__)

@inventory_bp.route('/', methods=['GET'])
@login_required
def index():
    if current_user.rol != 'admin':
        abort(403) # Lanza el 403 Forbidden al Vendedor
    productos = Product.query.order_by(Product.nombre).all()
    return render_template('inventory/index.html', productos=productos)

@inventory_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo():
    if current_user.rol != 'admin':
        abort(403)
        
    if request.method == 'POST':
        # Capturamos del formulario HTML (Aprovechamos Flask request en lugar de FlaskForm)
        nuevo_prod = Product(
            sku=request.form.get('sku').strip(),
            nombre=request.form.get('nombre').strip(),
            cantidad_stock=int(request.form.get('cantidad_stock', 0)),
            precio_costo=float(request.form.get('precio_costo', 0.0)),
            precio_minimo=float(request.form.get('precio_minimo', 0.0)),
            precio_sugerido=float(request.form.get('precio_sugerido', 0.0))
        )
        db.session.add(nuevo_prod)
        db.session.commit()
        flash('Producto creado exitosamente.', 'success')
        return redirect(url_for('inventory_bp.index'))
        
    return render_template('inventory/form.html')

@inventory_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_producto(id):
    if current_user.rol != 'admin':
        abort(403)
        
    # Regla: Buscar producto de manera segura
    producto = Product.query.get_or_404(id)
    
    if request.method == 'POST':
        # Regla: Reasignación y actualización dinámica
        producto.sku = request.form.get('sku').strip()
        producto.nombre = request.form.get('nombre').strip()
        producto.cantidad_stock = int(request.form.get('cantidad_stock', 0))
        producto.precio_costo = float(request.form.get('precio_costo', 0.0))
        producto.precio_minimo = float(request.form.get('precio_minimo', 0.0))
        producto.precio_sugerido = float(request.form.get('precio_sugerido', 0.0))
        
        db.session.commit() # Regla: Commit y redirección
        flash('Producto actualizado correctamente en base de datos.', 'success')
        return redirect(url_for('inventory_bp.index'))

    # Si es GET, pasamos el objeto para pre-llenar los campos ({% if producto %})
    return render_template('inventory/form.html', producto=producto)
