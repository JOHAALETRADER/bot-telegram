
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import os

TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🟢 Registrarme", callback_data="registro")],
        [InlineKeyboardButton("🔐 Ya tengo cuenta", callback_data="ya_cuenta")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("¡Hola! 👋 Selecciona una opción para continuar:", reply_markup=reply_markup)

async def responder_boton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "registro":
        await query.edit_message_text(
            "🟢 *¿Quieres acceder a todo mi contenido exclusivo?*

"
            "Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo usando este enlace:
"
            "👉 [https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS](https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS)

"
            "🧾 Luego de crear tu cuenta, es *muy importante* que me envíes tu *ID de Binomo* antes de realizar tu depósito, para poder validar tu registro.

"
            "💵 *Depósito mínimo:* 50 USD

"
            "🎁 *Mi comunidad es 100% gratuita*. Al seguir estos pasos recibirás:
"
            "✅ Acceso completo al contenido VIP
"
            "✅ Cursos, clases, herramientas y bots
"
            "✅ Participación automática en nuestro *nuevo sorteo exclusivo*

"
            "📲 ¡Te espero!",
            parse_mode="Markdown"
        )

    elif query.data == "ya_cuenta":
        await query.edit_message_text(
            "🔐 *¿Ya tienes una cuenta en Binomo?*

"
            "Para acceder a mi comunidad VIP y todas las herramientas, debes realizar tu registro correctamente con *mi enlace de referido*.

"
            "🔁 *¿Qué debes hacer?*
"
            "1️⃣ Copia y pega este enlace en una ventana de incógnito del navegador:
"
            "👉 [https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS](https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS)
"
            "2️⃣ O activa una *VPN* para cambiar tu dirección IP temporalmente
"
            "3️⃣ Usa un *correo electrónico que nunca hayas usado en Binomo*
"
            "4️⃣ Realiza tu registro de forma manual
"
            "5️⃣ 📩 *Muy importante:* Envíame tu *ID de Binomo* para validar tu registro

"
            "💰 *¿Tienes fondos en tu cuenta actual?*
"
            "✅ Si puedes retirar: hazlo y deposítalo en tu nueva cuenta
"
            "🗑 Si no tienes fondos: puedes eliminarla directamente desde tu perfil

"
            "🔒 *¿Cómo eliminar la cuenta?*
"
            "➡️ Ingresa a tu perfil
"
            "➡️ Ve al final de la página y selecciona: *Bloquear cuenta*

"
            "📲 Una vez hecho esto, escríbeme y activamos tu acceso VIP. 💼",
            parse_mode="Markdown"
        )

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(responder_boton))
    app.run_polling()
