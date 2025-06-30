
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

TOKEN = "AQUÍ_TU_TOKEN"

async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        photo = update.message.photo[-1]
        await update.message.reply_text(f"✅ file_id: {photo.file_id}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, recibir_foto))
    print("Bot corriendo. Esperando imágenes...")
    app.run_polling()
