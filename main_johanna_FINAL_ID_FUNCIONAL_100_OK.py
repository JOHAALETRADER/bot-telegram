
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from sqlalchemy.orm import sessionmaker
from models import Usuario
from datetime import datetime
from config import TOKEN, engine

Session = sessionmaker(bind=engine)

# Función para guardar mensajes en la base de datos
async def guardar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    texto = update.message.text
    with Session() as session:
        user = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if user:
            user.mensaje = texto
        else:
            session.add(Usuario(
                telegram_id=str(chat_id),
                nombre=update.effective_user.full_name,
                mensaje=texto,
                fecha_registro=datetime.utcnow()
            ))
        session.commit()

# === BLOQUE DE NOTIFICACIÓN Y RESPUESTA ===
ADMIN_ID = 5924691120  # ID de Johanna

# Notificar a admin cuando cualquier usuario (excepto ella misma) escriba
async def forward_message_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user.id != ADMIN_ID:
        user = update.message.from_user
        msg = f"📩 Nuevo mensaje de {user.full_name} (@{user.username if user.username else 'sin username'})\nID: {user.id}\n\n{update.message.text}"
        await context.bot.send_message(chat_id=ADMIN_ID, text=msg)

# Responder desde el admin a cualquier usuario (usando reply a mensaje)
async def reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.message.reply_to_message:
            lines = update.message.reply_to_message.text.split('\n')
            for line in lines:
                if "ID:" in line:
                    user_id = int(line.replace("ID:", "").strip())
                    await context.bot.send_message(chat_id=user_id, text=update.message.text)
                    await update.message.reply_text("✅ Mensaje entregado.")
                    return
        await update.message.reply_text("❌ No se pudo entregar el mensaje.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error al enviar el mensaje: {e}")

# === EJECUCIÓN PRINCIPAL ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_mensaje))

    # Nuevas funciones
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_message_to_admin))
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY, reply_to_user))

    logging.info("Bot corriendo...")
    app.run_polling()
