
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

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

MENSAJE_REGISTRARME = """Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace :

https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS

👉Luego de crear la cuenta es necesario y super importante que me envíes tu ID de Binomo para validar tu registro
antes de que realices un depósito en tu cuenta de trading..

Depósito mínimo 50 USD

IMPORTANTE LOS BENEFICIOS  VARIAN SEGÚN TU DEPOSITO

Mi comunidad es gratuita.. al seguir los pasos antes mencionados tendrás derecho a herramientas y beneficios, también estarás participando en el nuevo sorteo.

Te espero..!!
""".strip()

MENSAJE_YA_TENGO_CUENTA = """Para tener acceso a mi comunidad vip y todas las herramientas debes realizar tu registro con mi enlace..

Que debes hacer ?
Copia y pega el enlace de registro en barra de búsqueda de una ventana de incógnito de tu navegador o activa una VPN para cambiar dirección IP (en todo caso sería solo para el registro, luego inicias sesión normal) usa un correo que no hayas usado en Binomo y realiza tu registro de manera manual. SUPER IMPORTANTE enviame ID de binomo para validar.

Si tú cuenta actual tiene fondos y puedes retirar realizas el retiro para que lo deposites en tu nueva cuenta. Si no tiene fondos la puedes eliminar y también la puedes eliminar después de retirar.

Eliminas desde tu perfil al pie de la página en la opción BLOQUEAR CUENTA
""".strip()

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

    # Imagen de bienvenida (ruta local)
    with open("bienvenida.jpg", "rb") as img:
        await update.message.reply_photo(photo=InputFile(img), caption=MENSAJE_BIENVENIDA)

    # Botones nuevos
    keyboard = [
        [InlineKeyboardButton("🚀 Registrarme", callback_data="registrarme")],
        [InlineKeyboardButton("🔄 Ya tengo cuenta", callback_data="ya_tengo_cuenta")],
        [InlineKeyboardButton("💬 Enviar mensaje por WhatsApp", url=WHATSAPP_LINK)],
        [InlineKeyboardButton("📊 Ver canal de resultados", url=CANAL_RESULTADOS)]
    ]
    await update.message.reply_text(
        "👇 Elige una opción para continuar:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # Mensajes programados
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_1H), 3600)
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_3H), 10800)
    context.job_queue.run_once(lambda ctx: ctx.bot.send_message(chat_id, MENSAJE_24H), 86400)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "registrarme":
        await query.message.reply_text(MENSAJE_REGISTRARME)
    elif query.data == "ya_tengo_cuenta":
        await query.message.reply_text(MENSAJE_YA_TENGO_CUENTA)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()
