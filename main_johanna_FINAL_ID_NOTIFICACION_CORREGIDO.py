from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from telegram.constants import ParseMode
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import logging

# === CONFIGURACIÓN ===
TOKEN = "AQUÍ_TU_TOKEN"
ADMIN_ID = 5924691120  # ID de Johanna

# === BASE DE DATOS ===
# (Aquí iría tu setup de SQLAlchemy, clases, engine, etc. asumimos ya definido)

# === FUNCIONES ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! Bienvenido al bot.")

async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass  # Asume que aquí va tu lógica de botones

async def guardar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    texto = update.message.text
    with Session() as session:
        user = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if user:
            user.mensaje = texto
        else:
            session.add(Usuario(
                telegram_id=str(chat_id),
                nombre=update.effective_user.full_name,
                mensaje=texto,
                fecha_registro=datetime.utcnow()
            ))
        session.commit()

# Función para reenviar mensajes al admin
async def forward_message_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        user = update.message.from_user
        text = update.message.text
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📩 Nuevo mensaje de <b>{user.first_name}</b> (ID: <code>{user.id}</code>):\n\n<code>{text}</code>",
            parse_mode=ParseMode.HTML
        )

# === EJECUCIÓN ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_mensaje))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_message_to_admin))
    logging.info("Bot corriendo...")
    app.run_polling()