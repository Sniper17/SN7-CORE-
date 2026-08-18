from core.database import get_conn
SYSTEM={
'points':('!placos','Consulta seu saldo de pontos.','public','$(user), você tem $(points) $(currency). $(emoji) Sua posição no ranking é #$(rank).'),
'ranking':('!ranking','Mostra o ranking do canal.','public','$(ranking)'),
'duel':('!duelo','Inicia um duelo contra outro usuário.','public','$(duel_result)'),
'cmds':('!cmds','Lista os comandos personalizados da live.','public','$(commands)'),
'addcmd':('!addcmd','Cria ou atualiza um comando personalizado.','mod','✅ $(command) configurado.'),
'addpoint':('!addpoint','Adiciona pontos a um usuário.','mod','🪙 $(target) recebeu +$(amount) $(currency). Saldo: $(new_points) $(currency).'),
'settpoint':('!settpoint','Define o saldo de um usuário.','mod','🪙 Saldo de $(target): $(new_points) $(currency).'),
'delcmd':('!delcmd','Remove um comando personalizado.','mod','🗑️ $(command) removido.')}
def ensure_command_defaults(bid):
 bid=int(bid);c=get_conn()
 try:
  with c.cursor() as x:
   x.execute('SELECT currency_command FROM channels WHERE broadcaster_user_id=%s',(bid,));r=x.fetchone();pc=str((r[0] if r else None) or '!placos').lower()
   for k,(cmd,desc,cat,resp) in SYSTEM.items():
    if k=='points':cmd=pc
    x.execute("""INSERT INTO command_configs(broadcaster_user_id,command_key,command,description,response,enabled,category,is_system) VALUES(%s,%s,%s,%s,%s,TRUE,%s,TRUE) ON CONFLICT(broadcaster_user_id,command_key) DO UPDATE SET command=EXCLUDED.command,description=EXCLUDED.description,category=EXCLUDED.category,is_system=TRUE,updated_at=NOW()""",(bid,k,cmd,desc,resp,cat))
   x.execute('SELECT command,response FROM custom_commands WHERE broadcaster_user_id=%s',(bid,))
   for cmd,resp in x.fetchall():
    x.execute("""INSERT INTO command_configs(broadcaster_user_id,command_key,command,description,response,enabled,category,is_system) VALUES(%s,%s,%s,%s,%s,TRUE,'custom',FALSE) ON CONFLICT(broadcaster_user_id,command_key) DO UPDATE SET response=EXCLUDED.response,updated_at=NOW()""",(bid,'custom:'+cmd,cmd,'Comando personalizado desta live.',resp))
  c.commit()
 finally:c.close()
def _d(r):
 if not r:return None
 return {'id':r[0],'broadcaster_user_id':r[1],'command_key':r[2],'command':r[3],'description':r[4],'response':r[5],'enabled':bool(r[6]),'category':r[7],'is_system':bool(r[8]),'aliases':[]}
def list_commands(bid):
 ensure_command_defaults(bid);c=get_conn()
 try:
  with c.cursor() as x:
   x.execute("""SELECT id,broadcaster_user_id,command_key,command,description,response,enabled,category,is_system FROM command_configs WHERE broadcaster_user_id=%s ORDER BY CASE category WHEN 'public' THEN 1 WHEN 'mod' THEN 2 ELSE 3 END,command""",(int(bid),));out=[]
   for r in x.fetchall():
    q=_d(r);x.execute('SELECT alias FROM command_aliases WHERE broadcaster_user_id=%s AND command_id=%s ORDER BY alias',(int(bid),r[0]));q['aliases']=[z[0] for z in x.fetchall()];out.append(q)
   return out
 finally:c.close()
def find_command(bid,typed):
 ensure_command_defaults(bid);c=get_conn();typed=str(typed or '').strip().lower()
 try:
  with c.cursor() as x:
   x.execute("""SELECT c.id,c.broadcaster_user_id,c.command_key,c.command,c.description,c.response,c.enabled,c.category,c.is_system FROM command_configs c LEFT JOIN command_aliases a ON a.command_id=c.id AND a.broadcaster_user_id=c.broadcaster_user_id WHERE c.broadcaster_user_id=%s AND (c.command=%s OR a.alias=%s) LIMIT 1""",(int(bid),typed,typed));return _d(x.fetchone())
 finally:c.close()
def update_command(bid,key,command=None,response=None,enabled=None,description=None):
 c=get_conn()
 try:
  with c.cursor() as x:
   if command is not None:
    command=str(command).strip().lower()
    if not command.startswith('!'):raise ValueError('O comando deve começar com !')
    x.execute("""SELECT 1 FROM command_configs WHERE broadcaster_user_id=%s AND command=%s AND command_key<>%s UNION ALL SELECT 1 FROM command_aliases WHERE broadcaster_user_id=%s AND alias=%s LIMIT 1""",(int(bid),command,key,int(bid),command))
    if x.fetchone():raise ValueError('Essa palavra de ativação já está em uso.')
   fields=[];vals=[]
   for n,v in [('command',command),('response',response),('enabled',enabled),('description',description)]:
    if v is not None:fields.append(n+'=%s');vals.append(v)
   if not fields:return
   vals += [int(bid),key];x.execute('UPDATE command_configs SET '+','.join(fields)+',updated_at=NOW() WHERE broadcaster_user_id=%s AND command_key=%s',vals)
   if x.rowcount==0:raise ValueError('Comando não encontrado.')
   if command is not None and key=='points':x.execute('UPDATE channels SET currency_command=%s,updated_at=NOW() WHERE broadcaster_user_id=%s',(command,int(bid)))
  c.commit()
 finally:c.close()
def add_alias(bid,key,alias):
 alias=str(alias or '').strip().lower()
 if not alias.startswith('!'):raise ValueError('A palavra de ativação deve começar com !')
 c=get_conn()
 try:
  with c.cursor() as x:
   x.execute('SELECT id,command FROM command_configs WHERE broadcaster_user_id=%s AND command_key=%s',(int(bid),key));r=x.fetchone()
   if not r:raise ValueError('Comando não encontrado.')
   if alias==str(r[1]).lower():raise ValueError('Essa já é a palavra principal.')
   x.execute("""SELECT 1 FROM command_configs WHERE broadcaster_user_id=%s AND command=%s UNION ALL SELECT 1 FROM command_aliases WHERE broadcaster_user_id=%s AND alias=%s LIMIT 1""",(int(bid),alias,int(bid),alias))
   if x.fetchone():raise ValueError('Essa palavra de ativação já está em uso.')
   x.execute('INSERT INTO command_aliases(broadcaster_user_id,command_id,alias) VALUES(%s,%s,%s)',(int(bid),r[0],alias))
  c.commit()
 finally:c.close()
def delete_alias(bid,alias):
 c=get_conn()
 try:
  with c.cursor() as x:x.execute('DELETE FROM command_aliases WHERE broadcaster_user_id=%s AND alias=%s',(int(bid),str(alias).lower()));n=x.rowcount
  c.commit();return n>0
 finally:c.close()
def delete_custom(bid,key):
 c=get_conn()
 try:
  with c.cursor() as x:x.execute('DELETE FROM command_configs WHERE broadcaster_user_id=%s AND command_key=%s AND is_system=FALSE',(int(bid),key));n=x.rowcount
  c.commit();return n>0
 finally:c.close()
