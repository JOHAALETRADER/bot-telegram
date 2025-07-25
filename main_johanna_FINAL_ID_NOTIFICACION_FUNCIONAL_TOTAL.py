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


Mi comunidad VIP es gratuita. 

¡Te espero!"""

MENSAJE_YA_TENGO_CUENTA = """Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

¿Qué debes hacer? 👉 Si creaste tu cuenta con mi enlace envíame tu ID de Binomo en el botón de arriba.

🟡 Si no lo hiciste con mi enlace, haz lo siguiente:

1️⃣ Copia y pega el enlace de registro en una ventana de incógnito o activa una VPN para cambiar tu IP. Luego inicia sesión normal.

2️⃣ Usa un correo que NO hayas usado en Binomo y regístrate de forma manual.

3️⃣ ❗️SUPER IMPORTANTE: Envíame tu ID de Binomo para validar.

🔗 Enlace de registro: https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS
"""

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
        with open("bienvenidanuevasi.jpg", "rb") as img:
            await update.message.reply_photo(photo=InputFile(img), caption=MENSAJE_BIENVENIDA)
    except FileNotFoundError:
        await update.message.reply_text(MENSAJE_BIENVENIDA)


    kb = [
        [InlineKeyboardButton("🚀 Registrarme", callback_data="registrarme")],
        [InlineKeyboardButton("✅ Valida tu ID | ¿Dudas? Escríbeme", url="https://t.me/Johaaletradervalidacion")],
        [InlineKeyboardButton("✅ Ya tengo cuenta", callback_data="ya_tengo_cuenta")],
        [InlineKeyboardButton("🎁 Beneficios VIP", callback_data="beneficios_vip")],
        [InlineKeyboardButton("📊 Status según inversión", callback_data="status_inversion")],
        [InlineKeyboardButton("📈 Canal de resultados", url=CANAL_RESULTADOS)],
        [InlineKeyboardButton("🌐 Redes sociales", callback_data="redes_sociales")]
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
    chat_id = update.effective_chat.id
    q = update.callback_query
    await q.answer()
    if q.data == "registrarme":
        await q.message.reply_text(MENSAJE_REGISTRARME)
        await q.message.reply_video(
            video="BAACAgEAAxkBAAIBaGhdq0nQXi6B4N8uRwmaOHKkUarbAAIMBgACTgAB8UbIZIU9XTMCzjYE",
            caption="📹 Paso a paso en el vídeo"
        )

    elif q.data == "ya_tengo_cuenta":
        await q.message.reply_text(MENSAJE_YA_TENGO_CUENTA)


    elif q.data == "beneficios_vip":
        await q.message.reply_text("""🎁 Beneficios que recibirás:
- Acceso a 5 cursos (binarias, Forex, índices sintéticos), con certificación.
- Clases grabadas, acceso de por vida, clases privadas y acompañamiento en vivo.
- Guías, PDF, audiolibros, tablas de plan de trading y gestión de riesgo.
- Más de 200 señales diarias de lunes a sábado generadas con software propio.
- Bot de señales automático en tiempo real 24/7.
- Señales de CRYPTO IDX, pares de divisas, Forex e índices sintéticos.
- Bot y plantilla para MT4 (Forex) y MT5 (CRASH y BOOM). Sorteos, premios, Bonos y mucho más.

Recuerda que la cantidad de beneficios varia según tu inversión personal.""")

    elif q.data == "status_inversion":
        with open("IMG_20250626_172306_849.jpg", "rb") as f1:
            await context.bot.send_photo(chat_id=chat_id, photo=f1)
        with open("IMG_20250626_172303_416.jpg", "rb") as f2:
            await context.bot.send_photo(chat_id=chat_id, photo=f2)

    elif q.data == "redes_sociales":
        await q.message.reply_text("""🌐 Redes Sociales:

🔴 YouTube:
https://youtube.com/@johaalegria.trader?si=JemqmPes0Rz3WqEZ

🟣 Instagram:
https://www.instagram.com/johaale_trader?igsh=ZWI5dXNnaXN6aDNw

🎵 TikTok:
https://www.tiktok.com/@joha_binomo?_t=ZN-8xceLrp5GTe&_r=1

💬 Telegram:
https://t.me/JohaaleTraderTeams""")

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


# === FUNCIONES DE NOTIFICACIÓN AL ADMIN Y RESPUESTA DESDE EL BOT ===
async def notificar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        user = update.effective_user
        chat_id = update.effective_chat.id
        texto = update.message.text

        nombre_usuario = (
            f"@{user.username}" if user.username else user.first_name or "Usuario"
        )

        mensaje_admin = (
            f"Nuevo mensaje de {nombre_usuario} (ID: {chat_id}):\n"
            f"{texto}\n\n"
            "Responde a este mensaje con el formato: ID:[id] tu respuesta"
        )

        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=mensaje_admin)
        except Exception as e:
            print(f"Error al enviar mensaje al admin: {e}")

async def responder_desde_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.reply_to_message:
        mensaje = update.message.text or ""
        partes = mensaje.split("ID:")
        if len(partes) < 2:
            await update.message.reply_text("❌ Formato incorrecto. Usa: ID:[id] tu mensaje.")
            return

        id_y_texto = partes[1].strip().split(" ", 1)
        if len(id_y_texto) < 2:
            await update.message.reply_text("❌ Falta el texto de respuesta.")
            return

        target_id, respuesta = id_y_texto
        try:
            await context.bot.send_message(chat_id=int(target_id), text=respuesta)
            await update.message.reply_text("✅ Mensaje enviado.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error al enviar mensaje: {e}")
# === EJECUCIÓN ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_mensaje))
    logging.info("Bot corriendo…")

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, notificar_admin))
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY, responder_desde_admin))
    app.run_polling()
