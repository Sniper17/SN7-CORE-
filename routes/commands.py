from flask import Blueprint,jsonify,request
from core.database import get_conn
from core.command_system import list_commands,update_command,add_alias,delete_alias,delete_custom
commands_bp=Blueprint('commands',__name__)
@commands_bp.get('/<int:broadcaster_id>')
def get_commands(broadcaster_id):
 return jsonify({'ok':True,'commands':list_commands(broadcaster_id),'demo':False})
@commands_bp.post('/<int:broadcaster_id>')
def create_command(broadcaster_id):
 d=request.get_json(silent=True) or {};cmd=str(d.get('command','')).strip().lower();resp=str(d.get('response','')).strip()
 if not cmd.startswith('!') or not resp:return jsonify({'ok':False,'error':'comando/resposta inválidos'}),400
 c=get_conn()
 try:
  with c.cursor() as x:x.execute("""INSERT INTO command_configs(broadcaster_user_id,command_key,command,description,response,enabled,category,is_system) VALUES(%s,%s,%s,%s,%s,TRUE,'custom',FALSE)""",(broadcaster_id,'custom:'+cmd,cmd,str(d.get('description') or 'Comando personalizado desta live.'),resp))
  c.commit()
 finally:c.close()
 return jsonify({'ok':True,'commands':list_commands(broadcaster_id)})
@commands_bp.patch('/<int:broadcaster_id>/<path:key>')
def edit_command(broadcaster_id,key):
 d=request.get_json(silent=True) or {}
 try:update_command(broadcaster_id,key,command=d.get('command'),response=d.get('response'),enabled=d.get('enabled'),description=d.get('description'));return jsonify({'ok':True,'commands':list_commands(broadcaster_id)})
 except ValueError as e:return jsonify({'ok':False,'error':str(e)}),400
@commands_bp.delete('/<int:broadcaster_id>/<path:key>')
def delete_command(broadcaster_id,key):
 try:
  if not delete_custom(broadcaster_id,key):update_command(broadcaster_id,key,enabled=False)
  return jsonify({'ok':True,'commands':list_commands(broadcaster_id)})
 except ValueError as e:return jsonify({'ok':False,'error':str(e)}),400
@commands_bp.post('/<int:broadcaster_id>/<path:key>/aliases')
def create_alias(broadcaster_id,key):
 try:add_alias(broadcaster_id,key,(request.get_json(silent=True) or {}).get('alias'));return jsonify({'ok':True,'commands':list_commands(broadcaster_id)})
 except ValueError as e:return jsonify({'ok':False,'error':str(e)}),400
@commands_bp.delete('/<int:broadcaster_id>/<path:key>/aliases')
def remove_alias(broadcaster_id,key):
 try:return jsonify({'ok':True,'deleted':delete_alias(broadcaster_id,(request.get_json(silent=True) or {}).get('alias')),'commands':list_commands(broadcaster_id)})
 except ValueError as e:return jsonify({'ok':False,'error':str(e)}),400
