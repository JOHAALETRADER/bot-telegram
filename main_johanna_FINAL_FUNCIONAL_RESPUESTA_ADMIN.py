
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)
import logging
from config import TOKEN, ADMIN_ID

# === FUNCIONES DE NOTIFICACIÓN Y RESPUESTA DESDE EL ADMIN ===

async def notificar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        user = update.effective_user
        chat_id = update.effective_chat.id
        texto = update.message.text

        mensaje = (
            f"📩 Nuevo mensaje de {user.first_name} (@{user.username if user.username else 'sin usuario'}):\n\n"
            f"{texto}\n\n"
            f"🛡️ Responde con el formato: ID:{chat_id} tu mensaje"
        )

        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=mensaje)
        except Exception as e:
            print(f"Error al enviar mensaje al admin: {e}")

async def responder_a_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.reply_to_message:
        mensaje = update.message.text or ""
        partes = mensaje.split("ID:")
        if len(partes) < 2:
            await update.message.reply_text("❌ Formato incorrecto. Usa: ID:[id] tu mensaje.")
            return

        id_y_texto = partes[1].strip().split(" ", 1)
        if len(id_y_texto) < 2:
            await update.message.reply_text("❌ Falta el texto del mensaje.")
            return

        usuario_id, texto = id_y_texto
        try:
            await context.bot.send_message(chat_id=int(usuario_id), text=texto)
            await update.message.reply_text("✅ Mensaje enviado con éxito.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error al enviar mensaje: {e}")

# === COMANDO START Y CALLBACKS ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! Bienvenido al bot.")

async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass  # Aquí iría tu lógica de botones

async def guardar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass  # Aquí va tu lógica de guardar en base de datos

# === EJECUCIÓN ===

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_mensaje))
    app.add_handler(MessageHandler(filters.ALL, notificar_admin))  # ✅ Notifica al admin
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY, responder_a_usuario))  # ✅ Responde al usuario original
    logging.info("Bot corriendo…")
    app.run_polling()
