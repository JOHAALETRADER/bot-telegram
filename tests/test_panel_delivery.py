import sys,unittest,copy,ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import panel_delivery as panel
import panel_launch
from datetime import datetime
from test_exnova_registration import load,SOURCE
class PanelDeliveryTests(unittest.IsolatedAsyncioTestCase):
 def state(self,**kw):
  return dict(broker='EXNOVA',id_validated=True,trading_id='185456985',pending_trading_id='',deposit_count=1,level='PREMIUM',validated_total_cents=23000,**kw)
 def test_boundary_and_brokers(self):
  for broker in ('BINOMO','STOCKITY','EXNOVA'):
   for cents,result in ((20000,False),(22999,False),(23000,True),(50000,True)):
    a=self.state();a.update(broker=broker,validated_total_cents=cents)
    self.assertEqual(panel.qualifies([a]),result)
 def test_pending_and_unvalidated_and_no_cross_broker_sum(self):
  for field,value in [('id_validated',False),('trading_id',''),('pending_trading_id','987654321'),('deposit_count',0),('level','BASIC'),('broker','UNKNOWN')]:
   a=self.state();a[field]=value;self.assertFalse(panel.qualifies([a]),field)
  a=self.state();b=self.state();a.update(validated_total_cents=20000);b.update(broker='BINOMO',validated_total_cents=3000)
  self.assertFalse(panel.qualifies([a,b]))
 def test_bilingual_links_and_caption(self):
  for lang in ('es','en'):
   self.assertLessEqual(len(panel.welcome(lang)),1024)
   rows=panel.keyboard(lang).inline_keyboard
   self.assertEqual(rows[1][0].url,panel.GUIDE_URLS[lang]);self.assertEqual(rows[0][0].url,panel.PORTAL_URL)
   self.assertIn('14',panel.welcome(lang));self.assertNotIn('at least USD230 have been validated',panel.welcome(lang))
 async def test_photo_failure_falls_back_to_buttons(self):
  import tempfile
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'photo.png';p.write_bytes(b'fixture');old=panel.PHOTO_PATH;panel.PHOTO_PATH=p
   bot=SimpleNamespace(send_photo=AsyncMock(side_effect=RuntimeError('simulated Telegram failure')),send_message=AsyncMock())
   try:await panel.send_package(bot,123,'en')
   finally:panel.PHOTO_PATH=old
   bot.send_message.assert_awaited_once();self.assertEqual(len(bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard),3)
 async def test_delivery_is_persistent_and_requested_reopen_works(self):
  e,engine=load(SOURCE);e['_broker_rows']=lambda _: [self.state()]
  e['_PANEL_LAUNCH']=panel_launch.Store(engine,e['Session'],e['VIPAccessState'],e['BrokerAccountState'],e['PanelOnboardingState'])
  with e['Session']() as s:s.add(panel_launch.Member(telegram_id='123',existing=0,granted_at=datetime.utcnow()));s.commit()
  context=SimpleNamespace(bot=SimpleNamespace(send_photo=AsyncMock(),send_message=AsyncMock()))
  old=panel.send_package;panel.send_package=AsyncMock()
  try:
   self.assertTrue(await e['_panel_delivery_notify'](context,123,'es'))
   self.assertTrue(await e['_panel_delivery_notify'](context,123,'es'))
   self.assertEqual(panel.send_package.await_count,1)
   self.assertTrue(await e['_panel_delivery_notify'](context,123,'en',requested=True))
   self.assertEqual(panel.send_package.await_count,2)
  finally:panel.send_package=old;engine.dispose()
 async def test_failure_never_grants_or_undoes_vip_and_is_retryable(self):
  e,engine=load(SOURCE);e['_broker_rows']=lambda _: [self.state()]
  e['_PANEL_LAUNCH']=panel_launch.Store(engine,e['Session'],e['VIPAccessState'],e['BrokerAccountState'],e['PanelOnboardingState'])
  with e['Session']() as s:s.add(panel_launch.Member(telegram_id='123',existing=0,granted_at=datetime.utcnow()));s.commit()
  old=panel.send_package;panel.send_package=AsyncMock(side_effect=RuntimeError('simulated failure'))
  try:
   self.assertFalse(await e['_panel_delivery_notify'](SimpleNamespace(bot=object()),123,'es'))
   with e['Session']() as s:self.assertIsNone(s.get(e['PanelOnboardingState'],'123'))
   panel.send_package=AsyncMock()
   self.assertTrue(await e['_panel_delivery_notify'](SimpleNamespace(bot=object()),123,'es'))
  finally:panel.send_package=old;engine.dispose()
 async def test_unqualified_never_receives_anything(self):
  e,engine=load(SOURCE);e['_broker_rows']=lambda _: []
  old=panel.send_package;panel.send_package=AsyncMock()
  try:
   self.assertFalse(await e['_panel_delivery_notify'](SimpleNamespace(bot=object()),123,'es',requested=True))
   panel.send_package.assert_not_awaited()
  finally:panel.send_package=old;engine.dispose()
 def test_menu_and_callback_both_guard_eligibility(self):
  tree=ast.parse(SOURCE.read_text())
  menu=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='member_space_keyboard')
  self.assertTrue(any(isinstance(n,ast.If) and '_panel_delivery_eligible(chat_id)'==ast.unparse(n.test) and 'member_signals_panel' in ast.unparse(n) for n in ast.walk(menu)))
  cb=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='botones')
  arm=next(n for n in cb.body if isinstance(n,ast.If) and "q.data == 'member_signals_panel'"==ast.unparse(n.test))
  self.assertIn('not _panel_delivery_eligible(chat_id)',ast.unparse(arm))
if __name__=='__main__':unittest.main()
