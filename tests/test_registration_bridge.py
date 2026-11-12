import unittest,sys,json,ast
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import AsyncMock,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import registration_bridge as bridge
import panel_launch
from test_exnova_registration import load,SOURCE
class BridgeTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.e,self.engine=load(SOURCE);self.Session=self.e['Session'];self.client=bridge.Client(self.engine,self.Session,'a'*64,999)
  self.context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=7)),send_document=AsyncMock()))
 def tearDown(self):self.engine.dispose()
 def account(self):
  with self.Session() as s:s.add(self.e['BrokerAccountState'](account_key='123:EXNOVA',telegram_id='123',broker='EXNOVA',trading_id='185456985',id_validated=1,validated_deposit_count=1,validated_total_cents=20000,level='PREMIUM'));s.commit()
 def grant(self):
  self.account();launch=panel_launch.Store(self.engine,self.Session,self.e['VIPAccessState'],self.e['BrokerAccountState'],self.e['PanelOnboardingState'])
  with self.Session() as s:
   row=s.get(self.e['BrokerAccountState'],'123:EXNOVA');ticket=launch.record(s,123,'EXNOVA',row,dict(amount_cents=10000,new_count=2));s.commit()
  launch.review(ticket,True,999,self.e['_broker_rows'](123));return launch,ticket
 async def test_only_approved_local_grants_are_sent_and_retry_is_durable(self):
  self.account();launch=panel_launch.Store(self.engine,self.Session,self.e['VIPAccessState'],self.e['BrokerAccountState'],self.e['PanelOnboardingState'])
  self.assertIsNone(self.client.capture(launch,123,self.e['_broker_rows'](123)))
  with self.Session() as s:row=s.get(self.e['BrokerAccountState'],'123:EXNOVA');ticket=launch.record(s,123,'EXNOVA',row,dict(amount_cents=10000,new_count=2));s.commit()
  self.assertIsNone(self.client.capture(launch,123,self.e['_broker_rows'](123)))
  launch.review(ticket,True,999,self.e['_broker_rows'](123));event=self.client.capture(launch,123,self.e['_broker_rows'](123));self.assertEqual(event,ticket)
  self.client.api=AsyncMock(side_effect=bridge.BridgeError('bridge_unavailable'))
  with self.assertRaises(bridge.BridgeError):await self.client.apply(event)
  with self.Session() as s:self.assertIsNone(s.get(bridge.Grant,event).applied_at)
  self.client.api=AsyncMock(return_value={'state':'awaiting_link'});await self.client.apply(event)
  with self.Session() as s:self.assertIsNone(s.get(bridge.Grant,event).applied_at)
  self.client.api=AsyncMock(return_value={'state':'activated','user':{'state':'active','expires_at':'2026-11-25T00:00:00Z'}});await self.client.apply(event)
  await self.client.apply(event);self.assertEqual(self.client.api.await_count,1)
  other=bridge.Client(self.engine,self.Session,'a'*64,999);self.assertEqual((await other.apply(event))['state'],'already_applied')
 async def test_owner_proof_and_notice_retries_do_not_repeat_owner_card(self):
  report=dict(id='a'*20,user_name='Member',email='m@example.com',plan='all',method='nequi',amount_cents=6500000,currency='COP',reference='reference1')
  self.client.api=AsyncMock(return_value={'proof':None});await self.client.owner_notice(self.context,report);await self.client.owner_notice(self.context,report)
  self.context.bot.send_message.assert_awaited_once();self.assertEqual(self.context.bot.send_message.call_args.kwargs['chat_id'],999)
 async def test_receipt_delivery_failure_retries_only_receipt(self):
  import base64
  r=dict(id='b'*20,user_name='Member',email='m@example.com',plan='all',method='nequi',amount_cents=6500000,currency='COP',reference='reference2')
  async def api(path,data=None):return {'proof':{'mime':'application/pdf','base64':base64.b64encode(b'%PDF-fixture').decode()}} if path.endswith('/proof') else {'ok':True}
  self.client.api=AsyncMock(side_effect=api);self.context.bot.send_document.side_effect=RuntimeError('temporary send failure')
  with self.assertRaises(RuntimeError):await self.client.owner_notice(self.context,r)
  self.context.bot.send_document.side_effect=None;await self.client.owner_notice(self.context,r)
  self.assertEqual(self.context.bot.send_message.await_count,1);self.assertEqual(self.context.bot.send_document.await_count,2)
 async def test_user_notice_is_link_bound_bilingual_and_not_duplicated(self):
  self.client.api=AsyncMock(return_value={'ok':True});self.client.language=lambda _:'en'
  r=dict(id='c'*20,status='verified',telegram_id='123',renewed_until='2026-12-25T18:00:00Z')
  await self.client.user_notice(self.context,r);await self.client.user_notice(self.context,r)
  self.context.bot.send_message.assert_awaited_once();kw=self.context.bot.send_message.call_args.kwargs;self.assertEqual(kw['chat_id'],123);self.assertIn('confirmed',kw['text'])
 async def test_activation_confirmation_is_sent_once_to_member_and_owner(self):
  launch,ticket=self.grant();self.client.capture(launch,123,self.e['_broker_rows'](123))
  result={'state':'activated','user':{'name':'Member','state':'active','expires_at':'2026-11-25T18:00:00Z'}}
  self.client.api=AsyncMock(return_value=result);await self.client.apply(ticket)
  with self.Session() as s:payload=json.loads(s.get(bridge.Grant,ticket).payload)
  await self.client.activation_notice(self.context,ticket,payload,result);await self.client.activation_notice(self.context,ticket,payload,result)
  self.assertEqual(self.context.bot.send_message.await_count,2);self.assertEqual({x.kwargs['chat_id'] for x in self.context.bot.send_message.call_args_list},{123,999})
 async def test_non_owner_payment_callback_never_calls_backend(self):
  node=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='registration_bridge_callback')
  client=SimpleNamespace(enabled=True,api=AsyncMock());self.e['_REGISTRATION_BRIDGE']=client
  exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual bridge callback>','exec'),self.e)
  q=SimpleNamespace(from_user=SimpleNamespace(id=123),data='registration_pay_yes:'+'a'*20,answer=AsyncMock())
  await self.e['registration_bridge_callback'](SimpleNamespace(callback_query=q,effective_chat=SimpleNamespace(type='private')),self.context)
  client.api.assert_not_awaited();q.answer.assert_awaited_once()
 def test_link_tokens_are_not_saved_in_start_event_logs(self):
  node=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='start')
  log=next(n for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_log_event' and len(n.args)>1 and isinstance(n.args[1],ast.Constant) and n.args[1].value=='BOT_START')
  self.assertIsInstance(log.args[2],ast.IfExp);self.assertEqual(log.args[2].body.value,'panel_link')
  self.assertIn("startswith('panel_link_')",ast.unparse(log.args[2].test))
