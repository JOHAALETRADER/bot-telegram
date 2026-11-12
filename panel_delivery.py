"""Panel onboarding only; trading VIP levels and channel delivery remain separate."""
import logging
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
MIN_DEPOSIT_CENTS = 23000
PORTAL_URL = 'https://panel-api-production-1509.up.railway.app/portal'
ACCOUNT_URL = 'https://panel-api-production-1509.up.railway.app/account'
GUIDE_URLS = {
 'es': 'https://telegra.ph/JOHAALETRADER--Guía-de-instalación-y-acceso-al-panel-11-11',
 'en': 'https://telegra.ph/JOHAALETRADER--Panel-installation-and-access-guide-11-11',
}
PHOTO_PATH = Path(__file__).resolve().parent / 'docs' / 'panel' / 'panel-reference.png'

def qualifies(accounts):
    """Never add capital across brokers or trust a pending proof/legacy user total."""
    return any(
        a.get('broker') in {'BINOMO','STOCKITY','EXNOVA'}
        and a.get('id_validated')
        and str(a.get('trading_id') or '').strip()
        and not str(a.get('pending_trading_id') or '').strip()
        and int(a.get('deposit_count') or 0) > 0
        and a.get('level') in {'PREMIUM','PRESTIGE'}
        and int(a.get('validated_total_cents') or 0) >= MIN_DEPOSIT_CENTS
        for a in accounts
    )

def keyboard(lang):
    en = lang == 'en'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📥 Download panel' if en else '📥 Descargar panel', url=PORTAL_URL)],
        [InlineKeyboardButton('🖥 Installation and user guide' if en else '🖥 Guía de instalación y uso', url=GUIDE_URLS['en' if en else 'es'])],
        [InlineKeyboardButton('🔑 My panel access' if en else '🔑 Mi acceso al panel', url=ACCOUNT_URL)],
    ])

def welcome(lang):
    if lang == 'en':
        return (
            '🖥 YOUR JOHAALETRADER PANEL\n\n'
            'Your broker ID and the qualifying deposit under your panel access conditions have been validated. '
            'Your Premium community benefits continue as usual.\n\n'
            'Open My panel access and create your account if needed. If it is pending, send Johanna '
            'the email you registered with so she can link and approve your access. '
            'The 14-day trial starts with the first panel approval; check its expiry in My account.\n\n'
            'After the trial: Exnova USD15/month (COP50,000); all three brokers USD20/month (COP65,000). '
            'Three grace days; reconnection, when applicable, adds USD8. '
            'Your trading deposit stays in your broker account and is separate from this license.\n\n'
            'Download from your authorized portal and follow the guide. Windows computers only.'
        )
    return (
        '🖥 TU PANEL JOHAALETRADER\n\n'
        'Tu ID del broker y el depósito correspondiente a tu condición de acceso ya están validados. '
        'Conservas tus beneficios habituales de Premium.\n\n'
        'Abre Mi acceso al panel y crea tu cuenta si aún no la tienes. Si aparece pendiente, envía '
        'a Johanna el correo registrado para vincular y aprobar tu acceso. '
        'La prueba de 14 días comienza con la primera aprobación del panel; consulta su vencimiento en Mi cuenta.\n\n'
        'Después: Exnova USD15/mes (COP50.000); tres brokers USD20/mes (COP65.000). '
        'Tres días de gracia; cuando corresponda, reconexión adicional de USD8. '
        'Tu depósito permanece en el broker y es independiente de esta licencia.\n\n'
        'Descarga desde tu portal autorizado y sigue la guía. Solo para computadores Windows.'
    )

async def send_package(bot, chat_id, lang):
    text = welcome(lang)
    if PHOTO_PATH.is_file():
        try:
            with PHOTO_PATH.open('rb') as photo:
                await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, reply_markup=keyboard(lang))
            return
        except Exception:
            logging.exception('PANEL_ONBOARDING_PHOTO_FAILED user=%s; using text fallback', chat_id)
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard(lang), disable_web_page_preview=True)
