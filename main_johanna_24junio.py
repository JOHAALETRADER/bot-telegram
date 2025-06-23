
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import asyncio

BOT_TOKEN = "TU_TOKEN_AQUI"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Imagen de bienvenida
    with open("bienvenida_v20_johanna.jpg", "rb") as image_file:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(image_file),
            caption="👋 ¡Bienvenido a mi comunidad de trading!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📩 Registrarme", callback_data="registrarme")],
                [InlineKeyboardButton("✅ Ya tengo cuenta", callback_data="ya_tengo_cuenta")]
            ])
        )

    # Mensaje inmediato
    await context.bot.send_message(chat_id=chat_id, text="✅ El bot está activo y responde correctamente.")

    # Mensajes programados
    asyncio.create_task(mensaje_programado(chat_id, context, 3600, "⏰ ¿Tienes dudas con el registro o el depósito? Estoy aquí para ayudarte."))
    asyncio.create_task(mensaje_programado(chat_id, context, 10800, "📊 Recuerda que con tu registro tienes acceso a cursos, señales y más beneficios exclusivos."))
    asyncio.create_task(mensaje_programado(chat_id, context, 86400, "🎁 ¡Aprovecha ahora! El sorteo y los beneficios especiales están activos por poco tiempo."))

async def mensaje_programado(chat_id, context, delay, message):
    await asyncio.sleep(delay)
    await context.bot.send_message(chat_id=chat_id, text=message)

async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "registrarme":
        await query.message.reply_text(
            "Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:

"
            "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS

"
            "👉 Luego de crear la cuenta es necesario y super importante que me envíes tu ID de Binomo para validar tu registro antes de que realices un depósito en tu cuenta de trading.

"
            "Depósito mínimo 50 USD

"
            "IMPORTANTE: la cantidad de beneficios varía según tu depósito.

"
            "Mi comunidad es gratuita... Al seguir los pasos antes mencionados tendrás derecho a todos los beneficios y también estarás participando en el nuevo sorteo.

"
            "Te espero..!!"
        )
    elif query.data == "ya_tengo_cuenta":
        await query.message.reply_text(
            "Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

"
            "¿Qué debes hacer?

"
            "Copia y pega el enlace de registro en la barra de búsqueda de una ventana de incógnito de tu navegador o activa una VPN para cambiar tu dirección IP "
            "(en todo caso sería solo para el registro, luego inicias sesión normal). Usa un correo que no hayas usado en Binomo y realiza tu registro de manera manual.

"
            "❗ SUPER IMPORTANTE: envíame tu ID de Binomo para validar.

"
            "Si tu cuenta actual tiene fondos y puedes retirar, realiza el retiro para que lo deposites en tu nueva cuenta.
"
            "Si no tiene fondos, la puedes eliminar, también después de retirar.

"
            "📌 Puedes eliminarla desde tu perfil, al pie de la página, en la opción BLOQUEAR CUENTA"
        )

async def respuestas_genericas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hola. Recuerda usar el botón 📩 Registrarme o ✅ Ya tengo cuenta para comenzar. Si tienes dudas, estoy aquí para ayudarte.")

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, respuestas_genericas))

    print("Bot corriendo...")
    app.run_polling()
