
import logging
import os
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext, CallbackQueryHandler
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Configuración básica de logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Token del bot y URL de la base de datos
TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Conexión a la base de datos PostgreSQL
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# Función para manejar /start
async def start(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user = update.effective_user

    # Guardar usuario en base de datos
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY, username TEXT);")
        cur.execute("INSERT INTO users (id, username) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING;", (user.id, user.username))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error guardando en base de datos: {e}")

    # Botones de opciones
    keyboard = [
        [InlineKeyboardButton("✅ Registrarme", callback_data='registrarme')],
        [InlineKeyboardButton("🟣 Ya tengo cuenta", callback_data='ya_tengo_cuenta')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Enviar imagen con mensaje de bienvenida
    try:
        with open("bienvenida_johanna.jpg", "rb") as img:
            await context.bot.send_photo(chat_id=chat_id, photo=img, caption="¡Bienvenid@ a *JOHAALETRADER*! 👋\n\nAquí comienza tu camino al éxito financiero 🚀\n\nElige una opción para continuar:", reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"No se pudo enviar la imagen: {e}")
        await update.message.reply_text("¡Bienvenido a *JOHAALETRADER*! Elige una opción para continuar:", reply_markup=reply_markup, parse_mode="Markdown")

# Callback de botones
async def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    if query.data == "registrarme":
        msg = "Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:

https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS

👉 Luego de crear la cuenta, es necesario y *super importante* que me envíes tu ID de Binomo para validar tu registro antes de realizar el depósito.

Depósito mínimo: 50 USD

IMPORTANTE: La cantidad de beneficios varía según tu depósito.

Mi comunidad es gratuita. Siguiendo estos pasos accedes a todos los beneficios y participas en el nuevo sorteo. ¡Te espero!"
        await query.edit_message_text(text=msg, parse_mode="Markdown")

    elif query.data == "ya_tengo_cuenta":
        msg = "Para tener acceso a mi comunidad VIP y todas las herramientas, debes registrarte con mi enlace:

https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS

🔐 Usa una ventana de incógnito o una VPN para cambiar tu IP si ya tenías cuenta. Luego de registrar una nueva cuenta con un correo diferente, envíame tu ID de Binomo para validarla.

Si tienes fondos en tu cuenta actual, retíralos antes de eliminarla. Si no, puedes eliminarla directamente desde tu perfil, al final de la página (opción BLOQUEAR CUENTA)."
        await query.edit_message_text(text=msg, parse_mode="Markdown")

# Ejecutar la aplicación
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.run_polling()

if __name__ == "__main__":
    main()
