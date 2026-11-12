"""Offline regression of the real registration callbacks and persisted broker state.

Only selected AST definitions are loaded. Importing the production entry point
would initialize its database and Telegram application, which these tests avoid.
"""
import ast
import sys
import asyncio
import copy
import logging
import re
import unittest
import unicodedata
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'main.py'
if not SOURCE.exists():SOURCE=ROOT/'main_johanna_FINAL_ID_ACTUALIZADO.py'
ORIGINAL=ROOT/'main.original.py'
sys.path.insert(0,str(ROOT))
import panel_delivery
import panel_launch
URL='https://exnova.com/lp/start-trading/?aff=838919&aff_model=revenue&afftrack=JOHAALETRADER'
FUNCTIONS={
 '_panel_admin_confirmation','_panel_delivery_eligible','_panel_delivery_notify','utcnow_naive','_usd','_norm','_broker_norm','_broker_label','_broker_key','_broker_get',
 '_broker_rows','_broker_validated_brokers','_broker_flow_get','_broker_flow_set',
 '_broker_selection_keyboard','_broker_upgrade_window_open','_max_level','_vip_level_for_total_cents',
 '_broker_preview_deposit','_broker_seed_legacy_if_matching','_broker_bind_legacy_validated_id',
 '_vip_get_state','_vip_set_state','broker_user_callback','_admin_finalize_broker_id',
 '_admin_reject_broker_id','admin_broker_callback','_admin_apply_broker_deposit',
 '_referral_links_for_user','_personalize_referral_links','_url_with_query_param',
 '_organize_ai_registration_links','_id_validation_guidance'
}
CONSTANTS={'ENLACE_REFERIDO','ENLACE_REFERIDO_STOCKITY','ENLACE_REFERIDO_EXNOVA',
 'VIP_LEVEL_NONE','VIP_LEVEL_BASIC','VIP_LEVEL_PREMIUM','VIP_LEVEL_PRESTIGE','VIP_LEVEL_RANK',
 'VIP_LEVEL_THRESHOLDS_CENTS','BROKER_BINOMO','BROKER_STOCKITY','BROKER_EXNOVA','BROKERS',
 'UPGRADE_ACCUM_MAX_DEPOSITS','UPGRADE_ACCUM_WINDOW_DAYS','COLOMBIA_TZ',
 'STAGE_PRE','STAGE_POST','STAGE_DEPOSITED','PREMIUM100_MIN_CENTS','PREMIUM100_NORMAL_PREMIUM_CENTS',
 'GATILLO_ID_ERRADO','GATILLO_ID_ERRADO_EN','MENSAJE_REGISTRARME_ES','MENSAJE_REGISTRARME_EN',
 'CANAL_RESULTADOS'
}
CONSTANTS.update(f'MENSAJE_{hour}_{lang}' for hour in ('1H','3H','24H','48H') for lang in ('ES','EN'))

def load(path):
 env=dict(globals(),Base=declarative_base(),_PANEL_LAUNCH=None,_panel_launch_admin_notice=AsyncMock())
 body=[]
 for node in ast.parse(path.read_text()).body:
  if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in FUNCTIONS:body.append(node)
  elif isinstance(node,ast.ClassDef) and node.name in {'Usuario','BrokerAccountState','BrokerFlowState','VIPAccessState','PanelOnboardingState'}:body.append(node)
  elif isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in CONSTANTS for t in node.targets):body.append(node)
 exec(compile(ast.Module(body=body,type_ignores=[]),str(path),'exec'),env)
 engine=create_engine('sqlite:///:memory:')
 env['Base'].metadata.create_all(engine)
 env.update(Session=sessionmaker(bind=engine), ADMIN_ID=999,
   _is_private_user_id=lambda value:isinstance(value,int) and value>0,
   get_user_lang=lambda _: 'es',get_user_stage=lambda _: 'PRE',set_user_stage=Mock(),
   _get_saved_trading_id=lambda _: '',
   _sync_menu_campaign_for_stage=Mock(),_log_event=Mock(),_tracking_fire_event=Mock(),_telegram_display_name=lambda _: 'Fixture',
   _prune_pending_ai_after_operation=Mock(),_cancel_jobs_prefix=Mock(),schedule_series_a=Mock(),schedule_series_b=Mock(),
   _premium100_mark_id_validated=lambda *a:False,_premium100_mark_id_rejected=Mock(),
   _premium100_admin_deposit_candidate=lambda *a:False,
   _strict_validated_id_state=lambda _:False,
   _get_channel_source=lambda _: 'ORGANIC',_get_ads_click_token=lambda _: '',
   _admin_review_bind=Mock(),_admin_review_render=AsyncMock(),
   _vip_level_label=lambda level,lang:level,
   _vip_new_channel_keys=lambda old,new:['vip_main'],
   VIP_ACCESS_CHANNELS={'vip_main':{}},
   upgrade_info_keyboard=lambda _:InlineKeyboardMarkup([]),
   _vip_access_intro=lambda *a,**k:'Access',
   _vip_access_keyboard=lambda *a,**k:InlineKeyboardMarkup([]),
 )
 env['_personalize_referral_links']=env['_personalize_referral_links']
 return env,engine

class RegistrationTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.e,self.engine=load(SOURCE)
  self.context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()),user_data={})
 def tearDown(self):self.engine.dispose()
 def update(self,data,admin=False):
  user=SimpleNamespace(id=999 if admin else 123)
  q=SimpleNamespace(data=data,answer=AsyncMock(),message=SimpleNamespace(reply_text=AsyncMock()),from_user=user)
  return SimpleNamespace(callback_query=q,effective_user=user,effective_chat=SimpleNamespace(id=user.id,type='private'))
 def row(self,broker):
  with self.e['Session']() as session:
   return session.get(self.e['BrokerAccountState'],f'123:{broker}')
 def test_three_brokers_and_keyboards(self):
  for kind in ('id','deposit'):
   for lang in ('es','en'):
    buttons=self.e['_broker_selection_keyboard'](kind,lang).inline_keyboard[0]
    self.assertEqual([b.text for b in buttons],['🟡 BINOMO','🔵 STOCKITY','🟠 EXNOVA'])
  self.assertEqual(self.e['_broker_norm']('exnova'),'EXNOVA')
  self.assertEqual(self.e['_broker_norm']('bad'),'')
  self.assertEqual(self.e['_broker_label']('EXNOVA'),'Exnova')
 def test_standard_links_and_exact_affiliation_in_both_languages(self):
  for name in CONSTANTS:
   if name.startswith('MENSAJE_') or name.startswith('GATILLO_ID_ERRADO'):
    self.assertIn(URL,self.e[name])
  for lang in ('es','en'):
   text=self.e['_organize_ai_registration_links'](f'Register\n[Exnova]({URL})',lang)
   self.assertIn(URL,text);self.assertEqual(text.count(URL),1)
   self.assertNotIn(f']({URL})',text)
 def test_ads_preserves_existing_links_and_does_not_rewrite_exnova(self):
  self.e['_get_channel_source']=lambda _:'ADS'
  self.e['_get_ads_click_token']=lambda _:'click1'
  personalized=self.e['_personalize_referral_links'](self.e['MENSAJE_REGISTRARME_ES'],123)
  self.assertIn(URL,personalized)
  self.assertIn('sa=JTTRADERS_click1',personalized)
  self.assertEqual(self.e['_personalize_referral_links'](personalized,123),personalized)
 async def test_real_id_callback_pending_admin_validation_and_deposit(self):
  self.e['_broker_flow_set'](123,pending_trading_id='185456985')
  update=self.update('broker_id_select:EXNOVA')
  await self.e['broker_user_callback'](update,self.context)
  self.assertEqual(self.row('EXNOVA').pending_trading_id,'185456985')
  self.assertFalse(self.row('EXNOVA').id_validated)
  admin=self.context.bot.send_message.call_args.kwargs
  callbacks=[b.callback_data for row in admin['reply_markup'].inline_keyboard for b in row]
  self.assertIn('admin_broker_validate:123:EXNOVA',callbacks)
  self.assertIn('admin_broker_reject:123:EXNOVA',callbacks)
  ok,_=await self.e['_admin_finalize_broker_id'](self.context,123,'EXNOVA')
  self.assertTrue(ok);self.assertTrue(self.row('EXNOVA').id_validated)
  update=self.update('broker_deposit_select:EXNOVA')
  await self.e['broker_user_callback'](update,self.context)
  self.assertEqual(self.e['_broker_flow_get'](123)['pending_deposit_broker'],'EXNOVA')
  preview=self.e['_broker_preview_deposit'](123,'EXNOVA',23000,True)
  ok,msg=await self.e['_admin_apply_broker_deposit'](self.context,123,'EXNOVA',preview)
  self.assertTrue(ok,msg)
  self.assertEqual(self.row('EXNOVA').level,'PREMIUM')
  self.assertEqual(self.row('EXNOVA').validated_total_cents,23000)
  self.assertEqual(self.row('EXNOVA').validated_deposit_count,1)
  again,_=await self.e['_admin_apply_broker_deposit'](self.context,123,'EXNOVA',preview)
  self.assertFalse(again)
 async def test_all_existing_broker_id_callbacks_still_work_in_both_languages(self):
  for broker in ('BINOMO','STOCKITY'):
   for lang in ('es','en'):
    e,engine=load(SOURCE)
    try:
     e['get_user_lang']=lambda _,lang=lang:lang
     e['_broker_flow_set'](123,pending_trading_id='123456789')
     await e['broker_user_callback'](self.update('broker_id_select:'+broker),self.context)
     self.assertEqual(e['_broker_get'](123,broker)['pending_trading_id'],'123456789')
    finally:engine.dispose()
 async def test_unvalidated_exnova_cannot_borrow_a_legacy_id_or_deposit(self):
  self.e['_get_saved_trading_id']=lambda _: '123456789'
  self.e['_strict_validated_id_state']=lambda _:True
  self.e['_broker_seed_legacy_if_matching'](123,'EXNOVA','123456789')
  self.assertIsNone(self.row('EXNOVA'))
  self.assertFalse(self.e['_broker_bind_legacy_validated_id'](123,'EXNOVA'))
  await self.e['broker_user_callback'](self.update('broker_deposit_select:EXNOVA'),self.context)
  self.assertIsNone(self.row('EXNOVA'))
  self.assertEqual(self.e['_broker_flow_get'](123)['pending_deposit_broker'],'')
 async def test_exnova_rejection_and_administrator_only_buttons(self):
  self.e['_broker_flow_set'](123,pending_trading_id='123456789')
  await self.e['broker_user_callback'](self.update('broker_id_select:EXNOVA'),self.context)
  await self.e['admin_broker_callback'](self.update('admin_broker_validate:123:EXNOVA'),self.context)
  self.e['_admin_review_render'].assert_not_awaited()
  await self.e['admin_broker_callback'](self.update('admin_broker_validate:123:EXNOVA',True),self.context)
  self.assertIn('EXNOVA',self.e['_admin_review_render'].call_args.kwargs['text'])
  ok,_=await self.e['_admin_reject_broker_id'](self.context,123,'EXNOVA')
  self.assertTrue(ok)
  self.assertFalse(self.row('EXNOVA').id_validated)
  self.assertIsNone(self.row('EXNOVA').pending_trading_id)
 async def test_exnova_state_is_separate_from_binomo_and_stockity(self):
  for broker,value in [('BINOMO','111111111'),('STOCKITY','222222222'),('EXNOVA','333333333')]:
   self.e['_broker_flow_set'](123,pending_trading_id=value)
   await self.e['broker_user_callback'](self.update('broker_id_select:'+broker),self.context)
  ok,_=await self.e['_admin_finalize_broker_id'](self.context,123,'EXNOVA')
  self.assertTrue(ok)
  self.assertEqual(self.row('BINOMO').pending_trading_id,'111111111')
  self.assertFalse(self.row('BINOMO').id_validated)
  self.assertEqual(self.row('STOCKITY').pending_trading_id,'222222222')
  self.assertFalse(self.row('STOCKITY').id_validated)
 def test_legacy_output_and_promotion_core_are_unchanged(self):
  if not ORIGINAL.exists():self.skipTest('Baseline comparison is local to the change review')
  old,engine=load(ORIGINAL)
  try:
   for lang in ('es','en'):
    for text in ('No links',f"Register\n{old['ENLACE_REFERIDO']}",f"[Stockity]({old['ENLACE_REFERIDO_STOCKITY']})\n[Binomo]({old['ENLACE_REFERIDO']})"):
     self.assertEqual(self.e['_organize_ai_registration_links'](text,lang),old['_organize_ai_registration_links'](text,lang))
   a={n.name:ast.dump(n,include_attributes=False) for n in ast.parse(ORIGINAL.read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
   candidate=ast.parse(SOURCE.read_text())
   for node in candidate.body:
    if isinstance(node,ast.AsyncFunctionDef) and node.name=='_admin_apply_broker_deposit':
     node.body=[stmt for stmt in node.body if not (isinstance(stmt,ast.Expr) and isinstance(stmt.value,ast.Await) and isinstance(stmt.value.value,ast.Call) and isinstance(stmt.value.value.func,ast.Name) and stmt.value.value.func.id=='_panel_delivery_notify')]
   b={n.name:ast.dump(n,include_attributes=False) for n in candidate.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
   names=[name for name in a if name.startswith('_premium100_') or name in {'_admin_apply_broker_deposit','_vip_send_next_or_welcome','_broker_preview_deposit','_referral_links_for_user'}]
   for name in names:self.assertEqual(a[name],b[name],name)
  finally:engine.dispose()

if __name__=='__main__':unittest.main()
