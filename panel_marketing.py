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
            'account; once I validate the deposit, I enable access to your panel. Your usual Premium benefits continue.'
        )
    return (
        '🖥 PREMIUM + PANEL DE SEÑALES DESCARGABLE EN WINDOWS\n'
        'Incluye señales, alertas, control de resultados, bitácora y gestión de riesgo y capital; además, listas '
        'de pares de divisas y Crypto IDX del Software Premium Anticipado también los domingos, solo en el panel.\n'
        'Primero valido tu ID del broker. Después puedes depositar a partir de USD230 directamente en tu propia '
        'cuenta de trading; una vez que valide tu depósito, habilito el acceso al panel. Conservas los beneficios habituales de Premium.'
    )

def details(lang):
    if lang == 'en':
        return (
            '🖥 DISCOVER THE JOHAALETRADER SIGNALS PANEL\n'
            'A Windows application with signals, alerts, a journal, results tracking and risk-management tools. '
            'Exnova signals are panel-exclusive. Sunday currency-pair and Crypto IDX lists for Binomo/Stockity '
            'are also panel-only, when I prepare and confirm them.\n'
            'After I validate your ID and then your qualifying USD230 deposit in one broker account, '
            'I can approve your panel access. Your trading deposit stays in your account.\n'
            'The initial 14-day trial enables all three brokers. Afterwards, choose Exnova only: USD15/month '
            '(COP50,000), or all three: USD20/month (COP65,000). There are 3 grace days after expiry; '
            'then only the payment portal remains accessible. Reconnection adds USD8 when applicable.\n'
            'Read the full explanation below. The installation manual is delivered separately after validation.'
        )
    return (
        '🖥 CONOCE EL PANEL DE SEÑALES JOHAALETRADER\n'
        'Aplicación Windows con señales, alertas, bitácora, control de resultados y gestión de riesgo. '
        'Exnova es exclusivo del panel. Las listas de domingo de pares de divisas y Crypto IDX para '
        'Binomo/Stockity también van solo al panel, cuando las preparo y confirmo.\n'
        'Tras validar tu ID y después tu depósito desde USD230 en una misma cuenta de broker, puedo '
        'aprobar tu acceso al panel. Tu depósito permanece en tu cuenta de trading.\n'
        'Prueba inicial de 14 días con los tres brokers. Después eliges: Exnova USD15/mes (COP50.000) '
        'o los tres USD20/mes (COP65.000). Hay 3 días de gracia tras vencer; después solo queda el acceso '
        'al portal de pago. Reconexión adicional de USD8 cuando corresponda.\n'
        'Abajo tienes la explicación completa. El manual de instalación se entrega aparte, tras las validaciones.'
    )

def keyboard(lang):
    en = lang == 'en'
    return InlineKeyboardMarkup([
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
