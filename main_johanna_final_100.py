
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# Configuración
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
Base = declarative_base()

# Base de datos
class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, unique=True)
    nombre = Column(String)
    mensaje = Column(String)
    binomo_id = Column(String)
    registrado = Column(String)

engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Mensajes
MENSAJE_BIENVENIDA = "¡Bienvenido a mi comunidad de trading! Aquí encontrarás todo lo que necesitas para comenzar tu camino al éxito financiero. Presiona un botón para continuar:"
MENSAJE_REGISTRARME = """Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace :

https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS

👉Luego de crear la cuenta es necesario y super importante que me envíes tu ID de Binomo para validar tu registro 
antes de que realices un depósito en tu cuenta de trading..

Depósito mínimo 50 USD

IMPORTANTE LA CANTIDAD DE BENEFICIOS VARIA SEGÚN TU DEPOSITO

Mi comunidad es gratuita.. al seguir los pasos antes mencionados tendrás derecho a todos los beneficios y también estarás participando en el nuevo sorteo.

Te espero..!!"""
MENSAJE_YA_TENGO_CUENTA = """Para tener acceso a mi comunidad vip y todas las herramientas debes realizar tu registro con mi enlace..

Que debes hacer ?
Copia y pega el enlace de registro en barra de búsqueda de una ventana de incógnito de tu navegador o activa una VPN para cambiar dirección IP (en todo caso sería solo para el registro, luego inicias sesión normal) usa un correo que no hayas usado en Binomo y realiza tu registro de manera manual. SUPER IMPORTANTE enviame ID de binomo para validar.

Si tú cuenta actual tiene fondos y puedes retirar realizas el retiro para que lo deposites en tu nueva cuenta. Si no tiene fondos la puedes eliminar y también la puedes eliminar después de retirar.

Eliminas desde tu perfil al pie de la página en la opción BLOQUEAR CUENTA"""

MENSAJE_1_HORA = "Hola de nuevo 👋 ¿Ya pudiste registrarte en Binomo? Si necesitas ayuda, ¡aquí estoy!"
MENSAJE_3_HORAS = "Recuerda que para recibir todos los beneficios debes registrarte con el enlace que te envié anteriormente. ¡No dejes pasar esta oportunidad!"
MENSAJE_24_HORAS = "Ha pasado un día desde tu primer contacto. ¿Pudiste registrarte en Binomo? Escríbeme para ayudarte a comenzar."

# Función de seguimiento por tiempo
async def seguimiento_tiempo(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await context.bot.send_message(chat_id=data['chat_id'], text=data['mensaje'])
    except Exception as e:
        logging.error(f"Error enviando mensaje programado: {e}")

# Inicio
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_name = update.effective_user.full_name
    session = Session()
    if not session.query(Usuario).filter_by(telegram_id=str(chat_id)).first():
        nuevo = Usuario(telegram_id=str(chat_id), nombre=user_name)
        session.add(nuevo)
        session.commit()
    session.close()

    # Imagen y botones
    with open("bienvenida_johanna.jpg", "rb") as img:
        await context.bot.send_photo(chat_id=chat_id, photo=InputFile(img), caption=MENSAJE_BIENVENIDA, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Registrarme", callback_data="registrarme")],
            [InlineKeyboardButton("💬 Ya tengo cuenta", callback_data="ya_tengo")]
        ]))

    # Programar seguimientos
    context.job_queue.run_once(seguimiento_tiempo, 3600, data={"chat_id": chat_id, "mensaje": MENSAJE_1_HORA})
    context.job_queue.run_once(seguimiento_tiempo, 10800, data={"chat_id": chat_id, "mensaje": MENSAJE_3_HORAS})
    context.job_queue.run_once(seguimiento_tiempo, 86400, data={"chat_id": chat_id, "mensaje": MENSAJE_24_HORAS})

# Botones
async def botones_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "registrarme":
        await query.message.reply_text(MENSAJE_REGISTRARME)
    elif query.data == "ya_tengo":
        await query.message.reply_text(MENSAJE_YA_TENGO_CUENTA)

# Captura mensajes
async def guardar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    mensaje = update.message.text
    session = Session()
    usuario = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
    if usuario:
        usuario.mensaje = mensaje
    else:
        nuevo = Usuario(telegram_id=str(chat_id), nombre=update.effective_user.full_name, mensaje=mensaje)
        session.add(nuevo)
    session.commit()
    session.close()

# Run
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_mensaje))
    app.run_polling()
