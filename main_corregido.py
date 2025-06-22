
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import threading
import time

# === CONFIGURACIÓN PERSONAL ===
TOKEN = "8179287095:AAGYbsj3XDWmCS5Z9PyKj2YzFkCSNiGjiQ4"
WHATSAPP_LINK = "https://wa.me/573508354350"
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
ENLACE_REFERIDO = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"

# === MENSAJES PERSONALIZADOS ===
MENSAJE_BIENVENIDA = """👋 ¡Hola! Soy JOHAALETRADER.
Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable.
¿Lista o listo para registrarte y empezar a ganar?
"""

MENSAJE_REGISTRO = f"""🔗 Este es tu enlace de registro:
{ENLACE_REFERIDO}

💵 Depósito mínimo: $50 USD

🎁 Beneficios que recibirás:
- Acceso a 5 cursos (binarias, forex, índices sintéticos), uno con certificación.
- Clases grabadas, clases privadas y acompañamiento en vivo.
- Guías, PDF, audiolibros, tablas de plan de trading y gestión de riesgo.
- Más de 200 señales diarias de lunes a sábado generadas con software propio.
- Bot de señales automático en tiempo real 24/7.
- Señales de CRYPTO IDX, pares de divisas, forex e índices sintéticos.
- Bot y plantilla para MT4 (forex) y MT5 (CRASH y BOOM).
"""

MENSAJE_VALIDAR_ID = "📩 Si ya realizaste el registro con mi enlace, por favor envíame tu ID de Binomo antes de hacer tu inversión personal."

MENSAJE_1H = """📊 Recuerda que este camino no lo recorrerás sol@.
Tendrás acceso a cursos, señales y acompañamiento paso a paso.
Estoy aquí para ayudarte a lograr resultados reales en el trading. ¡Activa ya tu cuenta y empecemos!
"""

MENSAJE_3H = """📈 ¿Aún no te has registrado?
No dejes pasar esta oportunidad. Cada día que pasa es una nueva posibilidad de generar ingresos y adquirir habilidades reales.
✅ ¡Recuerda que solo necesitas $50 para comenzar con todo el respaldo!
"""

MENSAJE_24H = f"""🚀 Tu momento es ahora.
Tienes acceso a una comunidad, herramientas exclusivas y formación completa para despegar en el trading.
Haz tu primer paso y asegúrate de enviarme tu ID de Binomo para recibir todos los beneficios.
🔗 Canal de resultados: {CANAL_RESULTADOS}
"""

def start(update: Update, context: CallbackContext):
    user = update.effective_user
    update.message.reply_text(MENSAJE_BIENVENIDA)
    update.message.reply_text(MENSAJE_REGISTRO)
    update.message.reply_text(MENSAJE_VALIDAR_ID)

    keyboard = [[InlineKeyboardButton("💬 Enviar mensaje por WhatsApp", url=WHATSAPP_LINK)],
                [InlineKeyboardButton("🔎 Ver canal de resultados", url=CANAL_RESULTADOS)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("👇 Opciones rápidas:", reply_markup=reply_markup)

    # Iniciar mensajes de seguimiento
    threading.Thread(target=seguimiento, args=(context.bot, update.message.chat_id)).start()

def seguimiento(bot, chat_id):
    time.sleep(3600)  # 1 hora
    bot.send_message(chat_id, MENSAJE_1H)
    time.sleep(7200)  # +2 horas (total 3 horas)
    bot.send_message(chat_id, MENSAJE_3H)
    time.sleep(75600)  # +21 horas (total 24 horas)
    bot.send_message(chat_id, MENSAJE_24H)

def main():
    try:
        print("🤖 Bot iniciando...")
        updater = Updater(TOKEN, use_context=True)
        dp = updater.dispatcher
        dp.add_handler(CommandHandler("start", start))
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, start))
        updater.start_polling()
        updater.idle()
    except Exception as e:
        print(f"⚠️ El bot encontró un error: {e}")
        while True:
            time.sleep(60)  # Mantiene el proceso vivo si falla

if __name__ == '__main__':
    main()
