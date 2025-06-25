import logging
import asyncio
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
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
TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"
    id             = Column(Integer, primary_key=True)
    telegram_id    = Column(String, unique=True)
    nombre         = Column(String)
    mensaje        = Column(String)
    binomo_id      = Column(String)
    registrado     = Column(String)
    fecha_registro = Column(DateTime, default=datetime.utcnow)

engine = create_engine(DATABASE_URL, echo=False)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
from sqlalchemy import text

# Intentar agregar columna (solo una vez)

# === ENLACES ===
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
ENLACE_REFERIDO  = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"

# === MENSAJES ===
MENSAJE_BIENVENIDA = """👋 ¡Hola! Soy JOHAALETRADER.
Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable.
¿Lista o listo para registrarte y empezar a ganar?"""

MENSAJE_REGISTRARME = f"""Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:

{ENLACE_REFERIDO}

👉 Luego de crear la cuenta es necesario y súper importante que me envíes tu ID de Binomo para validar tu registro antes de que realices un depósito en tu cuenta de trading.

💰 Depósito mínimo 50 USD

IMPORTANTE: LA CANTIDAD DE BENEFICIOS VARÍA SEGÚN TU DEPÓSITO.

🎁 Beneficios que recibirás:
- Acceso a 5 cursos (binarias, forex, índices sintéticos), uno con certificación.
- Clases grabadas, clases privadas y acompañamiento en vivo.
- Guías, PDF, audiolibros, tablas de plan de trading y gestión de riesgo.
- Más de 200 señales diarias de lunes a sábado generadas con software propio.
- Bot de señales automático en tiempo real 24/7.
- Señales de CRYPTO IDX, pares de divisas, forex e índices sintéticos.
- Bot y plantilla para MT4 (forex) y MT5 (CRASH y BOOM).

Mi comunidad es gratuita. Al seguir los pasos antes mencionados tendrás derecho a todos los beneficios y también estarás participando en el nuevo sorteo.

¡Te espero!"""

MENSAJE_YA_TENGO_CUENTA = """Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

¿Qué debes hacer?
1️⃣ Copia y pega el enlace de registro en la barra de búsqueda de una ventana de incógnito de tu navegador o activa una VPN para cambiar tu dirección IP (esto es solo para el registro; luego inicias sesión normal).
2️⃣ Usa un correo que NO hayas usado en Binomo y realiza tu registro de manera manual.
3️⃣ ❗ SUPER IMPORTANTE: envíame tu ID de Binomo para validar.

🔗 Enlace de registro: https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"""

MENSAJE_1H = """📊 Recuerda que este camino no lo recorrerás sol@.
Tendrás acceso a cursos, señales y acompañamiento paso a paso.
Estoy aquí para ayudarte a lograr resultados reales en el trading. ¡Activa ya tu cuenta y empecemos!"""

MENSAJE_3H = """📈 ¿Aún no te has registrado?
No dejes pasar esta oportunidad. Cada día que pasa es una nueva posibilidad de generar ingresos y adquirir habilidades reales.
✅ ¡Recuerda que solo necesitas 50 USD para comenzar con todo el respaldo!"""

MENSAJE_24H = f"""🚀 Tu momento es ahora.
Tienes acceso a una comunidad, herramientas exclusivas y formación completa para despegar en el trading.
Da tu primer paso y asegúrate de enviarme tu ID de Binomo para recibir todos los beneficios.
🔗 Canal de resultados: {CANAL_RESULTADOS}"""


# === FUNCIONES DE MENSAJES PROGRAMADOS ===
def mensaje_1h(context: ContextTypes.DEFAULT_TYPE):
    context.bot.send_message(chat_id=context.job.context, text=MENSAJE_1H)

def mensaje_3h(context: ContextTypes.DEFAULT_TYPE):
    context.bot.send_message(chat_id=context.job.context, text=MENSAJE_3H)

def mensaje_24h(context: ContextTypes.DEFAULT_TYPE):
    context.bot.send_message(chat_id=context.job.context, text=MENSAJE_24H)

# === FUNCIONES ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    nombre = update.effective_user.full_name

    with Session() as session:
        user = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not user:
            nuevo_usuario = Usuario(
                telegram_id=str(chat_id),
                nombre=nombre,
                fecha_registro=datetime.utcnow()
            )
            session.add(nuevo_usuario)
            session.commit()

    try:
        with open("bienvenida_v20_johanna.jpg", "rb") as img:
            await update.message.reply_photo(photo=InputFile(img), caption=MENSAJE_BIENVENIDA)
    except FileNotFoundError:
        await update.message.reply_text(MENSAJE_BIENVENIDA)

    kb = [
        [InlineKeyboardButton("🚀 Registrarme", callback_data="registrarme")],
        [InlineKeyboardButton("✅ Ya tengo cuenta", callback_data="ya_tengo_cuenta")],
        [InlineKeyboardButton("✅ Valida tu ID aquí", url="https://t.me/Johaaletradervalidacion")],
        [InlineKeyboardButton("📊 Canal de resultados", url=CANAL_RESULTADOS)],
    ]
    await update.message.reply_text("👇 Elige una opción para continuar:", reply_markup=InlineKeyboardMarkup(kb))

        context.job_queue.run_once(mensaje_1h, when=3600, context=chat_id)
        logging.info(f"✅ Programado mensaje 1h para chat_id {chat_id}")
    
        context.job_queue.run_once(mensaje_3h, when=10800, context=chat_id)
        logging.info(f"✅ Programado mensaje 3h para chat_id {chat_id}")
    
        context.job_queue.run_once(mensaje_24h, when=86400, context=chat_id)
        logging.info(f"✅ Programado mensaje 24h para chat_id {chat_id}")
        logging.info(f"✅ Programado mensaje 24h para chat_id {chat_id}")

async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "registrarme":
        await q.message.reply_text(MENSAJE_REGISTRARME)
    elif q.data == "ya_tengo_cuenta":
        await q.message.reply_text(MENSAJE_YA_TENGO_CUENTA)

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

# === EJECUCIÓN ===

if __name__ == "__main__":
    import asyncio

    async def main():
        app = ApplicationBuilder().token(TOKEN).build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(botones))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_mensaje))

        logging.info("Bot corriendo…")
        await app.initialize()
        await app.start()
        await app.run_polling()

    asyncio.run(main())