"""Exercise the real reply pipeline offline, without Telegram or production DB access."""
import ast
import builtins
import json
import logging
import re
import sys
import unicodedata
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import panel_launch
import panel_marketing
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

SOURCE = ROOT / 'main_johanna_FINAL_ID_ACTUALIZADO.py'
TREE = ast.parse(SOURCE.read_text())
DEFS = {n.name: n for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFD', value or '') if unicodedata.category(c) != 'Mn').lower()


def load_reply(model_answer, *, lang='es', stage='PRE', level='NONE', cohort=False, multi=False, history='', planner_intents=()):
    """Mock only external state/model I/O; execute actual production reply functions."""
    env = dict(__builtins__=builtins, re=re, json=json, logging=logging,
               panel_marketing=panel_marketing, panel_launch=panel_launch,
               InlineKeyboardButton=InlineKeyboardButton, InlineKeyboardMarkup=InlineKeyboardMarkup,
               ContextTypes=SimpleNamespace(DEFAULT_TYPE=object))
    # The selected functions must never import or initialize the production bot.
    missing = {
        n.id for name in ('openai_answer', '_ai_delivery_text', 'ai_context_keyboard')
        for n in ast.walk(DEFS[name]) if isinstance(n, ast.Name) and n.id.startswith('_')
    }
    env.update({name: Mock(return_value='') for name in missing})
    captured = []
    plan = dict(primary='other', intents=list(planner_intents), amount_usd=None, multi_question=multi)

    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            payload = kwargs['json']
            captured.append(payload)
            value = json.dumps(plan) if payload['instructions'].startswith('Classify') else model_answer
            return SimpleNamespace(status_code=200, json=lambda: {'answer': value}, text='')

    env.update(
        HAS_HTTPX=True, OPENAI_API_KEY='offline-fixture', OPENAI_MODEL='offline-model',
        JOHA_KNOWLEDGE='PANEL / INTERFAZ QUE JOHANNA MUESTRA EN LIVE\nOLD: no se entrega a usuarios.',
        STAGE_PRE='PRE', STAGE_POST='POST', STAGE_DEPOSITED='DEPOSITED',
        VIP_LEVEL_NONE='NONE', VIP_LEVEL_BASIC='BASIC', VIP_LEVEL_PREMIUM='PREMIUM', VIP_LEVEL_PRESTIGE='PRESTIGE',
        TELEGRAPH_LEVELS_URL='https://example.com/es', TELEGRAPH_LEVELS_URL_EN='https://example.com/en',
        httpx=SimpleNamespace(AsyncClient=Client), _norm=norm,
        _PANEL_LAUNCH=SimpleNamespace(ready=True, existing=Mock(return_value=cohort)),
        _vip_get_state=Mock(return_value={'level': level}),
        _ai_runtime_context=Mock(return_value='Recorded account state; do not invent validations.'),
        _history_as_text=Mock(return_value=history),
        _responses_api_text=lambda value: value['answer'],
        _ai_answer_context_guard=lambda answer, *args: (answer, False),
        _translate_to_english=AsyncMock(side_effect=lambda value: value),
        _levels_url=lambda value: 'https://example.com/' + value,
        _vip_level_label=lambda value, *args: value,
        _vip_level_for_total_cents=lambda value: 'PREMIUM',
    )
    for name in ('_strip_redundant_ai_greeting', '_neutralize_ai_gender', '_ai_known_fact_guard',
                 '_trim_generic_ai_closer', '_time_management_style_guard', '_clean_ai_plain_text_format',
                 '_organize_ai_registration_links'):
        env[name] = lambda value, *args: value
    for name in ('openai_answer', '_ai_delivery_text', '_clarify_free_community', 'ai_context_keyboard'):
        exec(compile(ast.Module(body=[DEFS[name]], type_ignores=[]), str(SOURCE), 'exec'), env)
    return env, captured


class PanelReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_reported_access_question_replaces_old_private_live_reply_for_all_stages(self):
        for lang, question, old in (
            ('es', 'Buenas noches es que me gustaría tener acceso al panel',
             'La interfaz visual que muestro en mis lives es una herramienta privada, por eso no la entrego.'),
            ('en', 'Good evening, I would like access to the panel',
             "The interface is private, so I don't deliver or install it for members."),
        ):
            for stage in ('PRE', 'POST', 'DEPOSITED'):
                with self.subTest(lang=lang, stage=stage):
                    env, payloads = load_reply(old, lang=lang, stage=stage)
                    answer = await env['openai_answer'](question, 123, lang, stage)
                    for fact in ('Windows', 'Exnova', 'Binomo', 'Stockity', 'Crypto IDX', panel_marketing.INFO_URLS[lang]):
                        self.assertIn(fact, answer)
                    self.assertIn('AI Bot' if lang == 'en' else 'Bot IA', answer)
                    self.assertIn('monthly subscription' if lang == 'en' else 'suscripción mensual', answer)
                    self.assertNotIn('no la entrego', answer)
                    self.assertNotIn("don't deliver", answer)
                    self.assertNotIn('USD230', answer)
                    self.assertLess(len(answer), 1000)
                    self.assertIn('CURRENT MEMBER PANEL FACTS', payloads[-1]['instructions'])
                    self.assertIn('HORA OFICIAL DE LAS SEÑALES', payloads[-1]['instructions'])

    async def test_panel_overview_with_explicit_ai_bot_still_uses_panel_facts(self):
        env, _ = load_reply('El bot de Telegram depende de tu nivel.')
        answer = await env['openai_answer']('¿Qué incluye el panel con Bot IA?', 123, 'es', 'PRE')
        self.assertIn('Exnova, Binomo y Stockity', answer)
        self.assertIn('Software Premium Anticipado', answer)

    async def test_price_detail_is_preserved_and_does_not_revert_to_community_promotion(self):
        for lang, question, reply in (
            ('es', '¿Cuánto cuesta el panel y la reconexión?', 'El panel Exnova cuesta USD15 al mes y los tres brokers USD20. La reconexión añade USD8.'),
            ('en', 'How much does the panel cost, including reconnection?', 'The panel costs USD15 monthly for Exnova or USD20 for all three brokers. Reconnection adds USD8.'),
        ):
            env, payloads = load_reply(reply, lang=lang)
            env['_ai_entry_promo_answer'] = Mock(return_value='WRONG: community promotion USD100')
            answer = await env['openai_answer'](question, 123, lang, 'PRE')
            for value in ('USD15', 'USD20', 'USD8'):
                self.assertIn(value, answer)
            self.assertNotIn('WRONG', answer)
            self.assertNotIn('not a payment to me or a subscription', answer)
            self.assertNotIn('No es un pago a mí ni una suscripción', answer)
            env['_ai_entry_promo_answer'].assert_not_called()
            self.assertIn('totals USD23 / USD28', payloads[-1]['instructions'])

    async def test_launch_cohort_uses_snapshot_instead_of_current_premium_level(self):
        for cohort in (True, False, None):
            env, payloads = load_reply('Para activar el panel, reviso tu caso.', stage='DEPOSITED', level='PREMIUM', cohort=cohort)
            answer = await env['openai_answer']('Soy Premium, ¿necesito depositar USD100 para activar el panel?', 123, 'es', 'DEPOSITED')
            self.assertIn('reviso tu caso', answer)
            instructions = payloads[-1]['instructions']
            self.assertIn(f'confirmed by stored snapshot: {cohort}', instructions)
            self.assertIn('current Premium/Prestige level alone is insufficient', instructions)
            self.assertIn('PANEL target is USD230', instructions)
            self.assertIn('11 de noviembre de 2026', instructions)
            env['_PANEL_LAUNCH'].existing.assert_called_once_with(123)

    async def test_multiple_questions_keep_other_answer_and_live_followup_keeps_same_signals(self):
        env, _ = load_reply('No la entrego: es un panel privado.\n\nLa señal de las 10:00 en Colombia corresponde a las 11:00 en Venezuela.', multi=True)
        answer = await env['openai_answer']('Quiero el panel.\n¿A qué hora tomo la señal en Venezuela?', 123, 'es', 'PRE')
        self.assertIn('10:00', answer)
        self.assertIn('11:00', answer)
        self.assertNotIn('No la entrego', answer)
        env, _ = load_reply('El panel es privado.', history='El panel del live era una interfaz privada; señales por Telegram.')
        answer = await env['openai_answer']('Quiero esas señales que muestras', 123, 'es', 'PRE')
        self.assertIn('mismas señales', answer)
        self.assertIn('Windows', answer)

    def test_public_panel_button_does_not_inherit_promotion_cta_or_expose_download(self):
        for lang in ('es', 'en'):
            env, _ = load_reply('')
            env['_ai_entry_promo_available'] = Mock(return_value=True)
            keyboard = env['ai_context_keyboard']('Quiero el panel', lang, 123)
            buttons = [button for row in keyboard.inline_keyboard for button in row]
            self.assertEqual(len(buttons), 1)
            self.assertEqual(buttons[0].url, panel_marketing.INFO_URLS[lang])
            self.assertIsNone(buttons[0].callback_data)
            env['_ai_entry_promo_available'].assert_not_called()

    async def test_non_panel_community_reply_still_uses_existing_promotion_route(self):
        env, _ = load_reply('')
        env['_ai_entry_promo_answer'] = Mock(return_value='Original community offer')
        self.assertEqual(await env['openai_answer']('¿Cuánto deposito para entrar a la comunidad?', 123, 'es', 'PRE'), 'Original community offer')
        env['_ai_entry_promo_answer'].assert_called_once()
        self.assertIn('No es un pago a mí ni una suscripción', env['_clarify_free_community']('Premium desde USD200.', 'es'))


if __name__ == '__main__':
    unittest.main()
