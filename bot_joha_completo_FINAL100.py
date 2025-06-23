import logging import os from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup from telegram.ext import (ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes)

TOKEN = os.getenv("BOT_TOKEN") WHATSAPP_LINK = "https://wa.me/573508354350" CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx" ENLACE_REFERIDO = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS" IMAGEN_PORTADA = "https://chatgpt-images.s3.amazonaws.com/johaletrader_portada.png"

MENSAJE_BIENVENIDA = """👋 ¡Hola! Soy JOHAALETRADER. Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable. ¿Lista o listo para registrarte y empezar a ganar? """

MENSAJE_REGISTRO = f"""🔗 Este es tu enlace de registro: {ENLACE_REFERIDO}

💵 Depósito mínimo: $50 USD

🎁 Beneficios que recibirás:

Acceso a 5 cursos (binarias, forex, índices sintéticos), uno con certificación.

Clases grabadas, clases privadas y acompañamiento en vivo.

Guías, PDF, audiolibros, tablas de plan de trading y gestión de riesgo.

Más de 200 señales diarias de lunes a sábado generadas con software propio.

Bot de señales automático en tiempo real 24/7.

Señales de CRYPTO IDX, pares de divisas, forex e índices sintéticos.

Bot y plantilla para MT4 (forex) y MT5 (CRASH y BOOM). """


MENSAJE_VALIDAR_ID = "📩 Si ya realizaste el registro con mi enlace, por favor envíame tu ID de Binomo antes de hacer tu inversión personal."

MENSAJE_1H = """📊 Recuerda que este camino no lo recorrerás sol@. Tendrás acceso a cursos, señales y acompañamiento paso a paso. Estoy aquí para ayudarte a lograr resultados reales en el trading. ¡Activa ya tu cuenta y empecemos! """

MENSAJE_3H = """📈 ¿Aún no te has registrado? No dejes pasar esta oportunidad. Cada día que pasa es una nueva posibilidad de generar ingresos y adquirir habilidades reales. ✅ ¡Recuerda que solo necesitas $50 para comenzar con todo el respaldo! """

MENSAJE_24H = f"""🚀 Tu momento es ahora. Tienes acceso a una comunidad, herramientas exclusivas y formación completa para despegar en el trading. Haz tu primer paso y asegúrate de enviarme tu ID de Binomo para recibir todos los beneficios. 🔗 Canal de resultados: {CANAL_RESULTADOS} """

RESPUESTA_REGISTRO = """Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:

https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS

👉 Luego de crear la cuenta es necesario y súper importante que me envíes tu ID de Binomo para validar tu registro antes de que realices un depósito en tu cuenta de trading.

💵 Depósito mínimo: 50 USD

🎁 Mi comunidad es gratuita. Al seguir los pasos antes mencionados tendrás derecho a todos los beneficios y también estarás participando en el nuevo sorteo.

¡Te espero!"""

RESPUESTA_YA_CUENTA = """Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

¿Qué debes hacer? Copia y pega el enlace de registro en la barra de búsqueda de una ventana de incógnito de tu navegador o activa una VPN para cambiar la dirección IP (en todo caso sería solo para el registro, luego inicias sesión normal). Usa un correo que no hayas usado en Binomo y realiza tu registro de manera manual. SUPER IMPORTANTE: envíame tu ID de Binomo para validar.

Si tu cuenta actual tiene fondos y puedes retirar, realiza el retiro para que lo deposites en tu nueva cuenta. Si no tiene fondos, la puedes eliminar (incluso después de retirar).

🔒 Eliminas desde tu perfil al pie de la página en la opción: BLOQUEAR CUENTA"""

async def mostrar_imagen(update: Update, context: ContextTypes.DEFAULT_TYPE): await context.bot.send_photo( chat_id=update.effective_chat.id, photo=IMAGEN_PORTADA, caption="🎯 JOHAALETRADER\n_Tu camino al éxito financiero comienza aquí_", parse_mode="Markdown" )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE): chat_id = update.effective_chat.id

await mostrar_imagen(update, context)
await update.message.reply_text(MENSAJE_BIENVENIDA)
await update.message.reply_text(MENSAJE_REGISTRO)
await update.message.reply_text(MENSAJE_VALIDAR_ID)

botones = [
    [InlineKeyboardButton("🟢 Registro", callback_data="registro")],
    [InlineKeyboardButton("🔵 Ya tengo cuenta", callback_data="ya_cuenta")],
    [InlineKeyboardButton("💬 Enviar mensaje por WhatsApp", url=WHATSAPP_LINK)],
    [InlineKeyboardButton("🔎 Ver canal de resultados", url=CANAL_RESULTADOS)]
]
await update.message.reply_text("👇 Opciones rápidas:", reply_markup=InlineKeyboardMarkup(botones))

context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_1H), 3600)
context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_3H), 10800)
context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_24H), 86400)

async def responder_boton(update: Update, context: ContextTypes.DEFAULT_TYPE): query = update.callback_query await query.answer()

if query.data == "registro":
    await query.edit_message_text(RESPUESTA_REGISTRO)
elif query.data == "ya_c

