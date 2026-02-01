
# ===============================
# MAIN BOT FILE - FIXED & STABLE
# ===============================

import logging
import re
import asyncio
from datetime import timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# ===============================
# CONFIG
# ===============================

BOT_TOKEN = "YOUR_BOT_TOKEN"
ADMIN_ID = 123456789

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===============================
# UTILIDADES AUDITORÍA
# ===============================

async def audit(context, user_id, intent, text):
    msg = f"🤖 Auto\nINTENT: {intent}\nUSER_ID: {user_id}\n\nMensaje enviado:\n{text}"
    await context.bot.send_message(chat_id=ADMIN_ID, text=msg)

async def reply_user(context, user_id, text, intent):
    await context.bot.send_message(chat_id=user_id, text=text)
    await audit(context, user_id, intent, text)

# ===============================
# DETECTORES DE INTENCIÓN
# ===============================

RE_GREETING = re.compile(r"^(hola|buenas|buenos dias|buenas noches|hey)\b", re.I)
RE_DEP_LATER = re.compile(r"(despues|luego|cuando me paguen|esperando pago|mas tarde)", re.I)
RE_MIN50 = re.compile(r"(menos|10|20|30|40|no tengo 50|puedo con menos)", re.I)
RE_DEPOSIT_DONE = re.compile(r"(ya deposite|ya pague|deposito listo|ya llego el deposito)", re.I)
RE_LIVE = re.compile(r"(live|directo|transmision|stream|conectas|en vivo)", re.I)

# ===============================
# HANDLER MENSAJES
# ===============================

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.effective_user.id

    if RE_GREETING.match(text):
        await reply_user(context, user_id, "Hola 👋 estoy aquí para ayudarte.", "GREETING")
        return

    if RE_DEP_LATER.search(text):
        await reply_user(
            context,
            user_id,
            "Está perfecto 😊 Cuando realices tu depósito de 50 USD o más, escríbeme y lo revisamos para darte acceso 👇",
            "DEP_LATER",
        )
        return

    if RE_MIN50.search(text):
        await reply_user(
            context,
            user_id,
            "Para ingresar a mi comunidad VIP gratuita y acceder a todas las herramientas, el depósito mínimo es de 50 USD.",
            "MIN_50",
        )
        return

    if RE_DEPOSIT_DONE.search(text):
        await reply_user(
            context,
            user_id,
            "Perfecto ✅ Envíame aquí el comprobante de tu depósito o escríbeme a mi chat personal para habilitar tu acceso 👇",
            "DEPOSIT_CONFIRM",
        )
        return

    if RE_LIVE.search(text):
        await reply_user(
            context,
            user_id,
            "📅 Horarios de mis lives (hora Colombia):\n• Martes: 11:00 am y 8:00 pm\n• Miércoles: 8:00 pm\n• Jueves: 11:00 am y 8:00 pm\n• Viernes: 8:00 pm\n• Sábados: 11:00 am y 8:00 pm",
            "LIVE_INFO",
        )
        return

    # Si no entra en ninguna regla, NO responde (conversación humana)
    logger.info(f"Mensaje humano detectado: {text}")

# ===============================
# MAIN
# ===============================

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.run_polling()

if __name__ == "__main__":
    main()
