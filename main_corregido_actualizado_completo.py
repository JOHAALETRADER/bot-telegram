from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes
)
import os

# Token del bot desde las variables de entorno
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Enlaces personalizados
WHATSAPP_LINK = "https://wa.me/573053601385"
CANAL_RESULTADOS = "https://t.me/+XXXXX"
ENLACE_REGISTRO = "https://binomo.com?a=XXXXX"

# Mensaje de bienvenida personalizado
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💬 Escribirme por WhatsApp", url=WHATSAPP_LINK)],
        [InlineKeyboardButton("📣 Ver canal de resultados", url=CANAL_RESULTADOS)],
        [InlineKeyboardButton("✅ Ya me registré", callback_data="registro")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    mensaje = (
        "👋 ¡Hola! Soy *JOHAALETRADER*.

"
        "Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable.

"
        "📌 *¿Lista o listo para registrarte y empezar a ganar?*

"
        "👉 Regístrate aquí: [Haz clic para registrarte](" + ENLACE_REGISTRO + ")

"
        "Después de registrarte con el enlace, escríbeme por WhatsApp para validar tu registro y darte acceso a:
"
        "- Cursos exclusivos 🎓
"
        "- Señales en vivo 📊
"
        "- Bots automáticos 🤖
"
        "- Comunidad privada 🔒

"
        "_¡Vamos con toda! 💪_
"
    )
    await update.message.reply_markdown(mensaje, reply_markup=reply_markup)

# Callback de botones
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "registro":
        await query.edit_message_text(
            "📩 Si ya te registraste con el enlace, por favor envíame tu *ID de Binomo* antes de hacer tu inversión. ¡Estoy para ayudarte!"
        )

# Inicio de la aplicación
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling()
