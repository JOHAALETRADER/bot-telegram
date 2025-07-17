
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# Configuración básica
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = "AQUÍ_TU_TOKEN"
ADMIN_ID = 5924691120

# Función para enviar mensaje del usuario al admin
async def forward_message_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        user_id = update.message.from_user.id
        text = update.message.text
        username = update.message.from_user.username
        name = update.message.from_user.first_name
        message_to_admin = f"📩 *Nuevo mensaje recibido*

👤 Usuario: {name} (@{username})
🆔 ID: `{user_id}`

💬 Mensaje:
{text}"
        await context.bot.send_message(chat_id=ADMIN_ID, text=message_to_admin, parse_mode="Markdown")

# Función para que admin responda al usuario
async def reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.reply_to_message:
        original_text = update.message.reply_to_message.text
        user_id_line = [line for line in original_text.splitlines() if "ID:" in line]
        if user_id_line:
            try:
                user_id = int(user_id_line[0].split("`")[1])
                await context.bot.send_message(chat_id=user_id, text=update.message.text)
                await update.message.reply_text("✅ Respuesta enviada con éxito.")
            except Exception as e:
                await update.message.reply_text(f"❌ Error al enviar respuesta: {e}")

# Función básica de /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! Bienvenido al bot.")

# Ejecutar aplicación
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_message_to_admin))
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY, reply_to_user))

    app.run_polling()

if __name__ == "__main__":
    main()
