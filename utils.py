import re
from decimal import Decimal, InvalidOperation

def safe_int(val, default=0):
    """
    Convierte cualquier valor a entero de forma segura.
    Soporta None, strings vacíos, espacios, símbolos de moneda y separadores de miles.
    Previene errores 500 (ValueError / TypeError).
    """
    if val is None:
        return default
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    
    val_str = str(val).strip()
    if not val_str:
        return default
    
    # Remover símbolos comunes de moneda o espacios
    val_clean = val_str.replace('$', '').replace(' ', '')
    # Si contiene comas como separador de miles (ej: 1,000 o 100,000)
    val_clean = val_clean.replace(',', '')
    
    try:
        # Si viene con decimales ej: '150.00'
        return int(float(val_clean))
    except (ValueError, TypeError):
        return default

def safe_float(val, default=0.0):
    """
    Convierte cualquier valor a float de forma segura.
    Soporta None, strings vacíos, espacios, formato COP con puntos/comas.
    Previene errores 500 (ValueError / TypeError).
    """
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
        
    val_str = str(val).strip()
    if not val_str:
        return default
        
    val_clean = val_str.replace('$', '').replace(' ', '')
    # Si tiene comas de miles y punto decimal: 1,500.50 -> 1500.50
    # Si tiene solo comas como miles: 100,000 -> 100000
    if ',' in val_clean and '.' in val_clean:
        val_clean = val_clean.replace(',', '')
    elif ',' in val_clean and '.' not in val_clean:
        val_clean = val_clean.replace(',', '')
        
    try:
        return float(val_clean)
    except (ValueError, TypeError):
        return default

def safe_decimal(val, default=Decimal('0.00')):
    """
    Convierte cualquier valor a Decimal de forma segura.
    Soporta None, strings vacíos, enteros, floats y formato moneda.
    Previene InvalidOperation / TypeError.
    """
    if val is None:
        return Decimal(str(default))
    if isinstance(val, Decimal):
        return val
    if isinstance(val, (int, float)):
        return Decimal(str(val))
        
    val_str = str(val).strip()
    if not val_str:
        return Decimal(str(default))
        
    val_clean = val_str.replace('$', '').replace(' ', '')
    if ',' in val_clean and '.' in val_clean:
        val_clean = val_clean.replace(',', '')
    elif ',' in val_clean and '.' not in val_clean:
        val_clean = val_clean.replace(',', '')
        
    try:
        return Decimal(val_clean)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))
