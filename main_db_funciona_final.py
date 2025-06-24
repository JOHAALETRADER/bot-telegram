
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = "8179287095:AAGYbsj3XDWmCS5Z9PyKj2YzFkCSNiGjiQ4"
WHATSAPP_LINK = "https://wa.me/573508354350"
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
ENLACE_REFERIDO = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"

MENSAJE_BIENVENIDA = """👋 ¡Hola! Soy JOHAALETRADER.
Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable.
¿Lista o listo para registrarte y empezar a ganar?
""".strip()

MENSAJE_REGISTRARME = f"""Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:

{ENLACE_REFERIDO}

👉 Luego de crear la cuenta es necesario y súper importante que me envíes tu ID de Binomo para validar tu registro antes de que realices un depósito en tu cuenta de trading.

💰 Depósito mínimo 50 USD

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

¡Te espero!
""".strip().format(ENLACE_REFERIDO=ENLACE_REFERIDO)

MENSAJE_YA_TENGO_CUENTA = """Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

¿Qué debes hacer?
1️⃣ Copia y pega el enlace de registro en la barra de búsqueda de una ventana de incógnito de tu navegador o activa una VPN para cambiar tu dirección IP (esto es solo para el registro; luego inicias sesión normal).
2️⃣ Usa un correo que NO hayas usado en Binomo y realiza tu registro de manera manual.
3️⃣ ❗ SUPER IMPORTANTE: envíame tu ID de Binomo para validar.

Si tu cuenta actual tiene fondos y puedes retirar, realiza el retiro para depositarlo en tu nueva cuenta.
Si no tiene fondos, puedes eliminarla ahora o después de retirar.

📌 Elimínala desde tu perfil, al pie de la página, en la opción BLOQUEAR CUENTA.
""".strip()

MENSAJE_1H = """📊 Recuerda que este camino no lo recorrerás sol@.
Tendrás acceso a cursos, señales y acompañamiento paso a paso.
Estoy aquí para ayudarte a lograr resultados reales en el trading. ¡Activa ya tu cuenta y empecemos!
""".strip()

MENSAJE_3H = """📈 ¿Aún no te has registrado?
No dejes pasar esta oportunidad. Cada día que pasa es una nueva posibilidad de generar ingresos y adquirir habilidades reales.
✅ ¡Recuerda que solo necesitas 50 USD para comenzar con todo el respaldo!
""".strip()

MENSAJE_24H = f"""🚀 Tu momento es ahora.
Tienes acceso a una comunidad, herramientas exclusivas y formación completa para despegar en el trading.
Da tu primer paso y asegúrate de enviarme tu ID de Binomo para recibir todos los beneficios.
🔗 Canal de resultados: {CANAL_RESULTADOS}
""".strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        with open("bienvenida_v20_johanna.jpg", "rb") as img:
            await update.message.reply_photo(photo=InputFile(img), caption=MENSAJE_BIENVENIDA)
    except FileNotFoundError:
        await update.message.reply_text(MENSAJE_BIENVENIDA)

    kb = [
        [InlineKeyboardButton("🚀 Registrarme", callback_data="registrarme")],
        [InlineKeyboardButton("✅ Ya tengo cuenta", callback_data="ya_tengo_cuenta")],
        [InlineKeyboardButton("💬 WhatsApp", url=WHATSAPP_LINK)],
        [InlineKeyboardButton("📊 Canal de resultados", url=CANAL_RESULTADOS)],
    ]
    await update.message.reply_text("👇 Elige una opción para continuar:", reply_markup=InlineKeyboardMarkup(kb))

    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_1H), 3600)
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_3H), 10800)
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_24H), 86400)

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "registrarme":
        await q.message.reply_text(MENSAJE_REGISTRARME)
        await guardar_usuario(
            telegram_id=q.from_user.id,
            nombre=q.from_user.full_name,
            binomo_id="",
            registrado=False,
            mensaje="Presionó Registrarme"
        )
    elif q.data == "ya_tengo_cuenta":
        await q.message.reply_text(MENSAJE_YA_TENGO_CUENTA)
        await guardar_usuario(
            telegram_id=q.from_user.id,
            nombre=q.from_user.full_name,
            binomo_id="",
            registrado=True,
            mensaje="Presionó Ya tengo cuenta"
        )

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hola, usa /start para ver las opciones o presiona los botones de registro.")


# ========== Conexión a base de datos ==========
import os
from sqlalchemy import Column, Integer, String, Boolean, BigInteger
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
Base = declarative_base()
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True)
    nombre = Column(String)
    binomo_id = Column(String)
    registrado = Column(Boolean)
    mensaje = Column(String)

async def guardar_usuario(telegram_id, nombre, binomo_id, registrado, mensaje):
    async with async_session() as session:
        usuario = Usuario(
            telegram_id=telegram_id,
            nombre=nombre,
            binomo_id=binomo_id,
            registrado=registrado,
            mensaje=mensaje
        )
        session.add(usuario)
        await session.commit()

async def crear_tabla():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

import asyncio
asyncio.run(crear_tabla())
# ========== FIN conexión DB ==========

if __name__ == "__main__":

    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))
    print("Bot corriendo...")
    app.run_polling()
