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
ENLACE_STOCKITY  = "https://stockity-r3.com/?a=95604cd745da&t=0&ac=JOHAALETRADER"


# Chat personal / validación (URL del botón de soporte)
SUPPORT_URL = "https://t.me/Johaaletradervalidacion"

# Mensajes gatillo exactos (los que tú envías cuando validas manualmente)
GATILLO_ID_OK = """ID validado correctamente. ✅

Para activar tu acceso necesitas:

1️⃣ Depósito en tu cuenta de trading según el nivel elegido.
2️⃣ Activación de tu membresía correspondiente.

Cuando tengas tu depósito listo, escríbeme directamente y te guío para activar tu membresía y acceso."""

GATILLO_ID_OK_EN = """ID successfully validated. ✅

To activate your access you need:

1️⃣ Deposit into your trading account according to your chosen level.
2️⃣ Activation of your corresponding membership.

Once your deposit is ready, message me directly and I’ll guide you to activate your membership and access."""

GATILLO_ACCESO_OK = "confirmo cuenta activa"
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

MENSAJE_REGISTRARME_ES = f"""Es muy sencillo. Solo debes abrir tu cuenta de trading en Binomo o Stockity con uno de estos enlaces:

🔗 Binomo:
{ENLACE_REFERIDO}

🔗 Stockity:
{ENLACE_STOCKITY}

👉 Luego de crear la cuenta es necesario y súper importante que me envíes tu ID de Binomo o Stockity para validar tu registro antes de que realices un depósito en tu cuenta de trading.

💰 Depósito según nivel elegido 

IMPORTANTE: LA CANTIDAD DE BENEFICIOS VARÍA SEGÚN TU DEPÓSITO.
 
¡Te espero!"""

MENSAJE_REGISTRARME_EN = f"""It’s super simple. Open your trading account on Binomo or Stockity using one of these links:

🔗 Binomo:
{ENLACE_REFERIDO}

🔗 Stockity:
{ENLACE_STOCKITY}

👉 After creating the account, it’s very important that you send me your Binomo or Stockity ID so I can validate your registration **before** you make any deposit.

💰 Minimum deposit according to the chosen level

IMPORTANT: The amount of benefits varies depending on your deposit.

I’ll be waiting for you!"""

MENSAJE_YA_TENGO_CUENTA_ES = f"""Para tener acceso a mi comunidad VIP y todas las herramientas debes realizar tu registro con mi enlace.

¿Qué debes hacer? 👉 Si creaste tu cuenta con mi enlace envíame tu ID de Binomo o Stockity en el botón de arriba.

🟡 Si no lo hiciste con mi enlace, haz lo siguiente:

1️⃣ Copia y pega el enlace de registro en una ventana de incógnito o activa una VPN para cambiar tu IP. Luego inicia sesión normal.

2️⃣ Usa un correo que NO hayas usado en Binomo o Stockity y regístrate de forma manual.

3️⃣ ❗️SUPER IMPORTANTE: Envíame tu ID de Binomo o Stockity para validar.

🔗 Enlace de registro Binomo: {ENLACE_REFERIDO}

🔗 Enlace de registro Stockity: {ENLACE_STOCKITY}
"""

MENSAJE_YA_TENGO_CUENTA_EN = f"""To access my VIP community and all tools, you must register with my link.

What to do? 👉 If you created your account with my link, send me your Binomo or Stockity ID using the button above.

🟡 If you didn’t use my link, do this:

1️⃣ Copy and paste the registration link in an incognito window or turn on a VPN to change your IP. Then log in normally.

2️⃣ Use an email you have NOT used on Binomo or Stockity and register manually.

3️⃣ ❗️VERY IMPORTANT: Send me your Binomo or Stockity ID for validation.

🔗 Binomo registration link: {ENLACE_REFERIDO}

🔗 Stockity registration link: {ENLACE_STOCKITY}
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
Da tu primer paso y asegúrate de enviarme tu ID de Binomo o Stockity para recibir todos los beneficios.
🔗 Canal de resultados: {CANAL_RESULTADOS}"""

MENSAJE_48H_ES = f"""🚀 Han pasado 48 horas desde que iniciaste tu registro.
Aún estás a tiempo de activar tu cuenta y recibir todos los beneficios VIP.
Hazlo ahora con mi enlace y envíame tu ID de Binomo o Stockity para validarlo ✅
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
Take the first step and be sure to send me your Binomo or Stockity ID to receive all the benefits.
🔗 Results channel: {CANAL_RESULTADOS}"""

MENSAJE_48H_EN = f"""🚀 It’s been 48 hours since you started your registration.
You can still activate your account and unlock all VIP benefits.
Do it now using my link and send me your Binomo or Stockity ID for validation ✅
🔗 Registration: {ENLACE_REFERIDO}"""

# Beneficios (ES/EN)
BENEFICIOS_ES = """✨ Beneficios Exclusivos que Recibirás ✨

✅ Formación certificada: Binarias, Forex, Índices Sintéticos y enfoque Multi-Broker.
✅ Material premium de estudio: guías, PDFs, estrategias exitosas, planes de trading, gestión de riesgo e interés compuesto.
✅ Mentorías y operativas en vivo: acompañamiento constante y clases grabadas.
✅ +200 señales diarias de alta precisión: Divisas, CRYPTO IDX, Forex, índices sintéticos, futuros y spot en Binance.
✅ Bots automáticos 24/7: no pierdas oportunidades incluso cuando no estés conectado.
✅ Forex Automatizado: sistemas listos para ejecutar con configuración profesional.
✅ Preparación para Cuentas de Fondeo: estructura, disciplina y plan real de escalamiento.
✅ Herramientas avanzadas para MT4 y MT5.
✅ Bonos y beneficios adicionales según nivel.

⚡️ Los beneficios pueden variar según el nivel e inversión elegida. ⚡️
"""

BENEFICIOS_EN = """✨ Exclusive Benefits You’ll Receive ✨

✅ Certified Training: Binary Options, Forex, Synthetic Indices and a Multi-Broker approach.
✅ Premium Study Materials: guides, PDFs, proven strategies, trading plans, risk management and compound interest structures.
✅ Live Mentorship & Trading Sessions: ongoing support and recorded classes.
✅ 200+ High-Precision Signals Daily: Forex pairs, CRYPTO IDX, synthetic indices, futures and spot trading on Binance.
✅ 24/7 Automated Bots: never miss opportunities, even when you're offline.
✅ Automated Forex Systems: professionally configured and ready to execute.
✅ Funding Account Preparation: structure, discipline and real capital scaling strategy.
✅ Advanced Tools for MT4 and MT5.
✅ Additional bonuses and benefits depending on your level.

⚡️ Benefits may vary according to the selected level and capital. ⚡️
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

def live_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 TikTok (Lives)", url="https://www.tiktok.com/@joha_binomo?_t=ZN-8xceLrp5GTe&_r=1")],
        [InlineKeyboardButton("📲 Instagram (Lives)", url="https://www.instagram.com/johaale_trader/")],
        [InlineKeyboardButton("▶️ YouTube", url="https://www.youtube.com/@johaalegria.trader")],
        [InlineKeyboardButton("💬 Escríbeme aquí", url=SUPPORT_URL)],
    ])

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
            [InlineKeyboardButton("📊 Levels & Plans", callback_data="levels_plans_en")],
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
            [InlineKeyboardButton("📊 Gestión de capital", callback_data="gestion_capital")],
            [InlineKeyboardButton("🎁 Beneficios VIP", callback_data="beneficios_vip")],
            [InlineKeyboardButton("📊 Niveles y Planes", callback_data="niveles_planes")],
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

    # --- Niveles y Planes (informativo) ---
    if q.data == "niveles_planes":
        texto = (
            "📊 NUEVA ESTRUCTURA OFICIAL JT TRADERS\n\n"
            "🟢 Básico — desde $50 inversión en tu cuenta Binomo.\n"
            "Señales Crypto IDX limitadas + formación y gestión.\n\n"
            "🔵 Premium — desde $200 inversión.\n"
            "Señales completas, bots IA, operativa en vivo y multi-broker.\n\n"
            "🟣 Prestige — desde $500 inversión.\n"
            "Mentorías privadas, Forex automatizado y preparación para cuentas de fondeo.\n\n"
            "Consulta todos los detalles aquí:"
        )
        kb = [[InlineKeyboardButton("📄 Ver estructura completa", url="https://telegra.ph/EVOLUCI%C3%93N-OFICIAL-DE-NUESTRA-COMUNIDAD-02-27")]]
        await q.message.reply_text(texto, reply_markup=InlineKeyboardMarkup(kb))
        return



    # --- Levels & Plans (EN) ---
    if q.data == "levels_plans_en":
        texto = (
            "📊 OFFICIAL JT TRADERS COMMUNITY STRUCTURE\n\n"
            "🟢 Level 1 — Basic (from $50 trading capital in your Binomo account).\n"
            "Limited Crypto IDX signals + education & risk management.\n\n"
            "🔵 Level 2 — Premium (from $200 trading capital).\n"
            "Full signals, AI bots, live trading and multi-broker.\n\n"
            "See full details here:"
        )
        kb = [[InlineKeyboardButton("📄 View full structure", url="https://telegra.ph/OFFICIAL-EVOLUTION-OF-OUR-TRADING-COMMUNITY-02-28")]]
        await q.message.reply_text(texto, reply_markup=InlineKeyboardMarkup(kb))
        return

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
        saved_id = context.user_data.get("binomo_id")
        if saved_id:
            msg = (
                "Perfecto ✅\n\n"
                "Recibido. Estoy validando tu depósito ahora mismo.\n"
                "Te escribiré de nuevo para confirmar y habilitar tu acceso 🎉\n\n"
                "Si deseas, también puedes enviarlo a mi chat personal tocando el botón 👇"
            )
            await q.message.reply_text(msg, reply_markup=support_keyboard())
            await send_admin_auto_log(context, update, "AUTO_IMG_DEPOSIT_VALIDATING", msg)
            return

        msg = (
            "Perfecto ✅\n\n"
            "Recibido. Para continuar, envíame tu **ID de Binomo en texto** (solo el número) y lo dejo en validación 👇\n\n"
            "Si deseas, también puedes enviarlo a mi chat personal tocando el botón 👇"
        )
        await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_IMG_DEPOSIT_NEED_ID", msg)
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

    # --- Confirmación de depósito desde botones (tanto del precheck como del flujo POST) ---
    if q.data and (q.data.startswith("DEP_YES|") or q.data.startswith("dep_yes:")):
        msg = (
            "Perfecto ✅\n\n"
            "Envíame aquí tu **comprobante de depósito/activación** (foto o captura) y tu **ID de Binomo o Stockity en texto** "
            "(solo el número) para validarlo y habilitar tu acceso 👇"
        )
        context.user_data["awaiting_deposit_proof"] = True
        await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_DEPOSIT_CONFIRM_BTN", msg)
        return

    if q.data and (q.data.startswith("DEP_NO|") or q.data.startswith("dep_no:")):
        msg = "Perfecto ✅\n\nCuéntame en texto qué necesitas revisar 👇"
        context.user_data.pop("awaiting_deposit_proof", None)
        await q.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_DEP_NOT_RELATED_BTN", msg)
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

    elif q.data == "gestion_capital":
        texto = (
            "📊 GESTIÓN DE CAPITAL\n\n"
            "He habilitado un sistema limitado de gestión de capital para quienes desean participar directamente en mis operaciones.\n\n"
            "💼 Condiciones\n\n"
            "• Inversión mínima: $200 USD\n"
            "• Duración del ciclo: 4 meses\n"
            "• Objetivo de rendimiento aproximado: hasta 40% mensual según condiciones del mercado\n\n"
            "🔒 Por motivos de seguridad y control operativo, el capital se envía directamente a mi gestión, desde donde se ejecutan las operaciones siguiendo mi plan de trading y gestión de riesgo.\n\n"
            "📈 Durante el proceso recibirás reportes periódicos del crecimiento del capital.\n\n"
            "⚠️ El trading implica riesgo y los resultados pueden variar según condiciones del mercado.\n\n"
            "Si deseas participar, escribe GESTIÓN y te enviaré la información para comenzar."
        )
        await q.message.reply_text(texto)

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
                    txt = (update.message.text or "")
                    txtn = _norm(txt)
                    if (
    _norm(GATILLO_ID_OK) in txtn
    or _norm(GATILLO_ID_OK_EN) in txtn
    or ("id validado" in txtn)
    or ("id successfully validated" in txtn)
):
                        set_user_stage(destinatario_id, STAGE_POST)
                        # Cancelar Serie A y activar Serie B
                        _cancel_jobs_prefix(context, "A", destinatario_id)
                        schedule_series_b(destinatario_id, context)
                        await context.bot.send_message(chat_id=ADMIN_ID, text=f"✅ Gatillo OK detectado. Serie B activada para {destinatario_id}")

                    elif ("confirmo cuenta activa" in txtn) or ("cuenta esta activa" in txtn) or ("cuenta está activa" in txtn) or ("acceso confirmado" in txtn) or ("acceso activado" in txtn):
                        # Acceso final confirmado por admin: detener campañas A y B
                        set_user_stage(destinatario_id, STAGE_DEPOSITED)
                        _cancel_jobs_prefix(context, "A", destinatario_id)
                        _cancel_jobs_prefix(context, "B", destinatario_id)
                        await context.bot.send_message(chat_id=ADMIN_ID, text=f"✅ Acceso confirmado. Campañas detenidas para {destinatario_id}")
                    elif (_norm(GATILLO_ID_ERRADO) in txtn) or ("tu id esta errado" in txtn) or ("tu id está errado" in txtn):
                        set_user_stage(destinatario_id, STAGE_PRE)
                        # Mantener/renovar Serie A
                        schedule_series_a(destinatario_id, get_user_lang(destinatario_id), context)
                        await context.bot.send_message(chat_id=ADMIN_ID, text=f"ℹ️ Gatillo ERRADO detectado. Serie A continua para {destinatario_id}")
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


def detect_intent_es(texto: str) -> str:
    t = _norm(texto)

    # ---- SALUDOS (intuitivo) ----
    # Detecta saludos aunque vengan con "cómo estás", "qué tal", etc.
    # Si el texto también contiene una intención fuerte (depósito, 50, live, etc.), dejamos que gane esa intención.
    if re.search(r"^(hola|holi|hello|hey|buenas|buenos|buen día|buen dia|buenas noches|buenas tardes|buenos dias|buenos días)\b", t):
        # saludos + frases cortas típicas
        if any(k in t for k in ["como estas", "cómo estás", "que tal", "qué tal", "todo bien", "todo bn", "como vas", "cómo vas"]) or len(t) <= 22:
            return "GREETING"

    # ---- CONSULTA HUMANA (NO responder) ----
    if any(k in t for k in [
        "quiero consultar", "queria consultar", "quería consultar", "quiero hacerte una consulta", "quiero hacer una consulta", "quiero una consulta", "consulta",
        "tengo una duda", "tengo dudas", "no entiendo", "no entendi", "no entendí",
        "señales que no entiendo", "algo de las señales", "no entiendo las señales",
        "me explicas", "me explica", "necesito que me expliques", "necesito ayuda con las señales",
    ]):
        return "HUMAN_CHAT"

    # ---- Depósito luego / esperando pago ----
    if any(k in t for k in [
        "depositar despues", "depositar después", "puedo depositar despues", "puedo depositar después",
        "deposito despues", "depósito después", "deposito luego", "depósito luego",
        "mas tarde deposito", "más tarde deposito", "luego deposito", "despues deposito", "después deposito",
        "no tengo dinero ahora", "ahora no tengo dinero", "estoy esperando un pago", "esperando un pago",
        "cuando me paguen", "cuando me pague", "cuando tenga dinero", "cuando tenga plata",
        "por ahora no puedo", "por ahora no tengo", "aun no puedo", "aún no puedo",
    ]):
        return "DEP_LATER"

    # ---- Mínimo 50 / puedo con menos ----
    if any(k in t for k in [
        "no tengo 50", "no tengo cincuenta", "puedo con menos", "puedo iniciar con menos",
        "puedo empezar con menos", "con menos", "tengo 10", "solo tengo 10", "tengo diez",
        "puedo empezar con 10", "puedo iniciar con 10", "puedo con 10", "10 dolares", "10 dólares",
        "tengo 20", "tengo 30", "tengo 40", "puedo con 20", "puedo con 30", "puedo con 40",
        "con 20", "con 30", "con 40", "menos de 50", "menos de cincuenta",
        "puedo depositar 10", "puedo depositar 20", "puedo depositar 30", "puedo depositar 40",
        "puedo depositar menos", "puedo depositar con menos", "puedo depositar menos de 50",
        "depositar 10", "depositar 20", "depositar 30", "depositar 40",
        "deposito 10", "deposito 20", "deposito 30", "deposito 40",
        "puedo hacer un deposito de 10", "puedo hacer un deposito de 20", "puedo hacer un deposito de 30", "puedo hacer un deposito de 40",
        "puedo hacer deposito de 10", "puedo hacer deposito de 20", "puedo hacer deposito de 30", "puedo hacer deposito de 40",
        "deposito minimo", "depósito mínimo", "monto minimo", "monto mínimo", "minimo de deposito", "mínimo de depósito",
        "con 10 dolares", "con 20 dolares", "con 30 dolares", "con 40 dolares",
    ]):
        return "MIN_50"

    # ---- Ya deposité / acceso VIP ----
    if any(k in t for k in [
        "ya deposite", "ya deposité", "ya hice el deposito", "ya hice el depósito",
        "ya pague", "ya pagué", "deposito listo", "depósito listo", "ya esta el deposito", "ya está el depósito",
        "ya me llego el deposito", "ya me llegó el depósito", "ya me llego el pago", "ya me llegó el pago",
        "ya me activaron", "ya active", "ya activé", "activacion lista", "activación lista",
        "dame acceso", "darme acceso", "acceso al vip", "acceso vip", "habilitar acceso", "habilita mi acceso",
        "para que me des acceso", "para que me des acceso al vip", "para que me des acceso al VIP",
    ]):
        return "DEPOSITO"

    
    # ---- Ya me registré (pedir ID) ----
    if any(k in t for k in [
        "ya me registre", "ya me registré", "ya me registre ahora", "ya me registré ahora",
        "ya me registre y ahora", "ya me registré y ahora", "ya me registre que hago", "ya me registré que hago",
        "me registre", "me registré", "ya estoy registrado", "ya estoy registrada", "ya estoy registrad@",
        "ya tengo cuenta", "ya cree cuenta", "ya creé cuenta", "ya hice el registro", "ya realice el registro", "ya realicé el registro",
    ]):
        return "YA_REGISTRE"

# ---- Siguiente paso / qué sigue ----
    if any(k in t for k in [
        "que sigue", "qué sigue", "que paso sigue", "qué paso sigue", "paso sigue",
        "y ahora que", "y ahora qué", "entonces que sigue", "entonces qué sigue",
        "ok gracias entonces", "ok gracias", "ya me registre que hago", "ya me registré que hago",
        "que hago ahora", "qué hago ahora", "siguiente paso"
    ]):
        return "NEXT_STEP"

    # Si el mensaje contiene un ID (número) en cualquier parte (prioridad alta)
    m_id = re.search(r"\b\d{6,12}\b", t)
    if m_id:
        return "ID_SUBMIT"

    # ---- Dónde enviar el ID / te envío el ID ----
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

    # ---- Live / conexión (sinónimos) ----
    if any(k in t for k in [
        "horario", "horarios", "live", "en vivo", "directo", "transmision", "transmisión",
        "stream", "streaming", "conectas", "conectas hoy", "a que hora te conectas", "a qué hora te conectas",
        "a que hora haces live", "a qué hora haces live", "a que hora es el live", "a qué hora es el live",
        "a que hora estas en vivo", "a qué hora estás en vivo", "a que hora haces directo", "a qué hora haces directo",
            "te conectas",
        "conexion",
        "conexión",
        "transmitir",
        "horario de live",
        "horarios de live",
        "a que hora te conectas hoy",
        "a qué hora te conectas hoy",
        "a que hora te conectas", "a qué hora te conectas", "a que hora te conectas?", "a qué hora te conectas?",
        "a que hora haces live", "a qué hora haces live", "a que hora sales en vivo", "a qué hora sales en vivo",
        "cuando te conectas", "cuándo te conectas", "cuando hay live", "cuándo hay live",
        "cuando haces directo", "cuándo haces directo", "cuando estas en vivo", "cuándo estás en vivo",
        "transmision", "transmisión", "stream", "directo", "en vivo", "conexion", "conexión",
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

    # Si detecta un ID (solo número)
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
        "Entiendo 🤍\n\n"
        "Soy Johabot y para ayudarte mejor, prefiero revisarlo contigo directamente.\n\n"
        "Escríbeme a mi chat personal aquí 👇"
    )



async def binomo_helpcenter_snippets(query: str, max_results: int = 3) -> str:
    # Desactivado: no consultamos páginas externas desde el bot.
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

    # --- PRECHECK: si llega una imagen SIN TEXTO, NO llamamos IA. Preguntamos siempre qué es ---
    if update.message and update.message.photo:
        caption = (update.message.caption or "").strip()
        # Solo si viene SIN texto/caption mostramos los 3 botones, sin depender del stage
        if not caption:
            qtxt = "📩 Recibido. ¿Esta imagen es tu **ID** de Binomo, tu **comprobante de depósito/activación** o **era otra cosa**?"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📌 Es mi ID", callback_data=f"IMG_IS_ID|{chat_id}"),
                 InlineKeyboardButton("💳 Es mi depósito", callback_data=f"IMG_IS_DEP|{chat_id}")],
                [InlineKeyboardButton("❌ Era otra cosa", callback_data=f"IMG_IS_OTHER|{chat_id}")]
            ])
            await update.message.reply_text(qtxt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            await send_admin_auto_log(context, update, "AUTO_IMAGE", qtxt)
            return
        # Si trae caption, lo tratamos como texto normal (se sigue abajo)

    texto = update.message.text or update.message.caption or ""
    intent = detect_intent_es(texto)

    # --- Intenciones nuevas (reglas, SIN IA) ---
    if intent == "HUMAN_CHAT":
        # No responder (aquí contestas tú como humana)
        return

    if intent == "GREETING":
        msg = "¡Hola! 🤍 ¿En qué puedo ayudarte hoy?\n\nSi prefieres, también puedes escribirme al chat personal 👇"
        await update.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_GREETING", msg)
        return


    if intent == "YA_REGISTRE":
        msg = (
            "Sí ✅ Puedes enviarme tu ID por aquí mismo (solo el número) y lo dejo en validación.\n\n"
            "Si prefieres hacerlo directo conmigo, también puedes escribirme al chat personal 👇"
        )
        await update.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_YA_REGISTRE", msg)
        return


    if intent == "DEP_LATER":
        msg = (
            "Perfecto ✅\n\n"
            "Cuando tengas listo tu depósito de **50 USD o más**, escríbeme y lo revisamos para darte acceso 👇"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_DEPOSIT_LATER", msg)
        return

    if intent == "MIN_50":
        msg = (
            "Para ingresar a mi comunidad VIP gratuita y acceder a todas las herramientas, "
            "el depósito mínimo es de **50 USD**."
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_MIN50", msg)
        return

    if intent == "DEPOSITO":
        # Pide prueba/ID para habilitar acceso (sin inventar nada, sin HelpCenter)
        msg = (
            "Perfecto ✅\n\n"
            "Envíame aquí tu **comprobante de depósito/activación** (foto o captura) "
            "y también tu **ID de Binomo en texto** (solo el número) para validarlo y habilitar tu acceso 👇"
        )
        # Nota: NO detenemos Campaña B aquí. Solo pedimos comprobante/ID.
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_DEPOSIT_CONFIRM", msg)
        return


        # Si el usuario solo está enviando su ID, confirmamos recibido (validación manual)
    if intent == "ID_SUBMIT":
        m = re.search(r"\b\d{6,12}\b", (update.message.text or "").strip())
        if m:
            context.user_data["binomo_id"] = m.group(0)
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
        await update.message.reply_text(LIVE_HORARIOS_ES, parse_mode=ParseMode.MARKDOWN, reply_markup=live_keyboard())
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

    # Imagen SIN texto: flujo guiado por botones (ID / Depósito / Otra cosa)
    if update.message.photo and not (update.message.caption or "").strip():
        # Si ya veníamos esperando el comprobante, no volvemos a preguntar: lo tomamos como depósito
        if context.user_data.get("awaiting_deposit_proof"):
            # Si ya tenemos ID guardado, confirmamos recepción y pasamos a validación (sin pedirlo otra vez)
            saved_id = context.user_data.get("binomo_id")
            if saved_id:
                msg = (
                    "Perfecto ✅\n\n"
                    "Recibido. Estoy validando tu información ahora mismo.\n"
                    "Si todo está OK, te escribo para habilitar tu acceso y enviarte las herramientas 🤍"
                )
                context.user_data["awaiting_deposit_proof"] = False
                await update.message.reply_text(msg, reply_markup=support_keyboard())
                await send_admin_auto_log(context, update, "AUTO_DEPOSIT_PROOF_VALIDATING", msg)
                return

            # Si aún no tenemos ID, lo pedimos una sola vez
            msg = (
                "Perfecto ✅\n\n"
                "Ahora envíame tu **ID de Binomo o Stockity en texto** (solo el número) para dejarlo en validación y habilitar tu acceso 👇"
            )
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
            await send_admin_auto_log(context, update, "AUTO_DEPOSIT_PROOF_NEED_ID", msg)
            return
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📌 Es mi ID", callback_data=f"IMG_IS_ID|{chat_id}"),
                InlineKeyboardButton("💳 Es mi depósito", callback_data=f"IMG_IS_DEP|{chat_id}"),
            ],
            [InlineKeyboardButton("❌ Era otra cosa", callback_data=f"IMG_IS_OTHER|{chat_id}")],
        ])
        msg = "Recibido. ¿Esta imagen es tu ID de Binomo o Stockity, tu comprobante de depósito/activación o era otra cosa?"
        await update.message.reply_text(msg, reply_markup=kb)
        await send_admin_auto_log(context, update, "AUTO_IMAGE", msg)
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
