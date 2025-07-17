import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from models import Usuario  # Asegúrate de tener este modelo definido correctamente
from config import TOKEN  # Tu token debe estar en config.py

# Configuración del log
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ID del administrador
ADMIN_ID = 5924691120  # ID de Johanna

# Función para notificar mensaje del usuario al admin
async def forward_message_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        user_id = update.message.from_user.id
        text = update.message.text
        username = update.message.from_user.username or 'sin username'
        name = update.message.from_user.first_name
        message_to_admin = f"📩 *Nuevo mensaje recibido*

👤 Usuario: {name} (@{username})\n🆔 ID: {user_id}\n\n📄 Mensaje:\n{text}"
        await context.bot.send_message(chat_id=ADMIN_ID, text=message_to_admin, parse_mode="Markdown")

# Función para permitir al admin responder al usuario
async def reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.reply_to_message:
        original_text = update.message.reply_to_message.text
        user_id_line = [line for line in original_text.splitlines() if "ID:" in line]
        if user_id_line:
            try:
                user_id = int(user_id_line[0].split(":")[1].strip())
                await context.bot.send_message(chat_id=user_id, text=update.message.text)
                await update.message.reply_text("✅ Respuesta enviada con éxito.")
            except Exception as e:
                await update.message.reply_text(f"❌ Error al enviar respuesta: {e}")

# Configura la base de datos
engine = create_engine('sqlite:///usuarios.db')  # Cambia esto si usas PostgreSQL o Railway
Session = sessionmaker(bind=engine)

# Guarda los mensajes en la base de datos
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

# Respuesta al comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! Bienvenido al bot.")

# Función de botones (placeholder)
async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass  # Puedes implementar tu lógica aquí

# Ejecutar aplicación
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_mensaje))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_message_to_admin))
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY, reply_to_user))

    logging.info("Bot corriendo...")
    app.run_polling()
