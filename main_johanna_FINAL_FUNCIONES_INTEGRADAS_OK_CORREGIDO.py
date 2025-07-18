
# === IMPORTS NECESARIOS ===
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime

# === CONFIGURACIÓN DE LOGS ===
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# === VARIABLES GLOBALES ===
TOKEN = "AQUI_VA_TU_TOKEN"
ADMIN_ID = 5924691120  # ID de Johanna

# === CONFIGURACIÓN DE BASE DE DATOS ===
engine = create_engine("sqlite:///usuarios.db")
Session = sessionmaker(bind=engine)
Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String)
    nombre = Column(String)
    mensaje = Column(String)
    fecha_registro = Column(DateTime)

Base.metadata.create_all(engine)

# === COMANDO /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hola 👋 Bienvenido a nuestro bot.")

# === FUNCIÓN: BOTONES CALLBACK ===
async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass  # Placeholder si tienes botones, agregar aquí

# === FUNCIÓN: GUARDAR MENSAJE EN DB ===
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

# === FUNCIÓN: NOTIFICAR AL ADMIN ===
async def forward_message_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        user_id = update.message.from_user.id
        text = update.message.text
        username = update.message.from_user.username or 'sin username'
        name = update.message.from_user.first_name
        message_to_admin = f"📩 *Nuevo mensaje recibido*\n\n🙋 Usuario: {name} (@{username})\n🆔 ID: {user_id}\n\n📝 Mensaje:\n{text}"
        await context.bot.send_message(chat_id=ADMIN_ID, text=message_to_admin, parse_mode="Markdown")

# === FUNCIÓN: RESPONDER AL USUARIO ===
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

# === BLOQUE DE EJECUCIÓN ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_mensaje))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_message_to_admin))
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY, reply_to_user))

    logging.info("Bot corriendo...")
    app.run_polling()
