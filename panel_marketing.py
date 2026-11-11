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
            '🖥 PREMIUM + PANEL · from USD230 in ONE broker account, with ID and deposit validated by Johanna. '
            'Your deposit stays in your account; it is separate from the license.\n'
            'Windows panel with signals, alerts, a journal and risk-management tools. Exnova signals and Sunday '
            'Advance Premium Software lists for Binomo/Stockity are panel-exclusive; Telegram benefits continue.\n'
            '14-day trial with all three brokers. Afterwards: Exnova USD15/month (COP50,000), or all three USD20 '
            '(COP65,000). Three grace days; reconnection adds USD8 when applicable. See the panel button for details.'
        )
    return (
        '🖥 PREMIUM + PANEL · desde USD230 en UNA cuenta de broker, con ID y depósito validados por Johanna. '
        'Tu depósito queda en tu cuenta y es independiente de la licencia.\n'
        'Panel Windows con señales, alertas, bitácora y gestión de riesgo. Exnova y las listas de domingo del '
        'Software Premium Anticipado para Binomo/Stockity son exclusivas del panel; Telegram sigue igual.\n'
        'Prueba de 14 días con los tres brokers. Después: Exnova USD15/mes (COP50.000) o tres brokers USD20 '
        '(COP65.000). Tres días de gracia; reconexión adicional USD8 cuando corresponda. Más detalles en el botón del panel.'
    )

def keyboard(lang):
    en = lang == 'en'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📖 What it includes and how it works' if en else '📖 Qué incluye y cómo funciona', url=INFO_URLS['en' if en else 'es'])],
        [InlineKeyboardButton('🎁 Benefits, levels and plans' if en else '🎁 Beneficios, niveles y planes', callback_data='levels_plans_en' if en else 'niveles_planes')],
    ])

async def send_info(bot, chat_id, lang):
    text = brief(lang)
    if PHOTO_PATH.is_file():
        try:
            with PHOTO_PATH.open('rb') as photo:
                await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, reply_markup=keyboard(lang))
            return
        except Exception:
            logging.getLogger(__name__).warning('Panel information image unavailable; sending text fallback')
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard(lang))
