import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import asyncio

# Configuración básica
TOKEN = os.environ['BOT_TOKEN']
WHATSAPP_LINK = os.environ.get("WHATSAPP_LINK", "https://wa.me/573508354350")
CANAL_RESULTADOS = os.environ.get("CANAL_RESULTADOS", "https://t.me/+wyjkDFenUMlmMTUx")
ENLACE_REFERIDO = os.environ.get("ENLACE_REFERIDO", "https://binomo.com?a=95604cd745da&t=0&sa=JT")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    image_url = "https://i.imgur.com/ONzYWvZ.jpeg"  # Imagen motivacional

    keyboard = [
        [InlineKeyboardButton("✅ Ya tengo cuenta", callback_data="ya_cuenta")],
        [InlineKeyboardButton("📲 Registrarme ahora", url=ENLACE_REFERIDO)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_photo(
        chat_id=user_id,
        photo=image_url,
        caption="🌟 *Bienvenido a JOHAALETRADER* 🌟\n\n📈 Obtén acceso a señales, clases, bots y contenido exclusivo.\n\n👇 Elige una opción para continuar:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

    # Temporizadores
    await asyncio.sleep(3600)
    await context.bot.send_message(
        chat_id=user_id,
        text="⏰ *Hace 1 hora te registraste.* ¿Necesitas ayuda para empezar?",
        parse_mode="Markdown"
    )
    await asyncio.sleep(7200)
    await context.bot.send_message(
        chat_id=user_id,
        text="📊 *Hace 3 horas tomaste acción.* ¿Quieres saber cómo aprovechar al máximo los recursos?",
        parse_mode="Markdown"
    )
    await asyncio.sleep(75600)
    await context.bot.send_message(
        chat_id=user_id,
        text="💡 *Han pasado 24 horas.* ¡Recuerda que el primer paso hacia el éxito es tomar acción!",
        parse_mode="Markdown"
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "ya_cuenta":
        keyboard = [
            [InlineKeyboardButton("📈 Canal de Resultados", url=CANAL_RESULTADOS)],
            [InlineKeyboardButton("💬 Escríbeme por WhatsApp", url=WHATSAPP_LINK)]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🔐 *Perfecto*\n\n📌 Te invito a validar tu registro:\n✅ Revisa el canal de resultados\n✅ Escríbeme al WhatsApp",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )

def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button))

    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8443)),
        webhook_url=f"https://{os.environ['RAILWAY_STATIC_URL']}/{TOKEN}"
    )

if __name__ == '__main__':
    main()
