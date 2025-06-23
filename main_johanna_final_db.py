
import asyncio
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    JobQueue,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from models import Base  # Suponiendo que usas un archivo models.py

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# === MENSAJES ===
MENSAJE_BIENVENIDA = "¡Hola! Bienvenido al bot de trading JOHAALETRADER 💰📈"
MENSAJE_1H = "⏰ Ha pasado 1 hora desde que iniciaste. ¿Tienes dudas? ¡Estoy para ayudarte!"
MENSAJE_3H = "🚀 3 horas han pasado. ¿Listo para dar el siguiente paso?"
MENSAJE_24H = "💡 Ya pasó 1 día desde que conociste el bot. ¡No pierdas tu oportunidad!"

# === CONFIG DB ===
engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_sessionmaker = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📲 Registrarme", callback_data="registro")],
        [InlineKeyboardButton("✅ Ya tengo cuenta", callback_data="cuenta")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(MENSAJE_BIENVENIDA, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "registro":
        await query.edit_message_text(
            "Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:

"
            "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS

"
            "👉 Luego de crear la cuenta es necesario y super importante que me envíes tu ID de Binomo para validar tu registro "
            "antes de que realices un depósito en tu cuenta de trading.

"
            "Depósito mínimo 50 USD

"
            "Mi comunidad es gratuita. Al seguir los pasos antes mencionados tendrás derecho a todos los beneficios y también estarás participando en el nuevo sorteo.

"
            "Te espero..!!"
        )
    elif query.data == "cuenta":
        await query.edit_message_text(
            "Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

"
            "¿Qué debes hacer?
"
            "Copia y pega el enlace de registro en la barra de búsqueda de una ventana de incógnito de tu navegador o activa una VPN para cambiar tu dirección IP "
            "(en todo caso sería solo para el registro, luego inicias sesión normal). Usa un correo que no hayas usado en Binomo y realiza tu registro manualmente.

"
            "SUPER IMPORTANTE: envíame tu ID de Binomo para validar.

"
            "Si tu cuenta actual tiene fondos y puedes retirar, hazlo para depositarlo en la nueva cuenta. Si no tiene fondos, puedes eliminarla después del retiro.

"
            "Elimínala desde tu perfil al pie de la página en la opción BLOQUEAR CUENTA"
        )

# === JOBS ===
async def enviar_mensaje_1h(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.chat_id, text=MENSAJE_1H)

async def enviar_mensaje_3h(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.chat_id, text=MENSAJE_3H)

async def enviar_mensaje_24h(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.chat_id, text=MENSAJE_24H)

# === MAIN ===
async def main():
    await init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Programar mensajes automáticos después del /start
    async def programar_mensajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        context.job_queue.run_once(enviar_mensaje_1h, 3600, chat_id=chat_id)
        context.job_queue.run_once(enviar_mensaje_3h, 10800, chat_id=chat_id)
        context.job_queue.run_once(enviar_mensaje_24h, 86400, chat_id=chat_id)

    app.add_handler(CommandHandler("start", programar_mensajes))

    print("BOT INICIADO")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
