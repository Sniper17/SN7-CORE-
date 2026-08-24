from flask import Blueprint, jsonify, request
from core.auth import require_session_broadcaster
from core.automations import list_automations, save_automation, delete_automation

automations_bp=Blueprint('automations',__name__)

@automations_bp.get('/<int:broadcaster_id>')
def get_all(broadcaster_id):
    try: require_session_broadcaster(broadcaster_id)
    except PermissionError as exc: return jsonify({'ok':False,'error':str(exc)}),403
    return jsonify({'ok':True,'automations':list_automations(broadcaster_id)})

@automations_bp.post('/<int:broadcaster_id>')
def create(broadcaster_id):
    try: require_session_broadcaster(broadcaster_id)
    except PermissionError as exc: return jsonify({'ok':False,'error':str(exc)}),403
    try:
        aid=save_automation(broadcaster_id,request.get_json(silent=True) or {})
        return jsonify({'ok':True,'automations':list_automations(broadcaster_id),'id':aid})
    except (TypeError,ValueError) as exc: return jsonify({'ok':False,'error':str(exc)}),400
    except Exception as exc: print(f'[AUTOMATION] create: {exc}',flush=True); return jsonify({'ok':False,'error':str(exc)}),500

@automations_bp.put('/<int:broadcaster_id>/<int:automation_id>')
def update(broadcaster_id,automation_id):
    try: require_session_broadcaster(broadcaster_id)
    except PermissionError as exc: return jsonify({'ok':False,'error':str(exc)}),403
    try:
        save_automation(broadcaster_id,request.get_json(silent=True) or {},automation_id)
        return jsonify({'ok':True,'automations':list_automations(broadcaster_id)})
    except (TypeError,ValueError) as exc: return jsonify({'ok':False,'error':str(exc)}),400
    except Exception as exc: return jsonify({'ok':False,'error':str(exc)}),500

@automations_bp.delete('/<int:broadcaster_id>/<int:automation_id>')
def remove(broadcaster_id,automation_id):
    try: require_session_broadcaster(broadcaster_id)
    except PermissionError as exc: return jsonify({'ok':False,'error':str(exc)}),403
    try:
        delete_automation(broadcaster_id,automation_id)
        return jsonify({'ok':True,'automations':list_automations(broadcaster_id)})
    except Exception as exc: return jsonify({'ok':False,'error':str(exc)}),500
