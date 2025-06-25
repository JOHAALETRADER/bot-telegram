
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = "AQUI_TU_TOKEN"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Enviando video...")
    await update.message.reply_video(video="AAMCAQADGQECEeiLaFtfO3txAb2kW_PIRijnxG2Re3QAAtwFAANZ2Ub1RDuFRmP-MQEAB20AAzYE")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()
