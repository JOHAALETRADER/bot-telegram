
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import os
from sqlalchemy.ext.asyncio import create_async_engine

# Configuración básica del bot
BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_ID = int(os.getenv("USER_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

# Crear conexión a la base de datos
engine = create_async_engine(DATABASE_URL, echo=False)

# Configuración de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Mensajes
mensaje_bienvenida = "👋 ¡Hola! Bienvenido a JOHAALETRADER 🚀\n\nSelecciona una opción para comenzar:"
mensaje_registro = (
    "✅ Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:\n\n"
    "👉 https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS\n\n"
    "🔒 Luego de crear la cuenta, envíame tu ID de Binomo para validar tu registro antes de realizar un depósito mínimo de 50 USD.\n\n"
    "🎁 La cantidad de beneficios varía según tu depósito. ¡Te espero!"
)
mensaje_ya_tengo_cuenta = (
    "🔁 Para tener acceso a mi comunidad VIP y todas las herramientas, debes realizar tu registro con mi enlace.\n\n"
    "1. Copia y pega el enlace en una ventana de incógnito o usa VPN.\n"
    "2. Regístrate manualmente con un correo NO usado antes en Binomo.\n"
    "3. Envíame tu ID para validar.\n\n"
    "Si tu cuenta actual tiene fondos, retíralos y elimina tu cuenta desde el perfil con la opción 'Bloquear cuenta'."
)
mensaje_beneficios = (
    "🎁 Beneficios tras tu registro y depósito:\n"
    "- Acceso a 5 cursos exclusivos (binarias, forex e índices sintéticos).\n"
    "- Clases grabadas, privadas y en vivo.\n"
    "- Guías, PDF, audiolibros y planillas de trading.\n"
    "- Más de 200 señales diarias (lunes a sábado).\n"
    "- Bot de señales 24/7.\n"
    "- Señales CRYPTO IDX, pares de divisas, forex e índices sintéticos (CRASH y BOOM).\n"
    "- Acceso a plantilla y bot MT4/MT5.\n\n"
    "📲 ¡Todo esto por ser parte de nuestra comunidad!"
)

# Botones
keyboard = [
    [InlineKeyboardButton("📝 Registrarme", callback_data="registro")],
    [InlineKeyboardButton("🔓 Ya tengo cuenta", callback_data="ya_tengo_cuenta")],
    [InlineKeyboardButton("🎁 Ver beneficios", callback_data="beneficios")],
    [InlineKeyboardButton("📊 Canal de resultados", url="https://t.me/+wyjkDFenUMlmMTUx")],
    [InlineKeyboardButton("💬 WhatsApp", url="https://wa.me/573508354350")]
]
reply_markup = InlineKeyboardMarkup(keyboard)

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != USER_ID:
        return
    await update.message.reply_photo(
        photo="https://raw.githubusercontent.com/johaalegria/bot-telegrama/main/bienvenida_v20_johanna.jpg",
        caption=mensaje_bienvenida,
        reply_markup=reply_markup
    )

# Manejo de botones
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "registro":
        await query.edit_message_text(text=mensaje_registro)
    elif query.data == "ya_tengo_cuenta":
        await query.edit_message_text(text=mensaje_ya_tengo_cuenta)
    elif query.data == "beneficios":
        await query.edit_message_text(text=mensaje_beneficios)

# Ejecutar bot
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == '__main__':
    main()
