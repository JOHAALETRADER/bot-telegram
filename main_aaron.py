
from telegram import Update
from telegram.ext import ContextTypes
import logging

# === FUNCIÓN BOTONES ===
async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "registrarme":
        await q.message.reply_text(MENSAJE_REGISTRARME)
        await q.message.reply_video(
            video="BAACAgEAAxkBAAIBaGhdq0nQXi6B4N8uRwmaOHKkUarbAAIMBgACTgAB8UbIZIU9XTMCzjYE",
            caption="📹 Paso a paso en el vídeo"
        )

    elif q.data == "ya_tengo_cuenta":
        await q.message.reply_text(MENSAJE_YA_TENGO_CUENTA)

    elif q.data == "beneficios_vip":
        await q.message.reply_text(
            "🎁 Beneficios que recibirás:\n"
            "- Acceso a 5 cursos (binarias, Forex, índices sintéticos), con certificación.\n"
            "- Clases grabadas, acceso de por vida, clases privadas y acompañamiento en vivo.\n"
            "- Guías, PDF, audiolibros, tablas de plan de trading y gestión de riesgo.\n"
            "- Más de 200 señales diarias de lunes a sábado generadas con software propio.\n"
            "- Bot de señales automático en tiempo real 24/7.\n"
            "- Señales de CRYPTO IDX, pares de divisas, Forex e índices sintéticos.\n"
            "- Bot y plantilla para MT4 (Forex) y MT5 (CRASH y BOOM).\n"
            "  Sorteos, premios, Bonos y mucho más.\n\n"
            "Recuerda que la cantidad de beneficios varía según tu inversión personal."
        )

    elif q.data == "status_inversion":
        await q.message.reply_photo(photo=open("IMG_20250626_172306_849.jpg", "rb"))
        await q.message.reply_photo(photo=open("IMG_20250626_172303_416.jpg", "rb"))
