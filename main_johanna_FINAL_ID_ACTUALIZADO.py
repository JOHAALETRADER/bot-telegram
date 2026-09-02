import logging
import asyncio
import re
import json
import tempfile
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, time as dt_time, timezone
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
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
from sqlalchemy.orm import sessionmaker, declarative_base
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
BOT_VERSION = "v7.9.6-20260901-UNIFIED-LINK-FORMAT"


def utcnow_naive():
    """UTC actual sin tzinfo, compatible con las columnas TIMESTAMP existentes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_private_user_id(value) -> bool:
    """True únicamente para IDs positivos de chats privados de usuarios."""
    try:
        return int(value) > 0
    except Exception:
        return False


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


def _redact_sensitive_text(value):
    """Oculta el BOT_TOKEN si llega a aparecer dentro de los logs."""
    try:
        text = str(value)
    except Exception:
        return value

    token_env = os.getenv("BOT_TOKEN")
    if token_env:
        text = text.replace(token_env, "<REDACTED_BOT_TOKEN>")

    # Token expuesto dentro de URLs tipo https://api.telegram.org/bot<token>/...
    text = re.sub(r"bot\d{6,}:[A-Za-z0-9_-]{20,}", "bot<REDACTED_BOT_TOKEN>", text)
    # Token expuesto como valor aislado
    text = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "<REDACTED_BOT_TOKEN>", text)
    return text


class RedactSensitiveLogFilter(logging.Filter):
    """Filtra cualquier línea de log que accidentalmente incluya el token."""
    def filter(self, record):
        try:
            record.msg = _redact_sensitive_text(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: _redact_sensitive_text(v) for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(_redact_sensitive_text(v) for v in record.args)
                else:
                    record.args = _redact_sensitive_text(record.args)
        except Exception:
            pass
        return True


_redaction_filter = RedactSensitiveLogFilter()
_root_logger = logging.getLogger()
_root_logger.addFilter(_redaction_filter)
for _handler in _root_logger.handlers:
    _handler.addFilter(_redaction_filter)

# Baja el ruido de requests HTTP y evita exponer URLs completas en INFO.
for _logger_name in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection", "httpcore.proxy", "httpcore.http2"):
    logging.getLogger(_logger_name).setLevel(logging.WARNING)

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


class UserActivity(Base):
    """Actividad reciente separada de la tabla histórica de usuarios.

    Usar una tabla independiente evita alterar la tabla `usuarios` que ya existe
    en Railway y elimina el riesgo de romper consultas antiguas por una columna
    nueva que aún no haya sido migrada.
    """
    __tablename__ = "user_activity"
    telegram_id      = Column(String, primary_key=True)
    lang             = Column(String, default="es")
    last_activity_at = Column(DateTime, default=datetime.utcnow)


class BotEvent(Base):
    """Eventos operativos para reportes diarios sin alterar la tabla usuarios."""
    __tablename__ = "bot_events"
    id          = Column(Integer, primary_key=True)
    telegram_id = Column(String, index=True)
    event_type  = Column(String, index=True)
    detail      = Column(Text)
    created_at  = Column(DateTime, default=datetime.utcnow, index=True)


class CampaignJob(Base):
    """Tareas persistentes de remarketing A/B para sobrevivir reinicios de Railway."""
    __tablename__ = "campaign_jobs"
    id          = Column(Integer, primary_key=True)
    telegram_id = Column(String, index=True)
    series      = Column(String, index=True)
    step        = Column(String)
    lang        = Column(String, default="es")
    due_at      = Column(DateTime, index=True)
    sent_at     = Column(DateTime, nullable=True, index=True)
    created_at  = Column(DateTime, default=utcnow_naive)


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

# La actividad reciente se guarda en la tabla independiente `user_activity`.
# Base.metadata.create_all() la crea automáticamente sin modificar `usuarios`.

# --- fin migración ---

# === ENLACES ===
CANAL_RESULTADOS = "https://t.me/+wyjkDFenUMlmMTUx"
CANAL_ES = "https://t.me/JohaaleTrader_es"
CANAL_EN = "https://t.me/JohaaleTrader_en"
ENLACE_REFERIDO  = "https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"
ENLACE_REFERIDO_STOCKITY = "https://stockity-r3.com/?a=95604cd745da&t=0&ac=JOHAALETRADER"


# Chat personal / validación (URL del botón de soporte)
SUPPORT_URL = "https://t.me/Johaaletradervalidacion"

# Canales para avisos LIVE. El bot debe ser administrador con permiso para publicar.
INFO_CHANNEL_ID = os.getenv("INFO_CHANNEL_ID", "@JohaaleTrader_es")
try:
    VIP_CHAT_ID = int(os.getenv("VIP_CHAT_ID", "-1001946870620"))
except Exception:
    VIP_CHAT_ID = -1001946870620
try:
    VIP_TOPIC_ID = int(os.getenv("VIP_TOPIC_ID", "1"))
except Exception:
    VIP_TOPIC_ID = 1

TIKTOK_LIVE_URL = "https://www.tiktok.com/@joha_binomo?_t=ZN-8xceLrp5GTe&_r=1"
YOUTUBE_LIVE_URL = "https://www.youtube.com/@johaalegria.trader"
# Colombia no usa horario de verano; offset fijo evita depender de tzdata del sistema.
COLOMBIA_TZ = timezone(timedelta(hours=-5))

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

🔗 Stockity — opción principal:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — opción secundaria:
{ENLACE_REFERIDO}

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

🔗 Stockity — opción principal:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — opción secundaria:
{ENLACE_REFERIDO}

📌 SUPER IMPORTANTE: envíame el nuevo ID antes de depositar para validarlo."""

MENSAJE_YA_TENGO_CUENTA_EN = f"""If you already have a Stockity or Binomo account and it was NOT registered through my link, the first step is to review how to correctly link a new account.

✅ If your current account has funds, withdraw them first if the platform and your account conditions allow it. If you have an active bonus, review its withdrawal conditions first.

✅ If the platform allows a new account, register through my link using a different email that has never been used on that platform and the account holder’s real, verifiable information.

✅ If the account belongs to a family member, it must genuinely be that person’s account: their own information, identity document, and deposit/withdrawal methods in their name.

❗ Do not use a VPN to bypass country restrictions. If you have a country/availability issue, message me directly so I can review your case.

🔗 Stockity — primary option:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — secondary option:
{ENLACE_REFERIDO}

📌 VERY IMPORTANT: send me the new ID before depositing so I can validate it."""

# Recordatorios (ES) — tiempos internos; los mensajes no mencionan cuánto tiempo pasó
MENSAJE_1H_ES = f"""🚀 Si quieres empezar, el primer paso es mucho más sencillo de lo que parece.

Registra tu cuenta con uno de mis enlaces y envíame tu ID antes de depositar para validar que todo haya quedado correctamente vinculado.

✨ Desde el nivel Básico puedes comenzar con 50 USD y acceder a formación y herramientas según tu nivel.

👉 Da el paso ahora:

🔗 Stockity — opción principal:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — opción secundaria:
{ENLACE_REFERIDO}"""

MENSAJE_3H_ES = f"""📈 No necesitas aprender trading sin dirección. La idea de la comunidad es que tengas una ruta, formación, señales y herramientas que te ayuden a desarrollar tu operativa con estructura.

Tu siguiente acción es simple: crea tu cuenta con mi enlace y envíame tu ID para validarlo antes del depósito.

✅ Empieza hoy y deja listo tu acceso:

🔗 Stockity — opción principal:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — opción secundaria:
{ENLACE_REFERIDO}"""

MENSAJE_24H_ES = f"""✨ Si estabas esperando el momento para comenzar, conviértelo en una acción concreta.

Puedes elegir el nivel que mejor se ajuste a tu capital y avanzar paso a paso con formación, señales, bots y otras herramientas según corresponda.

🔥 Regístrate ahora, envíame tu ID y yo te indico el siguiente paso para activar correctamente tu acceso.

📊 Resultados de la comunidad: {CANAL_RESULTADOS}

🔗 Stockity — opción principal:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — opción secundaria:
{ENLACE_REFERIDO}"""

MENSAJE_48H_ES = f"""🎯 La diferencia entre seguir pensando en empezar y realmente avanzar es completar el primer paso.

Haz tu registro con mi enlace, envíame tu ID antes de depositar y déjame validar tu cuenta. A partir de ahí podrás elegir tu nivel y continuar con la activación.

🚀 Empieza ahora:

🔗 Stockity — opción principal:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — opción secundaria:
{ENLACE_REFERIDO}

Cuando termines, envíame tu ID y continuamos."""

# Recordatorios (EN) — internal timing only; messages do not mention elapsed time
MENSAJE_1H_EN = f"""🚀 If you want to get started, the first step is simpler than it looks.

Create your account using one of my links and send me your ID before depositing so I can validate that it was linked correctly.

✨ You can start at the Basic level from 50 USD and access education and tools according to your level.

👉 Take the first step now:

🔗 Stockity — primary option:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — secondary option:
{ENLACE_REFERIDO}"""

MENSAJE_3H_EN = f"""📈 You do not have to learn trading without direction. The community gives you a structured path with education, signals and tools to develop your trading process.

Your next action is simple: create your account with my link and send me your ID for validation before depositing.

✅ Start today:

🔗 Stockity — primary option:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — secondary option:
{ENLACE_REFERIDO}"""

MENSAJE_24H_EN = f"""✨ If you were waiting for the right moment to begin, turn that intention into a concrete action.

Choose the level that fits your capital and move forward step by step with education, signals, bots and other tools according to your level.

🔥 Register now, send me your ID and I will guide you through the next activation step.

📊 Community results: {CANAL_RESULTADOS}

🔗 Stockity — primary option:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — secondary option:
{ENLACE_REFERIDO}"""

MENSAJE_48H_EN = f"""🎯 The difference between thinking about starting and actually moving forward is completing the first step.

Register with my link, send me your ID before depositing, and let me validate your account. Then you can choose your level and continue with activation.

🚀 Start now:

🔗 Stockity — primary option:
{ENLACE_REFERIDO_STOCKITY}

🔗 Binomo — secondary option:
{ENLACE_REFERIDO}

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
    if not _is_private_user_id(chat_id):
        return
    try:
        await context.bot.send_message(chat_id=chat_id, text=text_es if lang == "es" else text_en, reply_markup=support_keyboard(lang))
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
    """Obtiene idioma sin cargar todas las columnas de Usuario.

    Así una columna auxiliar nunca puede impedir que Johanna reciba la
    notificación de un mensaje.
    """
    try:
        with Session() as session:
            row = session.query(Usuario.lang).filter_by(telegram_id=str(chat_id)).first()
            value = row[0] if row else None
            return value if value in ("es", "en") else "es"
    except Exception as e:
        logging.info("No pude leer idioma de %s; uso español: %s", chat_id, e)
        return "es"

def set_user_lang(chat_id: int, name: str, lang: str):
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not u:
            u = Usuario(telegram_id=str(chat_id), nombre=name, lang=lang, fecha_registro=utcnow_naive())
            session.add(u)
        else:
            u.lang = lang
        session.commit()
    _touch_user_activity(chat_id, lang)


# === UTIL: obtener/guardar etapa (stage) ===
STAGE_PRE = "PRE"
STAGE_POST = "POST"
STAGE_DEPOSITED = "DEPOSITED"

def get_user_stage(chat_id: int) -> str:
    try:
        with Session() as session:
            row = session.query(Usuario.stage).filter_by(telegram_id=str(chat_id)).first()
            s = row[0] if row and row[0] else STAGE_PRE
            return s if s in (STAGE_PRE, STAGE_POST, STAGE_DEPOSITED) else STAGE_PRE
    except Exception as e:
        logging.info("No pude leer etapa de %s; uso PRE: %s", chat_id, e)
        return STAGE_PRE

def set_user_stage(chat_id: int, stage: str):
    if stage not in (STAGE_PRE, STAGE_POST, STAGE_DEPOSITED):
        stage = STAGE_PRE
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if u:
            u.stage = stage
            session.commit()


def _extract_candidate_trading_id(text_value: str) -> str:
    """Extrae un ID de trading solo si el mensaje realmente parece un envío de ID."""
    raw = (text_value or "").strip()
    if not raw:
        return ""

    # Caso más habitual: el usuario pega únicamente su ID.
    if re.fullmatch(r"\d{6,12}", raw):
        return raw

    normalized = _norm(raw)
    id_context = any(k in normalized for k in (
        "id", "identificador", "account id", "user id",
        "stockity id", "binomo id", "mi id", "my id",
    ))
    if not id_context:
        return ""

    m = re.search(r"\b\d{6,12}\b", raw)
    return m.group(0) if m else ""


def _get_saved_trading_id(chat_id: int) -> str:
    try:
        with Session() as session:
            row = session.query(Usuario.binomo_id).filter_by(telegram_id=str(chat_id)).first()
            return str(row[0]).strip() if row and row[0] else ""
    except Exception as e:
        logging.warning("No pude leer ID guardado de %s: %s", chat_id, e)
        return ""


def _clear_saved_trading_id(chat_id: int):
    try:
        with Session() as session:
            u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
            if u:
                u.binomo_id = None
                session.commit()
    except Exception as e:
        logging.warning("No pude limpiar ID guardado de %s: %s", chat_id, e)


def _latest_event_row(chat_id: int, event_type: str):
    try:
        with Session() as session:
            return (
                session.query(BotEvent.created_at, BotEvent.detail)
                .filter(
                    BotEvent.telegram_id == str(chat_id),
                    BotEvent.event_type == event_type,
                )
                .order_by(BotEvent.created_at.desc(), BotEvent.id.desc())
                .first()
            )
    except Exception as e:
        logging.warning("No pude consultar evento %s de %s: %s", event_type, chat_id, e)
        return None


def _has_submitted_id_evidence(chat_id: int, trading_id: str) -> bool:
    """Confirma que el ID guardado provino realmente de un mensaje de ID del usuario."""
    trading_id = (trading_id or "").strip()
    if not trading_id:
        return False
    try:
        with Session() as session:
            rows = (
                session.query(BotEvent.created_at, BotEvent.detail)
                .filter(
                    BotEvent.telegram_id == str(chat_id),
                    BotEvent.event_type == "ID_SUBMITTED",
                )
                .order_by(BotEvent.created_at.desc(), BotEvent.id.desc())
                .limit(20)
                .all()
            )
        for created_at, detail in rows:
            if _extract_candidate_trading_id(detail or "") == trading_id:
                return True
        return False
    except Exception as e:
        logging.warning("No pude validar evidencia de ID enviado para %s: %s", chat_id, e)
        return False


def _strict_validated_id_state(chat_id: int) -> bool:
    """POST es válido solo si el MISMO ID fue enviado y luego validado explícitamente."""
    trading_id = _get_saved_trading_id(chat_id)
    if not trading_id:
        return False

    try:
        with Session() as session:
            submitted_rows = (
                session.query(BotEvent.created_at, BotEvent.detail)
                .filter(
                    BotEvent.telegram_id == str(chat_id),
                    BotEvent.event_type == "ID_SUBMITTED",
                )
                .order_by(BotEvent.created_at.desc(), BotEvent.id.desc())
                .limit(20)
                .all()
            )
            submitted_at = None
            for created_at, detail in submitted_rows:
                if _extract_candidate_trading_id(detail or "") == trading_id:
                    submitted_at = created_at
                    break

            if submitted_at is None:
                return False

            validated = (
                session.query(BotEvent.created_at, BotEvent.detail)
                .filter(
                    BotEvent.telegram_id == str(chat_id),
                    BotEvent.event_type == "ID_VALIDATED",
                )
                .order_by(BotEvent.created_at.desc(), BotEvent.id.desc())
                .first()
            )
            if not validated:
                return False

            validated_at, validation_detail = validated
            # Desde esta versión guardamos siempre el ID validado dentro del evento.
            if trading_id not in (validation_detail or ""):
                return False

            return bool(validated_at and validated_at >= submitted_at)
    except Exception as e:
        logging.warning("No pude verificar consistencia de validación para %s: %s", chat_id, e)
        return False


def _repair_inconsistent_stage(chat_id: int):
    """Autocorrige POST imposibles: sin ID enviado+validado vuelve a PRE."""
    stage = get_user_stage(chat_id)
    if stage == STAGE_POST and not _strict_validated_id_state(chat_id):
        set_user_stage(chat_id, STAGE_PRE)
        logging.warning(
            "🛡️ Stage inconsistente reparado para %s: POST -> PRE (faltaba evidencia estricta de ID enviado y validado)",
            chat_id,
        )
        return STAGE_PRE, True
    return stage, False


def _record_submitted_trading_id(chat_id: int, text_value: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Guarda un ID real y registra la evidencia sin confundir otros números con IDs."""
    trading_id = _extract_candidate_trading_id(text_value)
    if not trading_id:
        return ""

    previous_id = _get_saved_trading_id(chat_id)
    current_stage, _ = _repair_inconsistent_stage(chat_id)

    try:
        with Session() as session:
            u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
            if u:
                u.binomo_id = trading_id
                session.commit()
    except Exception as e:
        logging.warning("No pude guardar ID de trading para %s: %s", chat_id, e)
        return ""

    context.user_data["binomo_id"] = trading_id
    _log_event(chat_id, "ID_SUBMITTED", trading_id)

    # Si ya estaba POST y envía un ID DISTINTO, ese nuevo ID necesita validación.
    if current_stage == STAGE_POST and previous_id and previous_id != trading_id:
        set_user_stage(chat_id, STAGE_PRE)
        _cancel_jobs_prefix(context, "B", chat_id)
        schedule_series_a(chat_id, get_user_lang(chat_id), context)
        logging.info("ℹ️ Nuevo ID recibido para %s: vuelve a PRE hasta validarlo", chat_id)

    return trading_id


def _touch_user_activity(chat_id: int, lang: str | None = None):
    """Registra/actualiza actividad reciente solo para usuarios privados."""
    if not _is_private_user_id(chat_id):
        return
    try:
        resolved_lang = lang if lang in ("es", "en") else get_user_lang(chat_id)
        now = utcnow_naive()
        with Session() as session:
            row = session.get(UserActivity, str(chat_id))
            if row:
                row.last_activity_at = now
                row.lang = resolved_lang
            else:
                session.add(UserActivity(
                    telegram_id=str(chat_id),
                    lang=resolved_lang,
                    last_activity_at=now,
                ))
            session.commit()
    except Exception as e:
        logging.warning("No pude actualizar actividad de %s: %s", chat_id, e)


def _log_event(chat_id: int, event_type: str, detail: str = ""):
    """Registra eventos solo de usuarios privados; nunca interrumpe el bot si falla."""
    if not _is_private_user_id(chat_id):
        return
    try:
        with Session() as session:
            session.add(BotEvent(
                telegram_id=str(chat_id),
                event_type=(event_type or "UNKNOWN")[:60],
                detail=(detail or "")[:1500],
                created_at=utcnow_naive(),
            ))
            session.commit()
    except Exception as e:
        logging.warning("No pude registrar evento %s para %s: %s", event_type, chat_id, e)


def _colombia_day_utc_bounds(now_local=None):
    """Devuelve inicio y fin del día Colombia convertidos a UTC naive para PostgreSQL."""
    now_local = now_local or datetime.now(COLOMBIA_TZ)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def _event_user_ids(event_type: str, start_utc: datetime, end_utc: datetime):
    try:
        with Session() as session:
            rows = (
                session.query(BotEvent.telegram_id)
                .filter(BotEvent.event_type == event_type)
                .filter(BotEvent.created_at >= start_utc, BotEvent.created_at < end_utc)
                .distinct()
                .all()
            )
        valid_ids = set()
        for row in rows:
            if row and row[0] and _is_private_user_id(row[0]):
                valid_ids.add(str(row[0]))
        return valid_ids
    except Exception as e:
        logging.warning("No pude consultar eventos %s: %s", event_type, e)
        return set()


def _daily_report_text(now_local=None) -> str:
    now_local = now_local or datetime.now(COLOMBIA_TZ)
    start_utc, end_utc = _colombia_day_utc_bounds(now_local)
    writers = _event_user_ids("MESSAGE", start_utc, end_utc)
    ids_sent = _event_user_ids("ID_SUBMITTED", start_utc, end_utc)
    ids_validated = _event_user_ids("ID_VALIDATED", start_utc, end_utc)
    deposits_reported = _event_user_ids("DEPOSIT_REPORTED", start_utc, end_utc)
    activated = _event_user_ids("ACCOUNT_ACTIVATED", start_utc, end_utc)

    total_messages = 0
    no_id_no_deposit = 0
    waiting_deposit = 0
    try:
        with Session() as session:
            total_messages = (
                session.query(BotEvent.id)
                .filter(BotEvent.event_type == "MESSAGE")
                .filter(BotEvent.created_at >= start_utc, BotEvent.created_at < end_utc)
                .count()
            )
            if writers:
                rows = (
                    session.query(Usuario.telegram_id, Usuario.stage, Usuario.binomo_id)
                    .filter(Usuario.telegram_id.in_(list(writers)))
                    .all()
                )
                known = {str(r[0]): (r[1] or STAGE_PRE, r[2]) for r in rows}
                for uid in writers:
                    stage, saved_id = known.get(uid, (STAGE_PRE, None))
                    if stage == STAGE_PRE and not saved_id:
                        no_id_no_deposit += 1

            # Solo cuenta como "ID validado y pendiente de depósito" cuando
            # Johanna confirmó positivamente la validación durante el día.
            waiting_deposit = len(ids_validated - deposits_reported - activated)
    except Exception as e:
        logging.warning("No pude completar métricas de reporte diario: %s", e)

    fecha = now_local.strftime("%d/%m/%Y")
    return (
        f"📊 REPORTE DIARIO — {fecha}\n\n"
        f"👥 Personas que escribieron: {len(writers)}\n"
        f"💬 Mensajes recibidos: {total_messages}\n"
        f"🆔 Enviaron ID: {len(ids_sent)}\n"
        f"✅ ID validados: {len(ids_validated)}\n"
        f"💳 Avisaron que depositaron: {len(deposits_reported)}\n"
        f"🟢 Cuentas/depósitos confirmados: {len(activated)}\n"
        f"🟡 Escribieron pero siguen sin ID ni depósito: {no_id_no_deposit}\n"
        f"🔵 ID validado y pendientes de depósito: {waiting_deposit}\n\n"
        "⏰ Corte automático: 11:00 p. m. hora Colombia."
    )


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=_daily_report_text())
    except Exception as e:
        logging.warning("No pude enviar reporte diario: %s", e)


async def daily_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user and update.effective_user.id == ADMIN_ID:
        await update.effective_message.reply_text(_daily_report_text())


def schedule_daily_report(application):
    if not application.job_queue:
        return
    try:
        for job in application.job_queue.get_jobs_by_name("DAILY_REPORT_23_CO"):
            job.schedule_removal()
    except Exception:
        pass
    application.job_queue.run_daily(
        daily_report_job,
        time=dt_time(hour=23, minute=0, tzinfo=COLOMBIA_TZ),
        name="DAILY_REPORT_23_CO",
    )


# === Teclado de soporte (ES/EN según idioma del usuario) ===
def support_rows(lang: str = "es"):
    if lang == "en":
        return [
            [InlineKeyboardButton("💬 Message me here", url=SUPPORT_URL)],
            [InlineKeyboardButton("🏠 Back to main menu", callback_data="back_main_menu")],
        ]
    return [
        [InlineKeyboardButton("💬 Escríbeme aquí", url=SUPPORT_URL)],
        [InlineKeyboardButton("🏠 Volver al menú principal", callback_data="back_main_menu")],
    ]

def support_keyboard(lang: str = "es") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(support_rows(lang))

def live_keyboard(lang: str = "es") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 TikTok (Lives)", url=TIKTOK_LIVE_URL)],
        [InlineKeyboardButton("📲 Instagram (Lives)", url="https://www.instagram.com/johaale_trader/")],
        [InlineKeyboardButton("▶️ YouTube", url=YOUTUBE_LIVE_URL)],
        *support_rows(lang),
    ])

# === PANEL PRIVADO DE JOHANNA ===
ADMIN_MENU_TEXT = "⚙️ MENÚ ADMIN"


def admin_persistent_keyboard() -> ReplyKeyboardMarkup:
    """Botón persistente visible en el chat privado de Johanna."""
    return ReplyKeyboardMarkup(
        [[ADMIN_MENU_TEXT]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 /live — Aviso LIVE", callback_data="admin_panel_live")],
        [InlineKeyboardButton("📣 /marketing — Marketing manual", callback_data="admin_panel_marketing")],
        [InlineKeyboardButton("📊 /reporte — Reporte del día", callback_data="admin_panel_report")],
        [InlineKeyboardButton("🏠 /start — Inicio", callback_data="admin_panel_start")],
    ])


async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    await update.effective_message.reply_text(
        "🔐 PANEL ADMINISTRADOR\n\nElige una opción:",
        reply_markup=admin_panel_keyboard(),
    )


async def admin_panel_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Abre el panel al tocar el botón persistente."""
    await show_admin_panel(update, context)
    from telegram.ext import ApplicationHandlerStop
    raise ApplicationHandlerStop


async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    if query.data == "admin_panel_live":
        await live_command(update, context)
    elif query.data == "admin_panel_marketing":
        await marketing_command(update, context)
    elif query.data == "admin_panel_report":
        await context.bot.send_message(chat_id=ADMIN_ID, text=_daily_report_text())
    elif query.data == "admin_panel_start":
        lang = get_user_lang(ADMIN_ID)
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="🏠 Inicio — menú principal:",
            reply_markup=build_main_menu(lang),
        )


CAMPAIGN_OFFSETS = {
    "1h": 3600,
    "3h": 10800,
    "24h": 86400,
    "48h": 172800,
}


def _campaign_text_pair(series: str, step: str):
    """Devuelve (ES, EN) para un paso persistente de campaña."""
    if series == "B":
        mapping = {
            "1h": (MENSAJE_B_1H_ES, MENSAJE_B_1H_EN),
            "3h": (MENSAJE_B_3H_ES, MENSAJE_B_3H_EN),
            "24h": (MENSAJE_B_24H_ES, MENSAJE_B_24H_EN),
            "48h": (MENSAJE_B_48H_ES, MENSAJE_B_48H_EN),
        }
    else:
        mapping = {
            "1h": (MENSAJE_1H_ES, MENSAJE_1H_EN),
            "3h": (MENSAJE_3H_ES, MENSAJE_3H_EN),
            "24h": (MENSAJE_24H_ES, MENSAJE_24H_EN),
            "48h": (MENSAJE_48H_ES, MENSAJE_48H_EN),
        }
    return mapping.get(step, ("", ""))


def _delete_persistent_campaign_series(prefix: str, chat_id: int):
    """Elimina tareas A/B pendientes del usuario en PostgreSQL."""
    try:
        with Session() as session:
            session.query(CampaignJob).filter(
                CampaignJob.telegram_id == str(chat_id),
                CampaignJob.series == str(prefix),
                CampaignJob.sent_at.is_(None),
            ).delete(synchronize_session=False)
            session.commit()
    except Exception as e:
        logging.warning("No pude cancelar campaña persistente %s para %s: %s", prefix, chat_id, e)


def _cancel_jobs_prefix(context: ContextTypes.DEFAULT_TYPE, prefix: str, chat_id: int):
    """Cancela tanto los jobs en memoria como los pendientes persistidos."""
    if context.job_queue:
        for suf in ("1h", "3h", "24h", "48h"):
            name = f"{prefix}_{suf}_{chat_id}"
            try:
                for j in context.job_queue.get_jobs_by_name(name):
                    j.schedule_removal()
            except Exception:
                pass
    _delete_persistent_campaign_series(prefix, chat_id)


def _create_persistent_campaign_series(chat_id: int, series: str, lang: str, started_at=None):
    """Crea los cuatro vencimientos absolutos solo para usuarios privados."""
    if not _is_private_user_id(chat_id):
        logging.warning("🛡️ Campaña %s bloqueada para chat no privado %s", series, chat_id)
        return []
    started_at = started_at or utcnow_naive()
    lang = lang if lang in ("es", "en") else "es"
    records = []
    try:
        with Session() as session:
            # Limpieza defensiva: nunca debe quedar más de una serie pendiente del mismo tipo.
            session.query(CampaignJob).filter(
                CampaignJob.telegram_id == str(chat_id),
                CampaignJob.series == str(series),
                CampaignJob.sent_at.is_(None),
            ).delete(synchronize_session=False)

            for step, seconds in CAMPAIGN_OFFSETS.items():
                row = CampaignJob(
                    telegram_id=str(chat_id),
                    series=series,
                    step=step,
                    lang=lang,
                    due_at=started_at + timedelta(seconds=seconds),
                    sent_at=None,
                    created_at=utcnow_naive(),
                )
                session.add(row)
                session.flush()
                records.append({"id": row.id, "chat_id": chat_id, "series": series, "step": step, "lang": lang, "due_at": row.due_at})
            session.commit()
        return records
    except Exception as e:
        logging.warning("No pude persistir Serie %s para %s: %s", series, chat_id, e)
        return []


def _schedule_persistent_campaign_record(job_queue, record):
    if not job_queue or not record:
        return
    due_at = record.get("due_at")
    delay = max(2, int((due_at - utcnow_naive()).total_seconds())) if due_at else 2
    job_queue.run_once(
        persistent_campaign_job,
        when=delay,
        data={"campaign_job_id": int(record["id"])},
        name=f'{record["series"]}_{record["step"]}_{record["chat_id"]}',
    )


async def persistent_campaign_job(context: ContextTypes.DEFAULT_TYPE):
    """Envía un recordatorio A/B y marca el paso como enviado en BD."""
    data = context.job.data or {}
    job_id = data.get("campaign_job_id")
    if not job_id:
        return

    try:
        with Session() as session:
            row = session.get(CampaignJob, int(job_id))
            if not row or row.sent_at is not None:
                return
            chat_id = int(row.telegram_id)
            series = row.series or "A"
            step = row.step or ""
            lang = row.lang if row.lang in ("es", "en") else get_user_lang(chat_id)
            due_at = row.due_at
    except Exception as e:
        logging.warning("No pude leer campaign_job %s: %s", job_id, e)
        return

    # Protección absoluta: una campaña jamás puede enviarse a grupos/canales/temas.
    if not _is_private_user_id(chat_id):
        try:
            with Session() as session:
                stale = session.get(CampaignJob, int(job_id))
                if stale:
                    session.delete(stale)
                    session.commit()
        except Exception:
            pass
        logging.warning("🛡️ CampaignJob no privado descartado: %s", chat_id)
        return

    # Protección adicional por etapa, incluso si un cancel llegó durante un redeploy.
    stage, _ = _repair_inconsistent_stage(chat_id)
    if (series == "A" and stage != STAGE_PRE) or (series == "B" and stage != STAGE_POST):
        try:
            with Session() as session:
                stale = session.get(CampaignJob, int(job_id))
                if stale:
                    session.delete(stale)
                    session.commit()
        except Exception:
            pass
        return

    # Si el job despertó antes de su vencimiento, conserva el vencimiento original.
    now = utcnow_naive()
    if due_at and due_at > now + timedelta(seconds=1):
        _schedule_persistent_campaign_record(
            context.job_queue,
            {"id": int(job_id), "chat_id": chat_id, "series": series, "step": step, "lang": lang, "due_at": due_at},
        )
        return

    text_es, text_en = _campaign_text_pair(series, step)
    outbound = text_es if lang == "es" else text_en
    if not outbound:
        logging.warning("No existe texto de campaña %s %s para %s", series, step, chat_id)
        return

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=outbound,
            reply_markup=support_keyboard(lang),
            disable_web_page_preview=True,
        )
        with Session() as session:
            sent_row = session.get(CampaignJob, int(job_id))
            if sent_row and sent_row.sent_at is None:
                sent_row.sent_at = utcnow_naive()
                session.commit()
        logging.info("✅ Campaña %s %s enviada a chat_id %s (lang=%s)", series, step, chat_id, lang)
    except Exception as e:
        # No marcamos como enviado: un reinicio podrá recuperarlo.
        logging.warning("Job campaña %s %s falló para %s: %s", series, step, chat_id, e)


def schedule_series_a(chat_id: int, lang: str, context: ContextTypes.DEFAULT_TYPE):
    if not context.job_queue or not _is_private_user_id(chat_id):
        return
    _cancel_jobs_prefix(context, "A", chat_id)
    records = _create_persistent_campaign_series(chat_id, "A", lang)
    for record in records:
        _schedule_persistent_campaign_record(context.job_queue, record)
    logging.info("✅ Serie A PERSISTENTE programada para chat_id %s (lang=%s): 1h, 3h, 24h, 48h", chat_id, lang)


async def _send_job_message_B(context: ContextTypes.DEFAULT_TYPE, text_es: str, text_en: str):
    """Compatibilidad con jobs antiguos dentro del proceso actual."""
    chat_id, lang = context.job.data
    if not _is_private_user_id(chat_id):
        return
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text_es if lang == "es" else text_en,
            reply_markup=support_keyboard(lang),
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
    if not context.job_queue or not _is_private_user_id(chat_id):
        return
    lang = get_user_lang(chat_id)
    _cancel_jobs_prefix(context, "B", chat_id)
    records = _create_persistent_campaign_series(chat_id, "B", lang)
    for record in records:
        _schedule_persistent_campaign_record(context.job_queue, record)
    logging.info("✅ Serie B PERSISTENTE post-validación programada para chat_id %s (lang=%s): 1h, 3h, 24h, 48h", chat_id, lang)


def _sync_menu_campaign_for_stage(chat_id: int, lang: str, context: ContextTypes.DEFAULT_TYPE):
    """Mantiene la campaña correcta al abrir/cambiar idioma sin reiniciar sus tiempos."""
    lang = lang if lang in ("es", "en") else get_user_lang(chat_id)
    stage, repaired_stage = _repair_inconsistent_stage(chat_id)
    if repaired_stage:
        logging.info("✅ Usuario %s reparado a PRE antes de sincronizar campañas", chat_id)

    # PRE: solo Serie A. Si ya existe (pendiente o finalizada), NO reinicia el reloj.
    if stage == STAGE_PRE:
        _cancel_jobs_prefix(context, "B", chat_id)
        try:
            with Session() as session:
                rows = (
                    session.query(CampaignJob)
                    .filter(
                        CampaignJob.telegram_id == str(chat_id),
                        CampaignJob.series == "A",
                    )
                    .all()
                )
                pending = [row for row in rows if row.sent_at is None]
                for row in pending:
                    row.lang = lang
                if pending:
                    session.commit()
        except Exception as e:
            logging.warning("No pude sincronizar Serie A para %s: %s", chat_id, e)
            rows, pending = [], []

        if not rows:
            schedule_series_a(chat_id, lang, context)
        elif pending:
            logging.info(
                "✅ Serie A conservada para chat_id %s (lang=%s): tiempos originales intactos",
                chat_id, lang,
            )
        else:
            logging.info("ℹ️ Serie A ya finalizada para chat_id %s; no se reinicia", chat_id)
        return

    # POST: nunca debe volver a Serie A. La Serie B nace al validar el ID;
    # si ya existe, solo actualiza idioma y mantiene sus vencimientos originales.
    if stage == STAGE_POST:
        _cancel_jobs_prefix(context, "A", chat_id)
        try:
            with Session() as session:
                rows = (
                    session.query(CampaignJob)
                    .filter(
                        CampaignJob.telegram_id == str(chat_id),
                        CampaignJob.series == "B",
                    )
                    .all()
                )
                pending = [row for row in rows if row.sent_at is None]
                for row in pending:
                    row.lang = lang
                if pending:
                    session.commit()
        except Exception as e:
            logging.warning("No pude sincronizar Serie B para %s: %s", chat_id, e)
            rows, pending = [], []

        if pending:
            logging.info(
                "✅ Serie B conservada para chat_id %s (lang=%s): tiempos originales intactos",
                chat_id, lang,
            )
        elif rows:
            logging.info("ℹ️ Serie B ya finalizada para chat_id %s; no se reinicia", chat_id)
        else:
            logging.info(
                "ℹ️ Usuario POST %s sin Serie B pendiente; no se crea desde /start para no alterar el tiempo de validación",
                chat_id,
            )
        return

    # DEPOSITED: no debe recibir remarketing A ni B.
    _cancel_jobs_prefix(context, "A", chat_id)
    _cancel_jobs_prefix(context, "B", chat_id)
    logging.info("✅ Usuario DEPOSITED %s: sin campañas A/B", chat_id)


async def recover_pending_campaign_jobs(application):
    """Recupera campañas A/B pendientes sin reiniciar sus relojes tras un redeploy."""
    if not application.job_queue:
        return

    recovered = 0
    stale_ids = []
    repaired_users = 0
    try:
        # Repara estados POST imposibles de usuarios activos recientes.
        # Esto corrige registros heredados de versiones anteriores sin tocar POST válidos.
        cutoff = utcnow_naive() - timedelta(days=20)
        with Session() as session:
            active_post_ids = [
                str(r[0]) for r in (
                    session.query(Usuario.telegram_id)
                    .join(UserActivity, UserActivity.telegram_id == Usuario.telegram_id)
                    .filter(
                        Usuario.stage == STAGE_POST,
                        UserActivity.last_activity_at >= cutoff,
                    )
                    .all()
                )
            ]

        for telegram_id in active_post_ids:
            chat_id = int(telegram_id)
            if not _is_private_user_id(chat_id):
                continue
            stage, repaired = _repair_inconsistent_stage(chat_id)
            if repaired and stage == STAGE_PRE:
                repaired_users += 1
                _delete_persistent_campaign_series("B", chat_id)

                # Si la Serie A fue eliminada por el stage incorrecto anterior,
                # reconstruimos una sola vez sus 4 pasos desde este momento.
                with Session() as session:
                    any_a = (
                        session.query(CampaignJob.id)
                        .filter(
                            CampaignJob.telegram_id == str(chat_id),
                            CampaignJob.series == "A",
                        )
                        .first()
                    )
                if not any_a:
                    _create_persistent_campaign_series(
                        chat_id,
                        "A",
                        get_user_lang(chat_id),
                        started_at=utcnow_naive(),
                    )
                    logging.info("✅ Serie A reconstruida para %s tras reparar POST inválido", chat_id)

        with Session() as session:
            rows = (
                session.query(CampaignJob)
                .filter(CampaignJob.sent_at.is_(None))
                .order_by(CampaignJob.due_at.asc())
                .all()
            )
            snapshot = [
                {
                    "id": row.id,
                    "chat_id": int(row.telegram_id),
                    "series": row.series,
                    "step": row.step,
                    "lang": row.lang if row.lang in ("es", "en") else "es",
                    "due_at": row.due_at,
                }
                for row in rows
            ]

        for record in snapshot:
            if not _is_private_user_id(record["chat_id"]):
                stale_ids.append(record["id"])
                continue
            stage, _ = _repair_inconsistent_stage(record["chat_id"])
            valid = (record["series"] == "A" and stage == STAGE_PRE) or (record["series"] == "B" and stage == STAGE_POST)
            if not valid:
                stale_ids.append(record["id"])
                continue
            _schedule_persistent_campaign_record(application.job_queue, record)
            recovered += 1

        if stale_ids:
            with Session() as session:
                session.query(CampaignJob).filter(CampaignJob.id.in_(stale_ids)).delete(synchronize_session=False)
                session.commit()

        if recovered:
            logging.info("♻️ Campañas persistentes recuperadas tras reinicio: %s jobs pendientes", recovered)
        if repaired_users:
            logging.info("🛡️ Usuarios POST inconsistentes reparados al iniciar: %s", repaired_users)
    except Exception as e:
        logging.warning("No pude recuperar campañas persistentes: %s", e)


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
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    chat_id = update.effective_chat.id
    nombre = update.effective_user.full_name

    # Crear usuario si no existe (lang por defecto "es" hasta que elija)
    with Session() as session:
        user = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not user:
            nuevo_usuario = Usuario(
                telegram_id=str(chat_id),
                nombre=nombre,
                fecha_registro=utcnow_naive(),
                lang="es",
                stage="PRE",
            )
            session.add(nuevo_usuario)
        else:
            user.nombre = nombre
        session.commit()

    _touch_user_activity(chat_id, get_user_lang(chat_id))
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

    # Mantener la campaña correcta según la etapa SIN reiniciar relojes existentes.
    _sync_menu_campaign_for_stage(chat_id, lang, context)

# === BOTONES / CALLBACKS ===
async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not update.effective_chat or update.effective_chat.type != "private":
        if q:
            try:
                await q.answer()
            except Exception:
                pass
        return
    chat_id = update.effective_chat.id
    await q.answer()
    _touch_user_activity(chat_id)

    # Notificar interacción
    await notificar_interaccion(update, context)

    if q.data == "back_main_menu":
        lang = get_user_lang(chat_id)
        await q.message.reply_text(
            "👇 Elige una opción para continuar:" if lang == "es" else "👇 Choose an option to continue:",
            reply_markup=build_main_menu(lang),
        )
        return

    # --- Niveles y Planes (informativo) ---
    if q.data == "niveles_planes":
        texto = respuesta_niveles_es()
        kb = [[InlineKeyboardButton("📄 Ver estructura completa", url="https://telegra.ph/EVOLUCI%C3%93N-OFICIAL-DE-NUESTRA-COMUNIDAD-02-27")], *support_rows("es")]
        await q.message.reply_text(texto, reply_markup=InlineKeyboardMarkup(kb))
        return

    # --- Levels & Plans (EN) ---
    if q.data == "levels_plans_en":
        texto = respuesta_niveles_en()
        kb = [[InlineKeyboardButton("📄 View full structure", url="https://telegra.ph/OFFICIAL-EVOLUTION-OF-OUR-TRADING-COMMUNITY-02-28")], *support_rows("en")]
        await q.message.reply_text(texto, reply_markup=InlineKeyboardMarkup(kb))
        return


    # --- Acciones para imagen (ID vs depósito) — ES/EN ---
    lang = get_user_lang(chat_id)

    if q.data and q.data.startswith("IMG_IS_ID|"):
        msg = (
            (
                "Perfecto ✅\n"
                "Para poder validarlo necesito que me envíes el **ID en texto** (solo el número).\n"
                "📌 Ábrelo en Stockity o Binomo, cópialo y pégalo aquí.\n\n"
                "Si prefieres, también puedes escribirme al chat personal 👇"
            )
            if lang == "es" else
            (
                "Perfect ✅\n"
                "To validate it, I need you to send me the **ID as text** (numbers only).\n"
                "📌 Open your Stockity or Binomo profile, copy the ID and paste it here.\n\n"
                "If you prefer, you can also message me directly in my personal chat 👇"
            )
        )
        await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "IMG_IS_ID", msg)
        return

    if q.data and q.data.startswith("IMG_IS_DEP|"):
        saved_id = context.user_data.get("binomo_id")
        if saved_id:
            msg = (
                (
                    "Perfecto ✅\n\n"
                    "Recibido. Estoy validando tu depósito ahora mismo.\n"
                    "Te escribiré de nuevo para confirmar y habilitar tu acceso 🎉\n\n"
                    "Si deseas, también puedes enviarlo a mi chat personal tocando el botón 👇"
                )
                if lang == "es" else
                (
                    "Perfect ✅\n\n"
                    "Received. I’m validating your deposit now.\n"
                    "I’ll message you again to confirm it and enable your access 🎉\n\n"
                    "If you prefer, you can also send it to my personal chat using the button below 👇"
                )
            )
            await q.message.reply_text(msg, reply_markup=support_keyboard(lang))
            await send_admin_auto_log(context, update, "AUTO_IMG_DEPOSIT_VALIDATING", msg)
            return

        msg = (
            (
                "Perfecto ✅\n\n"
                "Recibido. Para continuar, envíame tu **ID de Stockity o Binomo en texto** (solo el número) y lo dejo en validación 👇\n\n"
                "Si deseas, también puedes enviarlo a mi chat personal tocando el botón 👇"
            )
            if lang == "es" else
            (
                "Perfect ✅\n\n"
                "Received. To continue, send me your **Stockity or Binomo ID as text** (numbers only) and I’ll leave it for validation 👇\n\n"
                "If you prefer, you can also send it to my personal chat using the button below 👇"
            )
        )
        await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "AUTO_IMG_DEPOSIT_NEED_ID", msg)
        return

    if q.data and q.data.startswith("IMG_IS_OTHER|"):
        msg = (
            (
                "Listo ✅\n"
                "Dime qué necesitas exactamente (bono, retiros, ID o horarios).\n"
                "O escríbeme al chat personal y lo revisamos en 1 minuto 👇"
            )
            if lang == "es" else
            (
                "Got it ✅\n"
                "Tell me exactly what you need help with (bonus, withdrawals, ID or schedules).\n"
                "Or message me directly in my personal chat and I’ll review it with you 👇"
            )
        )
        await q.message.reply_text(msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "IMG_IS_OTHER", msg)
        return

    # --- Confirmación de depósito desde botones (tanto del precheck como del flujo POST) ---
    if q.data and (q.data.startswith("DEP_YES|") or q.data.startswith("dep_yes:")):
        msg = (
            (
                "Perfecto ✅\n\n"
                "Envíame aquí tu **comprobante de depósito/activación** (foto o captura) y tu **ID de Stockity o Binomo en texto** "
                "(solo el número) para validarlo y habilitar tu acceso 👇"
            )
            if lang == "es" else
            (
                "Perfect ✅\n\n"
                "Send me your **deposit/activation proof** here (photo or screenshot) and your **Stockity or Binomo ID as text** "
                "(numbers only) so I can validate it and enable your access 👇"
            )
        )
        context.user_data["awaiting_deposit_proof"] = True
        await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "AUTO_DEPOSIT_CONFIRM_BTN", msg)
        return

    if q.data and (q.data.startswith("DEP_NO|") or q.data.startswith("dep_no:")):
        msg = (
            "Perfecto ✅\n\nCuéntame en texto qué necesitas revisar 👇"
            if lang == "es" else
            "Perfect ✅\n\nTell me in a text message what you need me to review 👇"
        )
        context.user_data.pop("awaiting_deposit_proof", None)
        await q.message.reply_text(msg, reply_markup=support_keyboard(lang))
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
            await q.message.reply_text(MENSAJE_REGISTRARME_ES, reply_markup=support_keyboard(lang))
            # Video SOLO en español
            await q.message.reply_video(
                video="BAACAgEAAxkBAAIBaGhdq0nQXi6B4N8uRwmaOHKkUarbAAIMBgACTgAB8UbIZIU9XTMCzjYE",
                caption="📹 Paso a paso en el vídeo",
                reply_markup=support_keyboard(lang),
            )
        else:
            await q.message.reply_text(MENSAJE_REGISTRARME_EN, reply_markup=support_keyboard(lang))

    elif q.data == "ya_tengo_cuenta":
        await q.message.reply_text(MENSAJE_YA_TENGO_CUENTA_ES if lang=="es" else MENSAJE_YA_TENGO_CUENTA_EN, reply_markup=support_keyboard(lang))

    elif q.data == "gestion_capital":
        texto_gestion = (
            "📊 GESTIÓN DE CAPITAL\n\n"
            "Tengo dos modalidades disponibles y este proceso lo reviso personalmente contigo.\n\n"
            "• Modalidad 3 meses: desde 200 USD. Objetivo estimado de 20–30% mensual, sujeto a resultados del trading.\n"
            "• Modalidad 2 meses: desde 100 USD. La estructura planteada busca generar hasta 30 USD semanales, sujeto a resultados.\n\n"
            "⚠️ Son objetivos, NO ganancias garantizadas. El trading implica riesgo.\n\n"
            "Si te interesa, escríbeme directamente y te explico condiciones, disponibilidad y proceso 👇"
        )
        await q.message.reply_text(texto_gestion, reply_markup=support_keyboard(lang))

    elif q.data == "gestion_capital_en":
        texto_gestion = (
            "📊 CAPITAL MANAGEMENT\n\n"
            "I currently have two options, and I review this process with you personally.\n\n"
            "• 3-month option: from 200 USD. Estimated target of 20–30% per month, subject to trading results.\n"
            "• 2-month option: from 100 USD. The structure aims for up to 30 USD per week, subject to results.\n\n"
            "⚠️ These are targets, NOT guaranteed profits. Trading involves risk.\n\n"
            "If you are interested, message me directly so I can explain the current conditions and availability 👇"
        )
        await q.message.reply_text(texto_gestion, reply_markup=support_keyboard(lang))

    elif q.data == "beneficios_vip":
        await q.message.reply_text(BENEFICIOS_ES if lang=="es" else BENEFICIOS_EN, reply_markup=support_keyboard(lang))

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
https://t.me/JohaaleTraderTeams""", reply_markup=support_keyboard(lang))
        else:
            await q.message.reply_text("""🌐 Social Media:

🔴 YouTube:
https://youtube.com/@johaalegria.trader?si=JemqmPes0Rz3WqEZ

🟣 Instagram:
https://www.instagram.com/johaale_trader?igsh=ZWI5dXNnaXN6aDNw

🎵 TikTok:
https://www.tiktok.com/@joha_binomo?_t=ZN-8xceLrp5GTe&_r=1

💬 Telegram:
https://t.me/JohaaleTraderTeams""", reply_markup=support_keyboard(lang))

# === PERSISTENCIA MENSAJE DEL USUARIO ===
async def guardar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda el último mensaje sin permitir que un fallo de BD corte el bot."""
    # MessageHandler también puede recibir updates editados; en ellos update.message es None.
    if update.message is None or update.effective_chat is None or update.effective_user is None:
        return
    chat_id = update.effective_chat.id
    texto = update.message.text or update.message.caption or ""
    nombre = update.effective_user.full_name
    lang = get_user_lang(chat_id)

    try:
        with Session() as session:
            result = session.execute(
                text("""
                    UPDATE usuarios
                    SET mensaje = :mensaje, nombre = :nombre
                    WHERE telegram_id = :telegram_id
                """),
                {
                    "mensaje": texto,
                    "nombre": nombre,
                    "telegram_id": str(chat_id),
                },
            )
            if result.rowcount == 0:
                session.execute(
                    text("""
                        INSERT INTO usuarios
                            (telegram_id, nombre, mensaje, fecha_registro, lang, stage)
                        VALUES
                            (:telegram_id, :nombre, :mensaje, :fecha_registro, :lang, :stage)
                    """),
                    {
                        "telegram_id": str(chat_id),
                        "nombre": nombre,
                        "mensaje": texto,
                        "fecha_registro": utcnow_naive(),
                        "lang": lang,
                        "stage": STAGE_PRE,
                    },
                )
            session.commit()
    except Exception as e:
        logging.warning("No pude guardar mensaje de %s, pero el bot continuará: %s", chat_id, e)

    # Actividad para LIVE/marketing y evento para reporte diario se guardan aparte.
    _touch_user_activity(chat_id, lang)
    _log_event(chat_id, "MESSAGE", texto)


# === NOTIFICACIONES AL ADMIN ===
async def notificar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Evita errores en updates editados/atípicos donde no existe update.message.
        if update.message is None or update.effective_user is None:
            return
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

def _is_positive_id_validation(text_value: str, original_question: str = "", saved_id: str = "") -> bool:
    """True solo si Johanna valida explícitamente el MISMO ID que el usuario envió."""
    t = _norm(text_value or "")
    original_raw = (original_question or "").strip()
    saved_id = (saved_id or "").strip()
    if not t or not saved_id:
        return False

    # La respuesta del admin debe estar asociada al mensaje donde aparece ESE ID.
    if saved_id not in original_raw:
        return False

    negatives = (
        "id no validado", "id no esta validado", "id no está validado",
        "id incorrecto", "id errado", "id esta errado", "id está errado",
        "no pude validar", "no puedo validar", "no lo valide", "no lo validé",
        "aun no valido", "aún no valido", "pendiente de validar",
    )
    if any(n in t for n in negatives):
        return False

    explicit_positives = (
        "id validado correctamente",
        "id correctamente validado",
        "tu id es correcto",
        "tu id esta correcto",
        "tu id está correcto",
        "id correcto",
        "id ya esta validado",
        "id ya está validado",
        "ya valide tu id",
        "ya validé tu id",
        "ya valide el id",
        "ya validé el id",
        "id successfully validated",
        "your id is correct",
        "i validated your id",
    )
    if any(p in t for p in explicit_positives):
        return True

    # Respuestas cortas siguen siendo válidas SOLO al responder al mensaje
    # que contiene exactamente el ID guardado; nunca por un número cualquiera.
    return t in (
        "correcto", "es correcto", "si correcto", "sí correcto",
        "esta correcto", "está correcto", "validado", "ya validado",
        "ya lo valide", "ya lo validé",
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
        chat_id_match = re.search(r'ID(?:\s+del\s+usuario)?[^0-9-]{0,40}(-?\d+)', base_text, re.IGNORECASE)
        if chat_id_match:
            destinatario_id = int(chat_id_match.group(1))
            if not _is_private_user_id(destinatario_id):
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="🛡️ Envío bloqueado: ese registro pertenece a un grupo/canal/tema, no a un usuario privado.",
                )
                return
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

                # Detectar mensajes gatillo con protección estricta del flujo:
                # PRE -> ID enviado -> Johanna valida ESE ID -> POST -> depósito -> DEPOSITED.
                try:
                    txt = (learned_reply if response_type == "voice" else (update.message.text or ""))
                    txtn = _norm(txt)
                    saved_id = _get_saved_trading_id(destinatario_id)

                    if _is_positive_id_validation(txt, original_question or "", saved_id):
                        if _has_submitted_id_evidence(destinatario_id, saved_id):
                            set_user_stage(destinatario_id, STAGE_POST)
                            _log_event(destinatario_id, "ID_VALIDATED", f"ID={saved_id} | {txt}")
                            _cancel_jobs_prefix(context, "A", destinatario_id)
                            schedule_series_b(destinatario_id, context)
                            await context.bot.send_message(
                                chat_id=ADMIN_ID,
                                text=f"✅ ID {saved_id} validado. Serie B activada para {destinatario_id}",
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=ADMIN_ID,
                                text=f"⚠️ No cambié a POST a {destinatario_id}: no existe evidencia de que ese ID haya sido enviado por el usuario.",
                            )

                    elif ("confirmo cuenta activa" in txtn) or ("cuenta esta activa" in txtn) or ("cuenta está activa" in txtn) or ("acceso confirmado" in txtn) or ("acceso activado" in txtn):
                        current_stage, _ = _repair_inconsistent_stage(destinatario_id)
                        if current_stage == STAGE_POST and _strict_validated_id_state(destinatario_id):
                            set_user_stage(destinatario_id, STAGE_DEPOSITED)
                            _log_event(destinatario_id, "ACCOUNT_ACTIVATED", txt)
                            _cancel_jobs_prefix(context, "A", destinatario_id)
                            _cancel_jobs_prefix(context, "B", destinatario_id)
                            await context.bot.send_message(chat_id=ADMIN_ID, text=f"✅ Acceso confirmado. Campañas detenidas para {destinatario_id}")
                        else:
                            await context.bot.send_message(
                                chat_id=ADMIN_ID,
                                text=f"⚠️ No marqué DEPOSITED a {destinatario_id}: primero debe existir un ID realmente enviado y validado.",
                            )

                    elif (_norm(GATILLO_ID_ERRADO) in txtn) or ("tu id esta errado" in txtn) or ("tu id está errado" in txtn):
                        set_user_stage(destinatario_id, STAGE_PRE)
                        _clear_saved_trading_id(destinatario_id)
                        _cancel_jobs_prefix(context, "B", destinatario_id)
                        schedule_series_a(destinatario_id, get_user_lang(destinatario_id), context)
                        await context.bot.send_message(chat_id=ADMIN_ID, text=f"ℹ️ ID rechazado. Usuario {destinatario_id} vuelve a PRE y Serie A continúa.")
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

            if not _is_private_user_id(chat_id):
                await query.edit_message_text("🛡️ Respuesta bloqueada: ese destino no es un usuario privado.")
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
# 8 minutos de prioridad para Johanna. Puede cambiarse en Railway con AI_WAIT_MINUTES.
try:
    AI_WAIT_MINUTES = max(1, int(os.getenv("AI_WAIT_MINUTES", "8")))
except Exception:
    AI_WAIT_MINUTES = 8
AI_WAIT_SECONDS = AI_WAIT_MINUTES * 60
AI_HISTORY_MAX_MESSAGES = 16

JOHA_KNOWLEDGE = os.getenv("JOHA_KNOWLEDGE", "").strip() or f"""
INFORMACIÓN OFICIAL DE JOHAALETRADER / JT TRADERS TEAMS

IDENTIDAD Y FORMA DE RESPONDER
- Responde en primera persona como Johanna / JOHAALETRADER. Nunca te presentes como Johabot ni como una IA.
- Puedes atender tanto prospectos que quieren entrar a la comunidad como miembros que ya están dentro.
- Mantén respuestas MUY fáciles de leer en Telegram: normalmente 2 a 4 párrafos cortos; una pregunta simple debe resolverse en pocas líneas.
- Sé muy positiva, motivadora, persuasiva y orientada a acción, sin sonar robótica ni repetir información.
- Usa algunos emojis con moderación y cierra con un llamado a la acción claro cuando corresponda: registro → envío de ID → depósito → activación/acceso.
- No presiones de forma engañosa y no inventes urgencias, cupos ni resultados.

REGISTRO Y ACCESO
- El acceso a la comunidad es GRATUITO. No existe una membresía adicional que el usuario deba pagar a Johanna para entrar.
- El usuario invierte/deposita en su PROPIA cuenta de trading. La cantidad de herramientas y beneficios depende del nivel elegido.
- Cuando debas mostrar los enlaces de registro, usa SIEMPRE esta estructura visual:
  🔗 Stockity — opción principal:
  {ENLACE_REFERIDO_STOCKITY}

  🔗 Binomo — opción secundaria:
  {ENLACE_REFERIDO}
- En inglés usa exactamente las etiquetas "🔗 Stockity — primary option:" y "🔗 Binomo — secondary option:", manteniendo la URL debajo y una línea en blanco entre plataformas.
- Después del registro, el usuario debe enviar su ID de Stockity o Binomo para validación ANTES de depositar.
- Chat personal/validación: {SUPPORT_URL}
- Nunca confirmes por tu cuenta que un ID, depósito, afiliación o acceso quedó validado. Esa confirmación la realiza Johanna manualmente.

NIVELES
- Básico: desde 50 USD en la cuenta de trading. Formación completa, comunidad inicial y herramientas/señales CRYPTO IDX limitadas.
- Premium: desde 200 USD. Incluye lo anterior más señales completas del software Premium, bot IA 24/7, operativas en vivo y enfoque multi-broker.
- Prestige: desde 500 USD. Incluye Premium más mentorías privadas, acompañamiento cercano y preparación para cuentas de fondeo.
- Si preguntan con cuánto es ideal iniciar, explica que se puede empezar desde 50 USD, pero que normalmente recomiendo 200 USD o más si está dentro de las posibilidades del usuario, porque desde Premium se aprovechan muchas más herramientas.
- Explica con buenas palabras que un capital más amplio da mayor margen operativo y más flexibilidad para aplicar gestión de riesgo y distribuir mejor las entradas. Eso puede ayudar a aprovechar mejor la estrategia y las herramientas, pero NO garantiza mejores resultados ni ganancias. Nunca digas que más inversión asegura más rentabilidad.
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
                created_at=utcnow_naive(),
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


def _johanna_examples_as_text(question: str = "", limit: int = 28, lang: str | None = None) -> str:
    """Recupera memoria relevante + ejemplos recientes de cómo responde Johanna.

    Si conocemos el idioma del usuario, priorizamos ejemplos guardados en ese mismo
    idioma para evitar que ejemplos españoles arrastren una respuesta EN hacia ES.
    """
    try:
        limit = max(4, min(limit, 40))
        with Session() as session:
            recent_query = session.query(JohannaExample)
            if lang in ("es", "en"):
                recent_query = recent_query.filter(JohannaExample.lang == lang)
            recent = (
                recent_query
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
                relevant_query = session.query(JohannaExample).filter(or_(*conditions))
                if lang in ("es", "en"):
                    relevant_query = relevant_query.filter(JohannaExample.lang == lang)
                relevant = (
                    relevant_query
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


def _decode_pending_payload(raw_value: str):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return {"messages": [], "answered_topics": []}
    try:
        data = json.loads(raw_value)
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            return {
                "messages": [str(x) for x in data.get("messages", []) if str(x).strip()],
                "answered_topics": [str(x) for x in data.get("answered_topics", []) if str(x).strip()],
            }
    except Exception:
        pass
    # Compatibilidad con pendientes creados por versiones anteriores.
    return {"messages": [raw_value], "answered_topics": []}


def _encode_pending_payload(messages, answered_topics):
    return json.dumps(
        {
            "v": 2,
            "messages": [str(x)[:5000] for x in messages if str(x).strip()][-8:],
            "answered_topics": list(dict.fromkeys(str(x) for x in answered_topics if str(x).strip()))[-30:],
        },
        ensure_ascii=False,
    )


def _set_pending_ai(chat_id: int, text_value: str, message_id: int, answered_topics=None):
    due_at = utcnow_naive() + timedelta(seconds=AI_WAIT_SECONDS)
    answered_topics = answered_topics or []
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not u:
            return due_at
        payload = {"messages": [], "answered_topics": []}
        if u.ai_pending_text and u.ai_pending_due_at and u.ai_pending_due_at >= utcnow_naive():
            payload = _decode_pending_payload(u.ai_pending_text)
        payload["messages"].append(text_value.strip())
        payload["answered_topics"].extend(answered_topics)
        u.ai_pending_text = _encode_pending_payload(payload["messages"], payload["answered_topics"])
        u.ai_pending_message_id = str(message_id)
        u.ai_pending_due_at = due_at
        session.commit()
    return due_at


def _get_pending_ai(chat_id: int):
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not u or not u.ai_pending_text or not u.ai_pending_due_at:
            return None
        payload = _decode_pending_payload(u.ai_pending_text)
        return {
            "text": "\n".join(payload["messages"]).strip(),
            "messages": payload["messages"],
            "answered_topics": payload["answered_topics"],
            "message_id": u.ai_pending_message_id,
            "due_at": u.ai_pending_due_at,
        }


def _clear_pending_ai_db(chat_id: int):
    pending_text = ""
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if u:
            payload = _decode_pending_payload(u.ai_pending_text or "")
            pending_text = "\n".join(payload["messages"]).strip()
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


def _add_intent(found, intent):
    if intent and intent not in found:
        found.append(intent)


def detect_all_intents(texto: str):
    """Detecta TODAS las intenciones conocidas presentes en un mismo mensaje.

    No hace ``return`` al encontrar la primera coincidencia. Esto permite atender
    mensajes naturales con varias dudas combinadas (niveles + bonos + registro +
    horarios + señales, etc.). Las partes que no tienen respuesta fija quedan para
    la IA diferida, que recibe el mensaje completo.
    """
    t = _norm(texto)
    found = []

    # ID numérico: solo si el mensaje realmente parece un envío de ID.
    # Evita confundir capitales, montos, fechas u otros números con un ID de trading.
    if _extract_candidate_trading_id(texto):
        _add_intent(found, "ID_SUBMIT")

    if any(k in t for k in [
        "gestion de capital", "gestión de capital", "gestionar capital", "manejas capital",
        "manejo de capital", "inversion contigo", "inversión contigo", "enviarte capital",
        "capital management", "manage my capital", "investment with you",
    ]):
        _add_intent(found, "GESTION_CAPITAL")

    if any(k in t for k in [
        "depositar despues", "depositar después", "puedo depositar despues", "puedo depositar después",
        "deposito despues", "depósito después", "deposito luego", "depósito luego",
        "mas tarde deposito", "más tarde deposito", "luego deposito", "despues deposito", "después deposito",
        "no tengo dinero ahora", "ahora no tengo dinero", "estoy esperando un pago", "esperando un pago",
        "cuando me paguen", "cuando tenga dinero", "cuando tenga plata", "por ahora no puedo", "aun no puedo",
    ]):
        _add_intent(found, "DEP_LATER")

    if any(k in t for k in [
        "no tengo 50", "no tengo cincuenta", "puedo con menos", "puedo iniciar con menos",
        "puedo empezar con menos", "menos de 50", "menos de cincuenta", "solo tengo 10", "tengo 10",
        "tengo 20", "tengo 30", "tengo 40", "puedo con 10", "puedo con 20", "puedo con 30", "puedo con 40",
        "depositar 10", "depositar 20", "depositar 30", "depositar 40",
    ]):
        _add_intent(found, "MIN_50")

    if any(k in t for k in [
        "ya deposite", "ya deposité", "ya hice el deposito", "ya hice el depósito", "deposito listo", "depósito listo",
        "ya pague", "ya pagué", "ya active", "ya activé", "dame acceso", "habilitar acceso", "acceso vip",
        "i deposited", "deposit done", "i made the deposit",
    ]):
        _add_intent(found, "DEPOSITO")

    # Diferenciamos "ya tengo cuenta" de "ya me registré con tu enlace".
    if any(k in t for k in [
        "ya tengo cuenta pero", "ya tengo una cuenta", "tengo cuenta en binomo", "tengo cuenta en stockity",
        "ya tenia cuenta", "ya tenía cuenta", "already have an account", "i already have an account",
    ]):
        _add_intent(found, "YA_TENGO_CUENTA")
    elif any(k in t for k in [
        "ya me registre", "ya me registré", "me registre", "me registré", "ya estoy registrado", "ya estoy registrada",
        "ya cree cuenta", "ya creé cuenta", "ya hice el registro", "ya realice el registro", "ya realicé el registro",
        "i registered", "i already registered",
    ]):
        _add_intent(found, "YA_REGISTRE")

    if any(k in t for k in [
        "como me registro", "cómo me registro", "como registrarme", "cómo registrarme", "quiero registrarme",
        "como hago para registrarme", "cómo hago para registrarme", "que hago para registrarme", "qué hago para registrarme",
        "como hago el registro", "cómo hago el registro", "como hago para hacer el registro", "cómo hago para hacer el registro",
        "quiero hacer el registro", "quiero hacer mi registro", "donde me registro", "dónde me registro",
        "enlace de registro", "link de registro", "registration link", "how do i register", "how can i register",
        "how do i sign up", "how can i sign up",
    ]):
        _add_intent(found, "REGISTRO")

    if any(k in t for k in [
        "que sigue", "qué sigue", "y ahora que", "y ahora qué", "siguiente paso", "que hago ahora", "qué hago ahora",
        "what next", "next step",
    ]):
        _add_intent(found, "NEXT_STEP")

    if "id" in t and any(k in t for k in [
        "te envio", "te envío", "enviar", "mando", "te mando", "por donde", "por dónde", "a donde", "a dónde",
        "donde te", "dónde te", "por aca", "por acá", "por aqui", "por aquí", "where do i send",
    ]):
        _add_intent(found, "WHERE_SEND_ID")

    if "id" in t and any(k in t for k in [
        "encuentro mi id", "encontrar mi id", "donde esta mi id", "dónde está mi id",
        "donde veo mi id", "dónde veo mi id", "como veo mi id", "cómo veo mi id",
        "buscar mi id", "ubico mi id", "aparece mi id", "find my id", "where is my id",
    ]):
        _add_intent(found, "ID")

    if any(k in t for k in ["vpn", "proxy"]):
        _add_intent(found, "VPN")
    if (("error" in t or "problema" in t) and ("pais" in t or "país" in t or "country" in t)):
        _add_intent(found, "PAIS")

    if any(k in t for k in [
        "horario", "horarios", "live", "en vivo", "directo", "transmision", "transmisión", "stream", "streaming",
        "a que hora te conectas", "a qué hora te conectas", "cuando te conectas", "cuándo te conectas",
        "cuando hay live", "cuándo hay live", "when do you go live", "live schedule",
    ]):
        _add_intent(found, "LIVE")

    if any(k in t for k in [
        "niveles", "nivel basico", "nivel básico", "nivel premium", "nivel prestige", "planes", "plan basico",
        "plan básico", "plan premium", "plan prestige", "cuanto necesito para entrar", "cuánto necesito para entrar",
        "cuanto debo depositar", "cuánto debo depositar", "inversion minima", "inversión mínima", "minimum investment",
        "cuanto cuesta", "cuánto cuesta", "cuanto vale", "cuánto vale", "que niveles tienes", "qué niveles tienes",
        "niveles disponibles", "planes disponibles", "cuanto hay que invertir", "cuánto hay que invertir",
        "como hago mi inversion", "cómo hago mi inversión", "levels", "plans", "basic level", "premium level", "prestige level",
    ]):
        _add_intent(found, "NIVELES")

    if any(k in t for k in ["bono", "bonos", "bonus", "100%", "70%", "top1_johatrader", "top_1johaale"]):
        _add_intent(found, "BONO")

    if any(k in t for k in [
        "beneficios", "beneficio", "que incluye", "qué incluye", "que recibo", "qué recibo", "vip incluye",
        "herramientas incluye", "benefits", "what is included", "what do i get",
    ]):
        _add_intent(found, "BENEFICIOS")

    if any(k in t for k in [
        "señales", "senales", "software premium", "señales premium", "senales premium", "crypto idx", "cryptoidx",
        "pares de divisas", "expiracion", "expiración", "martingala", "mg1", "mg2", "signals",
    ]):
        _add_intent(found, "SENALES")

    if any(k in t for k in [
        "bot ia", "bot de ia", "bot inteligencia artificial", "bot de inteligencia artificial", "señales 24/7", "senales 24/7",
        "ai bot", "signal bot", "bot 24/7",
    ]):
        _add_intent(found, "BOT_IA")

    if any(k in t for k in ["retiro", "retirar", "withdraw", "rechazo", "rechazado", "no me deja retirar"]):
        _add_intent(found, "RETIRO")
    if any(k in t for k in ["metodo", "método", "metodos", "métodos", "banco", "cuenta bancaria", "astropay", "nequi", "transfiya"]):
        _add_intent(found, "METODOS")
    if any(k in t for k in ["no me llega el correo", "no llega el correo", "no me llega email", "correo", "email"]):
        _add_intent(found, "EMAIL")

    if re.search(r"^(hola|holi|hello|hey|buenas|buenos|buen dia|buenas noches|buenas tardes)\b", t):
        _add_intent(found, "GREETING")

    return found


def _split_question_parts(texto: str):
    """Separa preguntas/cláusulas sin romper expresiones normales como 'Stockity y Binomo'."""
    raw = re.sub(r"\s+", " ", (texto or "").strip())
    if not raw:
        return []
    # Cortes fuertes.
    parts = re.split(r"[?;\n]+", raw)
    refined = []
    for part in parts:
        part = part.strip(" .,!¿¡")
        if not part:
            continue
        # Cortamos por "y" solo cuando introduce claramente otra pregunta/duda.
        sub = re.split(
            r"\s+(?:y|tambien|también|ademas|además|and|also)\s+(?=(?:como|cómo|que|qué|cual|cuál|cuanto|cuánto|cuando|cuándo|donde|dónde|puedo|tienes|tiene|dime|quiero|necesito|how|what|when|where|can|do|tell)\b)",
            part,
            flags=re.I,
        )
        refined.extend(x.strip(" .,!¿¡") for x in sub if x.strip(" .,!¿¡"))
    return refined or [raw]


def _question_analysis(texto: str):
    """Devuelve intenciones conocidas y fragmentos que necesitan IA."""
    intents = detect_all_intents(texto)
    unknown_parts = []
    for part in _split_question_parts(texto):
        p_intents = detect_all_intents(part)
        # Saludos solos no cuentan como una duda resuelta.
        meaningful = [i for i in p_intents if i != "GREETING"]
        if not meaningful and len(_norm(part)) >= 5:
            unknown_parts.append(part)
    return intents, unknown_parts


def respuesta_senales_es() -> str:
    return (
        "📊 Señales\n\n"
        "🚀 Software Premium anticipado: aproximadamente 200 o más señales de lunes a sábado entre Divisas y CRYPTO IDX. "
        "Cada señal trae su minuto de entrada; se toma en ese minuto o aprox. 2 segundos antes para reducir delay.\n\n"
        "🤖 Bot IA 24/7: si la alerta llega durante el minuto 10, la entrada corresponde al minuto 11, aunque llegue avanzada.\n\n"
        "⏱ Todas son a 1 minuto de expiración. MG1 y MG2 son opcionales; usarlos aumenta el riesgo."
    )


def respuesta_senales_en() -> str:
    return (
        "📊 Signals\n\n"
        "🚀 Premium advance software: approximately 200+ signals Monday to Saturday across currency pairs and CRYPTO IDX. "
        "Each signal includes its entry minute; enter at that minute or about 2 seconds before to reduce platform delay.\n\n"
        "🤖 24/7 AI bot: if an alert arrives during minute 10, the entry is taken at minute 11, even if the alert arrives late in minute 10.\n\n"
        "⏱ All signals use 1-minute expiration. MG1/MG2 are optional and increase risk."
    )


def respuesta_bot_ia_es() -> str:
    return (
        "🤖 Bot de señales IA 24/7\n\n"
        "Cuando recibes una alerta, la entrada se toma en el minuto siguiente. Ejemplo: alerta en minuto 10 → entrada en minuto 11. "
        "No importa si la alerta llegó con varios segundos avanzados.\n\n"
        "⏱ Expiración: 1 minuto. MG1/MG2 son opcionales y aumentan el riesgo."
    )


def respuesta_bot_ia_en() -> str:
    return (
        "🤖 24/7 AI signal bot\n\n"
        "When an alert arrives, take the entry on the next minute. Example: alert during minute 10 → entry on minute 11, even if it arrives late in minute 10.\n\n"
        "⏱ Expiration: 1 minute. MG1/MG2 are optional and increase risk."
    )


def _immediate_block(intent: str, lang: str):
    """Texto inmediato de una intención. None = requiere IA o manejo especial."""
    if intent == "NIVELES":
        return respuesta_niveles_es() if lang == "es" else respuesta_niveles_en()
    if intent == "BONO":
        return respuesta_bono_es() if lang == "es" else respuesta_bono_en()
    if intent == "LIVE":
        return LIVE_HORARIOS_ES if lang == "es" else LIVE_HORARIOS_EN
    if intent == "ID":
        return respuesta_id_es() if lang == "es" else "🆔 Open your Stockity or Binomo profile/settings and copy the ID / User ID number."
    if intent == "REGISTRO":
        return MENSAJE_REGISTRARME_ES if lang == "es" else MENSAJE_REGISTRARME_EN
    if intent == "YA_TENGO_CUENTA":
        return MENSAJE_YA_TENGO_CUENTA_ES if lang == "es" else MENSAJE_YA_TENGO_CUENTA_EN
    if intent == "BENEFICIOS":
        return BENEFICIOS_ES if lang == "es" else BENEFICIOS_EN
    if intent == "SENALES":
        return respuesta_senales_es() if lang == "es" else respuesta_senales_en()
    if intent == "BOT_IA":
        return respuesta_bot_ia_es() if lang == "es" else respuesta_bot_ia_en()
    if intent == "NEXT_STEP":
        return respuesta_next_step_es() if lang == "es" else "✅ The next step is to validate your Stockity or Binomo ID before depositing. Send me the ID as text (numbers only)."
    if intent == "WHERE_SEND_ID":
        return respuesta_where_send_id_es() if lang == "es" else "Yes ✅ Send your ID right here (numbers only) and I will leave it for validation."
    if intent == "YA_REGISTRE":
        return (
            "✅ Perfecto. Envíame tu ID de Stockity o Binomo por aquí mismo (solo el número) y lo dejo en validación."
            if lang == "es" else
            "✅ Perfect. Send your Stockity or Binomo ID here (numbers only) and I will leave it for validation."
        )
    if intent == "DEP_LATER":
        return (
            "✅ Perfecto. Cuando tengas listo tu depósito desde 50 USD, escríbeme y continuamos con la activación."
            if lang == "es" else
            "✅ Perfect. When you are ready with a deposit from 50 USD, message me and we will continue."
        )
    if intent == "MIN_50":
        return (
            "💰 El nivel Básico inicia desde 50 USD. Si en este momento tienes menos, escríbeme directamente para revisar tu caso."
            if lang == "es" else
            "💰 The Basic level starts from 50 USD. If you currently have less, message me directly so I can review your case."
        )
    if intent in ("VPN", "PAIS"):
        return (
            "🌎 Para VPN, restricción o error de país prefiero revisar tu caso directamente contigo."
            if lang == "es" else
            "🌎 For VPN/country restriction issues, I prefer to review your case directly with you."
        )
    if intent == "GESTION_CAPITAL":
        return (
            "📊 La gestión de capital la reviso personalmente porque depende de modalidad, disponibilidad y condiciones actuales. Escríbeme directamente y te explico."
            if lang == "es" else
            "📊 I review capital-management requests personally because they depend on the current option, availability and conditions. Message me directly."
        )
    return None


def _multi_info_response(texto: str, lang: str):
    """Compatibilidad: construye respuesta para todas las intenciones inmediatas detectadas."""
    intents, unknown_parts = _question_analysis(texto)
    blocks = []
    handled = []
    for intent in intents:
        if intent == "GREETING" and len(intents) > 1:
            continue
        block = _immediate_block(intent, lang)
        if block and block not in blocks:
            blocks.append(block)
            handled.append(intent)
    needs_ai = bool(unknown_parts) or any(i in intents for i in ("RETIRO", "METODOS", "EMAIL"))
    if "BONO" in intents and bono_requiere_guia(texto):
        needs_ai = True
    if len(intents) < 2 and not (handled and needs_ai):
        return None, intents, needs_ai
    return "\n\n────────────\n\n".join(blocks) if blocks else None, intents, needs_ai


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
        f"🔗 Stockity — opción principal:\n{ENLACE_REFERIDO_STOCKITY}\n\n"
        f"🔗 Binomo — opción secundaria:\n{ENLACE_REFERIDO}\n\n"
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
        f"🔗 Stockity — primary option:\n{ENLACE_REFERIDO_STOCKITY}\n\n"
        f"🔗 Binomo — secondary option:\n{ENLACE_REFERIDO}\n\n"
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


def _responses_api_text(payload_json: dict) -> str:
    """Extrae texto de una respuesta de /v1/responses sin alterar el formato."""
    texts = []
    for item in (payload_json or {}).get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text":
                texts.append(c.get("text", ""))
    return "\n".join(t for t in texts if t).strip()


def _looks_spanish_for_english_user(text_value: str) -> bool:
    """Detecta una salida claramente española cuando el usuario eligió English."""
    t = " " + _norm(text_value or "") + " "
    markers = (
        " hola ", " vamos ", " puedes ", " desde ", " comunidad ", " inversion ",
        " cuenta ", " formacion ", " herramientas ", " senales ", " bonos ",
        " recuerda ", " te recomiendo ", " si eres ", " para tu ", " aqui tienes ",
        " deposito ", " registrarte ", " preguntas ", " dudas ",
    )
    score = sum(1 for marker in markers if marker in t)
    return score >= 3


async def _translate_to_english(text_value: str) -> str:
    """Traduce una sola vez a inglés preservando formato, enlaces, códigos y emojis."""
    source = (text_value or "").strip()
    if not source or not (HAS_HTTPX and OPENAI_API_KEY):
        return ""
    payload = {
        "model": OPENAI_MODEL,
        "instructions": (
            "Translate the supplied message into natural English. Return ONLY the translated message. "
            "Preserve meaning, paragraph breaks, emojis, URLs, @usernames, trading platform names, "
            "amounts, percentages, promo codes and CTA structure exactly. Do not add explanations, "
            "warnings or new information. If the message is already English, return it unchanged."
        ),
        "input": source,
        "max_output_tokens": 1200,
        "store": False,
    }
    try:
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + OPENAI_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
        if resp.status_code != 200:
            logging.warning("Traducción EN: OpenAI devolvió %s: %s", resp.status_code, resp.text[:400])
            return ""
        return _responses_api_text(resp.json())
    except Exception as e:
        logging.warning("No pude traducir texto a inglés: %s", e)
        return ""


def _organize_ai_registration_links(answer: str, lang: str) -> str:
    """Presenta Stockity/Binomo en bloques separados y evita Markdown literal en Telegram."""
    value = (answer or "").strip()
    if not value:
        return value

    has_stockity = ENLACE_REFERIDO_STOCKITY in value
    has_binomo = ENLACE_REFERIDO in value
    if not (has_stockity or has_binomo):
        return value

    # Convierte cualquier [texto](URL) generado por IA a URL normal.
    if has_stockity:
        value = re.sub(
            r"\[[^\]\n]{1,140}\]\(\s*" + re.escape(ENLACE_REFERIDO_STOCKITY) + r"\s*\)",
            ENLACE_REFERIDO_STOCKITY,
            value,
        )
    if has_binomo:
        value = re.sub(
            r"\[[^\]\n]{1,140}\]\(\s*" + re.escape(ENLACE_REFERIDO) + r"\s*\)",
            ENLACE_REFERIDO,
            value,
        )

    # Retira las líneas viejas que contienen esos links para reconstruirlas con aire.
    clean_lines = []
    for line in value.splitlines():
        stripped = line.strip()
        normalized = _norm(stripped)

        if has_stockity and ENLACE_REFERIDO_STOCKITY in line:
            continue
        if has_binomo and ENLACE_REFERIDO in line:
            continue

        # Limpia etiquetas cortas tipo "- Stockity: Registro..." que quedarían solas.
        if len(stripped) <= 110:
            if has_stockity and "stockity" in normalized and (
                ":" in stripped or stripped.startswith(("-", "•", "🔗"))
                or "registro" in normalized or "register" in normalized
            ):
                continue
            if has_binomo and "binomo" in normalized and (
                ":" in stripped or stripped.startswith(("-", "•", "🔗"))
                or "registro" in normalized or "register" in normalized
            ):
                continue

        clean_lines.append(line)

    body = re.sub(r"\n{3,}", "\n\n", "\n".join(clean_lines)).strip()

    blocks = []
    if has_stockity:
        label = "🔗 Stockity — primary option:" if lang == "en" else "🔗 Stockity — opción principal:"
        blocks.append(f"{label}\n{ENLACE_REFERIDO_STOCKITY}")
    if has_binomo:
        label = "🔗 Binomo — secondary option:" if lang == "en" else "🔗 Binomo — opción secundaria:"
        blocks.append(f"{label}\n{ENLACE_REFERIDO}")

    links_block = "\n\n".join(blocks)
    return f"{body}\n\n{links_block}".strip() if body else links_block


async def openai_answer(question: str, chat_id: int, lang: str, stage: str, already_answered=None) -> str:
    if not (HAS_HTTPX and OPENAI_API_KEY):
        return ""
    try:
        if lang == "en":
            language_instruction = """
CRITICAL OUTPUT LANGUAGE RULE — ENGLISH:
- The user selected ENGLISH in the bot.
- Write the ENTIRE final answer in natural English only.
- NEVER answer in Spanish, even if the knowledge base, chat history or learned Johanna examples contain Spanish.
- Translate any Spanish source information internally before answering.
- Keep URLs, promo codes, brand names, amounts and percentages unchanged.
""".strip()
        else:
            language_instruction = """
REGLA CRÍTICA DE IDIOMA — ESPAÑOL:
- El usuario seleccionó ESPAÑOL en el bot.
- Escribe TODA la respuesta final en español natural.
""".strip()
        real_examples = _johanna_examples_as_text(question=question, limit=28, lang=lang)
        already_answered = already_answered or []
        answered_note = ", ".join(already_answered) if already_answered else ("none" if lang == "en" else "ninguno")
        system = f"""
{language_instruction}

Eres la voz digital de Johanna, conocida como JOHAALETRADER / JT TRADERS TEAMS.
RESPONDE EN PRIMERA PERSONA COMO SI FUERAS JOHANNA. No digas que eres Johabot, un asistente virtual, una IA o un modelo.
Tu función es atender prospectos y miembros usando la base oficial, el historial del usuario y ejemplos reales de respuestas de Johanna.

REGLA CRÍTICA PARA MENSAJES CON VARIAS DUDAS
- Lee el mensaje COMPLETO antes de responder.
- Si contiene 2, 3, 4 o más preguntas/dudas, responde TODAS, una por una, sin omitir ninguna.
- El usuario puede escribir varias dudas sin signos de interrogación; detecta también listas, frases unidas por "y", comas o saltos de línea.
- Temas que el bot ya respondió automáticamente antes de llamarte: {answered_note}.
- RESPONDE ÚNICAMENTE a lo que aparezca en “MENSAJE(S) PENDIENTE(S) DEL USUARIO”.
- NO vuelvas a contestar preguntas del historial que ya tengan respuesta.
- NO repitas los temas ya respondidos salvo una referencia mínima imprescindible para entender la duda restante.

ESTILO DE JOHANNA
- Cercano, totalmente positivo, motivador, persuasivo, directo, comercial y útil, sin exageraciones engañosas.
- PRIORIDAD: respuestas cortas que la gente sí lea. Pregunta simple: aprox. 60–120 palabras. Varias dudas: aprox. 120–220 palabras, solo lo necesario para responderlas todas.
- Normalmente 2 a 4 párrafos cortos. Si hay varias preguntas, usa bloques breves o numeración clara. Evita introducciones largas, repetir la pregunta o explicar dos veces lo mismo.
- Usa algunos emojis para hacer la respuesta atractiva, sin saturar.
- Contesta primero lo que preguntaron y termina, cuando corresponda, con un CTA claro y motivador hacia el siguiente paso: registro → ID → depósito → acceso.
- Si es un miembro actual, prioriza resolver su duda de señales, bots, clases o herramientas antes de hacer CTA comercial.
- Si preguntan por niveles/planes/inversión mínima, comienza aclarando que mi comunidad es GRATIS y que el dinero se deposita directamente en la PROPIA cuenta de trading. Muestra Básico/Premium/Prestige con emojis, SIN asteriscos alrededor de los nombres y SIN mencionar Forex automatizado. Incluye Stockity primero y Binomo segundo y recalca que ANTES de depositar deben enviarme el ID para validarlo conmigo.
- Si preguntan cuánto recomiendo para empezar: se puede iniciar desde 50 USD, pero la recomendación habitual es 200 USD o más si está dentro de sus posibilidades. Explica que un capital mayor ofrece más margen operativo y flexibilidad para gestionar riesgo y distribuir entradas, por lo que puede ayudar a aprovechar mejor las herramientas. NUNCA lo presentes como garantía de mejores resultados o ganancias.
- FORMATO DE ENLACES: nunca uses Markdown tipo [texto](URL). Si incluyes Stockity/Binomo, usa EXACTAMENTE bloques separados. En español: "🔗 Stockity — opción principal:" + URL en la línea siguiente, una línea en blanco, luego "🔗 Binomo — opción secundaria:" + URL en la línea siguiente. En inglés: "🔗 Stockity — primary option:" + URL, línea en blanco, luego "🔗 Binomo — secondary option:" + URL. Stockity siempre primero y Binomo después.
- Los ejemplos reales de Johanna sirven para aprender vocabulario, ritmo y conocimiento. No generalices una excepción claramente individual.

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
            f"MENSAJE(S) PENDIENTE(S) DEL USUARIO:\n{question.strip()}"
        )
        payload = {
            "model": OPENAI_MODEL,
            "instructions": system,
            "input": user_input,
            "max_output_tokens": 550,
            "store": False,
        }
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + OPENAI_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
        if resp.status_code != 200:
            logging.warning("OpenAI Responses API devolvió %s: %s", resp.status_code, resp.text[:500])
            return ""
        answer = _responses_api_text(resp.json())
        # Cinturón de seguridad: si un usuario EN recibe una salida claramente española
        # por influencia del historial/base de conocimiento, la traducimos una sola vez
        # antes de enviarla. Normalmente no se ejecuta gracias a la regla estricta arriba.
        if lang == "en" and _looks_spanish_for_english_user(answer):
            translated = await _translate_to_english(answer)
            if translated:
                answer = translated

        # Presentación estable de links para Telegram: sin Markdown literal y con separación.
        answer = _organize_ai_registration_links(answer, lang)
        return answer
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

    # Cinturón de seguridad final: jamás enviar IA a grupo/canal/tema.
    if not _is_private_user_id(chat_id):
        _clear_pending_ai_db(chat_id)
        logging.warning("🛡️ IA diferida no privada descartada: %s", chat_id)
        return

    expected_message_id = str(data.get("message_id") or "")
    pending = _get_pending_ai(chat_id)
    if not pending:
        return

    # Un mensaje nuevo puede haber reemplazado este job.
    if expected_message_id and str(pending.get("message_id") or "") != expected_message_id:
        return

    now = utcnow_naive()
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
    answer = await openai_answer(question, chat_id, lang, stage, pending.get("answered_topics") or [])
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
            reply_markup=support_keyboard(lang),
            disable_web_page_preview=True,
        )
        _clear_pending_ai_db(chat_id)
        _append_ai_exchange(chat_id, question, answer)
        await _send_scheduled_ai_admin_log(context, chat_id, question, answer)
    except Exception as e:
        logging.warning("No se pudo enviar la respuesta IA a %s: %s", chat_id, e)


def schedule_ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text_value: str, answered_topics=None):
    if not text_value or not text_value.strip():
        return
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    chat_id = update.effective_chat.id
    if not _is_private_user_id(chat_id):
        return
    message_id = update.effective_message.message_id
    _cancel_ai_job(context, chat_id)
    _set_pending_ai(chat_id, text_value, message_id, answered_topics=answered_topics or [])
    if context.job_queue:
        context.job_queue.run_once(
            delayed_ai_reply,
            when=AI_WAIT_SECONDS,
            data={"chat_id": chat_id, "message_id": str(message_id)},
            name=f"AI_REPLY_{chat_id}",
        )


async def recover_pending_ai_jobs(application):
    """Recupera únicamente IA pendiente de chats privados; limpia residuos de grupos/canales."""
    if not application.job_queue:
        return
    now = utcnow_naive()
    recovered = 0
    discarded_non_private = 0
    try:
        with Session() as session:
            users = (
                session.query(
                    Usuario.telegram_id,
                    Usuario.ai_pending_due_at,
                    Usuario.ai_pending_text,
                    Usuario.ai_pending_message_id,
                )
                .filter(Usuario.ai_pending_due_at.isnot(None))
                .all()
            )

        for telegram_id, due_at, pending_text, pending_message_id in users:
            if not pending_text:
                continue

            try:
                chat_id = int(telegram_id)
            except Exception:
                continue

            if not _is_private_user_id(chat_id):
                _clear_pending_ai_db(chat_id)
                discarded_non_private += 1
                continue

            delay = max(2, int((due_at - now).total_seconds()))
            application.job_queue.run_once(
                delayed_ai_reply,
                when=delay,
                data={"chat_id": chat_id, "message_id": str(pending_message_id or "")},
                name=f"AI_REPLY_{chat_id}",
            )
            recovered += 1

        logging.info("✅ IA pendientes recuperadas: %s", recovered)
        if discarded_non_private:
            logging.info("🧹 IA antiguas de grupos/canales eliminadas: %s", discarded_non_private)
    except Exception as e:
        logging.warning("No se pudieron recuperar respuestas IA pendientes: %s", e)


async def _send_user_blocks(update: Update, text_value: str, reply_markup=None):
    """Envía texto largo en bloques seguros para Telegram; teclado solo en el último."""
    text_value = (text_value or "").strip()
    if not text_value:
        return
    max_len = 3600
    chunks = []
    remaining = text_value
    while len(remaining) > max_len:
        cut = remaining.rfind("\n\n", 0, max_len)
        if cut < 900:
            cut = remaining.rfind("\n", 0, max_len)
        if cut < 900:
            cut = max_len
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    for idx, chunk in enumerate(chunks):
        await update.effective_message.reply_text(
            chunk,
            reply_markup=reply_markup if idx == len(chunks) - 1 else None,
            disable_web_page_preview=True,
        )


async def _handle_multi_question(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str, lang: str, intents, unknown_parts):
    """Responde todas las partes conocidas y deja solo lo restante para IA a los 8 min."""
    chat_id = update.effective_chat.id
    blocks = []
    handled = []
    needs_ai_topics = []

    # Si hay otras preguntas, el saludo no necesita un bloque separado.
    effective_intents = [i for i in intents if i != "GREETING"] or intents

    for intent in effective_intents:
        # Side effects de flujos operativos.
        if intent == "ID_SUBMIT":
            _record_submitted_trading_id(chat_id, texto, context)
            block = (
                "✅ ID recibido. Lo dejo en validación y te confirmaré cuando esté correcto."
                if lang == "es" else
                "✅ ID received. I will leave it for validation and confirm once it has been checked."
            )
            blocks.append(block); handled.append(intent); continue

        if intent == "DEPOSITO":
            _log_event(chat_id, "DEPOSIT_REPORTED", texto)
            block = (
                "💳 Perfecto. Envíame aquí el comprobante de depósito/activación y tu ID de Stockity o Binomo en texto para revisarlo y habilitar el acceso."
                if lang == "es" else
                "💳 Perfect. Send me the deposit/activation proof and your Stockity/Binomo ID as text so I can review it and enable access."
            )
            blocks.append(block); handled.append(intent); continue

        if intent in ("RETIRO", "METODOS", "EMAIL"):
            needs_ai_topics.append(intent)
            continue

        if intent == "BONO" and bono_requiere_guia(texto):
            # Mostramos los bonos activos ya mismo y dejamos condiciones/detalles a la IA.
            block = _immediate_block(intent, lang)
            if block:
                blocks.append(block); handled.append(intent)
            needs_ai_topics.append("BONO_DETALLE")
            continue

        block = _immediate_block(intent, lang)
        if block:
            if block not in blocks:
                blocks.append(block)
            handled.append(intent)
        elif intent not in ("GREETING",):
            needs_ai_topics.append(intent)

    if effective_intents == ["GREETING"]:
        blocks.append("¡Hola! 🤍 ¿En qué puedo ayudarte hoy?" if lang == "es" else "Hi! 🤍 How can I help you today?")
        handled.append("GREETING")

    # Si el mensaje mezcla preguntas conocidas con preguntas abiertas/no reconocidas,
    # evitamos enviar una respuesta parcial (por ejemplo solo BONO) y luego otra
    # respuesta de IA. En ese caso la IA recibe el mensaje COMPLETO y responde una
    # sola vez después de la ventana de prioridad de Johanna.
    #
    # Excepción: ID_SUBMIT/DEPOSITO mantienen su acuse inmediato por ser flujos
    # operativos sensibles; cualquier duda adicional sí queda para IA.
    mixed_needs_ai = bool(unknown_parts or needs_ai_topics)
    operational_handled = any(i in handled for i in ("ID_SUBMIT", "DEPOSITO"))
    if mixed_needs_ai and blocks and not operational_handled:
        schedule_ai_reply(update, context, texto.strip(), answered_topics=[])
        return True

    if blocks:
        combined = "\n\n────────────\n\n".join(blocks)
        keyboard = live_keyboard(lang) if "LIVE" in handled else support_keyboard(lang)
        await _send_user_blocks(update, combined, reply_markup=keyboard)
        await send_admin_auto_log(context, update, "MULTI_" + "+".join(effective_intents), combined)

    if unknown_parts or needs_ai_topics or not blocks:
        # La IA solo recibe las partes que quedaron SIN responder.
        # Esto evita que, después de una respuesta automática multi-pregunta,
        # vuelva a repetir niveles/bonos/registro a los 8 minutos.
        ai_parts = []
        for part in _split_question_parts(texto):
            p_intents = [i for i in detect_all_intents(part) if i != "GREETING"]
            if not p_intents:
                ai_parts.append(part)
                continue

            for p_intent in p_intents:
                if p_intent in needs_ai_topics:
                    ai_parts.append(part)
                    break
                if p_intent == "BONO" and "BONO_DETALLE" in needs_ai_topics:
                    ai_parts.append(part)
                    break

        # Conserva orden y elimina duplicados.
        ai_parts = list(dict.fromkeys(x.strip() for x in ai_parts if x and x.strip()))
        ai_text = "\n".join(ai_parts).strip()

        # Si no hubo bloques automáticos, la IA sí debe recibir el mensaje completo.
        if not ai_text and not blocks:
            ai_text = texto.strip()

        if ai_text:
            schedule_ai_reply(update, context, ai_text, answered_topics=handled)
    return True


# Nueva función para manejar mensajes de usuarios (texto o media)
async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Los filtros de PTB pueden hacer match también con mensajes editados.
    # Si no hay update.message normal, se ignora para no duplicar respuestas ni generar NoneType.
    if update.message is None or update.effective_chat is None:
        return

    # PRIORIDAD ABSOLUTA: Johanna recibe el mensaje inmediatamente.
    try:
        await notificar_admin(update, context)
    except Exception as e:
        logging.warning("No pude notificar al admin, continúo procesando: %s", e)

    try:
        await guardar_mensaje(update, context)
    except Exception as e:
        logging.warning("No pude persistir el mensaje, continúo procesando: %s", e)

    chat_id = update.effective_chat.id
    lang = get_user_lang(chat_id)

    # Voz/audio/video sin texto: revisión humana; la notificación ya fue enviada.
    if update.message.voice or update.message.audio or update.message.video:
        if not (update.message.caption or "").strip():
            return

    # Imagen sin texto: flujo guiado inmediato.
    if update.message and update.message.photo:
        caption = (update.message.caption or "").strip()
        if not caption:
            qtxt = (
                "📩 Recibido. ¿Esta imagen es tu ID de Stockity/Binomo, tu comprobante de depósito/activación o era otra cosa?"
                if lang == "es" else
                "📩 Received. Is this image your Stockity/Binomo ID, your deposit/activation proof, or something else?"
            )
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📌 Es mi ID" if lang == "es" else "📌 This is my ID", callback_data=f"IMG_IS_ID|{chat_id}"),
                    InlineKeyboardButton("💳 Es mi depósito" if lang == "es" else "💳 This is my deposit", callback_data=f"IMG_IS_DEP|{chat_id}")
                ],
                [InlineKeyboardButton("❌ Era otra cosa" if lang == "es" else "❌ Something else", callback_data=f"IMG_IS_OTHER|{chat_id}")],
                *support_rows(lang),
            ])
            await update.message.reply_text(qtxt, reply_markup=kb)
            await send_admin_auto_log(context, update, "AUTO_IMAGE", qtxt)
            return

    texto = update.message.text or update.message.caption or ""
    if not texto.strip():
        return

    intents, unknown_parts = _question_analysis(texto)
    meaningful = [i for i in intents if i != "GREETING"]

    # MULTI-PREGUNTA GENERAL:
    # - 2+ temas conocidos, o
    # - una parte conocida + otra parte no reconocida.
    if len(meaningful) >= 2 or (meaningful and unknown_parts):
        await _handle_multi_question(update, context, texto, lang, intents, unknown_parts)
        return

    # Si no se reconoce ninguna intención fija, la IA recibe el mensaje COMPLETO y
    # responderá todas las dudas después de la ventana de prioridad de Johanna.
    if not intents or (intents == ["GREETING"] and unknown_parts):
        schedule_ai_reply(update, context, texto)
        return

    intent = detect_intent_es(texto)
    # Si el detector antiguo devolvió OTRO pero el detector general sí encontró algo,
    # usamos la intención general detectada para no perder la consulta.
    if intent in ("OTRO", "HUMAN_CHAT") and meaningful:
        intent = meaningful[0]
    bonus_needs_guide = intent == "BONO" and bono_requiere_guia(texto)

    if intent == "GREETING":
        msg = "¡Hola! 🤍 ¿En qué puedo ayudarte hoy?" if lang == "es" else "Hi! 🤍 How can I help you today?"
        await update.message.reply_text(msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "AUTO_GREETING", msg)
        return

    if intent == "YA_TENGO_CUENTA":
        msg = MENSAJE_YA_TENGO_CUENTA_ES if lang == "es" else MENSAJE_YA_TENGO_CUENTA_EN
        await _send_user_blocks(update, msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "YA_TENGO_CUENTA", msg)
        return

    if intent == "REGISTRO":
        msg = MENSAJE_REGISTRARME_ES if lang == "es" else MENSAJE_REGISTRARME_EN
        await _send_user_blocks(update, msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "REGISTRO", msg)
        return

    if intent == "YA_REGISTRE":
        msg = _immediate_block("YA_REGISTRE", lang)
        await update.message.reply_text(msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "AUTO_YA_REGISTRE", msg)
        return

    if intent == "DEP_LATER":
        msg = _immediate_block("DEP_LATER", lang)
        await update.message.reply_text(msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "AUTO_DEPOSIT_LATER", msg)
        return

    if intent == "MIN_50":
        msg = _immediate_block("MIN_50", lang)
        await update.message.reply_text(msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "AUTO_MIN50_ESCALATE", msg)
        return

    if intent == "DEPOSITO":
        _log_event(chat_id, "DEPOSIT_REPORTED", texto)
        msg = (
            "Perfecto ✅\n\nEnvíame aquí tu comprobante de depósito/activación (foto o captura) y también tu ID de Stockity o Binomo en texto para validarlo y habilitar tu acceso."
            if lang == "es" else
            "Perfect ✅\n\nSend me your deposit/activation proof and your Stockity/Binomo ID as text so it can be validated and your access enabled."
        )
        await update.message.reply_text(msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "AUTO_DEPOSIT_CONFIRM", msg)
        return

    if intent == "ID_SUBMIT":
        _record_submitted_trading_id(chat_id, texto, context)
        msg = (
            "✅ Recibido. Ya tengo tu ID y lo dejo en validación. En breve te confirmo si está correcto."
            if lang == "es" else
            "✅ Received. I have your ID and it is now pending validation. I will confirm once it has been checked."
        )
        await update.message.reply_text(msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "ID_SUBMIT", msg)
        return

    if intent in ("GESTION_CAPITAL", "VPN", "PAIS"):
        msg = _immediate_block(intent, lang)
        await update.message.reply_text(msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, intent, msg)
        return

    if intent in ("NEXT_STEP", "WHERE_SEND_ID", "NIVELES", "LIVE", "ID", "BENEFICIOS", "SENALES", "BOT_IA"):
        msg = _immediate_block(intent, lang)
        if msg:
            keyboard = live_keyboard(lang) if intent == "LIVE" else support_keyboard(lang)
            await _send_user_blocks(update, msg, reply_markup=keyboard)
            await send_admin_auto_log(context, update, intent, msg)
            return

    if intent == "BONO":
        if not bonus_needs_guide:
            msg = respuesta_bono_es() if lang == "es" else respuesta_bono_en()
            await update.message.reply_text(msg, reply_markup=support_keyboard(lang))
            await send_admin_auto_log(context, update, "BONO_ACTIVO", msg)
            return
        # Pregunta detallada de bono: muestra los bonos y deja la explicación a la IA.
        msg = respuesta_bono_es() if lang == "es" else respuesta_bono_en()
        await update.message.reply_text(msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "BONO_ACTIVO", msg)
        schedule_ai_reply(update, context, texto, answered_topics=["BONO_ACTIVO"])
        return

    # RETIRO, MÉTODOS, EMAIL, HUMAN_CHAT, OTRO y cualquier consulta abierta.
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

        if not _is_private_user_id(chat_id):
            await update.message.reply_text(
                "🛡️ Envío bloqueado: /enviar solo permite IDs de usuarios privados. "
                "Los avisos al VIP se envían únicamente mediante el flujo LIVE."
            )
            return

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


# === MARKETING MANUAL + AVISO LIVE (20 DÍAS) ===
try:
    LIVE_BROADCAST_DAYS = max(1, int(os.getenv("LIVE_BROADCAST_DAYS", "20")))
except Exception:
    LIVE_BROADCAST_DAYS = 20
try:
    MARKETING_BROADCAST_DAYS = max(1, int(os.getenv("MARKETING_BROADCAST_DAYS", "20")))
except Exception:
    MARKETING_BROADCAST_DAYS = 20

LIVE_BROADCAST_MESSAGE_ES = (
    "🔴 **¡YA CASI EMPEZAMOS EL LIVE!** 🚀\n\n"
    "Voy a conectarme en vivo para operar, analizar el mercado y compartir la sesión contigo. ✨\n\n"
    "🎵 **TikTok es mi canal principal para el LIVE.** Toca el botón y entra ahora.\n"
    "▶️ Si transmito simultáneamente, también podrás entrar por YouTube."
)

LIVE_BROADCAST_MESSAGE_EN = (
    "🔴 **I’M ABOUT TO GO LIVE!** 🚀\n\n"
    "I’m going live to trade, analyze the market and share the session with you. ✨\n\n"
    "🎵 **TikTok is my main LIVE channel.** Tap the button and join now.\n"
    "▶️ If I stream simultaneously, you can also join on YouTube."
)


def live_broadcast_keyboard(user_chat: bool = True, lang: str = "es") -> InlineKeyboardMarkup:
    if lang == "en":
        rows = [
            [InlineKeyboardButton("🔴 JOIN LIVE ON TIKTOK", url=TIKTOK_LIVE_URL)],
            [InlineKeyboardButton("▶️ WATCH ON YOUTUBE", url=YOUTUBE_LIVE_URL)],
        ]
    else:
        rows = [
            [InlineKeyboardButton("🔴 ENTRAR AL LIVE EN TIKTOK", url=TIKTOK_LIVE_URL)],
            [InlineKeyboardButton("▶️ VER EN YOUTUBE", url=YOUTUBE_LIVE_URL)],
        ]
    if user_chat:
        rows.extend(support_rows(lang))
    return InlineKeyboardMarkup(rows)


def _active_recipients(days: int, include_deposited: bool):
    cutoff = utcnow_naive() - timedelta(days=days)
    try:
        with Session() as session:
            rows = (
                session.query(UserActivity.telegram_id, UserActivity.lang, Usuario.stage)
                .outerjoin(Usuario, Usuario.telegram_id == UserActivity.telegram_id)
                .filter(UserActivity.last_activity_at >= cutoff)
                .all()
            )
        recipients = []
        seen = set()
        for telegram_id, lang, stage in rows:
            try:
                cid = int(telegram_id)
            except Exception:
                continue
            if not _is_private_user_id(cid):
                continue
            if cid == ADMIN_ID or cid in seen:
                continue
            if not include_deposited and (stage or STAGE_PRE) == STAGE_DEPOSITED:
                continue
            seen.add(cid)
            recipients.append((cid, lang if lang in ("es", "en") else "es", stage or STAGE_PRE))
        return recipients
    except Exception as e:
        logging.warning("No pude obtener destinatarios activos: %s", e)
        return []


def _recent_live_recipients(days: int = LIVE_BROADCAST_DAYS):
    # LIVE sí incluye a usuarios DEPOSITED.
    return _active_recipients(days, include_deposited=True)


def _recent_marketing_recipients(days: int = MARKETING_BROADCAST_DAYS):
    # Marketing manual solo PRE y POST; nunca DEPOSITED.
    return _active_recipients(days, include_deposited=False)


def _live_preview_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➡️ Continuar sin imagen", callback_data="live_no_image")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="live_broadcast_cancel")],
    ])


def _live_confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sí, enviar aviso LIVE", callback_data="live_broadcast_confirm")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="live_broadcast_cancel")],
    ])


def _marketing_confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirmar marketing", callback_data="marketing_confirm")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="marketing_cancel")],
    ])


async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia LIVE y deja este flujo como el único borrador administrativo activo."""
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    context.user_data.pop("marketing_draft", None)
    context.user_data["admin_broadcast_flow"] = "live"
    recipients = _recent_live_recipients()
    context.user_data["live_draft"] = {"status": "awaiting_image", "photo_file_id": None}

    try:
        await update.effective_message.reply_text(
            "🔴 Aviso LIVE preparado.\n\n"
            f"👥 Usuarios activos últimos {LIVE_BROADCAST_DAYS} días: {len(recipients)}\n"
            f"📢 Canal informativo: {INFO_CHANNEL_ID}\n"
            "👑 Canal VIP: tema configurado\n\n"
            "Si quieres imagen en los canales, envíamela ahora como foto. "
            "Los usuarios privados recibirán SOLO el texto.\n\n"
            "Si no quieres imagen, toca Continuar sin imagen.",
            reply_markup=_live_preview_keyboard(),
        )
    except Exception:
        context.user_data.pop("live_draft", None)
        if context.user_data.get("admin_broadcast_flow") == "live":
            context.user_data.pop("admin_broadcast_flow", None)
        raise


async def _show_live_confirmation(context: ContextTypes.DEFAULT_TYPE, message, photo_file_id=None):
    if context.user_data.get("admin_broadcast_flow") != "live":
        return

    recipients = _recent_live_recipients()
    context.user_data["live_draft"] = {"status": "ready", "photo_file_id": photo_file_id}
    summary = (
        "🔴 CONFIRMAR LIVE\n\n"
        f"👥 Usuarios últimos {LIVE_BROADCAST_DAYS} días: {len(recipients)} (reciben solo texto)\n"
        f"📢 Informativo: {'imagen + texto' if photo_file_id else 'texto'}\n"
        f"👑 VIP: {'imagen + texto' if photo_file_id else 'texto'}\n"
        "🎵 TikTok + ▶️ YouTube incluidos.\n\n"
        "¿Confirmas el envío?"
    )
    if photo_file_id:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_file_id,
            caption=summary,
            reply_markup=_live_confirm_keyboard(),
        )
    else:
        await message.reply_text(summary, reply_markup=_live_confirm_keyboard())


async def marketing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia marketing y deja este flujo como el único borrador administrativo activo."""
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    context.user_data.pop("live_draft", None)
    context.user_data["admin_broadcast_flow"] = "marketing"
    recipients = _recent_marketing_recipients()
    context.user_data["marketing_draft"] = {
        "status": "awaiting_content",
        "photo_file_id": None,
        "text": "",
    }

    try:
        await update.effective_message.reply_text(
            "📣 MARKETING MANUAL\n\n"
            f"Se enviará únicamente a usuarios PRE/POST activos en los últimos {MARKETING_BROADCAST_DAYS} días.\n"
            f"👥 Destinatarios actuales: {len(recipients)}\n"
            "🚫 Los usuarios con cuenta ya activa (DEPOSITED) quedan excluidos.\n\n"
            "Envíame ahora un texto o una foto con texto en el caption. "
            "Si envías la foto sin texto, después te pediré el texto.\n\n"
            "🌐 Escríbelo una sola vez: los usuarios EN recibirán automáticamente la versión en inglés."
        )
    except Exception:
        context.user_data.pop("marketing_draft", None)
        if context.user_data.get("admin_broadcast_flow") == "marketing":
            context.user_data.pop("admin_broadcast_flow", None)
        raise


async def admin_draft_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Captura exclusivamente el borrador LIVE o MARKETING que esté activo."""
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    if update.effective_message.reply_to_message:
        return

    flow = context.user_data.get("admin_broadcast_flow")

    if flow == "live":
        context.user_data.pop("marketing_draft", None)
        live_draft = context.user_data.get("live_draft") or {}
        if live_draft.get("status") == "awaiting_image" and update.message.photo:
            photo_file_id = update.message.photo[-1].file_id
            await _show_live_confirmation(context, update.message, photo_file_id=photo_file_id)
            from telegram.ext import ApplicationHandlerStop
            raise ApplicationHandlerStop
        return

    if flow == "marketing":
        context.user_data.pop("live_draft", None)
        marketing_draft = context.user_data.get("marketing_draft")
        if not marketing_draft:
            return

        status = marketing_draft.get("status")
        if status == "awaiting_content":
            if update.message.photo:
                marketing_draft["photo_file_id"] = update.message.photo[-1].file_id
                caption = (update.message.caption or "").strip()
                if caption:
                    marketing_draft["text"] = caption
                    marketing_draft["status"] = "ready"
                else:
                    marketing_draft["status"] = "awaiting_caption"
                    context.user_data["marketing_draft"] = marketing_draft
                    await update.message.reply_text(
                        "📝 Imagen recibida. Ahora envíame el texto que quieres acompañar la imagen."
                    )
                    from telegram.ext import ApplicationHandlerStop
                    raise ApplicationHandlerStop
            elif update.message.text:
                marketing_draft["text"] = update.message.text.strip()
                marketing_draft["status"] = "ready"
            else:
                return

        elif status == "awaiting_caption" and update.message.text:
            marketing_draft["text"] = update.message.text.strip()
            marketing_draft["status"] = "ready"
        else:
            return

        if marketing_draft.get("status") == "ready":
            context.user_data["marketing_draft"] = marketing_draft
            recipients = _recent_marketing_recipients()
            preview = (
                "📣 VISTA PREVIA MARKETING\n\n"
                f"👥 Destinatarios PRE/POST últimos {MARKETING_BROADCAST_DAYS} días: {len(recipients)}\n"
                "🚫 DEPOSITED: excluidos\n\n"
                + (marketing_draft.get("text") or "")
            )
            if marketing_draft.get("photo_file_id"):
                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=marketing_draft["photo_file_id"],
                    caption=preview[:1024],
                    reply_markup=_marketing_confirm_keyboard(),
                )
            else:
                await update.message.reply_text(
                    preview,
                    reply_markup=_marketing_confirm_keyboard(),
                )

            from telegram.ext import ApplicationHandlerStop
            raise ApplicationHandlerStop


async def _send_live_to_channels(context: ContextTypes.DEFAULT_TYPE, photo_file_id=None):
    results = {"info": False, "vip": False}
    channel_kb = live_broadcast_keyboard(user_chat=False)
    try:
        if photo_file_id:
            await context.bot.send_photo(
                chat_id=INFO_CHANNEL_ID,
                photo=photo_file_id,
                caption=LIVE_BROADCAST_MESSAGE_ES,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=channel_kb,
            )
        else:
            await context.bot.send_message(
                chat_id=INFO_CHANNEL_ID,
                text=LIVE_BROADCAST_MESSAGE_ES,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=channel_kb,
            )
        results["info"] = True
    except Exception as e:
        logging.warning("No pude publicar LIVE en canal informativo: %s", e)

    try:
        # En Telegram el tema General tiene ID=1 y debe recibir mensajes como un
        # supergrupo normal, SIN message_thread_id. Para otros temas sí usamos
        # message_thread_id.
        kwargs = {
            "chat_id": VIP_CHAT_ID,
            "reply_markup": channel_kb,
            "parse_mode": ParseMode.MARKDOWN,
        }
        if VIP_TOPIC_ID and VIP_TOPIC_ID != 1:
            kwargs["message_thread_id"] = VIP_TOPIC_ID

        if photo_file_id:
            await context.bot.send_photo(
                photo=photo_file_id,
                caption=LIVE_BROADCAST_MESSAGE_ES,
                **kwargs,
            )
        else:
            await context.bot.send_message(
                text=LIVE_BROADCAST_MESSAGE_ES,
                **kwargs,
            )
        results["vip"] = True
    except Exception as e:
        results["vip_error"] = str(e)[:500]
        logging.warning("No pude publicar LIVE en VIP/tema: %s", e)
    return results


async def _safe_edit_callback_message(query, text_value: str):
    """Edita texto o caption según el tipo real del mensaje, evitando 400 Bad Request."""
    msg = getattr(query, "message", None)
    if not msg:
        return
    has_caption_media = bool(
        getattr(msg, "photo", None)
        or getattr(msg, "video", None)
        or getattr(msg, "animation", None)
        or getattr(msg, "document", None)
        or getattr(msg, "audio", None)
    )
    try:
        if has_caption_media:
            await query.edit_message_caption(caption=text_value)
        else:
            await query.edit_message_text(text_value)
    except Exception as e:
        logging.info("No pude actualizar mensaje de control: %s", e)


async def live_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    if query.data == "live_broadcast_cancel":
        context.user_data.pop("live_draft", None)
        if context.user_data.get("admin_broadcast_flow") == "live":
            context.user_data.pop("admin_broadcast_flow", None)
        await _safe_edit_callback_message(
            query,
            "❌ Aviso LIVE cancelado. No se envió ningún mensaje.",
        )
        return

    if query.data == "live_no_image":
        if context.user_data.get("admin_broadcast_flow") != "live":
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text="ℹ️ Ese borrador LIVE ya no está activo.",
            )
            return
        await _show_live_confirmation(context, query.message, photo_file_id=None)
        return

    if query.data != "live_broadcast_confirm":
        return

    if context.user_data.get("admin_broadcast_flow") != "live":
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="ℹ️ Ese borrador LIVE ya no está activo.",
        )
        return

    draft = context.user_data.get("live_draft") or {}
    photo_file_id = draft.get("photo_file_id")
    recipients = _recent_live_recipients()
    await _safe_edit_callback_message(
        query,
        f"⏳ Enviando LIVE a {len(recipients)} usuarios...",
    )

    sent = 0
    failed = 0
    for chat_id, lang, _stage in recipients:
        msg = LIVE_BROADCAST_MESSAGE_ES if lang == "es" else LIVE_BROADCAST_MESSAGE_EN
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=live_broadcast_keyboard(user_chat=True, lang=lang),
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception as e:
            retry_after = getattr(e, "retry_after", None)
            if retry_after:
                try:
                    await asyncio.sleep(float(retry_after) + 1)
                    await context.bot.send_message(
                        chat_id=chat_id, text=msg, parse_mode=ParseMode.MARKDOWN,
                        reply_markup=live_broadcast_keyboard(user_chat=True, lang=lang), disable_web_page_preview=True,
                    )
                    sent += 1
                    continue
                except Exception:
                    pass
            failed += 1
            logging.info("Aviso LIVE no entregado a %s: %s", chat_id, e)
        await asyncio.sleep(0.06)

    channel_results = await _send_live_to_channels(context, photo_file_id=photo_file_id)

    # Johanna recibe en su propio bot una copia EXACTA del aviso entregado a los
    # usuarios, con los mismos botones, para poder revisar cómo salió publicado.
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="📬 COPIA DEL AVISO LIVE ENVIADO\n\n" + LIVE_BROADCAST_MESSAGE_ES,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=live_broadcast_keyboard(user_chat=False),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logging.warning("No pude enviar copia LIVE al admin: %s", e)

    context.user_data.pop("live_draft", None)
    if context.user_data.get("admin_broadcast_flow") == "live":
        context.user_data.pop("admin_broadcast_flow", None)

    vip_line = f"👑 VIP: {'✅' if channel_results.get('vip') else '❌'}"
    if not channel_results.get("vip") and channel_results.get("vip_error"):
        vip_line += f"\n⚠️ Error VIP: {channel_results['vip_error']}"

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "✅ Aviso LIVE finalizado.\n\n"
            f"👥 Usuarios enviados: {sent}\n"
            f"🚫 No entregados: {failed}\n"
            f"📅 Ventana usada: últimos {LIVE_BROADCAST_DAYS} días\n"
            f"📢 Informativo: {'✅' if channel_results.get('info') else '❌'}\n"
            + vip_line
        ),
    )


async def marketing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    if query.data == "marketing_cancel":
        context.user_data.pop("marketing_draft", None)
        if context.user_data.get("admin_broadcast_flow") == "marketing":
            context.user_data.pop("admin_broadcast_flow", None)
        await _safe_edit_callback_message(
            query,
            "❌ Marketing cancelado. No se envió ningún mensaje.",
        )
        return

    if query.data != "marketing_confirm":
        return

    if context.user_data.get("admin_broadcast_flow") != "marketing":
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="ℹ️ Ese borrador de marketing ya no está activo.",
        )
        return

    draft = context.user_data.get("marketing_draft") or {}
    marketing_text = (draft.get("text") or "").strip()
    photo_file_id = draft.get("photo_file_id")
    if not marketing_text and not photo_file_id:
        await context.bot.send_message(chat_id=ADMIN_ID, text="⚠️ No hay contenido de marketing preparado.")
        return


    recipients = _recent_marketing_recipients()
    await _safe_edit_callback_message(
        query,
        f"⏳ Preparando y enviando marketing a {len(recipients)} usuarios PRE/POST...",
    )

    # Una sola traducción por campaña. Todos los usuarios EN comparten esta versión;
    # no hacemos una llamada a OpenAI por persona. La imagen, enlaces, códigos y emojis
    # se conservan; solo cambia el texto.
    has_english_recipients = any(lang == "en" for _cid, lang, _stage in recipients)
    marketing_text_en = ""
    translation_failed = False
    if marketing_text and has_english_recipients:
        marketing_text_en = await _translate_to_english(marketing_text)
        if not marketing_text_en:
            translation_failed = True
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ No pude generar la traducción inglesa del marketing. "
                    "Los usuarios EN no recibirán texto en español por error; se contarán como no entregados."
                ),
            )

    sent = 0
    failed = 0
    sent_es = 0
    sent_en = 0
    for chat_id, lang, _stage in recipients:
        outbound_text = marketing_text if lang == "es" else marketing_text_en
        # Si había texto y falló su traducción, no enviamos accidentalmente español a EN.
        if lang == "en" and marketing_text and not outbound_text:
            failed += 1
            continue
        try:
            if photo_file_id:
                if outbound_text and len(outbound_text) > 1000:
                    await context.bot.send_photo(chat_id=chat_id, photo=photo_file_id)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=outbound_text,
                        reply_markup=support_keyboard(lang),
                        disable_web_page_preview=True,
                    )
                else:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_file_id,
                        caption=outbound_text if outbound_text else None,
                        reply_markup=support_keyboard(lang),
                    )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=outbound_text,
                    reply_markup=support_keyboard(lang),
                    disable_web_page_preview=True,
                )
            sent += 1
            if lang == "en":
                sent_en += 1
            else:
                sent_es += 1
        except Exception as e:
            failed += 1
            logging.info("Marketing no entregado a %s: %s", chat_id, e)
        await asyncio.sleep(0.06)

    context.user_data.pop("marketing_draft", None)
    if context.user_data.get("admin_broadcast_flow") == "marketing":
        context.user_data.pop("admin_broadcast_flow", None)
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "✅ Marketing manual finalizado.\n\n"
            f"👥 Enviados: {sent}\n"
            f"🇪🇸 Español: {sent_es}\n"
            f"🇺🇸 English: {sent_en}\n"
            f"🚫 No entregados: {failed}\n"
            f"📅 Ventana: últimos {MARKETING_BROADCAST_DAYS} días\n"
            + ("⚠️ Traducción EN falló.\n" if translation_failed else "🌐 Traducción EN automática: activa.\n")
            + "🛡 DEPOSITED excluidos automáticamente."
        ),
    )


async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    await update.effective_message.reply_text(
        f"✅ Bot activo: {BOT_VERSION}\n"
        f"⏱ IA: {AI_WAIT_MINUTES} min\n"
        f"📣 Marketing/LIVE: {LIVE_BROADCAST_DAYS} días\n"
        "🧠 Multi-pregunta general: ACTIVA"
    )


async def admin_control_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router prioritario de comandos de Johanna.

    Intercepta los comandos administrativos incluso si Telegram no los entrega
    como entidad BotCommand. Al terminar detiene el procesamiento para impedir
    que responder_a_usuario los interprete como una respuesta manual.
    """
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    raw = (update.effective_message.text or "").strip()
    t = raw.lower()
    # Acepta /comando, /comando@NombreDelBot y LIVE escrito sin barra.
    base = t.split("@", 1)[0] if t.startswith("/") else t
    handled = True
    if base in ("/live", "live"):
        await live_command(update, context)
    elif base in ("/marketing", "marketing"):
        await marketing_command(update, context)
    elif base in ("/reporte", "reporte"):
        await daily_report_command(update, context)
    elif base in ("/version", "version"):
        await version_command(update, context)
    else:
        handled = False

    if handled:
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop


async def ignore_non_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ignora por completo mensajes/commands recibidos desde grupos, supergrupos, temas o canales."""
    from telegram.ext import ApplicationHandlerStop
    raise ApplicationHandlerStop


async def ignore_non_private_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bloquea botones viejos que todavía existan dentro de grupos/temas VIP."""
    chat = update.effective_chat
    if chat and chat.type != "private":
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop


def _cleanup_non_private_artifacts():
    """Limpia residuos creados antes del bloqueo global sin tocar usuarios privados."""
    cleared_ai = 0
    removed_activity = 0
    removed_campaigns = 0
    try:
        with Session() as session:
            users = session.query(Usuario).all()
            for user in users:
                if _is_private_user_id(user.telegram_id):
                    continue
                if user.ai_pending_text or user.ai_pending_due_at or user.ai_pending_message_id:
                    user.ai_pending_text = None
                    user.ai_pending_due_at = None
                    user.ai_pending_message_id = None
                    cleared_ai += 1

            activities = session.query(UserActivity).all()
            for row in activities:
                if not _is_private_user_id(row.telegram_id):
                    session.delete(row)
                    removed_activity += 1

            campaign_rows = (
                session.query(CampaignJob)
                .filter(CampaignJob.sent_at.is_(None))
                .all()
            )
            for row in campaign_rows:
                if not _is_private_user_id(row.telegram_id):
                    session.delete(row)
                    removed_campaigns += 1

            session.commit()

        if cleared_ai or removed_activity or removed_campaigns:
            logging.info(
                "🧹 Limpieza no privada: IA=%s | actividad=%s | campañas=%s",
                cleared_ai, removed_activity, removed_campaigns,
            )
    except Exception as e:
        logging.warning("No pude limpiar residuos no privados: %s", e)


async def post_init_app(application):
    logging.info("✅ Iniciando %s", BOT_VERSION)
    _cleanup_non_private_artifacts()
    await recover_pending_ai_jobs(application)
    await recover_pending_campaign_jobs(application)
    schedule_daily_report(application)
    try:
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text="⚙️ Panel administrador listo.",
            reply_markup=admin_persistent_keyboard(),
        )
    except Exception as e:
        logging.info("No pude mostrar el botón persistente del admin: %s", e)


# === EJECUCIÓN ===
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).post_init(post_init_app).build()

    # BLOQUEO GLOBAL: el VIP/grupos/temas/canales son solo destinos de salida.
    # Nada recibido allí puede activar menús, IA, campañas, reportes ni flujos del bot.
    app.add_handler(
        MessageHandler(~filters.ChatType.PRIVATE, ignore_non_private_message),
        group=-100,
    )
    app.add_handler(
        CallbackQueryHandler(ignore_non_private_callback),
        group=-100,
    )

    # Botón persistente privado del ADMIN. Tiene prioridad absoluta.
    app.add_handler(
        MessageHandler(
            filters.User(ADMIN_ID) & filters.TEXT & filters.Regex(r"^⚙️ MENÚ ADMIN$"),
            admin_panel_button,
        ),
        group=-20,
    )

    # Router administrativo PRIORITARIO. Evita que /live, /marketing o /reporte
    # terminen por error en el manejador genérico de respuestas del admin.
    app.add_handler(
        MessageHandler(
            filters.User(ADMIN_ID) & filters.TEXT & filters.Regex(r"(?i)^\s*(?:/live(?:@\w+)?|live|/marketing(?:@\w+)?|marketing|/reporte(?:@\w+)?|reporte|/version(?:@\w+)?|version)\s*$"),
            admin_control_router,
        ),
        group=-10,
    )

    # Comando /start (selector de idioma)
    app.add_handler(CommandHandler("start", start))

    # Aviso LIVE 20 días y marketing manual 20 días.
    app.add_handler(CommandHandler("live", live_command))
    app.add_handler(MessageHandler(filters.User(ADMIN_ID) & filters.Regex(r"(?i)^live$"), live_command))
    app.add_handler(CommandHandler("marketing", marketing_command))
    app.add_handler(CommandHandler("reporte", daily_report_command))
    app.add_handler(CommandHandler("version", version_command))

    # Captura fotos/texto de borradores antes del manejador normal del admin.
    app.add_handler(
        MessageHandler(filters.User(ADMIN_ID) & (filters.TEXT | filters.PHOTO) & ~filters.COMMAND, admin_draft_capture),
        group=-1,
    )

    # Enviar imagen, video, audio usando /enviar desde caption (solo multimedia)
    app.add_handler(MessageHandler(
        filters.User(ADMIN_ID) &
        (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO) &
        filters.CaptionRegex(r"^/enviar "),
        enviar_mensaje_directo
    ))

    # Panel privado del ADMIN (antes de cualquier callback general).
    app.add_handler(CallbackQueryHandler(admin_panel_callback, pattern="^admin_panel_"))

    # Confirmación/cancelación LIVE y marketing (antes del callback general).
    app.add_handler(CallbackQueryHandler(live_broadcast_callback, pattern="^(live_broadcast_|live_no_image$)"))
    app.add_handler(CallbackQueryHandler(marketing_callback, pattern="^marketing_"))

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

    # Mensajes normales de los usuarios (texto o media) — SOLO CHAT PRIVADO.
    # Excluye grupos, supergrupos, temas del VIP y canales para que nunca entren
    # al flujo de prospectos, notificaciones, reportes ni respuestas IA.
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE
        & (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE)
        & ~filters.COMMAND
        & ~filters.User(ADMIN_ID),
        manejar_mensaje,
    ))

    logging.info("Bot corriendo…")
    app.run_polling()
