from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from decorators import admin_required
from models import db, ZenicDataAgreementAudit, obtener_hora_bogota
from datetime import datetime
import hashlib
import json

legal_bp = Blueprint('legal_bp', __name__)

# Constante institucional de ZENIC S.A.S. (Encargado del Tratamiento)
ZENIC_CORP_DATA = {
    'razon_social': 'ZENIC S.A.S.',
    'nit': '902067639-6',
    'representante': 'Jhon Danny Gualdron Aparicio',
    'identificacion_rep': '1004910859 de Bogotá',
    'domicilio': 'Bogotá D.C., Colombia',
    'email_contacto': 'contacto@zenic.co',
    'version': 'v2026.1'
}

def obtener_ip_cliente(req):
    """Extrae la IP real considerando proxys inversos (Nginx, Cloudflare, etc.)."""
    if req.headers.get('X-Forwarded-For'):
        # Toma la primera IP de la cadena
        ip = req.headers.get('X-Forwarded-For').split(',')[0].strip()
        return ip
    elif req.headers.get('X-Real-IP'):
        return req.headers.get('X-Real-IP').strip()
    return req.remote_addr or '127.0.0.1'

@legal_bp.route('/acuerdo-datos', methods=['GET'])
@login_required
def acuerdo_datos():
    """
    Vista formal del Contrato y Hoja de Custodia Técnica Ley 1581 / SIC.
    Apta para visualización web y exportación física / PDF de alta resolución.
    """
    acuerdo = ZenicDataAgreementAudit.query.order_by(ZenicDataAgreementAudit.id.desc()).first()
    return render_template(
        'legal/acuerdo_datos.html',
        zenic=ZENIC_CORP_DATA,
        acuerdo=acuerdo,
        ahora=obtener_hora_bogota()
    )

@legal_bp.route('/firmar-acuerdo', methods=['POST'])
@login_required
@admin_required
def firmar_acuerdo():
    """
    Endpoint seguro para estampar firma electrónica y auditoría jurídica inmutable (Ley 527 de 1999).
    """
    # INMUTABILIDAD LEGAL: Si ya existe un acuerdo firmado, queda blindado de forma permanente.
    acuerdo_existente = ZenicDataAgreementAudit.query.order_by(ZenicDataAgreementAudit.id.desc()).first()
    if acuerdo_existente:
        msg = f"Este acuerdo ya fue formalizado y firmado válidamente el {acuerdo_existente.timestamp_utc5.strftime('%Y-%m-%d %H:%M:%S')}. Por disposición de la Ley 527 de 1999 y Ley 1581 de 2012, el documento es inmutable y no puede ser alterado ni firmado nuevamente."
        if request.is_json:
            return jsonify({'success': False, 'error': msg, 'already_signed': True}), 400
        flash(msg, 'warning')
        return redirect(url_for('legal_bp.acuerdo_datos'))

    try:
        # Soporta payload JSON o Formulario
        if request.is_json:
            data = request.get_json() or {}
        else:
            data = request.form.to_dict()

        business_name = (data.get('business_name') or 'cases.com').strip()
        business_nit = (data.get('business_nit') or '901.888.777-1').strip()
        legal_rep = (data.get('legal_representative') or current_user.nombre or 'Representante Legal').strip()
        legal_rep_id = (data.get('legal_rep_id') or '').strip()
        business_city = (data.get('business_city') or 'Bogotá D.C., Colombia').strip()
        business_email = (data.get('business_email') or current_user.email or '').strip()

        accepted_terms = 1 if str(data.get('accepted_terms')).lower() in ['1', 'true', 'on'] else 0
        accepted_dpa = 1 if str(data.get('accepted_dpa')).lower() in ['1', 'true', 'on'] else 0
        accepted_media = 1 if str(data.get('accepted_media_release')).lower() in ['1', 'true', 'on'] else 0
        signature_data = (data.get('signature_data') or '').strip()

        # Validaciones de cumplimiento legal estricto
        if not legal_rep_id:
            msg = 'Es obligatorio indicar el documento de identidad y lugar de expedición del representante legal.'
            if request.is_json:
                return jsonify({'success': False, 'error': msg}), 400
            flash(msg, 'danger')
            return redirect(url_for('legal_bp.acuerdo_datos'))

        if not (accepted_terms and accepted_dpa and accepted_media):
            msg = 'Debes aceptar las tres cláusulas obligatorias (Términos de custodia SIC, Tratamiento de datos y Autorización audiovisual).'
            if request.is_json:
                return jsonify({'success': False, 'error': msg}), 400
            flash(msg, 'danger')
            return redirect(url_for('legal_bp.acuerdo_datos'))

        if not signature_data or not signature_data.startswith('data:image'):
            msg = 'Debe plasmar su rúbrica digital manuscrita en el recuadro de firma.'
            if request.is_json:
                return jsonify({'success': False, 'error': msg}), 400
            flash(msg, 'danger')
            return redirect(url_for('legal_bp.acuerdo_datos'))

        ip_address = obtener_ip_cliente(request)
        user_agent = request.headers.get('User-Agent', 'Unknown')
        timestamp_ahora = obtener_hora_bogota()

        # Generación de Hash Criptográfico SHA-256 (Evidencia Digital Ley 527 de 1999)
        payload_para_hash = {
            'encargado_nit': ZENIC_CORP_DATA['nit'],
            'responsable_nit': business_nit,
            'responsable_nombre': business_name,
            'representante': legal_rep,
            'representante_id': legal_rep_id,
            'version': ZENIC_CORP_DATA['version'],
            'accepted_terms': accepted_terms,
            'accepted_dpa': accepted_dpa,
            'accepted_media': accepted_media,
            'ip': ip_address,
            'user_agent': user_agent,
            'timestamp': timestamp_ahora.strftime('%Y-%m-%d %H:%M:%S'),
            'signature_sample': signature_data[:64] # Muestra inicial de la rúbrica
        }
        raw_string = json.dumps(payload_para_hash, sort_keys=True)
        sha256_hash = hashlib.sha256(raw_string.encode('utf-8')).hexdigest()

        nuevo_acuerdo = ZenicDataAgreementAudit(
            client_id=1,
            user_id=current_user.id if current_user.is_authenticated else None,
            business_name=business_name,
            business_nit=business_nit,
            legal_representative=legal_rep,
            legal_rep_id=legal_rep_id,
            business_city=business_city,
            business_email=business_email,
            accepted_terms=accepted_terms,
            accepted_dpa=accepted_dpa,
            accepted_media_release=accepted_media,
            terms_version=ZENIC_CORP_DATA['version'],
            document_sha256=sha256_hash,
            ip_address=ip_address,
            user_agent=user_agent,
            signature_data=signature_data,
            timestamp_utc5=timestamp_ahora
        )

        db.session.add(nuevo_acuerdo)
        db.session.commit()

        if request.is_json:
            return jsonify({
                'success': True,
                'message': 'Acuerdo legal y autorización audiovisual firmados exitosamente.',
                'sha256': sha256_hash,
                'redirect_url': url_for('legal_bp.acuerdo_datos')
            })

        flash('¡Acuerdo formal de custodia de datos (SIC) y autorización audiovisual firmado exitosamente!', 'success')
        return redirect(url_for('legal_bp.acuerdo_datos'))

    except Exception as e:
        db.session.rollback()
        error_msg = f'Error procesando la firma electrónica: {str(e)}'
        if request.is_json:
            return jsonify({'success': False, 'error': error_msg}), 500
        flash(error_msg, 'danger')
        return redirect(url_for('legal_bp.acuerdo_datos'))

@legal_bp.route('/estado-acuerdo', methods=['GET'])
def estado_acuerdo():
    """Endpoint JSON liviano para verificar si el comercio ya cuenta con firma vigente."""
    acuerdo = ZenicDataAgreementAudit.query.order_by(ZenicDataAgreementAudit.id.desc()).first()
    if acuerdo:
        return jsonify({
            'firmado': True,
            'version': acuerdo.terms_version,
            'fecha_firma': acuerdo.timestamp_utc5.strftime('%Y-%m-%d %H:%M:%S'),
            'sha256': acuerdo.document_sha256,
            'comercio': acuerdo.business_name,
            'nit': acuerdo.business_nit,
            'representante': acuerdo.legal_representative
        })
    return jsonify({
        'firmado': False,
        'version': ZENIC_CORP_DATA['version']
    })

