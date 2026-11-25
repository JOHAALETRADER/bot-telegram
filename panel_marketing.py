"""Public presentation only. License/manual delivery remains in panel_delivery."""
import logging
import re
import unicodedata
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

INFO_URLS = {'es': 'https://telegra.ph/JOHAALETRADER--Conoce-el-panel-de-señales-11-11', 'en': 'https://telegra.ph/JOHAALETRADER--Discover-the-signals-panel-11-11'}
PHOTO_PATH = Path(__file__).resolve().parent / 'docs' / 'panel' / 'panel-reference.png'

def label(lang):
    return '🖥 Discover the signals panel' if lang == 'en' else '🖥 Conoce el panel de señales'

def is_question(text):
    """Recognize explicit member-panel questions without reading or changing state."""
    value = ''.join(c for c in unicodedata.normalize('NFD', text or '') if unicodedata.category(c) != 'Mn').lower()
    return bool(re.search(r'\bpanel(?:es|s)?\b', value)) and not bool(re.search(r'\b(?:admin|administrador|administrativo|administrator)\b', value))

def brief(lang):
    if lang == 'en':
        return (
            '🖥 JOHAALETRADER SIGNALS PANEL\n'
            'Downloadable Windows software to organize your trading.\n'
            '📡 AI Bot: currency pairs for Exnova, Binomo and Stockity; Crypto IDX for Binomo and Stockity.\n'
            '📋 Advance Premium Software: currency-pair and Crypto IDX lists for Binomo and Stockity.\n'
            '📊 Risk/capital management, a trading plan, results tracking, a journal and voice, sound and visual alerts.\n'
            'Choose Exnova or all three brokers. The panel is optional, with a monthly subscription separate from my free VIP community.\n'
            'Activation requires an account registered through my link. I first validate the ID; then you deposit into that same trading account, and I validate the deposit. This deposit is separate from the panel subscription.'
        )
    return (
        '🖥 PANEL DE SEÑALES JOHAALETRADER\n'
        'Una herramienta descargable para Windows que organiza tu operativa.\n'
        '📡 Bot IA: pares de divisas para Exnova, Binomo y Stockity; Crypto IDX para Binomo y Stockity.\n'
        '📋 Software Premium Anticipado: listas de divisas y Crypto IDX para Binomo y Stockity.\n'
        '📊 Gestión de riesgo y capital, plan de trading, control de resultados, bitácora y alertas por voz, sonido y visuales.\n'
        'Elige Exnova o los tres brokers. El panel es opcional y tiene una suscripción mensual independiente de mi comunidad VIP gratuita.\n'
        'Para activarlo necesitas una cuenta registrada con mi enlace. Primero valido el ID; después depositas en esa misma cuenta de trading y valido el depósito. Este depósito es independiente del pago de la suscripción al panel.'
    )

def details(lang):
    return brief(lang) + ('\n\n👇 Open “What it includes and how it works” for access conditions and full details.'
                          if lang == 'en' else
                          '\n\n👇 En «Qué incluye y cómo funciona» encontrarás las condiciones de acceso y la información completa.')

def keyboard(lang):
    en = lang == 'en'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📝 I want to register' if en else '📝 Quiero registrarme', callback_data='registrarme')],
        [InlineKeyboardButton('📖 What it includes and how it works' if en else '📖 Qué incluye y cómo funciona', url=INFO_URLS['en' if en else 'es'])],
        [InlineKeyboardButton('🎁 Benefits, levels and plans' if en else '🎁 Beneficios, niveles y planes', callback_data='levels_plans_en' if en else 'niveles_planes')],
    ])

async def send_info(bot, chat_id, lang):
    text = details(lang)
    if PHOTO_PATH.is_file():
        try:
            with PHOTO_PATH.open('rb') as photo:
                await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, reply_markup=keyboard(lang))
            return
        except Exception:
            logging.getLogger(__name__).warning('Panel information image unavailable; sending text fallback')
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard(lang))
