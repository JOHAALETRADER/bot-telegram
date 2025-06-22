
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = "8179287095:AAGYbsj3XDWmCS5Z9PyKj2YzFkCSNiGjiQ4"

# Función para enviar mensajes programados
async def enviar_mensajes_programados(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id

    await context.bot.send_message(
        chat_id=chat_id,
        text="✅ *1 HORA DESPUÉS:*
Recuerda que ya puedes escribirnos para validar tu registro. ¡Estamos atentos para ayudarte a empezar! 📈",
        parse_mode="Markdown"
    )
    await asyncio.sleep(7200)  # 2 horas más
    await context.bot.send_message(
        chat_id=chat_id,
        text="📊 *3 HORAS DESPUÉS:*
¿Tienes alguna duda o necesitas ayuda? Escríbenos por WhatsApp y con gusto te asistimos. 💬",
        parse_mode="Markdown"
    )
    await asyncio.sleep(75600)  # 21 horas más
    await context.bot.send_message(
        chat_id=chat_id,
        text="⏰ *24 HORAS DESPUÉS:*
Este es tu último recordatorio para aprovechar todos los beneficios. ¡Haz parte de nuestra comunidad de traders exitosos! 🚀",
        parse_mode="Markdown"
    )

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("✅ Canal de Resultados", url="https://t.me/+wyjkDFenUMlmMTUx")],
        [InlineKeyboardButton("📲 Escríbeme por WhatsApp", url="https://wa.me/573508354350")],
        [InlineKeyboardButton("📝 Validar Registro", url="https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 ¡Hola! Soy *JOHAALETRADER*.

Aquí tienes acceso directo a toda la información. Escoge una opción:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

    context.job_queue.run_once(enviar_mensajes_programados, delay=3600, chat_id=update.effective_chat.id)

# Inicializar la aplicación
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()
