import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

TOKEN = "8179287095:AAGYbsj3XDWmCS5Z9PyKj2YzFkCSNiGjiQ4"

# Mensajes programados
async def enviar_mensajes_programados(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id

    await context.bot.send_message(
        chat_id=chat_id,
        text="✅ *1 HORA DESPUÉS:*\nRecuerda que ya puedes escribirnos para validar tu registro. ¡Estamos atentos para ayudarte a empezar! 📈",
        parse_mode="Markdown"
    )
    await asyncio.sleep(7200)  # 2 horas más (total 3)
    await context.bot.send_message(
        chat_id=chat_id,
        text="📊 *3 HORAS DESPUÉS:*\n¿Tienes alguna duda o necesitas ayuda? Escríbenos por WhatsApp y con gusto te asistimos. 💬",
        parse_mode="Markdown"
    )
    await asyncio.sleep(75600)  # 21 horas más (total 24)
    await context.bot.send_message(
        chat_id=chat_id,
        text="⏰ *24 HORAS DESPUÉS:*\nEste es tu último recordatorio para aprovechar todos los beneficios. ¡Haz parte de nuestra comunidad de traders exitosos! 🚀",
        parse_mode="Markdown"
    )

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("✅ Canal de Resultados", url="https://t.me/resultadosjohtrader")],
        [InlineKeyboardButton("📲 Escríbeme por WhatsApp", url="https://wa.me/573053704569?text=Hola,%20quiero%20validar%20mi%20registro")],
        [InlineKeyboardButton("📝 Validar Registro", url="https://johtrader.com/registro")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 ¡Hola! Soy *JOHAALETRADER*.\n\nAquí tienes acceso directo a toda la información. Escoge una opción:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

    # Inicia mensajes programados
    context.job_queue.run_once(enviar_mensajes_programados, delay=3600, chat_id=update.effective_chat.id)

# Inicialización
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.run_polling()
