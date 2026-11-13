"""Verify public presentation routes without changing the restricted delivery gate."""
import ast,sys,unittest,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import panel_marketing as public
import panel_delivery as restricted
from telegram import InlineKeyboardButton,InlineKeyboardMarkup
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'main.py'
if not SOURCE.exists():SOURCE=ROOT/'main_johanna_FINAL_ID_ACTUALIZADO.py'
TREE=ast.parse(SOURCE.read_text())
class MarketingTests(unittest.IsolatedAsyncioTestCase):
 def test_public_pages_are_separate_and_do_not_expose_download_or_manual(self):
  for lang in ('es','en'):
   self.assertLessEqual(len(public.brief(lang)),1024)
   self.assertIn('230',public.brief(lang))
   self.assertIn('Windows',public.brief(lang).replace('WINDOWS','Windows'))
   self.assertNotIn('14',public.brief(lang))
   self.assertNotIn('USD15',public.brief(lang));self.assertNotIn('USD20',public.brief(lang))
   self.assertNotIn('licen',public.brief(lang).lower());self.assertNotIn('Johanna',public.brief(lang))
   self.assertNotEqual(public.brief(lang),public.details(lang))
   for value in ('USD','COP','230','100','14','license','licencia','deposit','depósito'):
    self.assertNotIn(value,public.details(lang))
   for value in ('Exnova','Crypto IDX','Windows'):
    self.assertIn(value,public.details(lang))
   for value in (('voz','sonido','visuales','Bitácora','plan de trading','riesgo','capital') if lang=='es' else ('voice','sound','visual','journal','Trading-plan','risk','capital')):
    self.assertIn(value,public.details(lang))
   self.assertLessEqual(len(public.details(lang).encode('utf-16-le'))//2,1024)
   self.assertNotEqual(public.INFO_URLS[lang],restricted.GUIDE_URLS[lang])
   buttons=[b for row in public.keyboard(lang).inline_keyboard for b in row]
   self.assertEqual(next(b.url for b in buttons if b.url),public.INFO_URLS[lang])
   self.assertFalse(any(b.url in (restricted.PORTAL_URL,restricted.ACCOUNT_URL,restricted.GUIDE_URLS[lang]) for b in buttons))
 def test_levels_show_public_button_for_both_languages(self):
  node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='levels_keyboard')
  env=dict(InlineKeyboardMarkup=InlineKeyboardMarkup,InlineKeyboardButton=InlineKeyboardButton,panel_marketing=public,_levels_url=lambda lang:'https://example.com/'+lang,support_rows=lambda *a:[])
  exec(compile(ast.Module(body=[node],type_ignores=[]),'<levels>','exec'),env)
  for lang in ('es','en'):
   buttons=[b for r in env['levels_keyboard'](lang,123).inline_keyboard for b in r]
   self.assertIn('panel_info',[b.callback_data for b in buttons])
   self.assertEqual(buttons[0].url,'https://example.com/'+lang)
 def test_public_callback_does_not_use_delivery_gate(self):
  callbacks=next(n for n in TREE.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='botones')
  route=next(n for n in callbacks.body if isinstance(n,ast.If) and isinstance(n.test,ast.Compare) and ast.unparse(n.test)=='q.data == \'panel_info\'')
  code=ast.unparse(route)
  self.assertIn('panel_marketing.send_info',code);self.assertNotIn('_panel_delivery',code)
  # Public marketing must not accidentally replace or loosen the private callback.
  private=next(n for n in callbacks.body if isinstance(n,ast.If) and ast.unparse(n.test)=="q.data == 'member_signals_panel'")
  self.assertIn('not _panel_delivery_eligible(chat_id)',ast.unparse(private))
 async def test_photo_and_text_fallback_preserve_public_buttons(self):
  with tempfile.TemporaryDirectory() as d:
   old=public.PHOTO_PATH;public.PHOTO_PATH=Path(d)/'panel.png';public.PHOTO_PATH.write_bytes(b'fixture')
   try:
    for lang in ('es','en'):
     bot=SimpleNamespace(send_photo=AsyncMock(),send_message=AsyncMock())
     await public.send_info(bot,123,lang)
     bot.send_photo.assert_awaited_once();bot.send_message.assert_not_awaited()
     self.assertEqual(bot.send_photo.call_args.kwargs['caption'],public.details(lang))
     bot.send_photo=AsyncMock(side_effect=RuntimeError('image failure'))
     await public.send_info(bot,123,lang)
     bot.send_message.assert_awaited_once()
     self.assertEqual(next(b.url for row in bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard for b in row if b.url),public.INFO_URLS[lang])
   finally:public.PHOTO_PATH=old
if __name__=='__main__':unittest.main()
