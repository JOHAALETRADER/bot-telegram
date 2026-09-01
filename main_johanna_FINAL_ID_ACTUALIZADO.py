import logging
import asyncio
import re
import json
from datetime import datetime, timedelta
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
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, text
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
        try:
            _append_ai_exchange(chat_id, pregunta, respuesta)
        except Exception:
            pass
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
    # Memoria y cola persistente para respuestas con IA
    ai_history             = Column(Text)
    ai_pending_text        = Column(Text)
    ai_pending_message_id  = Column(String)
    ai_pending_due_at      = Column(DateTime)

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

# --- Migración robusta de columnas IA (memoria + respuesta pendiente) ---
try:
    backend = engine.url.get_backend_name()
    if backend.startswith("postgres"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ai_history TEXT"))
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ai_pending_text TEXT"))
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ai_pending_message_id VARCHAR"))
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ai_pending_due_at TIMESTAMP"))
    elif backend == "sqlite":
        with engine.begin() as conn:
            cols = conn.execute(text("PRAGMA table_info(usuarios)")).fetchall()
            names = {c[1] for c in cols}
            if "ai_history" not in names:
                conn.execute(text("ALTER TABLE usuarios ADD COLUMN ai_history TEXT"))
            if "ai_pending_text" not in names:
                conn.execute(text("ALTER TABLE usuarios ADD COLUMN ai_pending_text TEXT"))
            if "ai_pending_message_id" not in names:
                conn.execute(text("ALTER TABLE usuarios ADD COLUMN ai_pending_message_id TEXT"))
            if "ai_pending_due_at" not in names:
                conn.execute(text("ALTER TABLE usuarios ADD COLUMN ai_pending_due_at DATETIME"))
except Exception as e:
    logging.warning("No se pudieron verificar/crear columnas IA: %s", e)

# --- fin migración ---

# === ENLACES ===
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
CANAL_ES = "https://t.me/JohaaleTrader_es"
CANAL_EN = "https://t.me/JohaaleTrader_en"
ENLACE_REFERIDO  = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"
ENLACE_REFERIDO_STOCKITY = "https://stockity-r3.com/?a=95604cd745da&t=0&ac=JOHAALETRADER"


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

# Mensajes Serie B (post-validación) — mismos tiempos internos, sin mencionar cuánto tiempo pasó
MENSAJE_B_1H_ES = """✅ Tu ID ya quedó validado y ya diste el paso más importante.

Ahora solo falta activar tu cuenta con el depósito correspondiente a tu nivel para desbloquear tu acceso y empezar a aprovechar las herramientas de la comunidad.

🚀 Haz tu depósito y escríbeme Ya deposité. Yo continúo contigo para habilitar tu acceso."""

MENSAJE_B_3H_ES = """💰 Si este será tu primer depósito, tienes disponible un bono del 100% con el código TOP1_JOHATRADER.

Tu registro ya está validado: estás a un solo paso de activar tu acceso. Aprovecha tu depósito, completa la activación y empieza con formación, señales y herramientas según tu nivel.

✅ Cuando lo hagas, escríbeme Ya deposité y continuamos de inmediato."""

MENSAJE_B_24H_ES = """🚀 Tu cuenta ya está lista para avanzar. Elige el nivel que mejor se ajuste a ti, realiza el depósito y activa los beneficios correspondientes.

🟢 Básico desde 50 USD
🔵 Premium desde 200 USD
🟣 Prestige desde 500 USD

Da el siguiente paso ahora y escríbeme Ya deposité para validar la activación y habilitar tu acceso."""

MENSAJE_B_48H_ES = """✨ Ya hiciste el registro y tu ID está validado. No dejes el proceso a medias cuando estás tan cerca de comenzar.

Completa tu depósito, activa tu nivel y empieza a utilizar la formación, señales, herramientas y acompañamiento disponibles para ti.

🔥 Hazlo ahora y escríbeme Ya deposité. Te ayudo a completar la activación."""

MENSAJE_B_1H_EN = """✅ Your ID has been validated and you have already completed the most important step.

Now you only need to fund your account according to your selected level to unlock your access and start using the community tools.

🚀 Make your deposit and message me I deposited so I can continue with your activation."""

MENSAJE_B_3H_EN = """💰 If this is your first deposit, you currently have a 100% bonus available with code TOP1_JOHATRADER.

Your registration is already validated. Complete your deposit and start accessing the education, signals and tools included in your level.

✅ Once done, message me I deposited and we will continue immediately."""

MENSAJE_B_24H_EN = """🚀 Your account is ready to move forward. Choose the level that fits you, make the deposit and activate the corresponding benefits.

🟢 Basic from 50 USD
🔵 Premium from 200 USD
🟣 Prestige from 500 USD

Take the next step and message me I deposited so I can validate the activation and enable your access."""

MENSAJE_B_48H_EN = """✨ Your registration is complete and your ID is validated. You are very close to starting, so there is no need to leave the process unfinished.

Complete your deposit, activate your level and start using the education, signals, tools and guidance available to you.

🔥 Do it now and message me I deposited so I can help you finish the activation."""

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
{ENLACE_REFERIDO_STOCKITY}

👉 Luego de crear la cuenta es necesario y súper importante que me envíes tu ID de Binomo o Stockity para validar tu registro antes de que realices un depósito en tu cuenta de trading.

💰 Depósito según nivel elegido 

IMPORTANTE: LA CANTIDAD DE BENEFICIOS VARÍA SEGÚN TU DEPÓSITO.
 
¡Te espero!"""

MENSAJE_REGISTRARME_EN = f"""It’s super simple. Open your trading account on Binomo or Stockity using one of these links:

🔗 Binomo:
{ENLACE_REFERIDO}

🔗 Stockity:
{ENLACE_REFERIDO_STOCKITY}

👉 After creating the account, it’s very important that you send me your Binomo or Stockity ID so I can validate your registration before you make any deposit.

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

🔗 Enlace de registro Stockity: {ENLACE_REFERIDO_STOCKITY}
"""

MENSAJE_YA_TENGO_CUENTA_EN = f"""To access my VIP community and all tools, you must register with my link.

What to do? 👉 If you created your account with my link, send me your Binomo or Stockity ID using the button above.

🟡 If you didn’t use my link, do this:

1️⃣ Copy and paste the registration link in an incognito window or turn on a VPN to change your IP. Then log in normally.

2️⃣ Use an email you have NOT used on Binomo or Stockity and register manually.

3️⃣ ❗️VERY IMPORTANT: Send me your Binomo or Stockity ID for validation.

🔗 Binomo registration link: {ENLACE_REFERIDO}

🔗 Stockity registration link: {ENLACE_REFERIDO_STOCKITY}
"""

# Recordatorios (ES) — tiempos internos; los mensajes no mencionan cuánto tiempo pasó
MENSAJE_1H_ES = f"""🚀 Si quieres empezar, el primer paso es mucho más sencillo de lo que parece.

Registra tu cuenta con uno de mis enlaces y envíame tu ID antes de depositar para validar que todo haya quedado correctamente vinculado.

✨ Desde el nivel Básico puedes comenzar con 50 USD y acceder a formación y herramientas según tu nivel.

👉 Da el paso ahora:
Binomo: {ENLACE_REFERIDO}
Stockity: {ENLACE_REFERIDO_STOCKITY}"""

MENSAJE_3H_ES = f"""📈 No necesitas aprender trading sin dirección. La idea de la comunidad es que tengas una ruta, formación, señales y herramientas que te ayuden a desarrollar tu operativa con estructura.

Tu siguiente acción es simple: crea tu cuenta con mi enlace y envíame tu ID para validarlo antes del depósito.

✅ Empieza hoy y deja listo tu acceso:
Binomo: {ENLACE_REFERIDO}
Stockity: {ENLACE_REFERIDO_STOCKITY}"""

MENSAJE_24H_ES = f"""✨ Si estabas esperando el momento para comenzar, conviértelo en una acción concreta.

Puedes elegir el nivel que mejor se ajuste a tu capital y avanzar paso a paso con formación, señales, bots y otras herramientas según corresponda.

🔥 Regístrate ahora, envíame tu ID y yo te indico el siguiente paso para activar correctamente tu acceso.

📊 Resultados de la comunidad: {CANAL_RESULTADOS}
🔗 Binomo: {ENLACE_REFERIDO}
🔗 Stockity: {ENLACE_REFERIDO_STOCKITY}"""

MENSAJE_48H_ES = f"""🎯 La diferencia entre seguir pensando en empezar y realmente avanzar es completar el primer paso.

Haz tu registro con mi enlace, envíame tu ID antes de depositar y déjame validar tu cuenta. A partir de ahí podrás elegir tu nivel y continuar con la activación.

🚀 Empieza ahora:
Binomo: {ENLACE_REFERIDO}
Stockity: {ENLACE_REFERIDO_STOCKITY}

Cuando termines, envíame tu ID y continuamos."""

# Recordatorios (EN) — internal timing only; messages do not mention elapsed time
MENSAJE_1H_EN = f"""🚀 If you want to get started, the first step is simpler than it looks.

Create your account using one of my links and send me your ID before depositing so I can validate that it was linked correctly.

✨ You can start at the Basic level from 50 USD and access education and tools according to your level.

👉 Take the first step now:
Binomo: {ENLACE_REFERIDO}
Stockity: {ENLACE_REFERIDO_STOCKITY}"""

MENSAJE_3H_EN = f"""📈 You do not have to learn trading without direction. The community gives you a structured path with education, signals and tools to develop your trading process.

Your next action is simple: create your account with my link and send me your ID for validation before depositing.

✅ Start today:
Binomo: {ENLACE_REFERIDO}
Stockity: {ENLACE_REFERIDO_STOCKITY}"""

MENSAJE_24H_EN = f"""✨ If you were waiting for the right moment to begin, turn that intention into a concrete action.

Choose the level that fits your capital and move forward step by step with education, signals, bots and other tools according to your level.

🔥 Register now, send me your ID and I will guide you through the next activation step.

📊 Community results: {CANAL_RESULTADOS}
🔗 Binomo: {ENLACE_REFERIDO}
🔗 Stockity: {ENLACE_REFERIDO_STOCKITY}"""

MENSAJE_48H_EN = f"""🎯 The difference between thinking about starting and actually moving forward is completing the first step.

Register with my link, send me your ID before depositing, and let me validate your account. Then you can choose your level and continue with activation.

🚀 Start now:
Binomo: {ENLACE_REFERIDO}
Stockity: {ENLACE_REFERIDO_STOCKITY}

When you finish, send me your ID and we will continue."""

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

async def _send_job_message_B(context: ContextTypes.DEFAULT_TYPE, text_es: str, text_en: str):
    chat_id, lang = context.job.data
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text_es if lang == "es" else text_en,
            reply_markup=support_keyboard(),
        )
    except Exception as e:
        logging.warning("Job B send failed to %s: %s", chat_id, e)

async def mensaje_B_1h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message_B(context, MENSAJE_B_1H_ES, MENSAJE_B_1H_EN)

async def mensaje_B_3h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message_B(context, MENSAJE_B_3H_ES, MENSAJE_B_3H_EN)

async def mensaje_B_24h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message_B(context, MENSAJE_B_24H_ES, MENSAJE_B_24H_EN)

async def mensaje_B_48h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message_B(context, MENSAJE_B_48H_ES, MENSAJE_B_48H_EN)

def schedule_series_b(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    if not context.job_queue:
        return
    lang = get_user_lang(chat_id)
    _cancel_jobs_prefix(context, "B", chat_id)
    context.job_queue.run_once(mensaje_B_1h, when=3600,  data=(chat_id, lang), name=f"B_1h_{chat_id}")
    context.job_queue.run_once(mensaje_B_3h, when=10800, data=(chat_id, lang), name=f"B_3h_{chat_id}")
    context.job_queue.run_once(mensaje_B_24h, when=86400, data=(chat_id, lang), name=f"B_24h_{chat_id}")
    context.job_queue.run_once(mensaje_B_48h, when=172800, data=(chat_id, lang), name=f"B_48h_{chat_id}")
    logging.info("✅ Serie B post-validación programada para chat_id %s (lang=%s)", chat_id, lang)


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
            "📌 Ábrelo en Binomo o Stockity, cópialo y pégalo aquí.\n\n"
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
            "Recibido. Para continuar, envíame tu **ID de Binomo o Stockity en texto** (solo el número) y lo dejo en validación 👇\n\n"
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
        texto_gestion = (
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
        await q.message.reply_text(texto_gestion)

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
        chat_id_match = re.search(r'ID(?:\s+del\s+usuario)?[^0-9]{0,40}(\d+)', base_text, re.IGNORECASE)
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

                # Si Johanna respondió dentro de la ventana de espera, la IA pendiente se cancela.
                manual_reply_text = update.message.text or "[Respuesta de voz enviada por Johanna]"
                _cancel_pending_ai(context, destinatario_id, manual_reply=manual_reply_text)

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
        admin_key = query.from_user.id

        if admin_key in usuarios_objetivo:
            del usuarios_objetivo[admin_key]
            await query.edit_message_text("❌ Has cancelado la respuesta al usuario.")
        else:
            await query.edit_message_text("ℹ️ No había ninguna respuesta pendiente por cancelar.")



# === IA / FAQ + RESPUESTA DIFERIDA ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Modelo económico para alto volumen; puede sobreescribirse desde Railway con OPENAI_MODEL
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
# 12 minutos: punto medio entre 10 y 15. Puede cambiarse en Railway con AI_WAIT_MINUTES.
try:
    AI_WAIT_MINUTES = max(1, int(os.getenv("AI_WAIT_MINUTES", "12")))
except Exception:
    AI_WAIT_MINUTES = 12
AI_WAIT_SECONDS = AI_WAIT_MINUTES * 60
AI_HISTORY_MAX_MESSAGES = 16

JOHA_KNOWLEDGE = os.getenv("JOHA_KNOWLEDGE", "").strip() or f"""
INFORMACIÓN OFICIAL DE JOHAALETRADER / JT TRADERS TEAMS

REGISTRO Y ACCESO
- Para acceder a la comunidad, el usuario debe registrarse con uno de los enlaces oficiales y enviar su ID de Binomo o Stockity para validación ANTES de depositar.
- Binomo: {ENLACE_REFERIDO}
- Stockity: {ENLACE_REFERIDO_STOCKITY}
- Chat personal/validación: {SUPPORT_URL}
- El depósito mínimo del nivel Básico es 50 USD. Los beneficios cambian según el nivel.
- Nunca confirmes por tu cuenta que un ID, depósito o acceso quedó validado. Esa confirmación la realiza Johanna manualmente.

NIVELES
- Básico: desde 50 USD. Formación completa, comunidad inicial y señales Crypto IDX limitadas.
- Premium: desde 200 USD. Incluye lo anterior más señales completas, bots IA 24/7, operativas en vivo y enfoque multi-broker.
- Prestige: desde 500 USD. Incluye Premium más mentorías privadas, acompañamiento cercano, Forex automatizado y preparación para cuentas de fondeo.

BENEFICIOS GENERALES
- Formación en binarias, Forex, índices sintéticos y enfoque multi-broker.
- Material de estudio, guías, PDFs, estrategias, planes de trading y gestión de riesgo.
- Mentorías, operativas en vivo y clases grabadas.
- Señales de Divisas, CRYPTO IDX, Forex, índices sintéticos, futuros y spot en Binance, según nivel.
- Bots automáticos 24/7, herramientas MT4/MT5 y otros beneficios según nivel.

BONOS ACTIVOS
- 100%: código TOP1_JOHATRADER. Solo para el PRIMER depósito. Puede utilizarse una sola vez.
- 70%: código TOP_1JOHAALE. Para depósitos posteriores. Puede utilizarse una sola vez.
- No inventes condiciones adicionales de los bonos. Si preguntan por requisitos de retiro, volumen, elegibilidad concreta de una cuenta o reglas que no estén aquí, explica que deben verificarse en la plataforma/cuenta y ofrece escalarlo a Johanna.

LIVES
- Lunes a sábado: 11:00 am, 5:00 pm y 9:00 pm (hora de Colombia), según la programación actual del bot.

GESTIÓN DE CAPITAL
- Sistema limitado. Inversión mínima 200 USD. Ciclo de 4 meses. Objetivo aproximado de hasta 40% mensual según condiciones de mercado. El trading implica riesgo y los resultados pueden variar. Nunca presentes ese objetivo como garantía.

REGLAS DE RESPUESTA
- No prometas ganancias, rentabilidad garantizada ni resultados seguros.
- No inventes datos. Si falta información, dilo y deriva a Johanna.
- No des instrucciones para evadir restricciones mediante VPN/proxy.
- Para validación de ID, comprobantes, depósitos, activación de acceso, bloqueos o casos particulares de cuenta, no tomes decisiones: deriva a Johanna.
""".strip()


def _load_ai_history(chat_id: int):
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not u or not u.ai_history:
            return []
        try:
            data = json.loads(u.ai_history)
            return data if isinstance(data, list) else []
        except Exception:
            return []


def _save_ai_history(chat_id: int, history):
    history = history[-AI_HISTORY_MAX_MESSAGES:]
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if u:
            u.ai_history = json.dumps(history, ensure_ascii=False)
            session.commit()


def _append_ai_exchange(chat_id: int, user_text: str, assistant_text: str):
    if not user_text and not assistant_text:
        return
    history = _load_ai_history(chat_id)
    if user_text and user_text != "(sin texto)":
        history.append({"role": "user", "content": str(user_text)[:1800]})
    if assistant_text:
        history.append({"role": "assistant", "content": str(assistant_text)[:2200]})
    _save_ai_history(chat_id, history)


def _history_as_text(chat_id: int) -> str:
    history = _load_ai_history(chat_id)[-12:]
    parts = []
    for item in history:
        role = "USUARIO" if item.get("role") == "user" else "ASISTENTE"
        content = str(item.get("content") or "").strip()
        if content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


def _set_pending_ai(chat_id: int, text_value: str, message_id: int):
    due_at = datetime.utcnow() + timedelta(seconds=AI_WAIT_SECONDS)
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not u:
            return due_at
        previous = (u.ai_pending_text or "").strip()
        # Si el usuario envía varios mensajes antes de que responda Johanna, se agrupan y el reloj se reinicia.
        if previous and u.ai_pending_due_at and u.ai_pending_due_at >= datetime.utcnow():
            combined = (previous + "\n" + text_value.strip()).strip()
        else:
            combined = text_value.strip()
        u.ai_pending_text = combined[-5000:]
        u.ai_pending_message_id = str(message_id)
        u.ai_pending_due_at = due_at
        session.commit()
    return due_at


def _get_pending_ai(chat_id: int):
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not u or not u.ai_pending_text or not u.ai_pending_due_at:
            return None
        return {
            "text": u.ai_pending_text,
            "message_id": u.ai_pending_message_id,
            "due_at": u.ai_pending_due_at,
        }


def _clear_pending_ai_db(chat_id: int):
    pending_text = ""
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if u:
            pending_text = (u.ai_pending_text or "").strip()
            u.ai_pending_text = None
            u.ai_pending_message_id = None
            u.ai_pending_due_at = None
            session.commit()
    return pending_text


def _cancel_ai_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    if not context.job_queue:
        return
    try:
        for job in context.job_queue.get_jobs_by_name(f"AI_REPLY_{chat_id}"):
            job.schedule_removal()
    except Exception:
        pass


def _cancel_pending_ai(context: ContextTypes.DEFAULT_TYPE, chat_id: int, manual_reply: str = ""):
    _cancel_ai_job(context, chat_id)
    pending_text = _clear_pending_ai_db(chat_id)
    if pending_text and manual_reply:
        _append_ai_exchange(chat_id, pending_text, manual_reply)
    elif pending_text:
        # Conserva la pregunta previa en memoria aunque un nuevo flujo automático la haya dejado obsoleta.
        _append_ai_exchange(chat_id, pending_text, "")


LIVE_HORARIOS_ES = (
    "📅 **HORARIOS DE MIS LIVES**\n\n"
    "🗓 **Lunes a Sábado**\n"
    "• 11:00 am\n"
    "• 5:00 pm\n"
    "• 9:00 pm\n\n"
    "🚀 *Nos vemos en vivo*"
)

LIVE_HORARIOS_EN = (
    "📅 **MY LIVE SCHEDULE**\n\n"
    "🗓 **Monday to Saturday**\n"
    "• 11:00 am\n"
    "• 5:00 pm\n"
    "• 9:00 pm\n\n"
    "🚀 *See you live*"
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

    # ---- CONSULTA ABIERTA (si Johanna no responde, la atiende la IA diferida) ----
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

    if any(k in t for k in ["bono", "bonus", "100%", "70%", "top1_johatrader", "top_1johaale"]):
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
        "🎁 Bonos activos\n\n"
        "💯 100% — TOP1_JOHATRADER\n"
        "Solo para el primer depósito. Se utiliza una sola vez.\n\n"
        "🔥 70% — TOP_1JOHAALE\n"
        "Para depósitos posteriores. Se utiliza una sola vez."
    )


def respuesta_bono_en() -> str:
    return (
        "🎁 Active bonuses\n\n"
        "💯 100% — TOP1_JOHATRADER\n"
        "For the first deposit only. It can be used once.\n\n"
        "🔥 70% — TOP_1JOHAALE\n"
        "For later deposits. It can be used once."
    )


def bono_requiere_guia(texto: str) -> bool:
    t = _norm(texto)
    detalles = [
        "como funciona", "cómo funciona", "explica", "explicame", "explícame",
        "condicion", "condición", "condiciones", "requisito", "requisitos",
        "retirar", "retiro", "withdraw", "volumen", "turnover", "rollover",
        "como usar", "cómo usar", "como aplic", "cómo aplic", "donde coloc", "dónde coloc",
        "que pasa", "qué pasa", "pierdo", "cancelar bono", "quitar bono",
        "how does", "how it works", "how does it work", "condition", "conditions",
        "requirement", "requirements", "how to use", "how do i use", "apply the bonus",
    ]
    return any(k in t for k in detalles) or len(t) > 130

def respuesta_id_es() -> str:
    return (
        "🆔 **¿Dónde encuentro mi ID de Binomo o Stockity?**\n\n"
        "1) Entra a tu cuenta (app o web).\n"
        "2) Ve a tu **perfil / ajustes** (icono de usuario).\n"
        "3) Busca el campo **ID** o **User ID** y cópialo.\n\n"
        "Si no lo ves, dime si estás en app o navegador y te guío 👇"
    )


def respuesta_next_step_es() -> str:
    return (
        "✅ Perfecto. El **siguiente paso** es validar tu **ID** para confirmar que tu registro quedó bien "
        "**antes de que deposites**.\n\n"
        "📌 Envíame aquí tu **ID de Binomo o Stockity** (solo el número) y lo dejo en validación.\n\n"
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
    # Se conserva por compatibilidad; la IA usa la base de conocimiento de JOHAALETRADER.
    return ""


async def openai_answer(question: str, chat_id: int, lang: str, stage: str) -> str:
    if not (HAS_HTTPX and OPENAI_API_KEY):
        return ""
    try:
        language_instruction = (
            "Responde en español." if lang == "es" else "Reply in English."
        )
        system = f"""
Eres Johabot, asistente virtual oficial de JOHAALETRADER / JT TRADERS TEAMS.
Tu función es responder consultas comerciales y de soporte usando EXCLUSIVAMENTE la información oficial suministrada abajo y el historial de la conversación.
{language_instruction}

ESTILO
- Cercano, positivo, claro, comercial y útil.
- Normalmente 2 a 6 líneas; amplía solo si la pregunta lo necesita.
- Da una respuesta directa primero.
- Puedes usar emojis con moderación.
- No digas que un dato está confirmado si requiere revisión humana.

LÍMITES IMPORTANTES
- No inventes información ni condiciones.
- No prometas ganancias ni resultados garantizados.
- No confirmes ID, depósito, afiliación, pago ni acceso VIP.
- Si la consulta requiere revisar la cuenta específica del usuario, comprobantes, validaciones o una condición que no aparece en la base, indícale que Johanna debe revisarlo personalmente y usa el chat de validación.
- No des instrucciones para saltar restricciones con VPN o proxy.
- Si preguntan por bonos de forma detallada, explica solo lo confirmado en la base; para condiciones de retiro/volumen/elegibilidad no documentadas, indica que deben verificarse en la cuenta o con Johanna.

ETAPA ACTUAL DEL USUARIO: {stage}

BASE DE CONOCIMIENTO OFICIAL:
{JOHA_KNOWLEDGE}
""".strip()

        history_text = _history_as_text(chat_id)
        user_input = (
            f"HISTORIAL RECIENTE:\n{history_text or '(sin historial previo)'}\n\n"
            f"MENSAJE ACTUAL DEL USUARIO:\n{question.strip()}"
        )
        payload = {
            "model": OPENAI_MODEL,
            "instructions": system,
            "input": user_input,
            "max_output_tokens": 700,
            "store": False,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": "Bearer " + OPENAI_API_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code != 200:
            logging.warning("OpenAI Responses API devolvió %s: %s", resp.status_code, resp.text[:500])
            return ""
        out = resp.json()
        texts = []
        for item in out.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    texts.append(c.get("text", ""))
        return "\n".join(t for t in texts if t).strip()
    except Exception as e:
        logging.warning("Error generando respuesta IA: %s", e)
        return ""


async def _send_scheduled_ai_admin_log(context: ContextTypes.DEFAULT_TYPE, chat_id: int, question: str, answer: str):
    text_value = (
        "🤖 RESPUESTA IA DIFERIDA\n"
        f"Usuario ID: {chat_id}\n"
        f"Espera configurada: {AI_WAIT_MINUTES} min\n\n"
        f"Pregunta:\n{question}\n\n"
        f"Respuesta:\n{answer}"
    )
    if len(text_value) > 3900:
        text_value = text_value[:3900] + "\n\n...(recortado)"
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text_value, disable_web_page_preview=True)
    except Exception as e:
        logging.info("No pude enviar log de IA diferida: %s", e)


async def delayed_ai_reply(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = int(data.get("chat_id"))
    expected_message_id = str(data.get("message_id") or "")
    pending = _get_pending_ai(chat_id)
    if not pending:
        return

    # Un mensaje nuevo puede haber reemplazado este job.
    if expected_message_id and str(pending.get("message_id") or "") != expected_message_id:
        return

    now = datetime.utcnow()
    due_at = pending["due_at"]
    if due_at > now + timedelta(seconds=1):
        delay = max(1, int((due_at - now).total_seconds()))
        if context.job_queue:
            context.job_queue.run_once(
                delayed_ai_reply,
                when=delay,
                data={"chat_id": chat_id, "message_id": pending.get("message_id")},
                name=f"AI_REPLY_{chat_id}",
            )
        return

    question = (pending.get("text") or "").strip()
    if not question:
        _clear_pending_ai_db(chat_id)
        return

    lang = get_user_lang(chat_id)
    stage = get_user_stage(chat_id)
    answer = await openai_answer(question, chat_id, lang, stage)
    if not answer:
        answer = (
            "Quiero darte una respuesta correcta y este caso necesita revisión directa. Escríbeme aquí 👇"
            if lang == "es" else
            "I want to give you an accurate answer and this case needs a direct review. Message me here 👇"
        )

    # Verificación final inmediatamente antes de enviar, por si Johanna respondió mientras se generaba la respuesta.
    latest = _get_pending_ai(chat_id)
    if not latest or str(latest.get("message_id") or "") != expected_message_id:
        return

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=answer,
            reply_markup=support_keyboard(),
            disable_web_page_preview=True,
        )
        _clear_pending_ai_db(chat_id)
        _append_ai_exchange(chat_id, question, answer)
        await _send_scheduled_ai_admin_log(context, chat_id, question, answer)
    except Exception as e:
        logging.warning("No se pudo enviar la respuesta IA a %s: %s", chat_id, e)


def schedule_ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text_value: str):
    if not text_value or not text_value.strip():
        return
    chat_id = update.effective_chat.id
    message_id = update.effective_message.message_id
    _cancel_ai_job(context, chat_id)
    _set_pending_ai(chat_id, text_value, message_id)
    if context.job_queue:
        context.job_queue.run_once(
            delayed_ai_reply,
            when=AI_WAIT_SECONDS,
            data={"chat_id": chat_id, "message_id": str(message_id)},
            name=f"AI_REPLY_{chat_id}",
        )


async def recover_pending_ai_jobs(application):
    """Recupera respuestas pendientes si Railway reinicia el servicio durante la espera."""
    if not application.job_queue:
        return
    now = datetime.utcnow()
    try:
        with Session() as session:
            users = session.query(Usuario).filter(Usuario.ai_pending_due_at.isnot(None)).all()
            for u in users:
                if not u.ai_pending_text:
                    continue
                delay = max(2, int((u.ai_pending_due_at - now).total_seconds()))
                application.job_queue.run_once(
                    delayed_ai_reply,
                    when=delay,
                    data={"chat_id": int(u.telegram_id), "message_id": str(u.ai_pending_message_id or "")},
                    name=f"AI_REPLY_{u.telegram_id}",
                )
        logging.info("✅ Respuestas IA pendientes recuperadas al iniciar el bot")
    except Exception as e:
        logging.warning("No se pudieron recuperar respuestas IA pendientes: %s", e)


# Nueva función para manejar mensajes de usuarios (texto o media)
async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Guardar y notificar al admin primero: Johanna siempre ve el mensaje antes de cualquier IA diferida.
    await guardar_mensaje(update, context)
    await notificar_admin(update, context)

    chat_id = update.effective_chat.id
    lang = get_user_lang(chat_id)
    stage = get_user_stage(chat_id)

    # Voz/audio/video sin texto: se deja para revisión humana.
    if update.message.voice or update.message.audio or update.message.video:
        if not (update.message.caption or "").strip():
            return

    # Imagen sin texto: flujo guiado inmediato; no se envía a IA.
    if update.message and update.message.photo:
        caption = (update.message.caption or "").strip()
        if not caption:
            qtxt = (
                "📩 Recibido. ¿Esta imagen es tu **ID** de Binomo o Stockity, tu **comprobante de depósito/activación** o **era otra cosa**?"
                if lang == "es" else
                "📩 Received. Is this image your **Binomo/Stockity ID**, your **deposit/activation proof**, or **something else**?"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📌 Es mi ID", callback_data=f"IMG_IS_ID|{chat_id}"),
                 InlineKeyboardButton("💳 Es mi depósito", callback_data=f"IMG_IS_DEP|{chat_id}")],
                [InlineKeyboardButton("❌ Era otra cosa", callback_data=f"IMG_IS_OTHER|{chat_id}")]
            ])
            await update.message.reply_text(qtxt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            await send_admin_auto_log(context, update, "AUTO_IMAGE", qtxt)
            return

    texto = update.message.text or update.message.caption or ""
    if not texto.strip():
        return
    intent = detect_intent_es(texto)
    bonus_needs_guide = intent == "BONO" and bono_requiere_guia(texto)

    # Si había una respuesta IA pendiente y el nuevo mensaje cae en un flujo inmediato,
    # se cancela la respuesta anterior para no contestar algo viejo minutos después.
    immediate_intents = {
        "GREETING", "YA_REGISTRE", "DEP_LATER", "MIN_50", "DEPOSITO",
        "ID_SUBMIT", "VPN", "PAIS", "NEXT_STEP", "WHERE_SEND_ID", "LIVE", "ID",
    }
    if intent in immediate_intents or (intent == "BONO" and not bonus_needs_guide):
        _cancel_pending_ai(context, chat_id)

    # Saludo simple: respuesta inmediata.
    if intent == "GREETING":
        msg = (
            "¡Hola! 🤍 ¿En qué puedo ayudarte hoy?\n\nSi prefieres, también puedes escribirme al chat personal 👇"
            if lang == "es" else
            "Hi! 🤍 How can I help you today?\n\nIf you prefer, you can also message me directly 👇"
        )
        await update.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_GREETING", msg)
        return

    # Flujos críticos y respuestas estructuradas: se mantienen automáticos para no romper la lógica actual.
    if intent == "YA_REGISTRE":
        msg = (
            "Sí ✅ Puedes enviarme tu ID por aquí mismo (solo el número) y lo dejo en validación.\n\nSi prefieres hacerlo directo conmigo, también puedes escribirme al chat personal 👇"
            if lang == "es" else
            "Yes ✅ Send me your ID here (numbers only) and I will leave it for validation.\n\nYou can also message me directly 👇"
        )
        await update.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_YA_REGISTRE", msg)
        return

    if intent == "DEP_LATER":
        msg = (
            "Perfecto ✅\n\nCuando tengas listo tu depósito de **50 USD o más**, escríbeme y continuamos con la activación 👇"
            if lang == "es" else
            "Perfect ✅\n\nWhen you are ready with a **50 USD or higher** deposit, message me and we will continue with activation 👇"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_DEPOSIT_LATER", msg)
        return

    if intent == "MIN_50":
        msg = (
            "Para ingresar a la comunidad y acceder a las herramientas del nivel Básico, el depósito mínimo es de **50 USD**."
            if lang == "es" else
            "The minimum deposit for the Basic level and its included tools is **50 USD**."
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_MIN50", msg)
        return

    if intent == "DEPOSITO":
        msg = (
            "Perfecto ✅\n\nEnvíame aquí tu **comprobante de depósito/activación** (foto o captura) y también tu **ID de Binomo o Stockity en texto** (solo el número) para validarlo y habilitar tu acceso 👇"
            if lang == "es" else
            "Perfect ✅\n\nSend me your **deposit/activation proof** (photo or screenshot) and your **Binomo/Stockity ID as text** (numbers only) so it can be validated and your access enabled 👇"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_DEPOSIT_CONFIRM", msg)
        return

    if intent == "ID_SUBMIT":
        m = re.search(r"\b\d{6,12}\b", (update.message.text or "").strip())
        if m:
            context.user_data["binomo_id"] = m.group(0)
            with Session() as session:
                u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
                if u:
                    u.binomo_id = m.group(0)
                    session.commit()
        respuesta_id_submit = (
            "✅ **Recibido.** Ya tengo tu ID.\nLo dejo en **validación** y en breve te confirmo si está correcto.\nMientras tanto, si quieres adelantar el proceso, escríbeme aquí 👇"
            if lang == "es" else
            "✅ **Received.** I have your ID.\nIt is now **pending validation** and I will confirm once it has been checked.\nYou can also message me directly here 👇"
        )
        await update.message.reply_text(respuesta_id_submit, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "ID_SUBMIT", respuesta_id_submit)
        return

    if intent in ("VPN", "PAIS"):
        msg = (
            "Para temas de VPN / error de país prefiero revisarlo contigo directamente 🤍\n\nToca el botón 👇"
            if lang == "es" else
            "For VPN / country restriction issues, I prefer to review your specific case directly 🤍\n\nTap the button below 👇"
        )
        await update.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, intent, msg)
        return

    if intent == "NEXT_STEP":
        msg = respuesta_next_step_es() if lang == "es" else "✅ The next step is to validate your Binomo or Stockity ID before depositing. Send me the ID as text (numbers only) and I will leave it for validation 👇"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, intent, msg)
        return

    if intent == "WHERE_SEND_ID":
        msg = respuesta_where_send_id_es() if lang == "es" else "Yes ✅ You can send your ID right here (numbers only) and I will leave it for validation. You can also message me directly 👇"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, intent, msg)
        return

    if intent == "LIVE":
        msg = LIVE_HORARIOS_ES if lang == "es" else LIVE_HORARIOS_EN
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=live_keyboard())
        await send_admin_auto_log(context, update, "LIVE", msg)
        return

    # Bono simple: solo muestra bonos activos. Si la consulta pide explicación/condiciones, entra la IA diferida.
    if intent == "BONO" and not bonus_needs_guide:
        msg = respuesta_bono_es() if lang == "es" else respuesta_bono_en()
        await update.message.reply_text(msg, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "BONO_ACTIVO", msg)
        return

    if intent == "ID":
        msg = respuesta_id_es() if lang == "es" else "🆔 Open your Binomo or Stockity profile/settings, find the **ID / User ID** field and copy the number. If you cannot find it, tell me whether you are using the app or browser 👇"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "ID", msg)
        return

    # Todas las demás preguntas: Johanna tiene prioridad. Si no responde, entra la IA después del tiempo configurado.
    # Esto incluye HUMAN_CHAT, RETIRO, METODOS, EMAIL, OTRO y consultas detalladas sobre bonos.
    schedule_ai_reply(update, context, texto)

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
            _cancel_pending_ai(context, chat_id, manual_reply=mensaje or "[Imagen enviada por Johanna]")
            await update.message.reply_text("✅ Imagen enviada con éxito.")
            return

        # Enviar imagen como DOCUMENTO
        if update.message.document and update.message.document.mime_type.startswith("image/"):
            await context.bot.send_document(chat_id=chat_id, document=update.message.document.file_id, caption=mensaje)
            _cancel_pending_ai(context, chat_id, manual_reply=mensaje or "[Imagen enviada por Johanna]")
            await update.message.reply_text("✅ Imagen enviada como documento.")
            return

        # Enviar video
        if update.message.video:
            await context.bot.send_video(chat_id=chat_id, video=update.message.video.file_id, caption=mensaje)
            _cancel_pending_ai(context, chat_id, manual_reply=mensaje or "[Video enviado por Johanna]")
            await update.message.reply_text("✅ Video enviado con éxito.")
            return

        # Enviar audio
        if update.message.audio:
            await context.bot.send_audio(chat_id=chat_id, audio=update.message.audio.file_id, caption=mensaje)
            _cancel_pending_ai(context, chat_id, manual_reply=mensaje or "[Audio enviado por Johanna]")
            await update.message.reply_text("✅ Audio enviado con éxito.")
            return

        # Enviar nota de voz
        if update.message.voice:
            await context.bot.send_voice(chat_id=chat_id, voice=update.message.voice.file_id)
            _cancel_pending_ai(context, chat_id, manual_reply="[Nota de voz enviada por Johanna]")
            await update.message.reply_text("✅ Nota de voz enviada con éxito.")
            return

        # Si no es archivo multimedia, enviar como texto
        if mensaje:
            await context.bot.send_message(chat_id=chat_id, text=mensaje)
            _cancel_pending_ai(context, chat_id, manual_reply=mensaje)
            await update.message.reply_text("✅ Mensaje enviado con éxito.")
        else:
            await update.message.reply_text("⚠️ No se pudo enviar nada. Revisa el contenido.")
    except Exception as e:
        print(f"❌ Error al enviar mensaje directo: {e}")
        await update.message.reply_text("⚠️ Ocurrió un error al intentar enviar el mensaje.")


# === EJECUCIÓN ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).post_init(recover_pending_ai_jobs).build()

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
