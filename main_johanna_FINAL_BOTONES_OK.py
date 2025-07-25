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
async def mensaje_1h(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.data, text=MENSAJE_1H)

async def mensaje_3h(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.data, text=MENSAJE_3H)

async def mensaje_24h(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.data, text=MENSAJE_24H)

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
        [InlineKeyboardButton("🎁 Beneficios VIP", callback_data="beneficios_vip")],
        [InlineKeyboardButton("📊 Status según inversión", callback_data="status_inversion")],
        [InlineKeyboardButton("🌐 Redes sociales", callback_data="redes_sociales")],

    ]
    await update.message.reply_text("👇 Elige una opción para continuar:", reply_markup=InlineKeyboardMarkup(kb))


    # Programar los mensajes diferidos
    if context.job_queue:
        context.job_queue.run_once(mensaje_1h, when=3600, data=chat_id)
        context.job_queue.run_once(mensaje_3h, when=10800, data=chat_id)
        context.job_queue.run_once(mensaje_24h, when=86400, data=chat_id)
        logging.info(f"✅ Programado mensaje 1h, 3h y 24h para chat_id {chat_id}")
    else:
        logging.warning("⚠️ Job queue no está disponible.")
async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "registrarme":
        await q.message.reply_text(MENSAJE_REGISTRARME)
        await q.message.reply_video(
            video="BAACAgEAAxkBAAIBaGhdq0nQXi6B4N8uRwmaOHKkUarbAAIMBgACTgAB8UbIZIU9XTMCzjYE",
            caption="📹 Paso a paso en el vídeo"
        )

    elif q.data == "ya_tengo_cuenta":
    elif q.data == "beneficios_vip":
        await q.message.reply_text(
            "🎁 Beneficios que recibirás:\n"
            "- Acceso a 5 cursos (binarias, Forex, índices sintéticos), con certificación.\n"
            "- Clases grabadas, acceso de por vida, clases privadas y acompañamiento en vivo.\n"
            "- Guías, PDF, audiolibros, tablas de plan de trading y gestión de riesgo.\n"
            "- Más de 200 señales diarias de lunes a sábado generadas con software propio.\n"
            "- Bot de señales automático en tiempo real 24/7.\n"
            "- Señales de CRYPTO IDX, pares de divisas, Forex e índices sintéticos.\n"
            "- Bot y plantilla para MT4 (Forex) y MT5 (CRASH y BOOM). Sorteos, premios, Bonos y mucho más.\n\n"
            "Recuerda que la cantidad de beneficios varía según tu inversión personal."
        )

    elif q.data == "status_inversion":
        await q.message.reply_photo(photo=open("IMG_20250626_172306_849.jpg", "rb"))
        await q.message.reply_photo(photo=open("IMG_20250626_172303_416.jpg", "rb"))

    elif q.data == "redes_sociales":
        await q.message.reply_text(
            "🌐 Aquí tienes mis redes sociales oficiales:\n\n"
            "📣 Canal de Telegram: https://t.me/JohaaleTraderTeams\n"
            "▶️ YouTube: https://youtube.com/@johaalegria.trader?si=JemqmPes0Rz3WqEZ\n"
            "📸 Instagram: https://www.instagram.com/johaale_trader?igsh=ZWI5dXNnaXN6aDNw\n"
            "🎵 TikTok: https://www.tiktok.com/@joha_binomo?_t=ZN-8xceLrp5GTe&_r=1"
        )

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
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_mensaje))
    logging.info("Bot corriendo…")
    app.run_polling()
