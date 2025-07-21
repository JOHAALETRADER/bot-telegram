import logging
import asyncio
import re
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

ADMIN_ID = 5924691120  # Tu ID personal de Telegram

# Diccionario temporal para guardar el ID del usuario al que se va a responder
usuarios_objetivo = {}

# === CONFIGURACIÓN ===
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"
    id             = Column(Integer, primary_key=True)
    telegram_id    = Column(String, unique=True)
    nombre         = Column(String)
    mensaje        = Column(String)
    binomo_id      = Column(String)
    registrado     = Column(String)
    fecha_registro = Column(DateTime, default=datetime.utcnow)

engine = create_engine(DATABASE_URL, echo=False)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
from sqlalchemy import text

# Intentar agregar columna (solo una vez)

# === ENLACES ===
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
ENLACE_REFERIDO  = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"

# === MENSAJES ===
MENSAJE_BIENVENIDA = """👋 ¡Hola! Soy JOHAALETRADER.
Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable.
¿Lista o listo para registrarte y empezar a ganar?"""

MENSAJE_REGISTRARME = f"""Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:

{ENLACE_REFERIDO}

👉 Luego de crear la cuenta es necesario y súper importante que me envíes tu ID de Binomo para validar tu registro antes de que realices un depósito en tu cuenta de trading.

💰 Depósito mínimo 50 USD

IMPORTANTE: LA CANTIDAD DE BENEFICIOS VARÍA SEGÚN TU DEPÓSITO.


Mi comunidad VIP es gratuita. 

¡Te espero!"""

MENSAJE_YA_TENGO_CUENTA = """Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

¿Qué debes hacer? 👉 Si creaste tu cuenta con mi enlace envíame tu ID de Binomo en el botón de arriba.

🟡 Si no lo hiciste con mi enlace, haz lo siguiente:

1️⃣ Copia y pega el enlace de registro en una ventana de incógnito o activa una VPN para cambiar tu IP. Luego inicia sesión normal.

2️⃣ Usa un correo que NO hayas usado en Binomo y regístrate de forma manual.

3️⃣ ❗️SUPER IMPORTANTE: Envíame tu ID de Binomo para validar.

🔗 Enlace de registro: https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS
"""

MENSAJE_1H = """📊 Recuerda que este camino no lo recorrerás sol@.
Tendrás acceso a cursos, señales y acompañamiento paso a paso.
Estoy aquí para ayudarte a lograr resultados reales en el trading. ¡Activa ya tu cuenta y empecemos!"""

MENSAJE_3H = """📈 ¿Aún no te has registrado?
No dejes pasar esta oportunidad. Cada día que pasa es una nueva posibilidad de generar ingresos y adquirir habilidades reales.
✅ ¡Recuerda que solo necesitas 50 USD para comenzar con todo el respaldo!"""

MENSAJE_24H = f"""🚀 Tu momento es ahora.
Tienes acceso a una comunidad, herramientas exclusivas y formación completa para despegar en el trading.
Da tu primer paso y asegúrate de enviarme tu ID de Binomo para recibir todos los beneficios.
🔗 Canal de resultados: {CANAL_RESULTADOS}"""


# === FUNCIONES DE MENSAJES PROGRAMADOS ===
async def mensaje_1h(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.data, text=MENSAJE_1H)

async def mensaje_3h(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.data, text=MENSAJE_3H)

async def mensaje_24h(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.data, text=MENSAJE_24H)

# === FUNCIONES ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    nombre = update.effective_user.full_name

    with Session() as session:
        user = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not user:
            nuevo_usuario = Usuario(
                telegram_id=str(chat_id),
                nombre=nombre,
                fecha_registro=datetime.utcnow()
            )
            session.add(nuevo_usuario)
            session.commit()

    try:
        with open("bienvenidanuevasi.jpg", "rb") as img:
            await update.message.reply_photo(photo=InputFile(img), caption=MENSAJE_BIENVENIDA)
    except FileNotFoundError:
        await update.message.reply_text(MENSAJE_BIENVENIDA)

    kb = [
        [InlineKeyboardButton("🚀 Registrarme", callback_data="registrarme")],
        [InlineKeyboardButton("✅ Valida tu ID | ¿Dudas? Escríbeme", url="https://t.me/Johaaletradervalidacion")],
        [InlineKeyboardButton("✅ Ya tengo cuenta", callback_data="ya_tengo_cuenta")],
        [InlineKeyboardButton("🎁 Beneficios VIP", callback_data="beneficios_vip")],
        [InlineKeyboardButton("📊 Status según inversión", callback_data="status_inversion")],
        [InlineKeyboardButton("📈 Canal de resultados", url=CANAL_RESULTADOS)],
        [InlineKeyboardButton("🌐 Redes sociales", callback_data="redes_sociales")]
    ]

    await update.message.reply_text("👇 Elige una opción para continuar:", reply_markup=InlineKeyboardMarkup(kb))

    user = update.effective_user
    mensaje_admin = (
        f"🚨 El usuario @{user.username or 'SinUsername'} (ID: {user.id}) ha tocado el botón Inicio."
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=mensaje_admin)

    # Programar los mensajes diferidos
    if context.job_queue:
        context.job_queue.run_once(mensaje_1h, when=3600, data=chat_id)
        context.job_queue.run_once(mensaje_3h, when=10800, data=chat_id)
        context.job_queue.run_once(mensaje_24h, when=86400, data=chat_id)
        logging.info(f"✅ Programado mensaje 1h, 3h y 24h para chat_id {chat_id}")
    else:
        logging.warning("⚠️ Job queue no está disponible.")
async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    q = update.callback_query
    await q.answer()
   # Notificar al admin que un usuario tocó un botón general
    await notificar_interaccion(update, context)
    if q.data == "registrarme":
        await q.message.reply_text(MENSAJE_REGISTRARME)
        await q.message.reply_video(
            video="BAACAgEAAxkBAAIBaGhdq0nQXi6B4N8uRwmaOHKkUarbAAIMBgACTgAB8UbIZIU9XTMCzjYE",
            caption="📹 Paso a paso en el vídeo"
        )

    elif q.data == "ya_tengo_cuenta":
        await q.message.reply_text(MENSAJE_YA_TENGO_CUENTA)


    elif q.data == "beneficios_vip":
        await q.message.reply_text("""🎁 Beneficios que recibirás:
- Acceso a 5 cursos (binarias, Forex, índices sintéticos), con certificación.
- Clases grabadas, acceso de por vida, clases privadas y acompañamiento en vivo.
- Guías, PDF, audiolibros, tablas de plan de trading y gestión de riesgo.
- Más de 200 señales diarias de lunes a sábado generadas con software propio.
- Bot de señales automático en tiempo real 24/7.
- Señales de CRYPTO IDX, pares de divisas, Forex e índices sintéticos.
- Bot y plantilla para MT4 (Forex) y MT5 (CRASH y BOOM). Sorteos, premios, Bonos y mucho más.

Recuerda que la cantidad de beneficios varia según tu inversión personal.""")

    elif q.data == "status_inversion":
        with open("IMG_20250626_172306_849.jpg", "rb") as f1:
            await context.bot.send_photo(chat_id=chat_id, photo=f1)
        with open("IMG_20250626_172303_416.jpg", "rb") as f2:
            await context.bot.send_photo(chat_id=chat_id, photo=f2)

    elif q.data == "redes_sociales":
        await q.message.reply_text("""🌐 Redes Sociales:

🔴 YouTube:
https://youtube.com/@johaalegria.trader?si=JemqmPes0Rz3WqEZ

🟣 Instagram:
https://www.instagram.com/johaale_trader?igsh=ZWI5dXNnaXN6aDNw

🎵 TikTok:
https://www.tiktok.com/@joha_binomo?_t=ZN-8xceLrp5GTe&_r=1

💬 Telegram:
https://t.me/JohaaleTraderTeams""")

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

async def notificar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        mensaje = update.message.text
        usuario = update.message.from_user
        chat_id = usuario.id
        nombre = f"@{usuario.username}" if usuario.username else usuario.first_name
        mensaje_usuario = update.message.text

        texto = (
            f"📩 Nuevo mensaje de {nombre} (ID: {chat_id}):\n\n"
            f"🗨️ {mensaje_usuario}\n\n"
            "✏️ Escribe tu respuesta a este usuario directamente respondiendo a este mensaje..."
        )

        botones = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Responder", callback_data="responder:{}:{}".format(chat_id, update.message.message_id))]
        ])

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=texto,
            reply_markup=botones
        )

    except Exception as e:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⚠️ Error notificando al admin: {e}"
        )
# Función para notificar al admin cuando el usuario interactúa con botones
async def notificar_interaccion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        usuario = query.from_user
        chat_id = usuario.id
        nombre = f"@{usuario.username}" if usuario.username else usuario.full_name
        data = query.data

        texto = (
            f"⚡ El usuario {nombre} (ID: {chat_id}) tocó un botón:\n"
            f"➡️ <code>{data}</code>"
        )

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=texto,
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⚠️ Error al notificar interacción: {e}"
        )

import re  # Asegúrate de tener este import al inicio de tu archivo

async def responder_a_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        original_text = update.message.reply_to_message.text_html_urled or update.message.reply_to_message.text
        chat_id_match = re.search(r'ID: (\d+)', original_text) or re.search(r'ID del usuario:.*?(\d+)', original_text)

        if chat_id_match:
            destinatario_id = int(chat_id_match.group(1))
            try:
                if update.message.voice:
                    await context.bot.send_voice(
                        chat_id=destinatario_id,
                        voice=update.message.voice.file_id,
                        caption="🎤 Respuesta en audio"
                    )
                else:
                    await context.bot.send_message(
                        chat_id=destinatario_id,
                        text=update.message.text
                    )

                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="✅ Mensaje enviado al usuario correctamente."
                )
            except Exception as e:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Error al enviar mensaje al usuario: {}".format(e)
                )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ No se pudo encontrar el ID del usuario en el mensaje original."
            )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Debes responder directamente al mensaje del usuario para que funcione."
        )

async def manejar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        data = query.data
        if data.startswith("responder:"):
            partes = data.split(":")
            if len(partes) != 3:
                await query.edit_message_text("❌ Error: formato de callback inválido.")
                return

            chat_id_str, message_id_str = partes[1], partes[2]

            try:
                chat_id = int(chat_id_str)
                message_id = int(message_id_str)
            except ValueError:
                await query.edit_message_text("❌ Error: ID inválido.")
                return

            usuarios_objetivo[query.from_user.id] = chat_id

            await query.edit_message_text(
    text=(
        f"✏️ <b>Ahora puedes responder al usuario.</b>\n\n"
        f"📨 Responde a este mensaje con el texto o audio que deseas enviar.\n"
        f"🆔 ID del usuario: <code>{chat_id}</code>"
    ),
    parse_mode=ParseMode.HTML,
    reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")]
    ])
)

    except Exception as e:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"❌ Error en manejar_callback: {e}"
        )

# Maneja la cancelación del ID de respuesta
async def cancelar_respuesta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        chat_id = query.message.chat.id

        if chat_id in usuarios_objetivo:
            del usuarios_objetivo[chat_id]
            await query.edit_message_text("❌ Has cancelado la respuesta al usuario.")
        else:
            await query.edit_message_text("ℹ️ No había ninguna respuesta pendiente por cancelar.")
            
# Nueva función para manejar mensajes de usuarios
async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await guardar_mensaje(update, context)
    await notificar_admin(update, context)

# Función mejorada para enviar texto, imagen o video al usuario, incluso si viene en caption
async def enviar_mensaje_directo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.caption:
            return  # Si no hay caption, no hace nada

        partes = update.message.caption.split(" ", 2)

        if len(partes) < 3:
            # Aceptar solo imagen/video sin caption (solo /enviar <id>)
            if len(partes) == 2 and (update.message.photo or update.message.document or update.message.video):
                chat_id = int(partes[1])
                mensaje = ""
            else:
                await update.message.reply_text('❗ Usa el formato:\n/enviar <chat_id> <mensaje>')
                return
        else:
            chat_id = int(partes[1])
            mensaje = partes[2]

        # Enviar imagen como PHOTO
        if update.message.photo:
            await context.bot.send_photo(chat_id=chat_id, photo=update.message.photo[-1].file_id, caption=mensaje)
            await update.message.reply_text("✅ Imagen enviada con éxito.")
            return

        # Enviar imagen como DOCUMENTO
        if update.message.document and update.message.document.mime_type.startswith("image/"):
            await context.bot.send_document(chat_id=chat_id, document=update.message.document.file_id, caption=mensaje)
            await update.message.reply_text("✅ Imagen enviada como documento.")
            return

        # Enviar video
        if update.message.video:
            await context.bot.send_video(chat_id=chat_id, video=update.message.video.file_id, caption=mensaje)
            await update.message.reply_text("✅ Video enviado con éxito.")
            return

        # Enviar audio
        if update.message.audio:
            await context.bot.send_audio(chat_id=chat_id, audio=update.message.audio.file_id, caption=mensaje)
            await update.message.reply_text("✅ Audio enviado con éxito.")
            return

        # Enviar nota de voz
        if update.message.voice:
            await context.bot.send_voice(chat_id=chat_id, voice=update.message.voice.file_id)
            await update.message.reply_text("✅ Nota de voz enviada con éxito.")
            return

        # Si no es archivo multimedia, enviar como texto
        if mensaje:
            await context.bot.send_message(chat_id=chat_id, text=mensaje)
            await update.message.reply_text("✅ Mensaje enviado con éxito.")
        else:
            await update.message.reply_text("⚠️ No se pudo enviar nada. Revisa el contenido.")
    except Exception as e:
        print(f"❌ Error al enviar mensaje directo: {e}")
        await update.message.reply_text("⚠️ Ocurrió un error al intentar enviar el mensaje.")


# === EJECUCIÓN ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    # Comando /start
    app.add_handler(CommandHandler("start", start))

    # Enviar imagen, video, audio usando /enviar desde caption (solo multimedia)
    app.add_handler(MessageHandler(
        filters.User(ADMIN_ID) &
        (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO) &
        filters.CaptionRegex(r"^/enviar "),
        enviar_mensaje_directo
    ))

    # Callback del botón "Responder"
    app.add_handler(CallbackQueryHandler(manejar_callback, pattern="^responder:"))

    # Callback del botón ❌ Cancelar
    app.add_handler(CallbackQueryHandler(cancelar_respuesta, pattern="^cancelar$"))

    # Botones generales (debe ir al final para no interceptar los anteriores)
    app.add_handler(CallbackQueryHandler(botones))

    # Mensajes del admin (responder a usuarios con texto o audio deslizando)
    app.add_handler(MessageHandler((filters.TEXT | filters.VOICE) & filters.User(ADMIN_ID), responder_a_usuario))

    # Mensajes normales de los usuarios
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.User(ADMIN_ID), manejar_mensaje))

    logging.info("Bot corriendo…")
    app.run_polling()



