"""Execute real start/delivery functions with in-memory persistence and mocked Telegram."""
import ast,copy,unittest,sys
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone
from unittest.mock import AsyncMock,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import panel_launch, panel_delivery, panel_marketing
from test_exnova_registration import load,SOURCE

class AuditTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.e,self.engine=load(SOURCE)
  self.e['_PANEL_LAUNCH']=panel_launch.Store(self.engine,self.e['Session'],self.e['VIPAccessState'],self.e['BrokerAccountState'],self.e['PanelOnboardingState'])
  self.e['_panel_delivery_eligible']=lambda uid:True
  self.bot=SimpleNamespace(send_message=AsyncMock(),send_photo=AsyncMock())
  self.context=SimpleNamespace(bot=self.bot,user_data={},args=[])
 def tearDown(self):self.engine.dispose()
 async def test_owner_confirmation_on_success_once_and_on_explicit_resend(self):
  old=panel_delivery.send_package;panel_delivery.send_package=AsyncMock()
  try:
   self.assertTrue(await self.e['_panel_delivery_notify'](self.context,123,'es'))
   self.bot.send_message.assert_awaited_once()
   args=self.bot.send_message.call_args.kwargs
   self.assertEqual(args['chat_id'],999);self.assertIn('PAQUETE DEL PANEL ENVIADO',args['text']);self.assertIn('123',args['text'])
   await self.e['_panel_delivery_notify'](self.context,123,'es')
   self.assertEqual(panel_delivery.send_package.await_count,1);self.assertEqual(self.bot.send_message.await_count,1)
   await self.e['_panel_delivery_notify'](self.context,123,'en',requested=True)
   self.assertEqual(panel_delivery.send_package.await_count,2);self.assertEqual(self.bot.send_message.await_count,2)
  finally:panel_delivery.send_package=old
 async def test_owner_notice_failure_keeps_delivery_and_retry_does_not_resend_package(self):
  old=panel_delivery.send_package;panel_delivery.send_package=AsyncMock()
  self.bot.send_message=AsyncMock(side_effect=RuntimeError('owner send failed'))
  try:
   self.assertTrue(await self.e['_panel_delivery_notify'](self.context,123,'es'))
   with self.e['Session']() as s:self.assertIsNotNone(s.get(self.e['PanelOnboardingState'],'123').delivered_at)
   self.bot.send_message=AsyncMock()
   self.assertTrue(await self.e['_panel_delivery_notify'](self.context,123,'es'))
   self.assertEqual(panel_delivery.send_package.await_count,1);self.bot.send_message.assert_awaited_once()
  finally:panel_delivery.send_package=old
 async def test_failed_member_delivery_never_reports_success_to_owner(self):
  old=panel_delivery.send_package;panel_delivery.send_package=AsyncMock(side_effect=RuntimeError('user send failed'))
  try:
   self.assertFalse(await self.e['_panel_delivery_notify'](self.context,123,'es'))
   self.bot.send_message.assert_not_awaited()
  finally:panel_delivery.send_package=old
 async def test_direct_panel_start_for_new_and_existing_users_keeps_registration(self):
  tree=ast.parse(SOURCE.read_text());node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='start')
  fake=SimpleNamespace(send_info=AsyncMock())
  self.e.update(panel_marketing=fake,_touch_user_activity=Mock(),set_user_lang=Mock())
  exec(compile(ast.Module(body=[node],type_ignores=[]),'<real start>','exec'),self.e)
  for uid,lang,stage,args,expected in [(123,'es','PRE',['panel'],'es'),(124,'en','DEPOSITED',['panel'],'en'),(125,'es','POST',['panel_en'],'en')]:
   if uid!=123:
    with self.e['Session']() as s:s.add(self.e['Usuario'](telegram_id=str(uid),nombre='Member',lang=lang,stage=stage));s.commit()
   self.e['get_user_lang']=lambda _, value=lang:value
   self.context.args=args
   update=SimpleNamespace(effective_chat=SimpleNamespace(id=uid,type='private'),effective_user=SimpleNamespace(id=uid,full_name='Member',language_code='es'),effective_message=SimpleNamespace(reply_text=AsyncMock()))
   await self.e['start'](update,self.context)
   self.assertEqual(fake.send_info.call_args.args,(self.bot,uid,expected))
   with self.e['Session']() as s:self.assertEqual(s.query(self.e['Usuario']).filter_by(telegram_id=str(uid)).one().stage,stage)
   self.e['_sync_menu_campaign_for_stage'].assert_not_called()
 async def test_direct_panel_start_refuses_groups(self):
  node=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='start')
  fake=SimpleNamespace(send_info=AsyncMock());self.e['panel_marketing']=fake
  exec(compile(ast.Module(body=[node],type_ignores=[]),'<real start>','exec'),self.e)
  await self.e['start'](SimpleNamespace(effective_chat=SimpleNamespace(type='group')),self.context)
  fake.send_info.assert_not_awaited()
 def test_no_changes_to_old_start_branches_or_core_deposit_logic(self):
  before=SOURCE.parent/'main.audit-before.py'
  if not before.exists():self.skipTest('Local baseline only')
  def defs(path):return {n.name:n for n in ast.parse(path.read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
  old,new=defs(before),defs(SOURCE)
  start=copy.deepcopy(new['start']);start.body=[n for n in start.body if not (isinstance(n,ast.If) and (('start_param in' in ast.unparse(n.test) and "'panel_en'" in ast.unparse(n.test)) or "start_param.startswith('panel_link_')"==ast.unparse(n.test)))]
  for n in ast.walk(start):
   if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_log_event' and len(n.args)>2 and isinstance(n.args[1],ast.Constant) and n.args[1].value=='BOT_START':n.args[2]=ast.parse("start_param or 'normal'",mode='eval').body
  self.assertEqual(ast.dump(start),ast.dump(old['start']))
  for name in old:
   if name not in {'start','_panel_delivery_notify','_panel_admin_confirmation','panel_launch_review_callback','post_init_app'}:self.assertEqual(ast.dump(old[name]),ast.dump(new[name]),name)
 async def test_non_owner_cannot_approve_a_panel_ticket(self):
  node=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='panel_launch_review_callback')
  self.e['_PANEL_LAUNCH']=Mock();self.e['_PANEL_LAUNCH'].review=Mock()
  exec(compile(ast.Module(body=[node],type_ignores=[]),'<real review>','exec'),self.e)
  q=SimpleNamespace(answer=AsyncMock(),data='panel_launch_yes:'+'a'*20)
  await self.e['panel_launch_review_callback'](SimpleNamespace(callback_query=q,effective_user=SimpleNamespace(id=123)),self.context)
  self.e['_PANEL_LAUNCH'].review.assert_not_called();q.answer.assert_awaited_once()
 async def test_panel_record_failure_preserves_real_vip_deposit(self):
  e,engine=load(SOURCE)
  try:
   with e['Session']() as s:
    s.add(e['BrokerAccountState'](account_key='123:EXNOVA',telegram_id='123',broker='EXNOVA',trading_id='185456985',id_validated=1,validated_deposit_count=1,validated_total_cents=20000,upgrade_accum_cents=20000,level='PREMIUM'))
    s.add(e['VIPAccessState'](telegram_id='123',level='PREMIUM',validated_total_cents=20000));s.commit()
   e['_PANEL_LAUNCH']=SimpleNamespace(record=Mock(side_effect=RuntimeError('isolated panel failure')),eligible=lambda *a:False)
   preview=e['_broker_preview_deposit'](123,'EXNOVA',10000,True)
   ok,_=await e['_admin_apply_broker_deposit'](self.context,123,'EXNOVA',preview)
   self.assertTrue(ok);self.assertEqual(e['_broker_get'](123,'EXNOVA')['validated_total_cents'],30000)
   self.assertEqual(e['_vip_get_state'](123)['level'],'PREMIUM')
   e['_panel_launch_admin_notice'].assert_not_awaited()
  finally:engine.dispose()
 def test_public_button_keeps_registration_and_does_not_deliver_manual(self):
  for lang in ('es','en'):
   buttons=[b for row in panel_marketing.keyboard(lang).inline_keyboard for b in row]
   self.assertIn('registrarme',[b.callback_data for b in buttons])
   self.assertNotIn('member_signals_panel',[b.callback_data for b in buttons])
   self.assertNotIn(panel_delivery.PORTAL_URL,[b.url for b in buttons])
