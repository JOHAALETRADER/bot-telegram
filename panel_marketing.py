"""Public presentation only. License/manual delivery remains in panel_delivery."""
import logging
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

INFO_URLS = {'es': 'https://telegra.ph/JOHAALETRADER--Conoce-el-panel-de-señales-11-11', 'en': 'https://telegra.ph/JOHAALETRADER--Discover-the-signals-panel-11-11'}
PHOTO_PATH = Path(__file__).resolve().parent / 'docs' / 'panel' / 'panel-reference.png'

def label(lang):
    return '🖥 Discover the signals panel' if lang == 'en' else '🖥 Conoce el panel de señales'

def brief(lang):
    if lang == 'en':
        return (
            '🖥 PREMIUM + DOWNLOADABLE WINDOWS SIGNALS PANEL\n'
            'Includes signals, alerts, results tracking, a trading journal and risk/capital-management tools, '
            'plus Sunday Advance Premium Software lists of currency pairs and Crypto IDX, exclusively in the panel.\n'
            'First, I validate your broker ID. Then you can deposit from USD230 directly into your own trading '
            'account; once I validate the deposit, I enable access to your panel. If you were already Premium or Prestige before launch, a NEW USD100 deposit from 11 November 2026 applies instead; notify me so I can validate it.'
        )
    return (
        '🖥 PREMIUM + PANEL DE SEÑALES DESCARGABLE EN WINDOWS\n'
        'Incluye señales, alertas, control de resultados, bitácora y gestión de riesgo y capital; además, listas '
        'de pares de divisas y Crypto IDX del Software Premium Anticipado también los domingos, solo en el panel.\n'
        'Primero valido tu ID del broker. Después puedes depositar a partir de USD230 directamente en tu propia '
        'cuenta de trading; una vez que valide tu depósito, habilito el acceso al panel. Si ya eras Premium o Prestige antes del lanzamiento, aplica un depósito NUEVO de USD100 desde el 11 de noviembre de 2026: notifícamelo para validarlo.'
    )

def details(lang):
    if lang == 'en':
        return (
            '🖥 DISCOVER THE JOHAALETRADER SIGNALS PANEL\n\n'
            'Organize your trading in one Windows application:\n'
            '📊 Signals and voice, sound and visual alerts to help you follow each entry.\n'
            '🎯 Trading-plan tools and risk/capital management to prepare your decisions.\n'
            '📒 A trading journal and results tracking to review your performance and habits.\n'
            '✨ Exnova signals exclusively in the panel.\n'
            '📅 Sunday Advance Premium Software lists of currency pairs and Crypto IDX, only in the panel.\n\n'
            'Your usual Telegram benefits continue. You stay in control of every trade.\n'
            '👇 Open “What it includes and how it works” for access conditions and full details.'
        )
    return (
        '🖥 CONOCE EL PANEL DE SEÑALES JOHAALETRADER\n\n'
        'Organiza tu operativa en una sola aplicación para Windows:\n'
        '📊 Señales y avisos por voz, sonido y alertas visuales para acompañar cada entrada.\n'
        '🎯 Herramientas de plan de trading y gestión de riesgo y capital para preparar tus decisiones.\n'
        '📒 Bitácora y control de resultados para revisar tu desempeño y tus hábitos.\n'
        '✨ Señales de Exnova exclusivas del panel.\n'
        '📅 Listas de divisas y Crypto IDX del Software Premium Anticipado también los domingos, solo en el panel.\n\n'
        'Tus beneficios habituales de Telegram continúan. Tú mantienes el control de cada operación.\n'
        '👇 En «Qué incluye y cómo funciona» encontrarás las condiciones de acceso y la información completa.'
    )

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
