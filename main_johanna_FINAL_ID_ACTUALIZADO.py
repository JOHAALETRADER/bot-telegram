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

import unicodedata
import html
import urllib.parse

try:
    import httpx
    HAS_HTTPX = True
except Exception:
    HAS_HTTPX = False

ADMIN_ID = 5924691120  # Tu ID personal de Telegram


async def send_admin_auto_log(context: ContextTypes.DEFAULT_TYPE, update: Update, intent: str, respuesta: str):
    """Envía al ADMIN la pregunta + la respuesta exacta (texto plano, sin Markdown)."""
    try:
        chat_id = update.effective_chat.id
        u = update.effective_user
        username = u.username or u.full_name or "usuario"
        msg = update.effective_message
        pregunta = ((getattr(msg, "text", None) or getattr(msg, "caption", None) or "").strip() or "(sin texto)")
        text = (
            "🤖 RESPUESTA AUTOMÁTICA\n"
            f"Usuario: @{username} | ID: {chat_id}\n"
            f"Intento: {intent}\n\n"
            "Pregunta:\n"
            f"{pregunta}\n\n"
            "Respuesta:\n"
            f"{respuesta}"
        )
        if len(text) > 3900:
            text = text[:3900] + "\n\n...(recortado)"
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, disable_web_page_preview=True)
    except Exception as e:
        logging.info("No pude enviar log de auto-respuesta: %s", e)





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
    # Idioma preferido ("es" / "en")
    lang           = Column(String, default="es")
    # Etapa del usuario: PRE (sin validar), POST (validado, esperando depósito), DEPOSITED
    stage          = Column(String, default="PRE")

engine = create_engine(DATABASE_URL, echo=False)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# --- Migración robusta de la columna lang (sin acceso manual a SQL) ---
try:
    backend = engine.url.get_backend_name()
    if backend.startswith("postgres"):
        # Postgres: crear columna si no existe usando bloque DO
        with engine.begin() as conn:
            conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='usuarios' AND column_name='lang'
                ) THEN
                    ALTER TABLE usuarios ADD COLUMN lang VARCHAR;
                END IF;
            END $$;
            """))
    elif backend == "sqlite":
        # SQLite: comprobar PRAGMA y añadir si falta
        with engine.begin() as conn:
            cols = conn.execute(text("PRAGMA table_info(usuarios)")).fetchall()
            if not any(c[1] == "lang" for c in cols):
                conn.execute(text("ALTER TABLE usuarios ADD COLUMN lang TEXT"))
except Exception as e:
    logging.warning("No se pudo verificar/crear columna 'lang': %s", e)


# --- Migración robusta de la columna stage (sin acceso manual a SQL) ---
try:
    backend = engine.url.get_backend_name()
    if backend.startswith("postgres"):
        with engine.begin() as conn:
            conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='usuarios' AND column_name='stage'
                ) THEN
                    ALTER TABLE usuarios ADD COLUMN stage VARCHAR;
                END IF;
            END $$;
            """))
    elif backend == "sqlite":
        with engine.begin() as conn:
            cols = conn.execute(text("PRAGMA table_info(usuarios)")).fetchall()
            if not any(c[1] == "stage" for c in cols):
                conn.execute(text("ALTER TABLE usuarios ADD COLUMN stage TEXT"))
except Exception as e:
    logging.warning("No se pudo verificar/crear columna 'stage': %s", e)
# --- fin migración stage ---

# --- fin migración ---

# === ENLACES ===
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
CANAL_ES = "https://t.me/JohaaleTrader_es"
CANAL_EN = "https://t.me/JohaaleTrader_en"
ENLACE_REFERIDO  = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"


# Chat personal / validación (URL del botón de soporte)
SUPPORT_URL = "https://t.me/Johaaletradervalidacion"

# Mensajes gatillo exactos (los que tú envías cuando validas manualmente)
GATILLO_ID_OK = "Tu ID es correcto puedes depositar en tu cuenta de trading Binomo a partir de 50 USD.\n\nCuando tú deposito este listo escríbeme para darte acceso"
GATILLO_ID_ERRADO = "Tu ID está errado.\n\nPara tener acceso a mi comunidad vip y todas las herramientas debes realizar tu registro con mi enlace..\n\nCopia y pega el enlace de registro en barra de búsqueda de una ventana de incógnito de tu navegador y usa otro correo.. luego me envías ID de binomo para validar.\n\nEnlace de registro:\n\nhttps://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"

# Mensajes Serie B (post-validación ES) — mismos tiempos (1h, 3h, 24h, 48h)
MENSAJE_B_1H_ES = "✅ Tu ID ya quedó validado.\n\n¿Ya activaste o depositaste en tu cuenta de Binomo?\nResponde: **Ya deposité** cuando esté listo 👇"
MENSAJE_B_3H_ES = "💰 Tip rápido: si tienes disponible el bono del **100%**, úsalo para potenciar tu primer depósito.\n\nCuando tu depósito esté listo, escríbeme **Ya deposité** y te habilito el acceso 👇"
MENSAJE_B_24H_ES = "🚀 Recuerda: para habilitar tu acceso VIP necesito confirmar tu **depósito/activación**.\n\nCuando esté listo, dime **Ya deposité** y lo activamos. Quedan cupos limitados ✅"
MENSAJE_B_48H_ES = "⏳ Último recordatorio: si ya activaste tu cuenta con depósito, escríbeme **Ya deposité** para habilitar tu acceso VIP gratuita.\n\nSi aún no, activa tu cuenta cuando puedas y me avisas ✅"

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


# === UTIL: obtener/guardar etapa (stage) ===
STAGE_PRE = "PRE"
STAGE_POST = "POST"
STAGE_DEPOSITED = "DEPOSITED"

def get_user_stage(chat_id: int) -> str:
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        s = (u.stage if u and u.stage else STAGE_PRE)
        return s if s in (STAGE_PRE, STAGE_POST, STAGE_DEPOSITED) else STAGE_PRE

def set_user_stage(chat_id: int, stage: str):
    if stage not in (STAGE_PRE, STAGE_POST, STAGE_DEPOSITED):
        stage = STAGE_PRE
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if u:
            u.stage = stage
            session.commit()

# === Teclado de soporte (solo para el flujo nuevo / redirecciones) ===
def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("💬 Escríbeme aquí", url=SUPPORT_URL)]])

# === Jobs nombrados para poder cancelar (Serie A y Serie B) ===
def _cancel_jobs_prefix(context: ContextTypes.DEFAULT_TYPE, prefix: str, chat_id: int):
    if not context.job_queue:
        return
    for suf in ("1h","3h","24h","48h"):
        name = f"{prefix}_{suf}_{chat_id}"
        try:
            for j in context.job_queue.get_jobs_by_name(name):
                j.schedule_removal()
        except Exception:
            pass

def schedule_series_a(chat_id: int, lang: str, context: ContextTypes.DEFAULT_TYPE):
    if not context.job_queue:
        return
    _cancel_jobs_prefix(context, "A", chat_id)
    context.job_queue.run_once(mensaje_1h, when=3600,  data=(chat_id, lang), name=f"A_1h_{chat_id}")
    context.job_queue.run_once(mensaje_3h, when=10800, data=(chat_id, lang), name=f"A_3h_{chat_id}")
    context.job_queue.run_once(mensaje_24h, when=86400, data=(chat_id, lang), name=f"A_24h_{chat_id}")
    context.job_queue.run_once(mensaje_48h, when=172800, data=(chat_id, lang), name=f"A_48h_{chat_id}")
    logging.info("✅ Serie A programada para chat_id %s (lang=%s)", chat_id, lang)

async def _send_job_message_B(context: ContextTypes.DEFAULT_TYPE, text_es: str):
    chat_id, _lang = context.job.data
    try:
        await context.bot.send_message(chat_id=chat_id, text=text_es, reply_markup=support_keyboard(), )
    except Exception as e:
        logging.warning("Job B send failed to %s: %s", chat_id, e)

async def mensaje_B_1h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message_B(context, MENSAJE_B_1H_ES)

async def mensaje_B_3h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message_B(context, MENSAJE_B_3H_ES)

async def mensaje_B_24h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message_B(context, MENSAJE_B_24H_ES)

async def mensaje_B_48h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message_B(context, MENSAJE_B_48H_ES)

def schedule_series_b(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    if not context.job_queue:
        return
    _cancel_jobs_prefix(context, "B", chat_id)
    context.job_queue.run_once(mensaje_B_1h, when=3600,  data=(chat_id, "es"), name=f"B_1h_{chat_id}")
    context.job_queue.run_once(mensaje_B_3h, when=10800, data=(chat_id, "es"), name=f"B_3h_{chat_id}")
    context.job_queue.run_once(mensaje_B_24h, when=86400, data=(chat_id, "es"), name=f"B_24h_{chat_id}")
    context.job_queue.run_once(mensaje_B_48h, when=172800, data=(chat_id, "es"), name=f"B_48h_{chat_id}")
    logging.info("✅ Serie B (post-validación ES) programada para chat_id %s", chat_id)


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

    # Crear usuario si no existe (lang por defecto "es" hasta que elija)
    with Session() as session:
        user = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not user:
            nuevo_usuario = Usuario(
                telegram_id=str(chat_id),
                nombre=nombre,
                fecha_registro=datetime.utcnow(),
                lang="es",
                stage="PRE"
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
    await context.bot.send_message(
        chat_id=chat_id,
        text=("👇 Elige una opción para continuar:" if lang=="es" else "👇 Choose an option to continue:"),
        reply_markup=build_main_menu(lang)
    )

    # Programar mensajes diferidos con lang (Serie A) — con nombres para evitar duplicados
    schedule_series_a(chat_id, lang, context)

# === BOTONES / CALLBACKS ===
async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    q = update.callback_query
    await q.answer()

    # Notificar interacción
    await notificar_interaccion(update, context)

    # --- Acciones para imagen (ID vs depósito) ---
    if q.data and q.data.startswith("IMG_IS_ID|"):
        msg = (
            "Perfecto ✅\n"
            "Para poder validarlo necesito que me envíes el **ID en texto** (solo el número).\n"
            "📌 Ábrelo en Binomo, cópialo y pégalo aquí.\n\n"
            "Si prefieres, también puedes escribirme al chat personal 👇"
        )
        await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "IMG_IS_ID", msg)
        return

    if q.data and q.data.startswith("IMG_IS_DEP|"):
        msg = "Recibido. ¿Esto es tu comprobante de depósito/activación?"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Sí, ya deposité", callback_data=f"DEP_YES|{chat_id}"),
            InlineKeyboardButton("❌ No, era otra cosa", callback_data=f"DEP_NO|{chat_id}"),
        ]])
        await q.message.reply_text(msg, reply_markup=kb)
        await send_admin_auto_log(context, update, "IMG_IS_DEP", msg)
        return

    if q.data and q.data.startswith("IMG_IS_OTHER|"):
        msg = (
            "Listo ✅\n"
            "Dime qué necesitas exactamente (bono, retiros, ID o horarios).\n"
            "O escríbeme al chat personal y lo revisamos en 1 minuto 👇"
        )
        await q.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "IMG_IS_OTHER", msg)
        return
    # --- NUEVO: respuestas para botones DEP_YES / DEP_NO ---
    if q.data and q.data.startswith("DEP_YES|"):
        msg = (
            "Perfecto ✅\n"
            "Para confirmar, envíame tu comprobante (captura) o dime la hora aproximada del depósito.\n"
            "Si prefieres, escríbeme al chat personal y lo revisamos 👇"
        )
        await q.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "DEP_YES_CONFIRM", msg)
        return

    if q.data and q.data.startswith("DEP_NO|"):
        msg = "Perfecto, cuéntame en texto qué necesitas revisar 👇"
        await q.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "DEP_NO_OTHER", msg)
        return



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
            # Video SOLO en español
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

    stage = get_user_stage(chat_id)
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

                # --- NUEVO: detectar mensaje gatillo y cambiar flujo ---
                try:
                    txt = (update.message.text or "").strip()
                    if is_gatillo_ok(txt):
                        set_user_stage(destinatario_id, STAGE_POST)
                        # Cancelar Serie A y activar Serie B
                        _cancel_jobs_prefix(context, "A", destinatario_id)
                        schedule_series_b(destinatario_id, context)
                        await context.bot.send_message(chat_id=ADMIN_ID, text=f"✅ Gatillo OK detectado. Serie B activada para {destinatario_id}")
                    elif is_gatillo_err(txt):
                        set_user_stage(destinatario_id, STAGE_PRE)
                        # Mantener/renovar Serie A
                        schedule_series_a(destinatario_id, get_user_lang(destinatario_id), context)
                        await context.bot.send_message(chat_id=ADMIN_ID, text="[GATILLO_ID_ERRADO] user_id={}".format(destinatario_id))
                except Exception as _e:
                    pass
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



# === IA / FAQ (ES) ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

LIVE_HORARIOS_ES = (
    "📅 **Horarios de mis lives (hora Colombia):**\n"
    "• **Martes:** 11:00 am y 8:00 pm\n"
    "• **Miércoles:** 8:00 pm\n"
    "• **Jueves:** 11:00 am y 8:00 pm\n"
    "• **Viernes:** 8:00 pm\n"
    "• **Sábados:** 11:00 am y 8:00 pm\n"
)

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s

def _norm_ws(s: str) -> str:
    # Normaliza: minúsculas, sin tildes, espacios colapsados
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def is_gatillo_ok(texto: str) -> bool:
    t = _norm_ws(texto)
    base = _norm_ws(GATILLO_ID_OK)
    return (t == base) or (base and t.startswith(base[:40]))

def is_gatillo_err(texto: str) -> bool:
    t = _norm_ws(texto)
    base = _norm_ws(GATILLO_ID_ERRADO)
    return (t == base) or (base and t.startswith(base[:35]))



def detect_intent_es(texto: str) -> str:
    t = _norm(texto)

    # Saludo simple (para responder corto). Si viene con pregunta, no entra aquí.
    if ("?" not in t) and re.fullmatch(r"(?:(hola|buenas|buenos dias|buenas tardes|buenas noches|hey|holi|hello|hi)\s*)+(?:joha|johabot|johanna)?\W*", t):
        return "GREETING"

    # Conversación humana (no responder automáticamente)
        if any(k in t for k in [
        "queria consultar", "quería consultar", "tengo una duda", "tengo dudas", "no entiendo",
        "quiero consultar", "consulta", "necesito ayuda", "puedo preguntar",
        "señales que no entiendo", "las señales", "sobre las señales"
    ]):
        return "HUMAN_CHAT"

    # Depósito luego / más tarde / esperar pago
    if any(k in t for k in [
        "deposito despues", "deposito después", "deposita despues", "deposita después",
        "no puedo depositar", "no puedo ahora", "ahora no", "no tengo dinero ahora",
        "cuando me paguen", "mas adelante", "más adelante", "luego deposito",
        "despues deposito", "después deposito", "otro dia deposito", "otro día deposito",
        "mas tarde", "más tarde", "todavia no puedo", "todavía no puedo",
        "puedo depositar despues",
        "puedo depositar después",
        "puedo depositar mas tarde",
        "puedo depositar más tarde",
        "estoy esperando un pago",
        "esperando un pago",
        "espero un pago",
        "cuando cobre",
        "cuando reciba",
        "cuando me llegue el pago",
        "cuando me llegue dinero",
        "no tengo dinero",
        "ahorita no puedo",
        "por ahora no puedo",
        "en este momento no puedo"
    ]):
        return "DEP_LATER"

    # Mínimo 50 / con menos / iniciar con menos
    if any(k in t for k in ["no tengo 50", "sin 50", "con menos", "menos de 50", "puedo con menos", "iniciar con menos", "empezar con menos"]):
        return "MIN50"

    # Números < 50 con contexto (depósito / inicio / ingreso)
    nums = [int(x) for x in re.findall(r"\b\d{1,3}\b", t)]
    if nums:
        mn = min(nums)
        if mn < 50 and any(k in t for k in ["deposit", "dolar", "usd", "empez", "inici", "entrar", "vip", "comunidad"]):
            return "MIN50"

    # Siguiente paso / qué sigue
    if any(k in t for k in [
        "que sigue", "qué sigue", "que paso sigue", "qué paso sigue", "paso sigue",
        "y ahora que", "y ahora qué", "entonces que sigue", "entonces qué sigue",
        "ok gracias entonces", "ok gracias", "ya me registre que hago", "ya me registré que hago",
        "que hago ahora", "qué hago ahora", "siguiente paso"
    ]):
        return "NEXT_STEP"

    # Dónde enviar el ID / te envío el ID
    if ("id" in t) and any(k in t for k in [
        "te envio", "te envío", "envio", "envío", "enviar", "mando", "te mando",
        "por donde", "por dónde", "a donde", "a dónde", "donde te", "dónde te",
        "por aca", "por acá", "por aqui", "por aquí"
    ]):
        return "WHERE_SEND_ID"

    if any(k in t for k in ["vpn", "proxy"]):
        return "VPN"
    if ("error" in t and ("pais" in t or "país" in t or "country" in t)) or ("me sale" in t and "pais" in t):
        return "PAIS"

    # Live / conexión / transmisión
    if any(k in t for k in [
        "horario", "horarios", "live", "en vivo", "directo",
        "transmision", "transmisión", "stream", "transmitir",
        "a que hora te conectas", "a qué hora te conectas", "te conectas", "conectas",
        "conexion", "conexión", "a que hora haces live", "a qué hora haces live",
        "a que hora estas en vivo", "a qué hora estás en vivo"
    ]):
        return "LIVE"

    if any(k in t for k in ["bono", "bonus", "100%"]):
        return "BONO"

    if "id" in t and any(k in t for k in ["donde", "como", "encuentro", "ver", "buscar", "ubico", "aparece"]):
        return "ID"

    if any(k in t for k in ["retiro", "retirar", "withdraw", "rechaz", "rechazo", "deneg", "no me deja retirar", "no me deja"]):
        return "RETIRO"

    if any(k in t for k in ["metodo", "metodos", "banco", "cuenta bancaria", "colombia", "astropay", "nequi", "transfiya"]):
        return "METODOS"

    if any(k in t for k in ["no me llega el correo", "no llega el correo", "no me llega email", "correo", "email"]):
        return "EMAIL"

    if any(k in t for k in ["ya deposite", "ya deposité", "ya hice el deposito", "ya hice el depósito", "ya active", "ya activé", "ya depositado", "ya quedo el deposito", "ya quedó el depósito", "ya me llego el deposito", "ya me llegó el depósito", "ya te llego el deposito", "te llego el deposito", "te llegó el depósito", "deposito listo", "depósito listo", "deposito realizado", "depósito realizado", "comprobante de deposito", "comprobante de depósito", "prueba de deposito", "prueba de depósito", "deposito para acceso", "depósito para acceso", "habilitar acceso", "acceso al vip", "acceso vip"]):
        return "DEPOSITO"


    if re.search(r"\b\d{6,}\b", t) and ("id" in t or t.strip().isdigit()):
        return "ID_SUBMIT"

    return "OTRO"

def respuesta_bono_es() -> str:
    return (
        "💰 **¿Cómo funciona el bono en Binomo?**\n\n"
        "El bono es **opcional** y puede aparecer al momento de depositar. "
        "Si lo activas, Binomo te añade un porcentaje extra para operar con más capital.\n\n"
        "📌 Ojo: los bonos suelen tener **condiciones** antes de poder retirar (por ejemplo, volumen mínimo). "
        "Las reglas exactas varían según tu cuenta y promo activa.\n\n"
        "Si quieres, escríbeme y lo revisamos según tu caso 👇"
    )

def respuesta_id_es() -> str:
    return (
        "🆔 **¿Dónde encuentro mi ID de Binomo?**\n\n"
        "1) Entra a tu cuenta (app o web).\n"
        "2) Ve a tu **perfil / ajustes** (icono de usuario).\n"
        "3) Busca el campo **ID** o **User ID** y cópialo.\n\n"
        "Si no lo ves, dime si estás en app o navegador y te guío 👇"
    )


def respuesta_next_step_es() -> str:
    return (
        "✅ Perfecto. El **siguiente paso** es validar tu **ID** para confirmar que tu registro quedó bien "
        "**antes de que deposites**.\n\n"
        "📌 Envíame aquí tu **ID de Binomo** (solo el número) y lo dejo en validación.\n\n"
        "Si prefieres, también puedes escribirme al chat personal 👇"
    )

def respuesta_where_send_id_es() -> str:
    return (
        "Sí ✅ Puedes enviarme tu **ID por aquí mismo** (solo el número) y lo dejo en validación.\n\n"
        "Si prefieres hacerlo directo conmigo, también puedes escribirme al chat personal 👇"
    )

def fallback_johabot_es() -> str:
    return (
        "Gracias por escribir 🙌\n\n"
        "Para ayudarte bien, prefiero revisarlo contigo directamente.\n"
        "Escríbeme aquí 👇"
    )
async def binomo_helpcenter_snippets(query: str, max_results: int = 3) -> str:
    if not HAS_HTTPX:
        return ""
    try:
        q = urllib.parse.quote(query)
        url = f"https://binomo2.zendesk.com/api/v2/help_center/articles/search.json?query={q}&locale=es-419"
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return ""
        data = r.json()
        results = data.get("results") or []
        if not results:
            return ""
        chunks = []
        for item in results[:max_results]:
            title = item.get("title") or ""
            body = item.get("body") or ""
            body = html.unescape(body)
            body = re.sub(r"<[^>]+>", " ", body)
            body = re.sub(r"\s+", " ", body).strip()[:900]
            link = item.get("html_url") or ""
            chunks.append(f"TITULO: {title}\nCONTENIDO: {body}\nFUENTE: {link}".strip())
        return "\n\n---\n\n".join(chunks)
    except Exception:
        return ""

async def openai_answer_es(question: str, context_text: str) -> str:
    if not (HAS_HTTPX and OPENAI_API_KEY):
        return ""
    try:
        system = (
            "Eres un asistente de soporte para usuarios de Binomo en español. "
            "Responde en 6–10 líneas, claro y directo. "
            "NO inventes información. Si algo depende del país, método de pago o datos de la cuenta, dilo. "
            "NO des instrucciones para evadir restricciones (VPN/proxy). "
            "Si el contexto no alcanza, responde exactamente con: NO_DATA"
        )
        payload = {
            "model": OPENAI_MODEL,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"PREGUNTA: {question}\n\nCONTEXTO:\n{context_text}"}
            ],
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=18) as client:
            resp = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + OPENAI_API_KEY},
                json=payload,
            )
        if resp.status_code != 200:
            return ""
        out = resp.json()
        texts = []
        for item in out.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    texts.append(c.get("text", ""))
        ans = "\n".join([t for t in texts if t]).strip()
        if (not ans) or ("NO_DATA" in ans):
            return ""
        return ans
    except Exception:
        return ""

# Nueva función para manejar mensajes de usuarios (texto o media)
async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Guardar y notificar al admin primero (así ves la pregunta antes del auto-reply)
    await guardar_mensaje(update, context)
    await notificar_admin(update, context)

    chat_id = update.effective_chat.id
    lang = get_user_lang(chat_id)
    if lang != "es":
        return  # IA solo español por ahora

    stage = get_user_stage(chat_id)

    # --- PRECHECK: si llega una imagen, NO llamamos IA. Primero preguntamos si es ID o depósito ---
    if update.message and update.message.photo:
        if stage not in (STAGE_POST, STAGE_DEPOSITED):
            qtxt = "📩 Recibido. ¿Esta imagen es tu **ID** de Binomo o un **comprobante de depósito/activación**?"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📌 Es mi ID", callback_data=f"IMG_IS_ID|{chat_id}"),
                 InlineKeyboardButton("💳 Es depósito", callback_data=f"IMG_IS_DEP|{chat_id}")],
                [InlineKeyboardButton("❌ Era otra cosa", callback_data=f"IMG_IS_OTHER|{chat_id}")]
            ])
            await update.message.reply_text(qtxt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            await send_admin_auto_log(context, update, "IMG_PRECHECK", qtxt)
            return

    texto = update.message.text or update.message.caption or ""
    intent = detect_intent_es(texto)

        # Si el usuario solo está enviando su ID, confirmamos recibido (validación manual)
    if intent == "ID_SUBMIT":
        respuesta_id_submit = (
            "✅ **Recibido.** Ya tengo tu ID.\n"
            "Lo dejo en **validación** y en breve te confirmo si está correcto.\n"
            "Mientras tanto, si quieres adelantar el proceso, escríbeme aquí 👇"
        )
        await update.message.reply_text(
            respuesta_id_submit,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=support_keyboard()
        )
        await send_admin_auto_log(context, update, "ID_SUBMIT", respuesta_id_submit)
        return


    # --- NUEVO: conversación humana -> NO responder (evitar respuestas genéricas) ---
    if intent == "HUMAN_CHAT":
        return

    # --- NUEVO: saludo simple -> responder corto ---
    if intent == "GREETING":
        msg = "Hola 👋 ¿En qué puedo ayudarte hoy?"
        await update.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "GREETING", msg)
        return

    # --- NUEVO: depósito después / más tarde ---
    if intent == "DEP_LATER":
        msg = "Está perfecto 😊\nCuando realices tu depósito de 50 USD o más, escríbeme y lo revisamos para darte acceso 👇"
        await update.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_DEPOSIT_LATER", msg)
        return

    # --- NUEVO: mínimo 50 USD (regla fija) ---
    if intent == "MIN50":
        msg = "Para ingresar a mi comunidad VIP gratuita y acceder a todas las herramientas, el depósito mínimo es de 50 USD.\nCuando tengas 50 USD o más, escríbeme y lo revisamos 👇"
        await update.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_MIN50", msg)
        return

    in_validation_flow = stage in (STAGE_POST, STAGE_DEPOSITED)

    # VPN o error país -> directo a chat personal
    if intent in ("VPN", "PAIS"):
        msg = "Para temas de VPN / error de país prefiero revisarlo contigo directo 🤍\n\nToca el botón 👇"
        await update.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, intent, msg)
        return


    # Qué sigue / siguiente paso
    if intent == "NEXT_STEP":
        msg = respuesta_next_step_es()
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, intent, msg)
        return

    # Dónde enviar el ID
    if intent == "WHERE_SEND_ID":
        msg = respuesta_where_send_id_es()
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, intent, msg)
        return

    # Lives
    if intent == "LIVE":
        await update.message.reply_text(LIVE_HORARIOS_ES, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "LIVE", LIVE_HORARIOS_ES)
        return

    # Bono
    if intent == "BONO":
        await update.message.reply_text(respuesta_bono_es(), parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "BONO", respuesta_bono_es())
        return

    # Dónde ver ID
    if intent == "ID":
        await update.message.reply_text(respuesta_id_es(), parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "ID", respuesta_id_es())
        return

    # Ya depositó / depósito listo (respuesta pro + CTA, sin IA)
if intent == "DEPOSITO":
    if stage != STAGE_DEPOSITED:
        # si ya estaba en POST, marcamos depositado y cancelamos campañas
        if stage == STAGE_POST:
            set_user_stage(chat_id, STAGE_DEPOSITED)
            _cancel_jobs_prefix(context, "B", chat_id)
            _cancel_jobs_prefix(context, "A", chat_id)

        msg = (
            "Perfecto ✅\n\n"
            "Para habilitar tu acceso, envíame aquí el comprobante del depósito/activación "
            "o escríbeme al chat personal 👇"
        )
        await update.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_DEPOSIT_CONFIRM", msg)
    return

# Captura sin texto durante POST: confirmación
    if stage == STAGE_POST and update.message.photo and not (update.message.caption or "").strip():
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Sí, ya deposité", callback_data=f"dep_yes:{chat_id}")],
            [InlineKeyboardButton("❌ No, era otra cosa", callback_data=f"dep_no:{chat_id}")]
        ])
        await update.message.reply_text("📩 Recibido. ¿Esto es tu comprobante de depósito/activación?", reply_markup=kb)
        return

    # En validación: no IA externa
    if in_validation_flow:
        await update.message.reply_text(fallback_johabot_es(), parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        return

    # PRE: intent de retiro/metodos/email/otro -> HelpCenter + OpenAI (si hay key)
    q = (texto.strip()[:200] if texto else "Binomo ayuda")
    snippets = await binomo_helpcenter_snippets(q)
    ans = ""
    if snippets:
        ans = await openai_answer_es(texto or q, snippets)
    if not ans:
        ans = fallback_johabot_es()

    await update.message.reply_text(ans, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
    await send_admin_auto_log(context, update, "AI_ANSWER", ans)

# Función para enviar texto/imagen/video al usuario, desde caption con /enviar
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

    # Callback de comprobante depósito (Serie B)
    app.add_handler(CallbackQueryHandler(manejar_callback, pattern="^dep_"))

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
