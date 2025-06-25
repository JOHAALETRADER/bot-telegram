import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, JobQueue
)
import asyncio
import os
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Configuración
TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
WHATSAPP_LINK = "https://wa.me/573508354350"
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
ENLACE_REFERIDO = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"
Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, unique=True)

engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Mensajes
mensaje_bienvenida = (
    "👋 ¡Hola! Bienvenido a la comunidad de trading.

"
    "Selecciona una opción para continuar:"
)

registro_texto = (
    "Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:

"
    f"{ENLACE_REFERIDO}

"
    "👉 Luego de crear la cuenta, es necesario y súper importante que me envíes tu ID de Binomo "
    "para validar tu registro antes de que realices un depósito.

"
    "Depósito mínimo: 50 USD

"
    "📌 IMPORTANTE: La cantidad de beneficios varía según tu depósito.

"
    "Mi comunidad es gratuita. ¡Te espero!"
)

ya_cuenta_texto = (
    "Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

"
    "📌 Copia y pega el enlace en una ventana de incógnito o activa una VPN, "
    "usa un correo no registrado previamente en Binomo.

"
    "Si tu cuenta tiene fondos, realiza el retiro y luego crea una nueva cuenta.
"
    "Puedes eliminarla después desde tu perfil en la opción BLOQUEAR CUENTA.

"
    f"{ENLACE_REFERIDO}

"
    "Después de registrarte, envíame tu ID de Binomo para validar."
)

def mensaje_1h(context: ContextTypes.DEFAULT_TYPE):
    context.bot.send_message(chat_id=context.job.context, text="⏰ Han pasado 1 hora desde tu registro. ¿Tienes alguna duda o necesitas ayuda para continuar?")

def mensaje_3h(context: ContextTypes.DEFAULT_TYPE):
    context.bot.send_message(chat_id=context.job.context, text="📣 Recuerda que los beneficios se activan luego de validar tu ID de Binomo y tu depósito.")

def mensaje_24h(context: ContextTypes.DEFAULT_TYPE):
    context.bot.send_message(chat_id=context.job.context, text="✅ ¡Han pasado 24 horas! Aún puedes aprovechar los beneficios exclusivos. ¿Necesitas ayuda?")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = Session()
    if not session.query(Usuario).filter_by(chat_id=chat_id).first():
        session.add(Usuario(chat_id=chat_id))
        session.commit()
    session.close()

    kb = [
        [InlineKeyboardButton("✅ Registrarme", callback_data="registro")],
        [InlineKeyboardButton("🔁 Ya tengo cuenta", callback_data="ya_cuenta")],
        [InlineKeyboardButton("📲 WhatsApp", url=WHATSAPP_LINK)],
        [InlineKeyboardButton("📊 Canal de Resultados", url=CANAL_RESULTADOS)],
    ]
    await context.bot.send_photo(chat_id=chat_id, photo="https://telegra.ph/file/18ef35a8ed728ea94698e.jpg")
    await context.bot.send_message(chat_id=chat_id, text=mensaje_bienvenida, reply_markup=InlineKeyboardMarkup(kb))

    context.job_queue.run_once(mensaje_1h, when=3600, context=chat_id)
    context.job_queue.run_once(mensaje_3h, when=10800, context=chat_id)
    context.job_queue.run_once(mensaje_24h, when=86400, context=chat_id)

async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "registro":
        await q.message.reply_text(registro_texto)
    elif q.data == "ya_cuenta":
        await q.message.reply_text(ya_cuenta_texto)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones))
    app.run_polling()