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
    # Nuevo: idioma preferido ("es" / "en")
    lang           = Column(String, default="es")

engine = create_engine(DATABASE_URL, echo=False)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Intentar agregar columna lang si no existe (idempotente)
try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS lang VARCHAR"))
except Exception as _e:
    pass

# === ENLACES ===
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
CANAL_ES = "https://t.me/JohaaleTrader_es"
CANAL_EN = "https://t.me/JohaaleTrader_en"
ENLACE_REFERIDO  = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"

# === MENSAJES (ES/EN) ===
WELCOME_IMG = "bienvenidanuevasi.jpg"

MENSAJE_BIENVENIDA_ES = """👋 ¡Hola! Soy JOHAALETRADER.
Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable.
¿Lista o listo para registrarte y empezar a ganar?"""

MENSAJE_BIENVENIDA_EN = """👋 Hi! I’m JOHAALETRADER.
I’m here to help you start in binary options trading safely, with guidance and real profitability.
Ready to register and start earning?"""

MENSAJE_REGISTRARME_ES = f"""Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo con este enlace:

{ENLACE_REFERIDO}

👉 Luego de crear la cuenta es necesario y súper importante que me envíes tu ID de Binomo para validar tu registro antes de que realices un depósito en tu cuenta de trading.

💰 Depósito mínimo 50 USD

IMPORTANTE: LA CANTIDAD DE BENEFICIOS VARÍA SEGÚN TU DEPÓSITO.

Mi comunidad VIP es gratuita. 
¡Te espero!"""

MENSAJE_REGISTRARME_EN = f"""It’s super simple. Open your trading account on Binomo using this link:

{ENLACE_REFERIDO}

👉 After creating the account, it’s very important that you send me your Binomo ID so I can validate your registration **before** you make any deposit.

💰 Minimum deposit: 50 USD

IMPORTANT: The amount of benefits varies depending on your deposit.

My VIP community is free.
I’ll be waiting for you!"""

MENSAJE_YA_TENGO_CUENTA_ES = f"""Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

¿Qué debes hacer? 👉 Si creaste tu cuenta con mi enlace envíame tu ID de Binomo en el botón de arriba.

🟡 Si no lo hiciste con mi enlace, haz lo siguiente:

1️⃣ Copia y pega el enlace de registro en una ventana de incógnito o activa una VPN para cambiar tu IP. Luego inicia sesión normal.

2️⃣ Usa un correo que NO hayas usado en Binomo y regístrate de forma manual.

3️⃣ ❗️SUPER IMPORTANTE: Envíame tu ID de Binomo para validar.

🔗 Enlace de registro: {ENLACE_REFERIDO}
"""

MENSAJE_YA_TENGO_CUENTA_EN = f"""To access my VIP community and all tools, you must register with my link.

What to do? 👉 If you created your account with my link, send me your Binomo ID using the button above.

🟡 If you didn’t use my link, do this:

1️⃣ Copy and paste the registration link in an incognito window or turn on a VPN to change your IP. Then log in normally.

2️⃣ Use an email you have NOT used on Binomo and register manually.

3️⃣ ❗️VERY IMPORTANT: Send me your Binomo ID for validation.

🔗 Registration link: {ENLACE_REFERIDO}
"""

# Recordatorios (ES)
MENSAJE_1H_ES = """📊 Recuerda que este camino no lo recorrerás sol@.
Tendrás acceso a cursos, señales y acompañamiento paso a paso.
Estoy aquí para ayudarte a lograr resultados reales en el trading. ¡Activa ya tu cuenta y empecemos!"""

MENSAJE_3H_ES = """📈 ¿Aún no te has registrado?
No dejes pasar esta oportunidad. Cada día que pasa es una nueva posibilidad de generar ingresos y adquirir habilidades reales.
✅ ¡Recuerda que solo necesitas 50 USD para comenzar con todo el respaldo!"""

MENSAJE_24H_ES = f"""🚀 Tu momento es ahora.
Tienes acceso a una comunidad, herramientas exclusivas y formación completa para despegar en el trading.
Da tu primer paso y asegúrate de enviarme tu ID de Binomo para recibir todos los beneficios.
🔗 Canal de resultados: {CANAL_RESULTADOS}"""

MENSAJE_48H_ES = f"""🚀 Han pasado 48 horas desde que iniciaste tu registro.
Aún estás a tiempo de activar tu cuenta y recibir todos los beneficios VIP.
Hazlo ahora con mi enlace y envíame tu ID de Binomo para validarlo ✅
🔗 Registro: {ENLACE_REFERIDO}"""

# Recordatorios (EN)
MENSAJE_1H_EN = """📊 Remember, you won’t walk this path alone.
You’ll get access to courses, signals, and step-by-step support.
I’m here to help you achieve real trading results. Activate your account and let’s begin!"""

MENSAJE_3H_EN = """📈 Haven’t registered yet?
Don’t miss this opportunity. Every day is a new chance to generate income and build real skills.
✅ Remember: you only need 50 USD to start with full support!"""

MENSAJE_24H_EN = f"""🚀 This is your moment.
You get access to a community, exclusive tools, and complete training to take off in trading.
Take the first step and be sure to send me your Binomo ID to receive all the benefits.
🔗 Results channel: {CANAL_RESULTADOS}"""

MENSAJE_48H_EN = f"""🚀 It’s been 48 hours since you started your registration.
You can still activate your account and unlock all VIP benefits.
Do it now using my link and send me your Binomo ID for validation ✅
🔗 Registration: {ENLACE_REFERIDO}"""

# Beneficios (ES/EN)
BENEFICIOS_ES = """✨ Beneficios Exclusivos que Recibirás ✨

✅ Acceso a cursos completos: Binarias, Forex e Índices Sintéticos, con certificación incluida.
✅ Clases grabadas y privadas: acceso de por vida, mentorías en vivo y acompañamiento constante.
✅ Material premium: guías, PDFs, audiolibros, tablas de plan de trading y gestión de riesgo.
✅ +200 señales diarias: de lunes a lunes, generadas con software propio de alta precisión.
✅ Bot de señales automático 24/7: opera en tiempo real sin que pierdas ninguna oportunidad.
✅ Señales de alto valor: CRYPTO IDX, pares de divisas, Forex, índices sintéticos, futuros y spot en Binance.
✅ Herramientas avanzadas: bot y plantillas listas para MT4 (Forex) y MT5 (Crash & Boom).
✅ Bonos y recompensas: sorteos, premios y beneficios adicionales para tu crecimiento.

⚡ Recuerda: la cantidad de beneficios puede variar según tu inversión personal. ⚡
"""

BENEFICIOS_EN = """✨ Exclusive Benefits You’ll Receive ✨

✅ Full courses included: Binary Options, Forex, and Synthetic Indices, with certification.
✅ Recorded & private classes: lifetime access, live mentoring, and ongoing support.
✅ Premium materials: guides, PDFs, audiobooks, trading plan tables, and risk management sheets.
✅ 200+ daily signals: from Monday to Sunday, generated with proprietary high-accuracy software.
✅ 24/7 auto signal bot: trade in real time so you never miss an opportunity.
✅ High-value signals: CRYPTO IDX, currency pairs, Forex, synthetic indices, futures, and Binance spot.
✅ Advanced tools: ready-to-use bots and templates for MT4 (Forex) and MT5 (Crash & Boom).
✅ Bonuses & rewards: raffles, prizes, and extra perks for your growth.

⚡ Note: the amount of benefits may vary depending on your personal investment. ⚡
"""

# === FUNCIONES DE MENSAJES PROGRAMADOS (usa lang por usuario) ===
async def _send_job_message(context: ContextTypes.DEFAULT_TYPE, text_es: str, text_en: str):
    chat_id, lang = context.job.data  # (chat_id, "es"/"en")
    try:
        await context.bot.send_message(chat_id=chat_id, text=text_es if lang == "es" else text_en)
    except Exception as e:
        logging.warning(f"Job send failed to {chat_id}: {e}")

async def mensaje_1h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message(context, MENSAJE_1H_ES, MENSAJE_1H_EN)

async def mensaje_3h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message(context, MENSAJE_3H_ES, MENSAJE_3H_EN)

async def mensaje_24h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message(context, MENSAJE_24H_ES, MENSAJE_24H_EN)

async def mensaje_48h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message(context, MENSAJE_48H_ES, MENSAJE_48H_EN)

# === UTIL: obtener/guardar idioma ===
def get_user_lang(chat_id: int) -> str:
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        return (u.lang if u and u.lang in ("es","en") else "es")

def set_user_lang(chat_id: int, name: str, lang: str):
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not u:
            u = Usuario(telegram_id=str(chat_id), nombre=name, lang=lang, fecha_registro=datetime.utcnow())
            session.add(u)
        else:
            u.lang = lang
        session.commit()

# === MENÚS POR IDIOMA ===
def build_main_menu(lang: str) -> InlineKeyboardMarkup:
    if lang == "en":
        kb = [
            [InlineKeyboardButton("🚀 Complete Registration", callback_data="registrarme")],
            [InlineKeyboardButton("✅ Validate your ID | Questions? DM me", url="https://t.me/Johaaletradervalidacion")],
            [InlineKeyboardButton("✅ I already have an account", callback_data="ya_tengo_cuenta")],
            [InlineKeyboardButton("🎁 VIP Benefits", callback_data="beneficios_vip")],
            [InlineKeyboardButton("📲 Channel in English", url=CANAL_EN)],
            [InlineKeyboardButton("📊 Results Channel", url=CANAL_RESULTADOS)],
            [InlineKeyboardButton("🌐 Social media", callback_data="redes_sociales")],
            [InlineKeyboardButton("🇪🇸 Cambiar a Español", callback_data="set_lang_es")],
        ]
    else:
        kb = [
            [InlineKeyboardButton("🚀 Completar registro", callback_data="registrarme")],
            [InlineKeyboardButton("✅ Valida tu ID | ¿Dudas? Escríbeme", url="https://t.me/Johaaletradervalidacion")],
            [InlineKeyboardButton("✅ Ya tengo cuenta", callback_data="ya_tengo_cuenta")],
            [InlineKeyboardButton("🎁 Beneficios VIP", callback_data="beneficios_vip")],
            [InlineKeyboardButton("📲 Canal en Español", url=CANAL_ES)],
            [InlineKeyboardButton("📊 Canal de resultados", url=CANAL_RESULTADOS)],
            [InlineKeyboardButton("🌐 Redes sociales", callback_data="redes_sociales")],
            [InlineKeyboardButton("🇺🇸 Switch to English", callback_data="set_lang_en")],
        ]
    return InlineKeyboardMarkup(kb)

def build_lang_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇪🇸 Español", callback_data="set_lang_es"),
         InlineKeyboardButton("🇺🇸 English", callback_data="set_lang_en")]
    ])

# === /start: primero elige idioma, luego bienvenida + menú por idioma; agenda jobs con lang ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    nombre = update.effective_user.full_name

    # Crear usuario si no existe (sin lang aún)
    with Session() as session:
        user = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not user:
            nuevo_usuario = Usuario(
                telegram_id=str(chat_id),
                nombre=nombre,
                fecha_registro=datetime.utcnow(),
                lang="es"
            )
            session.add(nuevo_usuario)
            session.commit()

    await update.message.reply_text("Elige tu idioma / Choose your language:", reply_markup=build_lang_picker())

    # Notificar admin
    user = update.effective_user
    mensaje_admin = f"🚨 El usuario @{user.username or 'SinUsername'} (ID: {user.id}) ejecutó /start (selección de idioma)."
    await context.bot.send_message(chat_id=ADMIN_ID, text=mensaje_admin)

# Enviar bienvenida y menú después de elegir idioma
async def send_welcome_and_menu(chat_id: int, lang: str, context: ContextTypes.DEFAULT_TYPE):
    # Bienvenida con imagen si existe
    try:
        with open(WELCOME_IMG, "rb") as img:
            await context.bot.send_photo(chat_id=chat_id, photo=InputFile(img),
                                         caption=(MENSAJE_BIENVENIDA_ES if lang=="es" else MENSAJE_BIENVENIDA_EN))
    except FileNotFoundError:
        await context.bot.send_message(chat_id=chat_id, text=(MENSAJE_BIENVENIDA_ES if lang=="es" else MENSAJE_BIENVENIDA_EN))

    # Menú
    await context.bot.send_message(chat_id=chat_id, text=("👇 Elige una opción para continuar:" if lang=="es" else "👇 Choose an option to continue:"),
                                   reply_markup=build_main_menu(lang))

    # Programar mensajes diferidos con lang
    if context.job_queue:
        context.job_queue.run_once(mensaje_1h, when=3600,  data=(chat_id, lang))
        context.job_queue.run_once(mensaje_3h, when=10800, data=(chat_id, lang))
        context.job_queue.run_once(mensaje_24h, when=86400, data=(chat_id, lang))
        context.job_queue.run_once(mensaje_48h, when=172800, data=(chat_id, lang))  # 48h
        logging.info(f"✅ Programado 1h, 3h, 24h y 48h para chat_id {chat_id} (lang={lang})")
    else:
        logging.warning("⚠️ Job queue no está disponible.")

# === BOTONES / CALLBACKS ===
async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    q = update.callback_query
    await q.answer()

    # Notificar interacción
    await notificar_interaccion(update, context)

    # Cambios de idioma
    if q.data == "set_lang_es":
        set_user_lang(chat_id, q.from_user.full_name, "es")
        await q.message.reply_text("✅ Idioma cambiado a Español.")
        await send_welcome_and_menu(chat_id, "es", context)
        return
    if q.data == "set_lang_en":
        set_user_lang(chat_id, q.from_user.full_name, "en")
        await q.message.reply_text("✅ Language switched to English.")
        await send_welcome_and_menu(chat_id, "en", context)
        return

    lang = get_user_lang(chat_id)

    if q.data == "registrarme":
        if lang == "es":
            await q.message.reply_text(MENSAJE_REGISTRARME_ES)
            # Video SOLO en español (no se copia en flujo inglés)
            await q.message.reply_video(
                video="BAACAgEAAxkBAAIBaGhdq0nQXi6B4N8uRwmaOHKkUarbAAIMBgACTgAB8UbIZIU9XTMCzjYE",
                caption="📹 Paso a paso en el vídeo"
            )
        else:
            await q.message.reply_text(MENSAJE_REGISTRARME_EN)

    elif q.data == "ya_tengo_cuenta":
        await q.message.reply_text(MENSAJE_YA_TENGO_CUENTA_ES if lang=="es" else MENSAJE_YA_TENGO_CUENTA_EN)

    elif q.data == "beneficios_vip":
        await q.message.reply_text(BENEFICIOS_ES if lang=="es" else BENEFICIOS_EN)

    elif q.data == "redes_sociales":
        if lang == "es":
            await q.message.reply_text("""🌐 Redes Sociales:

🔴 YouTube:
https://youtube.com/@johaalegria.trader?si=JemqmPes0Rz3WqEZ

🟣 Instagram:
https://www.instagram.com/johaale_trader?igsh=ZWI5dXNnaXN6aDNw

🎵 TikTok:
https://www.tiktok.com/@joha_binomo?_t=ZN-8xceLrp5GTe&_r=1

💬 Telegram:
https://t.me/JohaaleTraderTeams""")
        else:
            await q.message.reply_text("""🌐 Social Media:

🔴 YouTube:
https://youtube.com/@johaalegria.trader?si=JemqmPes0Rz3WqEZ

🟣 Instagram:
https://www.instagram.com/johaale_trader?igsh=ZWI5dXNnaXN6aDNw

🎵 TikTok:
https://www.tiktok.com/@joha_binomo?_t=ZN-8xceLrp5GTe&_r=1

💬 Telegram:
https://t.me/JohaaleTraderTeams""")

# === PERSISTENCIA MENSAJE DEL USUARIO ===
async def guardar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    texto = update.message.text or update.message.caption or ""
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

# === NOTIFICACIONES AL ADMIN ===
async def notificar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        usuario = update.message.from_user
        chat_id = usuario.id
        nombre = f"@{usuario.username}" if usuario.username else usuario.first_name
        lang = get_user_lang(chat_id)

        # Si es media, reenviamos media con caption incluyendo el ID para poder responder
        if update.message.photo:
            cap = update.message.caption or ""
            cap_final = f"📩 Foto de {nombre} (ID: {chat_id}) [lang={lang}]\n\n{cap}"
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=cap_final)
        elif update.message.video:
            cap = update.message.caption or ""
            cap_final = f"📩 Video de {nombre} (ID: {chat_id}) [lang={lang}]\n\n{cap}"
            await context.bot.send_video(chat_id=ADMIN_ID, video=update.message.video.file_id, caption=cap_final)
        elif update.message.audio:
            cap = update.message.caption or ""
            cap_final = f"📩 Audio de {nombre} (ID: {chat_id}) [lang={lang}]\n\n{cap}"
            await context.bot.send_audio(chat_id=ADMIN_ID, audio=update.message.audio.file_id, caption=cap_final)
        elif update.message.voice:
            cap_final = f"📩 Nota de voz de {nombre} (ID: {chat_id}) [lang={lang}]"
            await context.bot.send_voice(chat_id=ADMIN_ID, voice=update.message.voice.file_id, caption=cap_final)
        else:
            # Texto
            mensaje_usuario = update.message.text or ""
            texto = (
                f"📩 Nuevo mensaje de {nombre} (ID: {chat_id}) [lang={lang}]:\n\n"
                f"🗨️ {mensaje_usuario}\n\n"
                "✏️ Escribe tu respuesta a este mensaje (o usa audio) respondiendo a este mensaje…"
            )
            await context.bot.send_message(chat_id=ADMIN_ID, text=texto)

        # Botón para responder (solo cuando hay texto visible)
        botones = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Responder", callback_data="responder:{}:{}".format(chat_id, update.message.message_id))]
        ])
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text="Pulsa para responder al usuario:", reply_markup=botones)
        except:
            pass

    except Exception as e:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⚠️ Error notificando al admin: {e}"
        )

# Notificación de interacción con botones
async def notificar_interaccion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        usuario = query.from_user
        chat_id = usuario.id
        nombre = f"@{usuario.username}" if usuario.username else usuario.full_name
        data = query.data
        lang = get_user_lang(chat_id)

        texto = (
            f"⚡ El usuario {nombre} (ID: {chat_id}) [lang={lang}] tocó un botón:\n"
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

# === RESPUESTA DEL ADMIN (texto/audio) ===
async def responder_a_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Puede ser respuesta a un mensaje del admin que contenía texto o media con caption
    if update.message.reply_to_message:
        base_text = (
            update.message.reply_to_message.text_html_urled
            or update.message.reply_to_message.text
            or update.message.reply_to_message.caption
            or ""
        )
        chat_id_match = re.search(r'ID:\s*(\d+)', base_text)
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

# Nueva función para manejar mensajes de usuarios (texto o media)
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

    # Comando /start (selector de idioma)
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

    # Botones generales (incluye set_lang_es / set_lang_en / registrarme / etc.)
    app.add_handler(CallbackQueryHandler(botones))

    # Mensajes del admin (responder a usuarios con texto o audio deslizando)
    app.add_handler(MessageHandler((filters.TEXT | filters.VOICE) & filters.User(ADMIN_ID), responder_a_usuario))

    # Mensajes normales de los usuarios (texto o media)
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE) & ~filters.COMMAND & ~filters.User(ADMIN_ID), manejar_mensaje))

    logging.info("Bot corriendo…")
    app.run_polling()
