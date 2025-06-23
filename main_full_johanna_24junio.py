
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
import asyncio

# === CONFIGURACIÓN PERSONAL ===
TOKEN = "PON_AQUI_TU_BOT_TOKEN"  # <--- Reemplaza con tu token real
WHATSAPP_LINK = "https://wa.me/573508354350"
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
ENLACE_REFERIDO = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"

# === MENSAJES PERSONALIZADOS ===
MENSAJE_BIENVENIDA = """👋 ¡Hola! Soy JOHAALETRADER.
Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable.
¿Lista o listo para registrarte y empezar a ganar?
""".strip()

MENSAJE_REGISTRARME = """Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:

https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS

👉 Luego de crear la cuenta es necesario y súper importante que me envíes tu ID de Binomo para validar tu registro antes de que realices un depósito en tu cuenta de trading.

💰 Depósito mínimo 50 USD

IMPORTANTE: LA CANTIDAD DE BENEFICIOS VARÍA SEGÚN TU DEPÓSITO.

Mi comunidad es gratuita. Al seguir los pasos antes mencionados tendrás derecho a herramientas y beneficios, y también estarás participando en el nuevo sorteo.

¡Te espero!
""".strip()

MENSAJE_YA_TENGO_CUENTA = """Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

¿Qué debes hacer?
1️⃣ Copia y pega el enlace de registro en la barra de búsqueda de una ventana de incógnito de tu navegador **o** activa una VPN para cambiar tu dirección IP (esto es solo para el registro; luego inicias sesión normal).  
2️⃣ Usa un correo que **no** hayas usado en Binomo y realiza tu registro de manera manual.  
3️⃣ ❗ **SUPER IMPORTANTE**: envíame tu ID de Binomo para validar.

Si tu cuenta actual tiene fondos y puedes retirar, realiza el retiro para depositarlo en tu nueva cuenta.  
Si no tiene fondos, puedes eliminarla ahora o después de retirar.

📌 Elimínala desde tu perfil, al pie de la página, en la opción **BLOQUEAR CUENTA**.
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

# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # Enviar imagen de bienvenida (si está presente)
    try:
        with open("bienvenida_v20_johanna.jpg", "rb") as img:
            await update.message.reply_photo(
                photo=InputFile(img),
                caption=MENSAJE_BIENVENIDA
            )
    except FileNotFoundError:
        await update.message.reply_text(MENSAJE_BIENVENIDA)

    # Botones principales
    keyboard = [
        [InlineKeyboardButton("🚀 Registrarme", callback_data="registrarme")],
        [InlineKeyboardButton("✅ Ya tengo cuenta", callback_data="ya_tengo_cuenta")],
        [InlineKeyboardButton("💬 WhatsApp", url=WHATSAPP_LINK)],
        [InlineKeyboardButton("📊 Canal de resultados", url=CANAL_RESULTADOS)],
    ]
    await update.message.reply_text(
        "👇 Elige una opción para continuar:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # Programar mensajes de seguimiento
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_1H), when=3600)
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_3H), when=10800)
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_24H), when=86400)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "registrarme":
        await query.message.reply_text(MENSAJE_REGISTRARME)
    elif query.data == "ya_tengo_cuenta":
        await query.message.reply_text(MENSAJE_YA_TENGO_CUENTA)

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hola, usa /start para ver las opciones o presiona los botones de registro."
    )

# === MAIN ===
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))

    print("Bot corriendo...")
    app.run_polling()
