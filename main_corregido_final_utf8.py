import logging
import asyncio
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# Token del bot desde variable de entorno.
BOT_TOKEN = os.getenv("BOT_TOKEN")

# --- Enlaces personalizados ---
WHATSAPP_LINK = "https://wa.me/573053704569?text=Hola,%20quiero%20validar%20mi%20registro"
CANAL_RESULTADOS = "https://t.me/resultadosjohtrader"
ENLACE_REGISTRO = "https://johtrader.com/registro"

# --- Función para enviar mensajes programados (1 h, 3 h, 24 h) ---
async def mensajes_programados(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id

    # 1 hora
    await context.bot.send_message(
        chat_id=chat_id,
        text="\u2705 *1 HORA DESPUÉS:*\n"
             "Recuerda que ya puedes escribirnos para validar tu registro. "
             "¡Estamos atentos para ayudarte a empezar! \U0001F4C8",
        parse_mode="Markdown",
    )

    # 2 horas más (total 3 h)
    await asyncio.sleep(7200)
    await context.bot.send_message(
        chat_id=chat_id,
        text="\U0001F4CA *3 HORAS DESPUÉS:*\n"
             "¿Tienes alguna duda o necesitas ayuda? Escríbenos por WhatsApp y con gusto te asistimos. \U0001F4AC",
        parse_mode="Markdown",
    )

    # 21 horas más (total 24 h)
    await asyncio.sleep(75600)
    await context.bot.send_message(
        chat_id=chat_id,
        text="\u23F0 *24 HORAS DESPUÉS:*\n"
             "Este es tu último recordatorio para aprovechar todos los beneficios. "
             "¡Haz parte de nuestra comunidad de traders exitosos! \U0001F680",
        parse_mode="Markdown",
    )

# --- Comando /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_chat.id

    keyboard = [
        [InlineKeyboardButton("\u2705 Canal de Resultados", url=CANAL_RESULTADOS)],
        [InlineKeyboardButton("\U0001F4F2 Escríbeme por WhatsApp", url=WHATSAPP_LINK)],
        [InlineKeyboardButton("\U0001F4DD Validar Registro", url=ENLACE_REGISTRO)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "\U0001F44B ¡Hola! Soy *JOHAALETRADER*.\n\n"
        "Aquí tienes acceso directo a toda la información. ¡Escoge una opción!",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )

    # Programa los mensajes de seguimiento (1 h, 3 h, 24 h)
    context.job_queue.run_once(mensajes_programados, when=3600, chat_id=user_id)

# --- Configuración e inicio del bot ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if not BOT_TOKEN:
        raise RuntimeError("La variable de entorno BOT_TOKEN no está definida.")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    # Si luego agregas botones con callback_data, descomenta la siguiente línea:
    # app.add_handler(CallbackQueryHandler(mi_funcion_callback))

    app.run_polling()
