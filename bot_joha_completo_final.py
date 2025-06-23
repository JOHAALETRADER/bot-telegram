
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# === CONFIGURACIÓN PERSONAL ===
TOKEN = "8179287095:AAGYbsj3XDWmCS5Z9PyKj2YzFkCSNiGjiQ4"
WHATSAPP_LINK = "https://wa.me/573508354350"
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
ENLACE_REFERIDO = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"

# === MENSAJES PERSONALIZADOS ===
MENSAJE_BIENVENIDA = """👋 ¡Hola! Soy JOHAALETRADER.
Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable.
¿Lista o listo para registrarte y empezar a ganar?
""".strip()

MENSAJE_REGISTRO = f"""🔗 Este es tu enlace de registro:
{ENLACE_REFERIDO}

💵 Depósito mínimo: $50 USD

🎁 Beneficios que recibirás:
- Acceso a 5 cursos (binarias, forex, índices sintéticos), uno con certificación.
- Clases grabadas, clases privadas y acompañamiento en vivo.
- Guías, PDF, audiolibros, tablas de plan de trading y gestión de riesgo.
- Más de 200 señales diarias de lunes a sábado generadas con software propio.
- Bot de señales automático en tiempo real 24/7.
- Señales de CRYPTO IDX, pares de divisas, forex e índices sintéticos.
- Bot y plantilla para MT4 (forex) y MT5 (CRASH y BOOM).
""".strip()

MENSAJE_VALIDAR_ID = "📩 Si ya realizaste el registro con mi enlace, por favor envíame tu ID de Binomo antes de hacer tu inversión personal."

MENSAJE_1H = """📊 Recuerda que este camino no lo recorrerás sol@.
Tendrás acceso a cursos, señales y acompañamiento paso a paso.
Estoy aquí para ayudarte a lograr resultados reales en el trading. ¡Activa ya tu cuenta y empecemos!
""".strip()

MENSAJE_3H = """📈 ¿Aún no te has registrado?
No dejes pasar esta oportunidad. Cada día que pasa es una nueva posibilidad de generar ingresos y adquirir habilidades reales.
✅ ¡Recuerda que solo necesitas $50 para comenzar con todo el respaldo!
""".strip()

MENSAJE_24H = f"""🚀 Tu momento es ahora.
Tienes acceso a una comunidad, herramientas exclusivas y formación completa para despegar en el trading.
Haz tu primer paso y asegúrate de enviarme tu ID de Binomo para recibir todos los beneficios.
🔗 Canal de resultados: {CANAL_RESULTADOS}
""".strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(MENSAJE_BIENVENIDA)
    await update.message.reply_text(MENSAJE_REGISTRO)
    await update.message.reply_text(MENSAJE_VALIDAR_ID)

    keyboard = [
        [InlineKeyboardButton("💬 Enviar mensaje por WhatsApp", url=WHATSAPP_LINK)],
        [InlineKeyboardButton("🔎 Ver canal de resultados", url=CANAL_RESULTADOS)]
    ]
    await update.message.reply_text(
        "👇 Opciones rápidas:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # Schedule follow‑ups
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_1H), 3600)
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_3H), 10800)
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_24H), 86400)


from telegram import InputFile

# === NUEVA IMAGEN DE PRESENTACIÓN ===
def enviar_imagen(update: Update, context: CallbackContext):
    context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo="https://i.ibb.co/YXkYZFj/johaaletrader-bot-presentacion.jpg",
        caption="¡Bienvenido(a)! Soy Joha Ale Trader 🤖✨",
    )

# === NUEVOS BOTONES ===
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def opciones_registro(update: Update, context: CallbackContext):
    keyboard = [
        [
            InlineKeyboardButton("✅ Ya tengo cuenta", callback_data="ya_tengo"),
            InlineKeyboardButton("📝 Registro", callback_data="registro"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Selecciona una opción:", reply_markup=reply_markup)

def manejar_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    if query.data == "ya_tengo":
        query.edit_message_text(text="Perfecto, valida tu acceso con Joha por WhatsApp 💬: https://wa.me/573508354350")
    elif query.data == "registro":
        query.edit_message_text(text="Regístrate con este enlace y avísame por WhatsApp para validar tu ingreso: https://binomo.com?a=95604cd745da&t=0&sa=JT")

# Asegúrate de registrar estos handlers en tu Application


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()
