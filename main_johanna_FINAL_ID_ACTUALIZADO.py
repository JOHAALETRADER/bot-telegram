import logging
import asyncio
import re
import json
import tempfile
import shutil
import subprocess
from pathlib import Path
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
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, text, or_
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
    # Última interacción conocida; sirve para avisos LIVE a usuarios recientes.
    last_activity_at       = Column(DateTime, default=datetime.utcnow)


class JohannaExample(Base):
    """Ejemplos reales de cómo Johanna responde a usuarios.

    Se usan como memoria progresiva de estilo y contexto operativo.
    No sustituyen las reglas oficiales ni convierten automáticamente una
    excepción individual en una regla general.
    """
    __tablename__ = "johanna_examples"
    id             = Column(Integer, primary_key=True)
    source_chat_id = Column(String)
    user_text      = Column(Text)
    response_text  = Column(Text)
    response_type  = Column(String, default="text")
    lang           = Column(String, default="es")
    created_at     = Column(DateTime, default=datetime.utcnow)


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

# --- Migración de última actividad para avisos LIVE ---
try:
    backend = engine.url.get_backend_name()
    if backend.startswith("postgres"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMP"))
            conn.execute(text("UPDATE usuarios SET last_activity_at = COALESCE(last_activity_at, fecha_registro)"))
    elif backend == "sqlite":
        with engine.begin() as conn:
            cols = conn.execute(text("PRAGMA table_info(usuarios)")).fetchall()
            names = {c[1] for c in cols}
            if "last_activity_at" not in names:
                conn.execute(text("ALTER TABLE usuarios ADD COLUMN last_activity_at DATETIME"))
            conn.execute(text("UPDATE usuarios SET last_activity_at = COALESCE(last_activity_at, fecha_registro)"))
except Exception as e:
    logging.warning("No se pudo verificar/crear last_activity_at: %s", e)

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

Tu acceso a la comunidad es gratuito. Para continuar solo necesitas:

1️⃣ Realizar el depósito en tu propia cuenta de trading según el nivel elegido.
2️⃣ Escribirme cuando esté listo para validar la activación y habilitar tus herramientas.

Cuando tengas tu depósito listo, escríbeme directamente y continuamos."""

GATILLO_ID_OK_EN = """ID successfully validated. ✅

Access to my community is free. To continue, you only need to:

1️⃣ Make the deposit in your own trading account according to your selected level.
2️⃣ Message me once it is ready so I can validate the activation and enable your tools.

When your deposit is ready, message me directly and we will continue."""

GATILLO_ACCESO_OK = "confirmo cuenta activa"
GATILLO_ID_ERRADO = f"""Tu ID está errado.

Para tener acceso a mi comunidad VIP y sus herramientas, tu cuenta debe quedar registrada correctamente con uno de mis enlaces oficiales.

Haz un nuevo registro únicamente si la plataforma lo permite, utilizando un correo que NO hayas usado antes en esa plataforma y tus datos reales y verificables.

🔗 Stockity: {ENLACE_REFERIDO_STOCKITY}
🔗 Binomo: {ENLACE_REFERIDO}

Luego envíame el nuevo ID para validarlo antes de depositar."""

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

MENSAJE_REGISTRARME_ES = f"""Es muy sencillo. Abre tu cuenta de trading con uno de mis enlaces oficiales:

🔗 Stockity — opción principal:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — opción secundaria:
{ENLACE_REFERIDO}

👉 Después de crear la cuenta, envíame tu ID de Stockity o Binomo para validar que el registro quedó correctamente vinculado ANTES de que realices cualquier depósito.

💰 El acceso a mi comunidad es gratuito. Tu depósito queda en tu propia cuenta de trading y la cantidad de beneficios/herramientas depende del nivel que elijas.

¡Te espero! 🚀"""

MENSAJE_REGISTRARME_EN = f"""It’s very simple. Open your trading account using one of my official links:

🔗 Stockity — primary option:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — secondary option:
{ENLACE_REFERIDO}

👉 After creating the account, send me your Stockity or Binomo ID so I can validate that the registration is correctly linked BEFORE you make any deposit.

💰 Access to my community is free. Your deposit stays in your own trading account, and the benefits/tools depend on the level you choose.

I’ll be waiting for you! 🚀"""

MENSAJE_YA_TENGO_CUENTA_ES = f"""Si ya tienes una cuenta de Stockity o Binomo y NO fue registrada con mi enlace, primero revisamos cómo dejar correctamente vinculada una nueva cuenta.

✅ Si tu cuenta actual tiene saldo, retíralo primero si la plataforma y las condiciones de tu cuenta lo permiten. Si tienes un bono activo, revisa antes sus condiciones de retiro.

✅ Si la plataforma permite crear una nueva cuenta, haz el registro con mi enlace usando un correo diferente que nunca hayas usado en esa plataforma y datos reales/verificables del titular.

✅ Si la cuenta pertenece a un familiar, debe ser realmente la cuenta de esa persona: sus propios datos, documento y medios de depósito/retiro a su nombre.

❗ No uses VPN para saltar restricciones de país. Si tienes un problema de disponibilidad o país, escríbeme directamente para revisar tu caso.

🔗 Stockity: {ENLACE_REFERIDO_STOCKITY}
🔗 Binomo: {ENLACE_REFERIDO}

📌 SUPER IMPORTANTE: envíame el nuevo ID antes de depositar para validarlo."""

MENSAJE_YA_TENGO_CUENTA_EN = f"""If you already have a Stockity or Binomo account and it was NOT registered through my link, the first step is to review how to correctly link a new account.

✅ If your current account has funds, withdraw them first if the platform and your account conditions allow it. If you have an active bonus, review its withdrawal conditions first.

✅ If the platform allows a new account, register through my link using a different email that has never been used on that platform and the account holder’s real, verifiable information.

✅ If the account belongs to a family member, it must genuinely be that person’s account: their own information, identity document, and deposit/withdrawal methods in their name.

❗ Do not use a VPN to bypass country restrictions. If you have a country/availability issue, message me directly so I can review your case.

🔗 Stockity: {ENLACE_REFERIDO_STOCKITY}
🔗 Binomo: {ENLACE_REFERIDO}

📌 VERY IMPORTANT: send me the new ID before depositing so I can validate it."""

# Recordatorios (ES) — tiempos internos; los mensajes no mencionan cuánto tiempo pasó
MENSAJE_1H_ES = f"""🚀 Si quieres empezar, el primer paso es mucho más sencillo de lo que parece.

Registra tu cuenta con uno de mis enlaces y envíame tu ID antes de depositar para validar que todo haya quedado correctamente vinculado.

✨ Desde el nivel Básico puedes comenzar con 50 USD y acceder a formación y herramientas según tu nivel.

👉 Da el paso ahora:
Stockity: {ENLACE_REFERIDO_STOCKITY}
Binomo: {ENLACE_REFERIDO}"""

MENSAJE_3H_ES = f"""📈 No necesitas aprender trading sin dirección. La idea de la comunidad es que tengas una ruta, formación, señales y herramientas que te ayuden a desarrollar tu operativa con estructura.

Tu siguiente acción es simple: crea tu cuenta con mi enlace y envíame tu ID para validarlo antes del depósito.

✅ Empieza hoy y deja listo tu acceso:
Stockity: {ENLACE_REFERIDO_STOCKITY}
Binomo: {ENLACE_REFERIDO}"""

MENSAJE_24H_ES = f"""✨ Si estabas esperando el momento para comenzar, conviértelo en una acción concreta.

Puedes elegir el nivel que mejor se ajuste a tu capital y avanzar paso a paso con formación, señales, bots y otras herramientas según corresponda.

🔥 Regístrate ahora, envíame tu ID y yo te indico el siguiente paso para activar correctamente tu acceso.

📊 Resultados de la comunidad: {CANAL_RESULTADOS}
🔗 Stockity: {ENLACE_REFERIDO_STOCKITY}
🔗 Binomo: {ENLACE_REFERIDO}"""

MENSAJE_48H_ES = f"""🎯 La diferencia entre seguir pensando en empezar y realmente avanzar es completar el primer paso.

Haz tu registro con mi enlace, envíame tu ID antes de depositar y déjame validar tu cuenta. A partir de ahí podrás elegir tu nivel y continuar con la activación.

🚀 Empieza ahora:
Stockity: {ENLACE_REFERIDO_STOCKITY}
Binomo: {ENLACE_REFERIDO}

Cuando termines, envíame tu ID y continuamos."""

# Recordatorios (EN) — internal timing only; messages do not mention elapsed time
MENSAJE_1H_EN = f"""🚀 If you want to get started, the first step is simpler than it looks.

Create your account using one of my links and send me your ID before depositing so I can validate that it was linked correctly.

✨ You can start at the Basic level from 50 USD and access education and tools according to your level.

👉 Take the first step now:
Stockity: {ENLACE_REFERIDO_STOCKITY}
Binomo: {ENLACE_REFERIDO}"""

MENSAJE_3H_EN = f"""📈 You do not have to learn trading without direction. The community gives you a structured path with education, signals and tools to develop your trading process.

Your next action is simple: create your account with my link and send me your ID for validation before depositing.

✅ Start today:
Stockity: {ENLACE_REFERIDO_STOCKITY}
Binomo: {ENLACE_REFERIDO}"""

MENSAJE_24H_EN = f"""✨ If you were waiting for the right moment to begin, turn that intention into a concrete action.

Choose the level that fits your capital and move forward step by step with education, signals, bots and other tools according to your level.

🔥 Register now, send me your ID and I will guide you through the next activation step.

📊 Community results: {CANAL_RESULTADOS}
🔗 Stockity: {ENLACE_REFERIDO_STOCKITY}
🔗 Binomo: {ENLACE_REFERIDO}"""

MENSAJE_48H_EN = f"""🎯 The difference between thinking about starting and actually moving forward is completing the first step.

Register with my link, send me your ID before depositing, and let me validate your account. Then you can choose your level and continue with activation.

🚀 Start now:
Stockity: {ENLACE_REFERIDO_STOCKITY}
Binomo: {ENLACE_REFERIDO}

When you finish, send me your ID and we will continue."""

# Beneficios (ES/EN)
BENEFICIOS_ES = """✨ Beneficios Exclusivos que Recibirás ✨

✅ Formación: Binarias, Forex, Índices Sintéticos y enfoque Multi-Broker.
✅ Material premium de estudio: guías, PDFs, estrategias, planes de trading, gestión de riesgo e interés compuesto.
✅ Mentorías y operativas en vivo: acompañamiento y clases grabadas.
✅ Software Premium anticipado: aproximadamente 200 o más señales de lunes a sábado entre Divisas y CRYPTO IDX, según nivel.
✅ Bot de inteligencia artificial con señales 24/7.
✅ Preparación para cuentas de fondeo y herramientas MT4/MT5 según nivel.
✅ Bonos y beneficios adicionales según nivel.

⚡️ El acceso a la comunidad es gratuito; las herramientas disponibles dependen del nivel/inversión elegida. ⚡️
"""

BENEFICIOS_EN = """✨ Exclusive Benefits You’ll Receive ✨

✅ Education: Binary Options, Forex, Synthetic Indices and a Multi-Broker approach.
✅ Premium study material: guides, PDFs, strategies, trading plans, risk management and compound interest.
✅ Live mentoring and trading sessions, plus recorded classes.
✅ Premium advance signal software: approximately 200 or more signals from Monday to Saturday across currency pairs and CRYPTO IDX, depending on level.
✅ AI signal bot available 24/7.
✅ Funded-account preparation and MT4/MT5 tools depending on level.
✅ Additional bonuses and benefits according to level.

⚡️ Community access is free; available tools depend on the selected level/investment. ⚡️
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


def _touch_user_activity(chat_id: int):
    """Actualiza la fecha de actividad sin alterar el resto del usuario."""
    try:
        with Session() as session:
            u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
            if u:
                u.last_activity_at = datetime.utcnow()
                session.commit()
    except Exception as e:
        logging.info("No pude actualizar última actividad de %s: %s", chat_id, e)


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
            [InlineKeyboardButton("📊 Capital Management", callback_data="gestion_capital_en")],
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
                stage="PRE",
                last_activity_at=datetime.utcnow(),
            )
            session.add(nuevo_usuario)
        else:
            user.nombre = nombre
            user.last_activity_at = datetime.utcnow()
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
    _touch_user_activity(chat_id)

    # Notificar interacción
    await notificar_interaccion(update, context)

    # --- Niveles y Planes (informativo) ---
    if q.data == "niveles_planes":
        texto = respuesta_niveles_es()
        kb = [[InlineKeyboardButton("📄 Ver estructura completa", url="https://telegra.ph/EVOLUCI%C3%93N-OFICIAL-DE-NUESTRA-COMUNIDAD-02-27")]]
        await q.message.reply_text(texto, reply_markup=InlineKeyboardMarkup(kb))
        return

    # --- Levels & Plans (EN) ---
    if q.data == "levels_plans_en":
        texto = respuesta_niveles_en()
        kb = [[InlineKeyboardButton("📄 View full structure", url="https://telegra.ph/OFFICIAL-EVOLUTION-OF-OUR-TRADING-COMMUNITY-02-28")]]
        await q.message.reply_text(texto, reply_markup=InlineKeyboardMarkup(kb))
        return


    # --- Acciones para imagen (ID vs depósito) ---
    if q.data and q.data.startswith("IMG_IS_ID|"):
        msg = (
            "Perfecto ✅\n"
            "Para poder validarlo necesito que me envíes el **ID en texto** (solo el número).\n"
            "📌 Ábrelo en Stockity o Binomo, cópialo y pégalo aquí.\n\n"
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
            "Recibido. Para continuar, envíame tu **ID de Stockity o Binomo en texto** (solo el número) y lo dejo en validación 👇\n\n"
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
            "Envíame aquí tu **comprobante de depósito/activación** (foto o captura) y tu **ID de Stockity o Binomo en texto** "
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
            "Tengo dos modalidades disponibles y este proceso lo reviso personalmente contigo.\n\n"
            "• Modalidad 3 meses: desde 200 USD. Objetivo estimado de 20–30% mensual, sujeto a resultados del trading.\n"
            "• Modalidad 2 meses: desde 100 USD. La estructura planteada busca generar hasta 30 USD semanales, sujeto a resultados.\n\n"
            "⚠️ Son objetivos, NO ganancias garantizadas. El trading implica riesgo.\n\n"
            "Si te interesa, escríbeme directamente y te explico condiciones, disponibilidad y proceso 👇"
        )
        await q.message.reply_text(texto_gestion, reply_markup=support_keyboard())

    elif q.data == "gestion_capital_en":
        texto_gestion = (
            "📊 CAPITAL MANAGEMENT\n\n"
            "I currently have two options, and I review this process with you personally.\n\n"
            "• 3-month option: from 200 USD. Estimated target of 20–30% per month, subject to trading results.\n"
            "• 2-month option: from 100 USD. The structure aims for up to 30 USD per week, subject to results.\n\n"
            "⚠️ These are targets, NOT guaranteed profits. Trading involves risk.\n\n"
            "If you are interested, message me directly so I can explain the current conditions and availability 👇"
        )
        await q.message.reply_text(texto_gestion, reply_markup=support_keyboard())

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
            user.last_activity_at = datetime.utcnow()
        else:
            session.add(Usuario(
                telegram_id=str(chat_id),
                nombre=update.effective_user.full_name,
                mensaje=texto,
                fecha_registro=datetime.utcnow(),
                last_activity_at=datetime.utcnow(),
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
    # Puede ser respuesta a un mensaje del admin que contenía texto o media con caption.
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
            pending_before = _get_pending_ai(destinatario_id)
            original_question = (pending_before or {}).get("text") or _extract_question_from_admin_message(base_text)
            try:
                response_type = "text"
                learned_reply = ""
                pending_cleared_early = False

                if update.message.voice:
                    await context.bot.send_voice(
                        chat_id=destinatario_id,
                        voice=update.message.voice.file_id,
                        caption="🎤 Respuesta en audio"
                    )
                    response_type = "voice"
                    # Cancelamos la IA INMEDIATAMENTE después de enviar el audio, antes
                    # de transcribir, para que no pueda responder mientras procesamos la voz.
                    _cancel_ai_job(context, destinatario_id)
                    _clear_pending_ai_db(destinatario_id)
                    pending_cleared_early = True

                    # La transcripción ocurre después de enviar el audio, para no demorar al usuario.
                    learned_reply = await _transcribe_admin_voice(context, update.message.voice.file_id)
                    manual_reply_text = learned_reply or "[Respuesta de voz enviada por Johanna]"
                else:
                    await context.bot.send_message(
                        chat_id=destinatario_id,
                        text=update.message.text
                    )
                    learned_reply = (update.message.text or "").strip()
                    manual_reply_text = learned_reply

                # Si Johanna respondió dentro de la ventana de espera, la IA pendiente se cancela.
                if pending_cleared_early:
                    if original_question or manual_reply_text:
                        _append_ai_exchange(destinatario_id, original_question or "", manual_reply_text)
                else:
                    _cancel_pending_ai(context, destinatario_id, manual_reply=manual_reply_text)

                # Aprende de la respuesta real (incluida la transcripción de audio, si fue posible).
                if learned_reply:
                    _save_johanna_example(
                        destinatario_id,
                        original_question or "",
                        learned_reply,
                        get_user_lang(destinatario_id),
                        response_type=response_type,
                    )

                # Detectar mensajes gatillo y cambiar el flujo sin alterar la lógica actual.
                try:
                    txt = (learned_reply if response_type == "voice" else (update.message.text or ""))
                    txtn = _norm(txt)
                    if (
                        _norm(GATILLO_ID_OK) in txtn
                        or _norm(GATILLO_ID_OK_EN) in txtn
                        or ("id validado" in txtn)
                        or ("id successfully validated" in txtn)
                    ):
                        set_user_stage(destinatario_id, STAGE_POST)
                        _cancel_jobs_prefix(context, "A", destinatario_id)
                        schedule_series_b(destinatario_id, context)
                        await context.bot.send_message(chat_id=ADMIN_ID, text=f"✅ Gatillo OK detectado. Serie B activada para {destinatario_id}")

                    elif ("confirmo cuenta activa" in txtn) or ("cuenta esta activa" in txtn) or ("cuenta está activa" in txtn) or ("acceso confirmado" in txtn) or ("acceso activado" in txtn):
                        set_user_stage(destinatario_id, STAGE_DEPOSITED)
                        _cancel_jobs_prefix(context, "A", destinatario_id)
                        _cancel_jobs_prefix(context, "B", destinatario_id)
                        await context.bot.send_message(chat_id=ADMIN_ID, text=f"✅ Acceso confirmado. Campañas detenidas para {destinatario_id}")

                    elif (_norm(GATILLO_ID_ERRADO) in txtn) or ("tu id esta errado" in txtn) or ("tu id está errado" in txtn):
                        set_user_stage(destinatario_id, STAGE_PRE)
                        schedule_series_a(destinatario_id, get_user_lang(destinatario_id), context)
                        await context.bot.send_message(chat_id=ADMIN_ID, text=f"ℹ️ Gatillo ERRADO detectado. Serie A continúa para {destinatario_id}")
                except Exception as _e:
                    logging.info("No pude procesar gatillo de respuesta manual: %s", _e)

                if response_type == "voice" and learned_reply:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="✅ Audio enviado al usuario y transcrito para aprendizaje de estilo."
                    )
                elif response_type == "voice":
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="✅ Audio enviado al usuario. (La transcripción para aprendizaje no estuvo disponible, pero el envío funcionó correctamente.)"
                    )
                else:
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
# Modelo recomendado para transcribir respuestas de voz de Johanna.
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-transcribe")
# 12 minutos: punto medio entre 10 y 15. Puede cambiarse en Railway con AI_WAIT_MINUTES.
try:
    AI_WAIT_MINUTES = max(1, int(os.getenv("AI_WAIT_MINUTES", "12")))
except Exception:
    AI_WAIT_MINUTES = 12
AI_WAIT_SECONDS = AI_WAIT_MINUTES * 60
AI_HISTORY_MAX_MESSAGES = 16

JOHA_KNOWLEDGE = os.getenv("JOHA_KNOWLEDGE", "").strip() or f"""
INFORMACIÓN OFICIAL DE JOHAALETRADER / JT TRADERS TEAMS

IDENTIDAD Y FORMA DE RESPONDER
- Responde en primera persona como Johanna / JOHAALETRADER. Nunca te presentes como Johabot ni como una IA.
- Puedes atender tanto prospectos que quieren entrar a la comunidad como miembros que ya están dentro.
- Mantén respuestas breves, normalmente 2 a 5 párrafos cortos, positivas, claras, comerciales y fáciles de leer.
- Usa algunos emojis con moderación y termina, cuando sea natural, llevando al siguiente paso útil: registro → envío de ID → depósito → activación/acceso.
- No presiones de forma engañosa y no inventes urgencias, cupos ni resultados.

REGISTRO Y ACCESO
- El acceso a la comunidad es GRATUITO. No existe una membresía adicional que el usuario deba pagar a Johanna para entrar.
- El usuario invierte/deposita en su PROPIA cuenta de trading. La cantidad de herramientas y beneficios depende del nivel elegido.
- Opción principal de registro: Stockity: {ENLACE_REFERIDO_STOCKITY}
- Opción secundaria: Binomo: {ENLACE_REFERIDO}
- Después del registro, el usuario debe enviar su ID de Stockity o Binomo para validación ANTES de depositar.
- Chat personal/validación: {SUPPORT_URL}
- Nunca confirmes por tu cuenta que un ID, depósito, afiliación o acceso quedó validado. Esa confirmación la realiza Johanna manualmente.

NIVELES
- Básico: desde 50 USD en la cuenta de trading. Formación completa, comunidad inicial y herramientas/señales CRYPTO IDX limitadas.
- Premium: desde 200 USD. Incluye lo anterior más señales completas del software Premium, bot IA 24/7, operativas en vivo y enfoque multi-broker.
- Prestige: desde 500 USD. Incluye Premium más mentorías privadas, acompañamiento cercano y preparación para cuentas de fondeo.
- Si un usuario tiene menos de 50 USD, no negocies una excepción ni prometas acceso: indícale que debe escribirle directamente a Johanna para revisar su caso.

SI YA TIENE CUENTA
- Si la cuenta actual no fue registrada con los enlaces de Johanna y tiene saldo, puede retirarlo primero si la plataforma y las condiciones de la cuenta lo permiten. Si existe un bono activo, debe revisar antes las condiciones aplicables.
- Solo se debe crear una nueva cuenta si la plataforma lo permite, con un correo distinto que nunca haya sido usado en esa plataforma y con datos reales/verificables del titular.
- Si un familiar abre una cuenta, debe ser genuinamente la cuenta de esa persona: sus propios datos, documento y medios de depósito/retiro a su nombre.
- Nunca recomiendes usar identidad/documentos ajenos para hacer pasar una cuenta como propia.
- Nunca indiques usar VPN/proxy para evadir restricciones geográficas. Los casos de país o disponibilidad se escalan directamente a Johanna.

BONOS ACTIVOS
- 100%: código TOP1_JOHATRADER. Solo para el PRIMER depósito. Puede utilizarse una sola vez.
- 70%: código TOP_1JOHAALE. Para depósitos posteriores. Puede utilizarse una sola vez.
- Para una pregunta simple sobre bonos, indica únicamente los bonos activos anteriores.
- Si preguntan por requisitos de retiro, volumen, elegibilidad concreta, reglas o condiciones que no estén aquí, no inventes: explica que deben verificarse en la plataforma/cuenta y ofrece escalarlo a Johanna.

LIVES
- Los lives públicos suelen realizarse de lunes a sábado.
- Normalmente hay una sesión alrededor de las 5:00 p. m. (hora Colombia) y una sesión nocturna que puede variar entre 8:00 p. m., 8:30 p. m. o 9:00 p. m.
- Algunos sábados puede no haber transmisión.
- Las sesiones privadas VIP no tienen un horario fijo que debas inventar: Johanna las anuncia previamente dentro del canal VIP.

SEÑALES — SOFTWARE PREMIUM ANTICIPADO
- Aproximadamente 200 o más señales de lunes a sábado, principalmente Divisas y CRYPTO IDX.
- Cada señal ya trae un minuto de entrada preestablecido. La entrada se toma en ese minuto o aproximadamente 2 segundos antes para reducir el efecto del delay de la plataforma.
- Todas las señales se trabajan con expiración de 1 minuto.
- Puede utilizarse Martingala 1 y Martingala 2 de forma opcional. No es obligatorio y aumenta el riesgo/exposición; nunca lo presentes como garantía de recuperación ni de ganancia.

SEÑALES — BOT DE INTELIGENCIA ARTIFICIAL
- El bot de señales funciona 24/7.
- La entrada se toma en el minuto inmediatamente siguiente al minuto en que llega la alerta. Ejemplo: si la alerta llega durante el minuto 10, la entrada se toma en el minuto 11, aunque haya llegado con 20 o 30 segundos avanzados.
- Expiración: 1 minuto. Martingala 1 y 2 son opcionales y aumentan el riesgo.

GESTIÓN DE CAPITAL — SIEMPRE ESCALAR A JOHANNA
- Johanna maneja personalmente cualquier consulta o activación de gestión de capital. Nunca entregues wallets, instrucciones de transferencia ni confirmes recepción de dinero.
- Modalidad 3 meses: desde 200 USD. Se ha planteado un objetivo estimado de 20–30% mensual, sujeto a resultados de trading. Al finalizar el tercer mes se liquida el ciclo según resultados y se devuelve el capital correspondiente.
- Modalidad 2 meses: desde 100 USD. La estructura planteada busca hasta 30 USD semanales durante 2 meses, sujeto a resultados.
- Estas cifras son objetivos/estructuras anunciadas, NO ganancias garantizadas. El trading implica riesgo y los resultados pueden ser inferiores o existir pérdidas.
- Ante cualquier interés en gestión, deriva al chat personal de Johanna.

TEMAS SENSIBLES — ESCALAR A JOHANNA
- País donde Stockity/Binomo no esté disponible, VPN/proxy o restricción geográfica.
- Usuario con menos de 50 USD que solicita una excepción.
- Gestión de capital.
- Validación de ID, comprobantes, depósitos, activación de acceso, bloqueos y casos particulares de una cuenta.
- Cualquier dato que requiera comprobar el estado real de una cuenta.

REGLAS GENERALES
- No prometas ganancias, rentabilidad garantizada, precisión garantizada ni resultados seguros.
- No inventes información. Si falta un dato, dilo y deriva a Johanna.
- No solicites contraseñas, códigos 2FA, seed phrases ni credenciales sensibles.
""".strip()



def _should_learn_manual_response(response_text: str) -> bool:
    """Evita convertir comandos/gatillos técnicos en ejemplos de conversación."""
    t = (response_text or "").strip()
    if len(t) < 8 or t.startswith("/"):
        return False
    tn = _norm(t) if "_norm" in globals() else t.lower()
    technical = [
        "confirmo cuenta activa",
        "acceso confirmado",
        "acceso activado",
        "id validado correctamente",
        "id successfully validated",
        "tu id esta errado",
        "tu id está errado",
    ]
    return not any(k in tn for k in technical)


def _save_johanna_example(source_chat_id: int, user_text: str, response_text: str, lang: str, response_type: str = "text"):
    """Guarda una respuesta real de Johanna como memoria global de estilo/conocimiento.

    Los ejemplos nuevos ayudan a la IA a parecerse cada vez más a Johanna. Las
    excepciones claramente individuales deben seguir tratándose como individuales.
    """
    response_text = (response_text or "").strip()
    if not _should_learn_manual_response(response_text):
        return
    try:
        with Session() as session:
            session.add(JohannaExample(
                source_chat_id=str(source_chat_id),
                user_text=(user_text or "")[:3500],
                response_text=response_text[:5000],
                response_type=(response_type or "text")[:20],
                lang=lang if lang in ("es", "en") else "es",
                created_at=datetime.utcnow(),
            ))
            # Los ejemplos se conservan: forman la memoria acumulativa de Johanna.
            # Al responder no se envían todos al modelo; se recuperan los más
            # recientes y los más relacionados con la pregunta actual.
            session.commit()
    except Exception as e:
        logging.warning("No pude guardar ejemplo de Johanna: %s", e)


def _memory_keywords(question: str):
    """Palabras útiles para recuperar ejemplos antiguos relacionados con la consulta."""
    raw = (question or "").lower()
    words = re.findall(r"[a-záéíóúüñ0-9_]{4,}", raw)
    stop = {
        "para", "como", "cómo", "esto", "esta", "este", "tengo", "quiero", "puedo", "donde", "dónde",
        "cuando", "cuándo", "cual", "cuál", "porque", "sobre", "hola", "gracias", "favor", "informacion",
        "información", "with", "what", "when", "where", "which", "that", "this", "have", "want", "your",
        "about", "please", "hello", "thanks", "could", "would", "there",
    }
    out = []
    for w in words:
        if w in stop or w in out:
            continue
        out.append(w)
        if len(out) >= 7:
            break
    return out


def _johanna_examples_as_text(question: str = "", limit: int = 28) -> str:
    """Recupera memoria relevante + ejemplos recientes de cómo responde Johanna."""
    try:
        limit = max(4, min(limit, 40))
        with Session() as session:
            recent = (
                session.query(JohannaExample)
                .order_by(JohannaExample.created_at.desc())
                .limit(max(8, limit // 2))
                .all()
            )

            relevant = []
            keywords = _memory_keywords(question)
            if keywords:
                conditions = []
                for kw in keywords:
                    conditions.append(JohannaExample.user_text.ilike(f"%{kw}%"))
                    conditions.append(JohannaExample.response_text.ilike(f"%{kw}%"))
                relevant = (
                    session.query(JohannaExample)
                    .filter(or_(*conditions))
                    .order_by(JohannaExample.created_at.desc())
                    .limit(limit)
                    .all()
                )

        # Primero los ejemplos relacionados; completamos con estilo reciente.
        rows = []
        seen = set()
        for r in relevant + recent:
            if r.id in seen:
                continue
            seen.add(r.id)
            rows.append(r)
            if len(rows) >= limit:
                break

        parts = []
        total = 0
        for r in rows:
            q = (r.user_text or "").strip()
            a = (r.response_text or "").strip()
            if not a:
                continue
            piece = (f"USUARIO: {q}\n" if q else "") + f"JOHANNA: {a}"
            if total + len(piece) > 14000:
                continue
            parts.append(piece)
            total += len(piece)
        return "\n\n".join(parts)
    except Exception as e:
        logging.info("No pude cargar ejemplos de Johanna: %s", e)
        return ""


def _extract_question_from_admin_message(base_text: str) -> str:
    """Intenta recuperar la pregunta original desde la notificación enviada al admin."""
    s = (base_text or "").strip()
    if not s:
        return ""
    m = re.search(r"🗨️\s*(.*?)(?:\n\n✏️|\Z)", s, flags=re.S)
    if m:
        return m.group(1).strip()
    # Para notificaciones de media, el caption suele quedar después del encabezado.
    if s.startswith("📩") and "\n\n" in s:
        tail = s.split("\n\n", 1)[1].strip()
        if tail and not tail.startswith("Pulsa para responder"):
            return tail
    return ""


def _convert_voice_to_mp3(raw_bytes: bytes):
    """Convierte la nota OGG/Opus de Telegram a MP3 si ffmpeg está disponible."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        try:
            import imageio_ffmpeg  # opcional; si está instalado, trae un binario ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    try:
        with tempfile.TemporaryDirectory(prefix="joha_voice_") as td:
            in_path = os.path.join(td, "voice.ogg")
            out_path = os.path.join(td, "voice.mp3")
            with open(in_path, "wb") as f:
                f.write(raw_bytes)
            subprocess.run(
                [ffmpeg_bin, "-y", "-loglevel", "error", "-i", in_path, "-vn", "-ac", "1", "-b:a", "64k", out_path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=45,
            )
            return Path(out_path).read_bytes()
    except Exception as e:
        logging.info("No pude convertir nota de voz para transcripción: %s", e)
        return None


async def _transcribe_admin_voice(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> str:
    """Transcribe una respuesta de voz de Johanna para aprender su estilo/contenido.

    La voz se envía al usuario aunque esta transcripción falle; el aprendizaje de
    audio es una capa adicional y nunca rompe el flujo principal.
    """
    if not (HAS_HTTPX and OPENAI_API_KEY and file_id):
        return ""
    try:
        tg_file = await context.bot.get_file(file_id)
        raw = bytes(await tg_file.download_as_bytearray())
        if not raw:
            return ""

        # Telegram entrega notas de voz normalmente como OGG/Opus; OpenAI admite
        # formatos como MP3/WAV/WEBM, por eso convertimos de forma local.
        mp3_bytes = await asyncio.to_thread(_convert_voice_to_mp3, raw)
        if not mp3_bytes:
            logging.warning("Transcripción de voz omitida: Railway necesita ffmpeg (o imageio-ffmpeg) para convertir OGG/Opus.")
            return ""
        if len(mp3_bytes) > 25 * 1024 * 1024:
            logging.warning("Nota de voz demasiado grande para transcripción (>25 MB).")
            return ""

        files_payload = {"file": ("johanna_voice.mp3", mp3_bytes, "audio/mpeg")}
        data_payload = {
            "model": OPENAI_TRANSCRIBE_MODEL,
            "prompt": "Conversación de trading de JOHAALETRADER. Términos frecuentes: Stockity, Binomo, CRYPTO IDX, martingala, señales, Premium, Prestige, ID, depósito.",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": "Bearer " + OPENAI_API_KEY},
                files=files_payload,
                data=data_payload,
            )
        if resp.status_code != 200:
            logging.warning("OpenAI transcription devolvió %s: %s", resp.status_code, resp.text[:350])
            return ""
        data = resp.json()
        return str(data.get("text") or "").strip()
    except Exception as e:
        logging.warning("No pude transcribir respuesta de voz de Johanna: %s", e)
        return ""


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
    "🗓 **Normalmente de lunes a sábado**\n"
    "• Primera sesión: alrededor de 5:00 pm\n"
    "• Sesión nocturna: puede ser 8:00 pm, 8:30 pm o 9:00 pm\n\n"
    "🇨🇴 Hora Colombia. Algunos sábados puede no haber transmisión.\n"
    "🔐 Las sesiones privadas VIP las anuncio previamente dentro del canal VIP.\n\n"
    "🚀 *Nos vemos en vivo*"
)

LIVE_HORARIOS_EN = (
    "📅 **MY LIVE SCHEDULE**\n\n"
    "🗓 **Usually Monday to Saturday**\n"
    "• First session: around 5:00 pm\n"
    "• Evening session: may be 8:00 pm, 8:30 pm or 9:00 pm\n\n"
    "🇨🇴 Colombia time. Some Saturdays there may be no public live.\n"
    "🔐 Private VIP sessions are announced in advance inside the VIP channel.\n\n"
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

    # ---- Gestión de capital: siempre la revisa Johanna directamente ----
    if any(k in t for k in [
        "gestion de capital", "gestión de capital", "gestionar capital", "manejas capital",
        "manejo de capital", "inversion contigo", "inversión contigo", "enviarte capital",
        "capital management", "manage my capital", "investment with you"
    ]):
        return "GESTION_CAPITAL"

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

    # ---- Niveles / planes / inversión mínima ----
    if any(k in t for k in [
        "niveles", "nivel basico", "nivel básico", "nivel premium", "nivel prestige",
        "planes", "plan basico", "plan básico", "plan premium", "plan prestige",
        "cuanto necesito para entrar", "cuánto necesito para entrar",
        "cuanto debo depositar", "cuánto debo depositar",
        "inversion minima", "inversión mínima", "minimum investment",
        "cuanto cuesta", "cuánto cuesta", "cuanto vale", "cuánto vale",
        "que niveles tienes", "qué niveles tienes", "niveles disponibles", "planes disponibles",
        "cuanto hay que invertir", "cuánto hay que invertir", "de cuanto es la inversion", "de cuánto es la inversión",
        "levels", "plans", "basic level", "premium level", "prestige level"
    ]):
        return "NIVELES"

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


def respuesta_niveles_es() -> str:
    return (
        "📊 Niveles JT TRADERS\n\n"
        "💜 Mi comunidad es totalmente GRATIS. No pagas una membresía: la inversión de cada nivel se deposita directamente en TU propia cuenta de trading.\n\n"
        "🟢 Básico — desde 50 USD\n"
        "Formación + herramientas y señales CRYPTO IDX limitadas.\n\n"
        "🔵 Premium — desde 200 USD\n"
        "Señales completas, bot IA 24/7, operativas en vivo y enfoque multi-broker.\n\n"
        "🟣 Prestige — desde 500 USD\n"
        "Todo Premium + mentorías privadas, acompañamiento cercano y preparación para cuentas de fondeo.\n\n"
        "⚠️ IMPORTANTE: primero regístrate con uno de mis enlaces y ANTES de depositar en tu cuenta de trading envíame tu ID para verificar que quedó correctamente vinculado conmigo.\n\n"
        f"⭐ Stockity — opción principal:\n{ENLACE_REFERIDO_STOCKITY}\n\n"
        f"🔹 Binomo — opción secundaria:\n{ENLACE_REFERIDO}\n\n"
        "🚀 Haz tu registro, envíame tu ID y yo te indico el siguiente paso."
    )


def respuesta_niveles_en() -> str:
    return (
        "📊 JT TRADERS Levels\n\n"
        "💜 My community is completely FREE. There is no membership fee: the investment for each level is deposited directly into YOUR own trading account.\n\n"
        "🟢 Basic — from 50 USD\n"
        "Education + limited CRYPTO IDX signals/tools.\n\n"
        "🔵 Premium — from 200 USD\n"
        "Full signals, 24/7 AI bot, live trading and multi-broker access.\n\n"
        "🟣 Prestige — from 500 USD\n"
        "Everything in Premium + private mentoring, closer guidance and funded-account preparation.\n\n"
        "⚠️ IMPORTANT: register with one of my links first and BEFORE depositing into your trading account, send me your ID so I can verify that it is correctly linked to me.\n\n"
        f"⭐ Stockity — primary option:\n{ENLACE_REFERIDO_STOCKITY}\n\n"
        f"🔹 Binomo — secondary option:\n{ENLACE_REFERIDO}\n\n"
        "🚀 Complete your registration, send me your ID and I’ll guide you through the next step."
    )


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
        "🆔 **¿Dónde encuentro mi ID de Stockity o Binomo?**\n\n"
        "1) Entra a tu cuenta (app o web).\n"
        "2) Ve a tu **perfil / ajustes** (icono de usuario).\n"
        "3) Busca el campo **ID** o **User ID** y cópialo.\n\n"
        "Si no lo ves, dime si estás en app o navegador y te guío 👇"
    )


def respuesta_next_step_es() -> str:
    return (
        "✅ Perfecto. El **siguiente paso** es validar tu **ID** para confirmar que tu registro quedó bien "
        "**antes de que deposites**.\n\n"
        "📌 Envíame aquí tu **ID de Stockity o Binomo** (solo el número) y lo dejo en validación.\n\n"
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
        "Quiero revisar bien tu caso para darte la información correcta.\n\n"
        "Escríbeme directamente aquí y lo revisamos 👇"
    )



async def binomo_helpcenter_snippets(query: str, max_results: int = 3) -> str:
    # Se conserva por compatibilidad; la IA usa la base de conocimiento de JOHAALETRADER.
    return ""


async def openai_answer(question: str, chat_id: int, lang: str, stage: str) -> str:
    if not (HAS_HTTPX and OPENAI_API_KEY):
        return ""
    try:
        language_instruction = "Responde en español." if lang == "es" else "Reply in English."
        real_examples = _johanna_examples_as_text(question=question, limit=28)
        system = f"""
Eres la voz digital de Johanna, conocida como JOHAALETRADER / JT TRADERS TEAMS.
RESPONDE EN PRIMERA PERSONA COMO SI FUERAS JOHANNA. No digas que eres Johabot, un asistente virtual, una IA o un modelo.
Tu función es atender prospectos y miembros de la comunidad usando la base oficial, el historial del usuario y ejemplos reales de respuestas de Johanna.
{language_instruction}

ESTILO DE JOHANNA
- Cercano, muy positivo, directo, comercial y útil, sin exageraciones engañosas.
- Normalmente 2 a 5 párrafos CORTOS. La gente debe poder leer la respuesta rápido.
- Usa algunos emojis para hacer la respuesta atractiva, sin saturar.
- Contesta primero lo que preguntaron y, cuando sea natural, mueve al siguiente paso útil: registro → ID → depósito → acceso.
- Si es un miembro actual, prioriza resolver su duda de señales, bots, clases o herramientas antes de hacer cualquier CTA comercial.
- Si preguntan por niveles, planes, inversión mínima o cuánto necesitan para entrar, SIEMPRE debes comenzar aclarando que mi comunidad es GRATIS y que el dinero se deposita directamente en la PROPIA cuenta de trading del usuario. Luego muestra Básico/Premium/Prestige con emojis, SIN asteriscos alrededor de los nombres y SIN mencionar Forex automatizado. Al final incluye los enlaces de Stockity primero y Binomo segundo, y recalca que ANTES de depositar deben enviarme el ID para validarlo conmigo.
- Los ejemplos reales de Johanna sirven para aprender vocabulario, ritmo y conocimiento nuevo. Si un ejemplo contiene una regla general claramente expresada por Johanna y es más reciente que información vieja, puedes usarla. NUNCA generalices una excepción que claramente se refiera a una sola persona o caso.

LÍMITES IMPORTANTES
- No inventes información, promociones, cupos, resultados ni horarios exactos no confirmados.
- No prometas ganancias ni resultados garantizados.
- No confirmes ID, depósito, afiliación, pago ni acceso VIP.
- Para gestión de capital, menos de 50 USD, VPN/restricción de país, validaciones, comprobantes, bloqueos o revisión de una cuenta específica: deriva directamente a Johanna usando el chat de validación.
- No des instrucciones para evadir KYC, usar identidad/documentos ajenos como si fueran propios, ni saltar restricciones con VPN/proxy.
- No solicites claves, contraseñas, códigos 2FA, seed phrases ni credenciales.
- Si falta un dato oficial, dilo con naturalidad y deriva a Johanna; no rellenes huecos.

ETAPA ACTUAL DEL USUARIO: {stage}

BASE DE CONOCIMIENTO OFICIAL:
{JOHA_KNOWLEDGE}

EJEMPLOS REALES RECIENTES DE CÓMO RESPONDE JOHANNA:
{real_examples or '(todavía no hay suficientes ejemplos manuales guardados)'}
""".strip()

        history_text = _history_as_text(chat_id)
        user_input = (
            f"HISTORIAL RECIENTE DE ESTE USUARIO:\n{history_text or '(sin historial previo)'}\n\n"
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
                "📩 Recibido. ¿Esta imagen es tu **ID** de Stockity o Binomo, tu **comprobante de depósito/activación** o **era otra cosa**?"
                if lang == "es" else
                "📩 Received. Is this image your **Stockity/Binomo ID**, your **deposit/activation proof**, or **something else**?"
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
        "ID_SUBMIT", "VPN", "PAIS", "NEXT_STEP", "WHERE_SEND_ID", "LIVE", "ID", "NIVELES", "GESTION_CAPITAL",
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
            "El nivel Básico inicia desde **50 USD** ✅\n\nSi en este momento cuentas con menos de ese monto, escríbeme directamente y reviso tu caso contigo 👇"
            if lang == "es" else
            "The Basic level starts from **50 USD** ✅\n\nIf you currently have less than that amount, message me directly so I can review your case with you 👇"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "AUTO_MIN50_ESCALATE", msg)
        return

    if intent == "DEPOSITO":
        msg = (
            "Perfecto ✅\n\nEnvíame aquí tu **comprobante de depósito/activación** (foto o captura) y también tu **ID de Stockity o Binomo en texto** (solo el número) para validarlo y habilitar tu acceso 👇"
            if lang == "es" else
            "Perfect ✅\n\nSend me your **deposit/activation proof** (photo or screenshot) and your **Stockity/Binomo ID as text** (numbers only) so it can be validated and your access enabled 👇"
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

    if intent == "GESTION_CAPITAL":
        msg = (
            "📊 Sí, manejo opciones de gestión de capital, pero este tema lo reviso **personalmente** contigo porque depende de la modalidad, condiciones y disponibilidad.\n\nEscríbeme directamente aquí y te explico todo 👇"
            if lang == "es" else
            "📊 Yes, I offer capital-management options, but I review this **personally** with you because it depends on the current option, conditions and availability.\n\nMessage me directly here and I’ll explain everything 👇"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "GESTION_CAPITAL_ESCALATE", msg)
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
        msg = respuesta_next_step_es() if lang == "es" else "✅ The next step is to validate your Stockity or Binomo ID before depositing. Send me the ID as text (numbers only) and I will leave it for validation 👇"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, intent, msg)
        return

    if intent == "WHERE_SEND_ID":
        msg = respuesta_where_send_id_es() if lang == "es" else "Yes ✅ You can send your ID right here (numbers only) and I will leave it for validation. You can also message me directly 👇"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, intent, msg)
        return

    if intent == "NIVELES":
        msg = respuesta_niveles_es() if lang == "es" else respuesta_niveles_en()
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard(), disable_web_page_preview=True)
        await send_admin_auto_log(context, update, "NIVELES", msg)
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
        msg = respuesta_id_es() if lang == "es" else "🆔 Open your Stockity or Binomo profile/settings, find the **ID / User ID** field and copy the number. If you cannot find it, tell me whether you are using the app or browser 👇"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard())
        await send_admin_auto_log(context, update, "ID", msg)
        return

    # Todas las demás preguntas: Johanna tiene prioridad. Si no responde, entra la IA después del tiempo configurado.
    # Esto incluye HUMAN_CHAT, RETIRO, METODOS, EMAIL, OTRO y consultas detalladas sobre bonos.
    # Gestión de capital, VPN/país, menos de 50 USD y validaciones se escalan de inmediato y no llegan a la IA.
    schedule_ai_reply(update, context, texto)

def _learn_direct_admin_message(chat_id: int, response_text: str, response_type: str = "text"):
    try:
        pending = _get_pending_ai(chat_id)
        question = (pending or {}).get("text") or ""
        _save_johanna_example(chat_id, question, response_text, get_user_lang(chat_id), response_type=response_type)
    except Exception as e:
        logging.info("No pude guardar aprendizaje de /enviar: %s", e)


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
            if mensaje:
                _learn_direct_admin_message(chat_id, mensaje, "photo_caption")
            _cancel_pending_ai(context, chat_id, manual_reply=mensaje or "[Imagen enviada por Johanna]")
            await update.message.reply_text("✅ Imagen enviada con éxito.")
            return

        # Enviar imagen como DOCUMENTO
        if update.message.document and update.message.document.mime_type.startswith("image/"):
            await context.bot.send_document(chat_id=chat_id, document=update.message.document.file_id, caption=mensaje)
            if mensaje:
                _learn_direct_admin_message(chat_id, mensaje, "document_caption")
            _cancel_pending_ai(context, chat_id, manual_reply=mensaje or "[Imagen enviada por Johanna]")
            await update.message.reply_text("✅ Imagen enviada como documento.")
            return

        # Enviar video
        if update.message.video:
            await context.bot.send_video(chat_id=chat_id, video=update.message.video.file_id, caption=mensaje)
            if mensaje:
                _learn_direct_admin_message(chat_id, mensaje, "video_caption")
            _cancel_pending_ai(context, chat_id, manual_reply=mensaje or "[Video enviado por Johanna]")
            await update.message.reply_text("✅ Video enviado con éxito.")
            return

        # Enviar audio
        if update.message.audio:
            await context.bot.send_audio(chat_id=chat_id, audio=update.message.audio.file_id, caption=mensaje)
            if mensaje:
                _learn_direct_admin_message(chat_id, mensaje, "audio_caption")
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
            _learn_direct_admin_message(chat_id, mensaje, "text")
            _cancel_pending_ai(context, chat_id, manual_reply=mensaje)
            await update.message.reply_text("✅ Mensaje enviado con éxito.")
        else:
            await update.message.reply_text("⚠️ No se pudo enviar nada. Revisa el contenido.")
    except Exception as e:
        print(f"❌ Error al enviar mensaje directo: {e}")
        await update.message.reply_text("⚠️ Ocurrió un error al intentar enviar el mensaje.")


# === AVISO MASIVO DE LIVE PARA USUARIOS RECIENTES ===
try:
    LIVE_BROADCAST_DAYS = max(1, int(os.getenv("LIVE_BROADCAST_DAYS", "14")))
except Exception:
    LIVE_BROADCAST_DAYS = 14

LIVE_BROADCAST_MESSAGE_ES = (
    "🔴 **¡YA CASI EMPEZAMOS EL LIVE!** 🚀\n\n"
    "Voy a conectarme en vivo para operar, analizar el mercado y compartir la sesión contigo. ✨\n\n"
    "🎵 **TikTok es mi canal principal para el LIVE.** Toca el botón de abajo y entra ahora.\n"
    "▶️ Si transmito simultáneamente, también podrás entrar por YouTube."
)

LIVE_BROADCAST_MESSAGE_EN = (
    "🔴 **I’M ABOUT TO GO LIVE!** 🚀\n\n"
    "I’m going live to trade, analyze the market and share the session with you. ✨\n\n"
    "🎵 **TikTok is my main LIVE channel.** Tap the button below and join now.\n"
    "▶️ If I stream simultaneously, you can also join on YouTube."
)


def live_broadcast_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 ENTRAR AL LIVE EN TIKTOK", url="https://www.tiktok.com/@joha_binomo?_t=ZN-8xceLrp5GTe&_r=1")],
        [InlineKeyboardButton("▶️ VER EN YOUTUBE", url="https://www.youtube.com/@johaalegria.trader")],
    ])


def _recent_live_recipients(days: int = LIVE_BROADCAST_DAYS):
    """Obtiene usuarios recientes.

    Para usuarios antiguos creados antes de que existiera last_activity_at no es
    posible reconstruir la fecha exacta de sus mensajes históricos. Si no hay
    ningún usuario reciente detectable, usa como recuperación a usuarios que
    tienen un mensaje guardado en la base para no perder los contactos previos.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    try:
        with Session() as session:
            recent_rows = (
                session.query(Usuario.telegram_id, Usuario.lang)
                .filter(Usuario.last_activity_at.isnot(None))
                .filter(Usuario.last_activity_at >= cutoff)
                .all()
            )

            mode = "recent"
            rows = recent_rows
            if not rows:
                rows = (
                    session.query(Usuario.telegram_id, Usuario.lang)
                    .filter(Usuario.mensaje.isnot(None))
                    .filter(Usuario.mensaje != "")
                    .all()
                )
                mode = "legacy_fallback"

        recipients = []
        seen = set()
        for telegram_id, lang in rows:
            try:
                cid = int(telegram_id)
            except Exception:
                continue
            if cid == ADMIN_ID or cid in seen:
                continue
            seen.add(cid)
            recipients.append((cid, lang if lang in ("es", "en") else "es"))
        return recipients, mode
    except Exception as e:
        logging.warning("No pude obtener destinatarios LIVE: %s", e)
        return [], "error"


async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Abre confirmación; escribir /live o exactamente 'live' nunca envía sin confirmar."""
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    recipients, recipient_mode = _recent_live_recipients()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sí, enviar aviso LIVE", callback_data="live_broadcast_confirm")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="live_broadcast_cancel")],
    ])
    audience_text = (
        f"Se enviará a **{len(recipients)} usuarios** con actividad detectada en los últimos **{LIVE_BROADCAST_DAYS} días**.\n\n"
        if recipient_mode == "recent"
        else f"No encontré actividad reciente con fecha confiable. Como estos contactos ya existían antes de activar el registro de última actividad, usaré **{len(recipients)} usuarios que sí tienen mensajes guardados** en la base.\n\n"
    )
    await update.effective_message.reply_text(
        "🔴 Aviso LIVE preparado.\n\n" + audience_text + "¿Confirmas el envío?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb,
    )


async def live_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    if query.data == "live_broadcast_cancel":
        await query.edit_message_text("❌ Aviso LIVE cancelado. No se envió ningún mensaje.")
        return

    if query.data != "live_broadcast_confirm":
        return

    recipients, recipient_mode = _recent_live_recipients()
    audience_label = "usuarios recientes" if recipient_mode == "recent" else "contactos con mensajes guardados"
    await query.edit_message_text(
        f"⏳ Enviando aviso LIVE a {len(recipients)} {audience_label}..."
    )

    sent = 0
    failed = 0
    for chat_id, lang in recipients:
        msg = LIVE_BROADCAST_MESSAGE_ES if lang == "es" else LIVE_BROADCAST_MESSAGE_EN
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=live_broadcast_keyboard(),
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception as e:
            # Si Telegram pide una pausa por rate limit, esperamos y reintentamos una vez.
            retry_after = getattr(e, "retry_after", None)
            if retry_after:
                try:
                    await asyncio.sleep(float(retry_after) + 1)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=msg,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=live_broadcast_keyboard(),
                        disable_web_page_preview=True,
                    )
                    sent += 1
                    await asyncio.sleep(0.06)
                    continue
                except Exception:
                    pass
            failed += 1
            logging.info("Aviso LIVE no entregado a %s: %s", chat_id, e)

        # Ritmo conservador para no golpear límites de Telegram.
        await asyncio.sleep(0.06)

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "✅ Aviso LIVE finalizado.\n\n"
            f"👥 Enviados: {sent}\n"
            f"🚫 No entregados: {failed}\n"
            + (f"📅 Ventana usada: últimos {LIVE_BROADCAST_DAYS} días" if recipient_mode == "recent" else "📚 Modo recuperación: contactos con mensajes históricos guardados")
        ),
    )


# === EJECUCIÓN ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).post_init(recover_pending_ai_jobs).build()

    # Comando /start (selector de idioma)
    app.add_handler(CommandHandler("start", start))

    # Aviso LIVE: /live o escribir exactamente "live" abre confirmación; nunca envía accidentalmente.
    app.add_handler(CommandHandler("live", live_command))
    app.add_handler(MessageHandler(filters.User(ADMIN_ID) & filters.Regex(r"(?i)^live$"), live_command))

    # Enviar imagen, video, audio usando /enviar desde caption (solo multimedia)
    app.add_handler(MessageHandler(
        filters.User(ADMIN_ID) &
        (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO) &
        filters.CaptionRegex(r"^/enviar "),
        enviar_mensaje_directo
    ))

    # Confirmación/cancelación del aviso LIVE (antes del callback general).
    app.add_handler(CallbackQueryHandler(live_broadcast_callback, pattern="^live_broadcast_"))

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
