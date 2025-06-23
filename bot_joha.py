# -*- coding: utf-8 -*-
import logging, os, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# === CONFIGURACIÓN PERSONAL ===
TOKEN            = os.getenv("BOT_TOKEN")
WHATSAPP_LINK    = "https://wa.me/573508354350"
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
ENLACE_REFERIDO  = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"
IMAGEN_PORTADA   = "https://chatgpt-images.s3.amazonaws.com/johaletrader_portada.png"

# === MENSAJES EXISTENTES ===
MENSAJE_BIENVENIDA = """👋 ¡Hola! Soy JOHAALETRADER.
Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable.
¿Lista o listo para registrarte y empezar a ganar?
"""

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
"""

MENSAJE_VALIDAR_ID = "📩 Si ya realizaste el registro con mi enlace, por favor envíame tu ID de Binomo antes de hacer tu inversión personal."

MENSAJE_1H = """📊 Recuerda que este camino no lo recorrerás sol@.
Tendrás acceso a cursos, señales y acompañamiento paso a paso.
Estoy aquí para ayudarte a lograr resultados reales en el trading. ¡Activa ya tu cuenta y empecemos!
"""

MENSAJE_3H = """📈 ¿Aún no te has registrado?
No dejes pasar esta oportunidad. Cada día que pasa es una nueva posibilidad de generar ingresos y adquirir habilidades reales.
✅ ¡Recuerda que solo necesitas $50 para comenzar con todo el respaldo!
"""

MENSAJE_24H = f"""🚀 Tu momento es ahora.
Tienes acceso a una comunidad, herramientas exclusivas y formación completa para despegar en el trading.
Haz tu primer paso y asegúrate de enviarme tu ID de Binomo para recibir todos los beneficios.
🔗 Canal de resultados: {CANAL_RESULTADOS}
"""

# === TEXTOS NUEVOS PARA LOS BOTONES ===
TEXTO_REGISTRO_BOTON = (
    "🟢 *¿Quieres acceder a todo mi contenido exclusivo?*\n\n"
    "Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo usando este enlace:\n"
    f"👉 [{ENLACE_REFERIDO}]({ENLACE_REFERIDO})\n\n"
    "🧾 Luego de crear tu cuenta, es *muy importante* que me envíes tu *ID de Binomo* antes de realizar tu depósito, "
    "para poder validar tu registro.\n\n"
    "💵 *Depósito mínimo:* 50 USD\n\n"
    "🎁 *Mi comunidad es 100 % gratuita*. Al seguir estos pasos recibirás:\n"
    "✅ Acceso completo al contenido VIP\n"
    "✅ Cursos, clases, herramientas y bots\n"
    "✅ Participación automática en nuestro *nuevo sorteo exclusivo*\n\n"
    "📲 ¡Te espero!"
)

TEXTO_YA_CUENTA_BOTON = (
    "🔐 *¿Ya tienes una cuenta en Binomo?*\n\n"
    "Para acceder a mi comunidad VIP y todas las herramientas, debes realizar tu registro correctamente con *mi enlace de referido*.\n\n"
    "🔁 *¿Qué debes hacer?*\n"
    "1️⃣ Copia y pega este enlace en una ventana de incógnito del navegador:\n"
    f"👉 [{ENLACE_REFERIDO}]({ENLACE_REFERIDO})\n"
    "2️⃣ O activa una *VPN* para cambiar tu dirección IP temporalmente.\n"
    "3️⃣ Usa un *correo electrónico que nunca hayas usado en Binomo*.\n"
    "4️⃣ Realiza tu registro de forma manual.\n"
    "5️⃣ 📩 *Muy importante:* Envíame tu *ID de Binomo* para validar tu registro.\n\n"
    "💰 *¿Tienes fondos en tu cuenta actual?*\n"
    "✅ Si puedes retirar: hazlo y deposítalo en tu nueva cuenta.\n"
    "🗑 Si no tienes fondos: puedes eliminarla directamente desde tu perfil.\n\n"
    "🔒 *¿Cómo eliminar la cuenta?*\n"
    "➡️ Ingresa a tu perfil.\n"
    "➡️ Ve al final de la página y selecciona: *Bloquear cuenta*.\n\n"
    "📲 Una vez hecho esto, escríbeme y activamos tu acceso VIP. 💼"
)

# === /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # Imagen de presentación
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=IMAGEN_PORTADA,
        caption="🎯 *JOHAALETRADER*\n_Tu camino al éxito financiero comienza aquí_",
        parse_mode="Markdown",
    )

    # Mensajes originales
    await update.message.reply_text(MENSAJE_BIENVENIDA)
    await update.message.reply_text(MENSAJE_REGISTRO)
    await update.message.reply_text(MENSAJE_VALIDAR_ID)

    # Botones
    keyboard = [
        [InlineKeyboardButton("🟢 Registro", callback_data="registro")],
        [InlineKeyboardButton("🔵 Ya tengo cuenta", callback_data="ya_cuenta")],
        [InlineKeyboardButton("💬 Enviar mensaje por WhatsApp", url=WHATSAPP_LINK)],
        [InlineKeyboardButton("🔎 Ver canal de resultados", url=CANAL_RESULTADOS)],
    ]
    await update.message.reply_text(
        "👇 Opciones rápidas:", reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # Mensajes programados
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_1H),  3600)
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_3H), 10800)
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_24H), 86400)

# === Botones ===
async def responder_boton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "registro":
        await query.edit_message_text(TEXTO_REGISTRO_BOTON, parse_mode="Markdown")
    elif query.data == "ya_cuenta":
        await query.edit_message_text(TEXTO_YA_CUENTA_BOTON, parse_mode="Markdown")

# === Main ===
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(responder_boton))

    # Solo una instancia en Railway → polling funciona sin conflicto
    app.run_polling()
