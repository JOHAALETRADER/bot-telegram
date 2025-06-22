import logging
import asyncio
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
import os

TOKEN = os.getenv("BOT_TOKEN")
USER_ID = int(os.getenv("USER_ID"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Botones de bienvenida
botones_inicio = InlineKeyboardMarkup([
    [InlineKeyboardButton("✅ Validar Registro", url="https://wa.me/573053095829")],
    [InlineKeyboardButton("📊 Canal de Resultados", url="https://t.me/+UOxZVZMtDNozNzMx")],
    [InlineKeyboardButton("📥 Plan VIP + Bot Gratis", url="https://wa.me/573053095829")]
])

# Mensajes programados
mensajes_programados = [
    (3600, "👋 Recuerda escribirme por WhatsApp para validar tu registro y acceder a los beneficios: https://wa.me/573053095829"),
    (10800, "🚀 Ya han pasado 3 horas y no hemos validado tu acceso. Escríbeme por WhatsApp y activa tus beneficios: https://wa.me/573053095829"),
    (86400, "⏰ Han pasado 24 horas. Último llamado para acceder gratis al bot y cursos. Escríbeme aquí: https://wa.me/573053095829")
]

# Función para enviar mensajes temporizados
async def enviar_mensajes_temporizados(context: ContextTypes.DEFAULT_TYPE):
    for segundos, mensaje in mensajes_programados:
        await asyncio.sleep(segundos)
        await context.bot.send_message(chat_id=USER_ID, text=mensaje)

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Hola! Soy *JOHAALETRADER*. 👩‍💻

Estoy aquí para ayudarte a acceder a todos los beneficios del plan VIP gratuito.

Pulsa el botón para escribirme por WhatsApp y validar tu registro 👇",
        parse_mode="Markdown",
        reply_markup=botones_inicio
    )
    asyncio.create_task(enviar_mensajes_temporizados(context))

# Respuesta a botones (si usas callback)
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

# Código principal
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling()

if __name__ == "__main__":
    main()
