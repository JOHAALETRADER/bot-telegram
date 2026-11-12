import ast,unittest,sys
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import panel_launch as launch
from test_exnova_registration import load,SOURCE
class LaunchTests(unittest.TestCase):
 def setUp(self):
  self.e,self.engine=load(SOURCE);self.Session=self.e['Session']
 def tearDown(self):self.engine.dispose()
 def account(self,uid='123',broker='EXNOVA',level='PREMIUM',total=20000):
  row=self.e['BrokerAccountState'](account_key=uid+':'+broker,telegram_id=uid,broker=broker,trading_id='185456985',pending_trading_id=None,id_validated=1,validated_deposit_count=1,validated_total_cents=total,upgrade_accum_cents=total,level=level)
  with self.Session() as s:s.add(row);s.commit()
 def boot(self):
  return launch.Store(self.engine,self.Session,self.e['VIPAccessState'],self.e['BrokerAccountState'],self.e['PanelOnboardingState'])
 def record(self,store,amount,uid='123',accum=False,newaccum=0,sequence=2):
  with self.Session() as s:
   row=s.get(self.e['BrokerAccountState'],uid+':EXNOVA')
   ticket=store.record(s,uid,'EXNOVA',row,dict(amount_cents=amount,new_count=sequence,accumulates=accum,new_accum_cents=newaccum));s.commit();return ticket
 def accounts(self,uid='123'):
  return self.e['_broker_rows'](int(uid))
 def test_existing_100_requires_owner_date_confirmation_not_old_total(self):
  self.account(total=50000);store=self.boot()
  self.assertTrue(store.existing(123));self.assertFalse(store.eligible(123,self.accounts()))
  self.assertIsNone(self.record(store,9999))
  t=self.record(store,10000)
  self.assertFalse(store.eligible(123,self.accounts()))
  r,_=store.review(t,True,999,self.accounts());self.assertEqual(r['route'],'EXISTING100')
  self.assertTrue(store.eligible(123,self.accounts()))
  store2=self.boot();self.assertTrue(store2.eligible(123,self.accounts()))
  self.assertEqual(store2.pending(),[])
 def test_basic_and_new_cannot_use_existing_100_or_closed_old_accum(self):
  self.account(level='BASIC',total=5000);store=self.boot()
  self.assertFalse(store.existing(123))
  with self.Session() as s:r=s.get(self.e['BrokerAccountState'],'123:EXNOVA');r.level='PREMIUM';s.commit()
  self.assertIsNone(self.record(store,10000,newaccum=50000))
  self.assertIsNone(self.record(store,18000,accum=False,newaccum=23000))
  self.assertIsNone(self.record(store,18000,accum=True,newaccum=22999))
  t=self.record(store,18000,accum=True,newaccum=23000)
  self.assertEqual(store.lookup(t)['route'],'ENTRY230')
 def test_new_single_230_and_no_cross_account_borrowing(self):
  store=self.boot();self.account();self.assertFalse(store.existing(123))
  self.assertIsNone(self.record(store,22999));t=self.record(store,23000)
  self.assertFalse(store.eligible(123,self.accounts()))
  wrong=[dict(self.accounts()[0],broker='BINOMO')]
  r,_=store.review(t,True,999,wrong);self.assertIsNone(r)
  store.review(t,True,999,self.accounts());self.assertTrue(store.eligible(123,self.accounts()))
  self.assertFalse(store.eligible(123,wrong))
 def test_snapshot_survives_later_upgrade_and_restart(self):
  self.account(level='BASIC');store=self.boot()
  with self.Session() as s:r=s.get(self.e['BrokerAccountState'],'123:EXNOVA');r.level='PRESTIGE';s.commit()
  self.assertFalse(self.boot().existing(123));self.assertIsNone(self.record(store,10000))
 def test_duplicate_ticket_and_rejection_never_grants(self):
  self.account();store=self.boot();t=self.record(store,10000)
  self.assertEqual(t,self.record(store,10000))
  store.review(t,False,999,self.accounts());r,msg=store.review(t,True,999,self.accounts())
  self.assertEqual(r['status'],'REJECTED');self.assertFalse(store.eligible(123,self.accounts()))
  self.assertEqual(self.accounts()[0]['level'],'PREMIUM')
 def test_pending_id_and_changed_id_block_delivery(self):
  self.account();store=self.boot();t=self.record(store,10000)
  for a in [dict(self.accounts()[0],pending_trading_id='999999'),dict(self.accounts()[0],id_validated=False),dict(self.accounts()[0],trading_id='DIFFERENT')]:
   self.assertIsNone(store.review(t,True,999,[a])[0])
  self.assertFalse(store.eligible(123,self.accounts()))
 def test_previously_delivered_access_preserved(self):
  self.account();
  with self.Session() as s:s.add(self.e['PanelOnboardingState'](telegram_id='123',delivered_at=datetime.utcnow()));s.commit()
  store=self.boot();self.assertTrue(store.eligible(123,self.accounts()));self.assertIsNone(self.record(store,10000))
 def test_global_prestige_qualifies_without_faking_broker_amount(self):
  self.account(level='BASIC',total=5000)
  with self.Session() as s:s.add(self.e['VIPAccessState'](telegram_id='123',level='PRESTIGE'));s.commit()
  store=self.boot();self.assertTrue(store.existing(123));self.assertIsNotNone(self.record(store,10000))
 def test_texts_and_handler_ownership(self):
  for lang in ('es','en'):
   t=launch.conditions(lang);self.assertIn('100',t);self.assertIn('230',t)
  tree=ast.parse(SOURCE.read_text());cb=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='panel_launch_review_callback')
  code=ast.unparse(cb);self.assertIn('update.effective_user.id != ADMIN_ID',code)
  self.assertLess(code.index('update.effective_user.id != ADMIN_ID'),code.index('_PANEL_LAUNCH.review'))
 def test_core_level_and_promo_algorithms_unchanged(self):
  before=SOURCE.parent/'main.before.py'
  if not before.exists():self.skipTest('Local baseline only')
  def definitions(path):return {n.name:ast.dump(n) for n in ast.parse(path.read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
  old,new=definitions(before),definitions(SOURCE)
  for name in old:
   if name.startswith('_premium100_') or name in {'_broker_preview_deposit','_vip_set_state','_broker_upgrade_window_open','_admin_finalize_broker_id','_vip_new_channel_keys','_vip_level_for_total_cents'}:self.assertEqual(old[name],new[name],name)
if __name__=='__main__':unittest.main()

class DepositIntegrationTests(unittest.IsolatedAsyncioTestCase):
 async def test_real_deposit_hook_preserves_level_and_waits_for_date_validation(self):
  e,engine=load(SOURCE)
  try:
   with e['Session']() as s:
    s.add(e['BrokerAccountState'](account_key='123:EXNOVA',telegram_id='123',broker='EXNOVA',trading_id='185456985',id_validated=1,validated_deposit_count=1,validated_total_cents=20000,upgrade_accum_cents=20000,level='PREMIUM'))
    s.add(e['VIPAccessState'](telegram_id='123',level='PREMIUM',validated_total_cents=20000));s.commit()
   store=launch.Store(engine,e['Session'],e['VIPAccessState'],e['BrokerAccountState'],e['PanelOnboardingState']);e['_PANEL_LAUNCH']=store
   context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()),user_data={})
   preview=e['_broker_preview_deposit'](123,'EXNOVA',10000,True)
   ok,_=await e['_admin_apply_broker_deposit'](context,123,'EXNOVA',preview)
   self.assertTrue(ok);self.assertEqual(e['_broker_get'](123,'EXNOVA')['validated_total_cents'],30000)
   self.assertEqual(e['_vip_get_state'](123)['level'],'PREMIUM')
   self.assertFalse(store.eligible(123,e['_broker_rows'](123)))
   ticket=store.pending(123)[0]['ticket'];e['_panel_launch_admin_notice'].assert_awaited_once()
   duplicate,_=await e['_admin_apply_broker_deposit'](context,123,'EXNOVA',preview)
   self.assertFalse(duplicate);self.assertEqual(len(store.pending(123)),1)
   store.review(ticket,True,999,e['_broker_rows'](123))
   with e['Session']() as s:
    event=s.get(launch.Deposit,ticket);self.assertEqual(event.deposited_since_launch_confirmed,1);self.assertEqual(event.id_before_deposit_confirmed,1)
   self.assertTrue(e['_panel_delivery_eligible'](123))
  finally:engine.dispose()
