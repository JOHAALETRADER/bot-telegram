import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# === CONFIGURACIÓN ===
logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    raise ValueError("❌ La variable de entorno BOT_TOKEN no está definida.")
if not ADMIN_ID or not ADMIN_ID.isdigit():
    raise ValueError("❌ La variable de entorno ADMIN_ID no está definida o no es un número válido.")

ADMIN_ID = int(ADMIN_ID)
DATABASE_URL = os.getenv("DATABASE_URL")

Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, unique=True)
    nombre = Column(String)
    mensaje = Column(String)
    binomo_id = Column(String)
    registrado = Column(String)
    fecha_registro = Column(DateTime, default=datetime.utcnow)

engine = create_engine(DATABASE_URL, echo=False)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
from sqlalchemy import text

# === FUNCIONES PRINCIPALES ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡Hola! Bienvenido al bot.")

async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("Ejemplo", callback_data="ejemplo")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Presiona un botón:", reply_markup=reply_markup)

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

async def forward_message_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        chat_id = update.effective_chat.id
        msg = update.message
        await context.bot.forward_message(
            chat_id=ADMIN_ID,
            from_chat_id=msg.chat.id,
            message_id=msg.message_id
        )
        user = update.effective_user
        text = f"🆕 Nuevo mensaje de <b>{user.username or user.first_name}</b>:

{msg.text}

🆔 ID:{chat_id}"
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode=ParseMode.HTML)

async def reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.reply_to_message:
        text = update.message.text
        target_line = update.message.reply_to_message.text
        try:
            user_id = int(target_line.split("ID:")[1].split()[0])
            try:
                await context.bot.send_message(chat_id=user_id, text=text)
                await update.message.reply_text("✅ Enviado con éxito.")
            except Exception as e:
                await update.message.reply_text(f"❌ Error al responder: {e}")
        except Exception:
            await update.message.reply_text("⚠️ No se encontró ID en el mensaje.")

async def notificar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user = update.effective_user
    chat_id = update.effective_chat.id
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📥 Mensaje de {user.username or user.first_name} (@{chat_id}):
{text}",
        )
    except Exception as e:
        logging.error(f"Error enviando notificación al admin: {e}")

# === EJECUCIÓN ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_mensaje))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_message_to_admin))
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY, reply_to_user))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, notificar_admin))

    logging.info("✅ Bot corriendo...")
    app.run_polling()
