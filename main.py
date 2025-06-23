
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = "TU_TOKEN_AQUI"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [
            InlineKeyboardButton("✅ Registrarme", callback_data="registrarme"),
            InlineKeyboardButton("🟡 Ya tengo cuenta", callback_data="ya_tengo_cuenta"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 ¡Bienvenido a la comunidad de trading! Selecciona una opción para comenzar:",
        reply_markup=reply_markup
    )

async def botones_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "registrarme":
        mensaje = (
            "Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:

"
            "👉 https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS

"
            "Luego de crear la cuenta es necesario y súper importante que me envíes tu ID de Binomo para validar tu registro "
            "antes de que realices un depósito en tu cuenta de trading.

"
            "💰 Depósito mínimo 50 USD

"
            "IMPORTANTE: LA CANTIDAD DE BENEFICIOS VARÍA SEGÚN TU DEPÓSITO.

"
            "Mi comunidad es gratuita. Al seguir los pasos antes mencionados tendrás derecho a todos los beneficios y también "
            "estarás participando en el nuevo sorteo.

"
            "¡Te espero!"
        )
        await query.edit_message_text(text=mensaje)

    elif query.data == "ya_tengo_cuenta":
        mensaje = (
            "Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

"
            "🔒 ¿Qué debes hacer?
"
            "Copia y pega el enlace de registro en la barra de búsqueda de una ventana de incógnito de tu navegador o activa una VPN para cambiar tu dirección IP (esto es solo para el registro, luego puedes iniciar sesión normal).

"
            "Usa un correo que no hayas usado en Binomo y realiza tu registro de manera manual. SUPER IMPORTANTE enviarme tu ID de Binomo para validar.

"
            "Si tu cuenta actual tiene fondos y puedes retirar, realiza el retiro para que lo deposites en tu nueva cuenta.
"
            "Si no tiene fondos, la puedes eliminar o eliminar después de retirar. Puedes hacerlo desde tu perfil, al pie de página en la opción: BLOQUEAR CUENTA."
        )
        await query.edit_message_text(text=mensaje)

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones_callback))
    app.run_polling()
