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
    ChatMemberHandler,
    ChatJoinRequestHandler,
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

# Grupo privado de reportes ADS.
# Railway puede sobreescribir este valor con la variable REPORT_CHAT_ID.
DEFAULT_REPORT_CHAT_ID = -5324419580
try:
    REPORT_CHAT_ID = int((os.getenv("REPORT_CHAT_ID", str(DEFAULT_REPORT_CHAT_ID)) or str(DEFAULT_REPORT_CHAT_ID)).strip())
except Exception:
    REPORT_CHAT_ID = DEFAULT_REPORT_CHAT_ID

# Dashboard privado de ADS REPORTS. Railway puede sobreescribirlo sin modificar código.
DASHBOARD_URL = (
    os.getenv("DASHBOARD_URL", "https://johaale-tracking-production.up.railway.app/dashboard")
    or "https://johaale-tracking-production.up.railway.app/dashboard"
).strip()

BOT_VERSION = "v7.10.26-20260918-PROCESS-BUTTONS-CLEAN"
# v7.10.26: soporte/menú se reservan para cierres e información;
# durante pasos operativos se muestra únicamente la acción necesaria para continuar.
TELEGRAPH_LEVELS_URL = "https://telegra.ph/NIVELES-JT-TRADERS-TEAMS-09-18"


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
        respuesta = _personalize_referral_links(respuesta, chat_id)
        u = update.effective_user
        user_label = _telegram_display_name(u)
        msg = update.effective_message
        pregunta = ((getattr(msg, "text", None) or getattr(msg, "caption", None) or "").strip() or "(sin texto)")
        text = (
            "🤖 RESPUESTA AUTOMÁTICA\n"
            f"Usuario: {user_label} | ID: {chat_id}\n"
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

# === PUENTE DE TRACKING (servicio independiente) ===
# Si alguna variable falta o el servicio externo falla, el bot principal continúa
# funcionando exactamente igual; el tracking nunca bloquea los flujos del bot.
TRACKING_BASE_URL = (os.getenv("TRACKING_BASE_URL") or "").strip().rstrip("/")
TRACKING_SECRET = (os.getenv("TRACKING_SECRET") or "").strip()

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


class ChannelSourceAttribution(Base):
    """Origen first-touch del usuario al entrar al canal informativo ES.

    Se mantiene en tabla independiente para no alterar `usuarios` ni ninguna
    consulta histórica del bot. ADS es la única fuente especial; todo lo que
    no tenga marca ADS queda como ORGANIC_OTHER.
    """
    __tablename__ = "channel_source_attribution"
    telegram_id   = Column(String, primary_key=True)
    source        = Column(String, default="ORGANIC_OTHER", index=True)
    first_seen_at = Column(DateTime, default=utcnow_naive, index=True)
    last_seen_at  = Column(DateTime, default=utcnow_naive, index=True)


class ChannelJoinEvent(Base):
    """Cada ingreso/reingreso detectado al canal, separado del historial del bot."""
    __tablename__ = "channel_join_events"
    id          = Column(Integer, primary_key=True)
    telegram_id = Column(String, index=True)
    source      = Column(String, default="ORGANIC_OTHER", index=True)
    invite_name = Column(String)
    created_at  = Column(DateTime, default=utcnow_naive, index=True)


class AdsClickAttribution(Base):
    """Último click_id confirmado de publicidad para personalizar enlaces de afiliado."""
    __tablename__ = "ads_click_attribution"
    telegram_id = Column(String, primary_key=True)
    click_id    = Column(String(40), index=True)
    updated_at  = Column(DateTime, default=utcnow_naive, index=True)


class VIPAccessState(Base):
    """Estado global de acceso a la comunidad (nivel más alto del usuario)."""
    __tablename__ = "vip_access_state"
    telegram_id           = Column(String, primary_key=True)
    validated_total_cents = Column(Integer, default=0)
    level                 = Column(String, default="NONE", index=True)
    pending_access_keys   = Column(Text)
    welcome_level         = Column(String)
    updated_at            = Column(DateTime, default=utcnow_naive, index=True)


class BrokerAccountState(Base):
    """Cuenta independiente por Telegram + broker, sin mezclar Binomo y Stockity."""
    __tablename__ = "broker_account_state"
    account_key                 = Column(String, primary_key=True)
    telegram_id                 = Column(String, index=True)
    broker                      = Column(String, index=True)
    trading_id                  = Column(String)
    pending_trading_id          = Column(String)
    id_validated                = Column(Integer, default=0, index=True)
    validated_total_cents       = Column(Integer, default=0)
    upgrade_accum_cents         = Column(Integer, default=0)
    validated_deposit_count     = Column(Integer, default=0)
    first_validated_deposit_at  = Column(DateTime, nullable=True)
    level                       = Column(String, default="NONE", index=True)
    updated_at                  = Column(DateTime, default=utcnow_naive, index=True)


class BrokerFlowState(Base):
    """Selecciones cortas de broker persistentes ante redeploys."""
    __tablename__ = "broker_flow_state"
    telegram_id            = Column(String, primary_key=True)
    pending_trading_id     = Column(String)
    pending_deposit_broker = Column(String)
    updated_at             = Column(DateTime, default=utcnow_naive, index=True)


class VIPChannelMap(Base):
    """Mapa aprendido de canal VIP -> chat_id para no depender del invite_link."""
    __tablename__ = "vip_channel_map"
    access_key = Column(String, primary_key=True)
    chat_id    = Column(String, unique=True, index=True)
    title      = Column(String)
    updated_at = Column(DateTime, default=utcnow_naive, index=True)


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

# === NIVELES Y ACCESOS VIP ===
VIP_LEVEL_NONE = "NONE"
VIP_LEVEL_BASIC = "BASIC"
VIP_LEVEL_PREMIUM = "PREMIUM"
VIP_LEVEL_PRESTIGE = "PRESTIGE"
VIP_LEVEL_RANK = {VIP_LEVEL_NONE: 0, VIP_LEVEL_BASIC: 1, VIP_LEVEL_PREMIUM: 2, VIP_LEVEL_PRESTIGE: 3}
VIP_LEVEL_THRESHOLDS_CENTS = {VIP_LEVEL_BASIC: 5000, VIP_LEVEL_PREMIUM: 20000, VIP_LEVEL_PRESTIGE: 50000}

# Los enlaces conservan solicitud de acceso. El mismo bot los aprueba automáticamente
# cuando el Telegram ID tiene nivel suficiente y el bot es administrador del canal.
VIP_ACCESS_CHANNELS = {
    "vip_main": {
        "name_es": "JT TRADERS TEAMS · VIP Principal",
        "name_en": "JT TRADERS TEAMS · Main VIP",
        "url": "https://t.me/+k1--4ts-vnc4ODUx",
        "levels": (VIP_LEVEL_BASIC, VIP_LEVEL_PREMIUM, VIP_LEVEL_PRESTIGE),
        "desc_es": "Comunidad principal dividida por temas, con educación y metodología completa.",
        "desc_en": "Main community organized by topics, with education and the complete methodology.",
    },
    "crypto_basic": {
        "name_es": "Señales CRYPTO IDX · Básico",
        "name_en": "CRYPTO IDX Signals · Basic",
        "url": "https://t.me/+xYyDG-g72tM2OWJh",
        "levels": (VIP_LEVEL_BASIC,),
        "desc_es": "30–50 señales CRYPTO IDX de lunes a viernes. Entrada en el minuto exacto indicado, expiración de 1 minuto y hasta Martingala 2 opcional.",
        "desc_en": "30–50 CRYPTO IDX signals Monday to Friday. Enter at the exact indicated minute, 1-minute expiry, with optional Martingale up to level 2.",
    },
    "module3": {
        "name_es": "Binary Teams · Módulo 3 — Introducción al Análisis Bursátil",
        "name_en": "Binary Teams · Module 3 — Introduction to Market Analysis",
        "url": "https://t.me/+imlcZTiobAs0YTJh",
        "levels": (VIP_LEVEL_BASIC, VIP_LEVEL_PREMIUM, VIP_LEVEL_PRESTIGE),
        "desc_es": "Curso de introducción al análisis bursátil para todos los niveles.",
        "desc_en": "Introduction to market analysis course for every level.",
    },
    "signals_premium": {
        "name_es": "Señales Premium +300",
        "name_en": "Premium Signals +300",
        "url": "https://t.me/+fe5N2iolLGk0ZjBh",
        "levels": (VIP_LEVEL_PREMIUM, VIP_LEVEL_PRESTIGE),
        "desc_es": "+300 señales de lunes a sábado entre CRYPTO IDX, pares de divisas, índices sintéticos y Forex. Entrada en el minuto exacto indicado, expiración de 1 minuto y hasta Martingala 2 opcional.",
        "desc_en": "300+ signals Monday to Saturday across CRYPTO IDX, currency pairs, synthetic indices and Forex. Enter at the exact indicated minute, 1-minute expiry, with optional Martingale up to level 2.",
    },
    "ai_crypto": {
        "name_es": "IA Premium Automática CRYPTO IDX 24/7",
        "name_en": "Premium AI Automatic CRYPTO IDX 24/7",
        "url": "https://t.me/+flQSWX86gc45M2Rh",
        "levels": (VIP_LEVEL_PREMIUM, VIP_LEVEL_PRESTIGE),
        "desc_es": "Señales automáticas CRYPTO IDX 24/7. La entrada se toma al minuto siguiente de recibir la alerta, con expiración de 1 minuto y hasta Martingala 2 opcional.",
        "desc_en": "Automatic CRYPTO IDX signals 24/7. Enter on the minute immediately after the alert, with 1-minute expiry and optional Martingale up to level 2.",
    },
    "module4": {
        "name_es": "Binary Teams · Módulo 4 — Smart Money Concept",
        "name_en": "Binary Teams · Module 4 — Smart Money Concept",
        "url": "https://t.me/+G56hMIAyasFjNTY5",
        "levels": (VIP_LEVEL_PREMIUM, VIP_LEVEL_PRESTIGE),
        "desc_es": "Módulo formativo de Smart Money Concept.",
        "desc_en": "Smart Money Concept training module.",
    },
    "fx_auto": {
        "name_es": "Divisas Automáticas 24/7 Premium",
        "name_en": "Premium Automatic FX 24/7",
        "url": "https://t.me/+sXmHCpet-kQ2OGFh",
        # ID real aprendido en prueba Telegram 18/09/2026. Permite reconocer
        # solicitudes aunque Telegram no entregue invite_link.
        "chat_id": -1002234063282,
        "levels": (VIP_LEVEL_PRESTIGE,),
        "desc_es": "Señales automáticas de divisas 24/7. La entrada se toma al minuto siguiente de recibir la alerta, expiración de 1 minuto y hasta Martingala 2 opcional.",
        "desc_en": "Automatic currency-pair signals 24/7. Enter on the minute immediately after the alert, with 1-minute expiry and optional Martingale up to level 2.",
    },
    "madness": {
        "name_es": "Madness Trading Avanzado · ALGO & LIT",
        "name_en": "Madness Advanced Trading · ALGO & LIT",
        "url": "https://t.me/+wHK6JhJXktoxMzZh",
        "levels": (VIP_LEVEL_PRESTIGE,),
        "desc_es": "Formación avanzada con metodología ALGO & LIT.",
        "desc_en": "Advanced training with the ALGO & LIT methodology.",
    },
}

VIP_LEVEL_CHANNEL_KEYS = {
    VIP_LEVEL_NONE: [],
    VIP_LEVEL_BASIC: ["vip_main", "crypto_basic", "module3"],
    VIP_LEVEL_PREMIUM: ["vip_main", "module3", "signals_premium", "ai_crypto", "module4"],
    VIP_LEVEL_PRESTIGE: ["vip_main", "module3", "signals_premium", "ai_crypto", "module4", "fx_auto", "madness"],
}


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


def _usd(cents: int) -> str:
    cents = int(cents or 0)
    value = cents / 100
    return f"{value:.2f}" if cents % 100 else str(int(value))


def _parse_usd_to_cents(value: str) -> int | None:
    raw = (value or "").strip().replace("USD", "").replace("usd", "").replace("$", "").replace(" ", "")
    # En Colombia es común usar coma decimal. Si aparecen coma y punto, el último separador se toma como decimal.
    if not raw:
        return None
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", raw):
        return None
    try:
        cents = int(round(float(raw) * 100))
    except Exception:
        return None
    if cents <= 0 or cents > 100000000:  # hasta USD 1.000.000 como barrera anti-error
        return None
    return cents


def _vip_level_for_total_cents(total_cents: int) -> str:
    total_cents = max(0, int(total_cents or 0))
    if total_cents >= VIP_LEVEL_THRESHOLDS_CENTS[VIP_LEVEL_PRESTIGE]:
        return VIP_LEVEL_PRESTIGE
    if total_cents >= VIP_LEVEL_THRESHOLDS_CENTS[VIP_LEVEL_PREMIUM]:
        return VIP_LEVEL_PREMIUM
    if total_cents >= VIP_LEVEL_THRESHOLDS_CENTS[VIP_LEVEL_BASIC]:
        return VIP_LEVEL_BASIC
    return VIP_LEVEL_NONE


def _vip_level_label(level: str, lang: str = "es") -> str:
    labels_es = {VIP_LEVEL_NONE: "Sin nivel", VIP_LEVEL_BASIC: "Básico", VIP_LEVEL_PREMIUM: "Premium", VIP_LEVEL_PRESTIGE: "Prestige"}
    labels_en = {VIP_LEVEL_NONE: "No level", VIP_LEVEL_BASIC: "Basic", VIP_LEVEL_PREMIUM: "Premium", VIP_LEVEL_PRESTIGE: "Prestige"}
    return (labels_es if lang == "es" else labels_en).get(level, level or VIP_LEVEL_NONE)


def _vip_next_level(level: str, total_cents: int):
    if level == VIP_LEVEL_NONE:
        target = VIP_LEVEL_BASIC
    elif level == VIP_LEVEL_BASIC:
        target = VIP_LEVEL_PREMIUM
    elif level == VIP_LEVEL_PREMIUM:
        target = VIP_LEVEL_PRESTIGE
    else:
        return None, 0
    needed = max(0, VIP_LEVEL_THRESHOLDS_CENTS[target] - int(total_cents or 0))
    return target, needed


BROKER_BINOMO = "BINOMO"
BROKER_STOCKITY = "STOCKITY"
BROKERS = (BROKER_BINOMO, BROKER_STOCKITY)
UPGRADE_ACCUM_MAX_DEPOSITS = 3
UPGRADE_ACCUM_WINDOW_DAYS = 30
UPGRADE_PROOF_MAX_HOURS = 72


def _broker_norm(value: str) -> str:
    raw = (value or "").strip().upper()
    return raw if raw in BROKERS else ""


def _broker_label(broker: str) -> str:
    return "Stockity" if _broker_norm(broker) == BROKER_STOCKITY else "Binomo"


def _broker_key(chat_id: int, broker: str) -> str:
    return f"{int(chat_id)}:{_broker_norm(broker)}"


def _broker_get(chat_id: int, broker: str, create: bool = False):
    broker = _broker_norm(broker)
    if not broker:
        return None
    try:
        with Session() as session:
            row = session.get(BrokerAccountState, _broker_key(chat_id, broker))
            if not row and create:
                row = BrokerAccountState(
                    account_key=_broker_key(chat_id, broker), telegram_id=str(chat_id), broker=broker,
                    id_validated=0, validated_total_cents=0, upgrade_accum_cents=0,
                    validated_deposit_count=0, level=VIP_LEVEL_NONE, updated_at=utcnow_naive(),
                )
                session.add(row); session.commit(); session.refresh(row)
            if not row:
                return None
            return {
                "broker": broker,
                "trading_id": (row.trading_id or "").strip(),
                "pending_trading_id": (row.pending_trading_id or "").strip(),
                "id_validated": bool(row.id_validated),
                "validated_total_cents": int(row.validated_total_cents or 0),
                "upgrade_accum_cents": int(row.upgrade_accum_cents or 0),
                "deposit_count": int(row.validated_deposit_count or 0),
                "first_deposit_at": row.first_validated_deposit_at,
                "level": row.level if row.level in VIP_LEVEL_RANK else VIP_LEVEL_NONE,
                "updated_at": row.updated_at,
            }
    except Exception as e:
        logging.warning("No pude leer cuenta %s/%s: %s", chat_id, broker, e)
        return None


def _broker_rows(chat_id: int, validated_only: bool = False):
    rows = []
    for broker in BROKERS:
        state = _broker_get(chat_id, broker, create=False)
        if state and (not validated_only or state.get("id_validated")):
            rows.append(state)
    return rows


def _broker_validated_brokers(chat_id: int):
    return [state["broker"] for state in _broker_rows(chat_id, validated_only=True)]


def _broker_flow_get(chat_id: int):
    try:
        with Session() as session:
            row = session.get(BrokerFlowState, str(chat_id))
            return {
                "pending_trading_id": (row.pending_trading_id or "").strip() if row else "",
                "pending_deposit_broker": _broker_norm(row.pending_deposit_broker) if row else "",
            }
    except Exception as e:
        logging.warning("No pude leer broker_flow de %s: %s", chat_id, e)
        return {"pending_trading_id": "", "pending_deposit_broker": ""}


def _broker_flow_set(chat_id: int, *, pending_trading_id=None, pending_deposit_broker=None):
    try:
        with Session() as session:
            row = session.get(BrokerFlowState, str(chat_id))
            if not row:
                row = BrokerFlowState(telegram_id=str(chat_id))
                session.add(row)
            if pending_trading_id is not None:
                row.pending_trading_id = str(pending_trading_id or "")
            if pending_deposit_broker is not None:
                row.pending_deposit_broker = _broker_norm(pending_deposit_broker)
            row.updated_at = utcnow_naive(); session.commit()
        return True
    except Exception as e:
        logging.warning("No pude guardar broker_flow de %s: %s", chat_id, e)
        return False


def _broker_selection_keyboard(kind: str, lang: str = "es") -> InlineKeyboardMarkup:
    prefix = "broker_id_select" if kind == "id" else "broker_deposit_select"
    rows = [[
        # Colores visuales según marca: Binomo amarillo, Stockity azul.
        InlineKeyboardButton("🟡 BINOMO", callback_data=f"{prefix}:BINOMO"),
        InlineKeyboardButton("🔵 STOCKITY", callback_data=f"{prefix}:STOCKITY"),
    ]]
    # Paso operativo: no mostramos salidas hasta que el usuario identifique el broker.
    return InlineKeyboardMarkup(rows)


def _broker_upgrade_window_open(state, now=None) -> bool:
    if not state:
        return True
    now = now or utcnow_naive()
    count = int(state.get("deposit_count") or 0)
    first = state.get("first_deposit_at")
    if count >= UPGRADE_ACCUM_MAX_DEPOSITS:
        return False
    if first and now > first + timedelta(days=UPGRADE_ACCUM_WINDOW_DAYS):
        return False
    return True


def _max_level(a: str, b: str) -> str:
    return a if VIP_LEVEL_RANK.get(a, 0) >= VIP_LEVEL_RANK.get(b, 0) else b


def _broker_seed_legacy_if_matching(chat_id: int, broker: str, trading_id: str):
    """Si el ID coincide con el único ID legado, migra su nivel sin perderlo."""
    state = _broker_get(chat_id, broker, create=True)
    if not state or state.get("trading_id") or state.get("pending_trading_id"):
        return
    old_id = _get_saved_trading_id(chat_id)
    legacy = _vip_get_state(chat_id, create=False)
    if not (
        legacy and old_id and old_id == trading_id
        and (int(legacy.get("total_cents") or 0) > 0 or legacy.get("level") != VIP_LEVEL_NONE)
    ):
        return
    try:
        with Session() as session:
            row = session.get(BrokerAccountState, _broker_key(chat_id, broker))
            viprow = session.get(VIPAccessState, str(chat_id))
            row.trading_id = trading_id
            row.id_validated = 1
            row.validated_total_cents = int(legacy.get("total_cents") or 0)
            row.upgrade_accum_cents = int(legacy.get("total_cents") or 0)
            row.validated_deposit_count = 1
            row.first_validated_deposit_at = (viprow.updated_at if viprow else utcnow_naive())
            row.level = legacy.get("level") or VIP_LEVEL_NONE
            row.updated_at = utcnow_naive(); session.commit()
        logging.info("♻️ Cuenta legacy migrada a %s para %s", broker, chat_id)
    except Exception as e:
        logging.warning("No pude migrar cuenta legacy %s/%s: %s", chat_id, broker, e)


def _broker_bind_legacy_validated_id(chat_id: int, broker: str) -> bool:
    """Asocia un ID ya validado de versiones previas al broker elegido por el usuario."""
    broker = _broker_norm(broker)
    saved_id = _get_saved_trading_id(chat_id)
    if not broker or not re.fullmatch(r"\d{6,12}", saved_id or ""):
        return False
    # Si existe estado VIP previo, migra también su monto/nivel de referencia.
    _broker_seed_legacy_if_matching(chat_id, broker, saved_id)
    state = _broker_get(chat_id, broker, create=False)
    if state and state.get("id_validated"):
        return True
    if not _strict_validated_id_state(chat_id) and get_user_stage(chat_id) != STAGE_DEPOSITED:
        return False
    try:
        with Session() as session:
            row = session.get(BrokerAccountState, _broker_key(chat_id, broker))
            if not row:
                row = BrokerAccountState(account_key=_broker_key(chat_id, broker), telegram_id=str(chat_id), broker=broker)
                session.add(row)
            row.trading_id = saved_id
            row.pending_trading_id = None
            row.id_validated = 1
            row.updated_at = utcnow_naive()
            session.commit()
        logging.info("♻️ ID validado legacy asociado a %s para %s", broker, chat_id)
        return True
    except Exception as e:
        logging.warning("No pude asociar ID legacy %s/%s: %s", chat_id, broker, e)
        return False


def upgrade_conditions_text(lang: str = "es") -> str:
    if lang == "en":
        return (
            "📈 UPGRADE CONDITIONS\n\n"
            "• The first 3 validated deposits on the SAME broker/account may accumulate toward a level upgrade.\n"
            "• The accumulation window lasts 30 days from the first validated deposit.\n"
            "• To be included in that accumulation, proof must be sent within 72 hours of the deposit.\n"
            "• After the 3rd validated deposit or once the 30-day window ends, an upgrade requires ONE new deposit that by itself reaches the full minimum of the new level.\n"
            "• Binomo and Stockity are managed separately and their deposits are never added together.\n"
            "• Only deposits reported and validated through this chat are considered."
        )
    return (
        "📈 CONDICIONES PARA SUBIR DE NIVEL\n\n"
        "• Los primeros 3 depósitos validados de una MISMA cuenta/broker pueden acumularse para subir de nivel.\n"
        "• La ventana de acumulación dura 30 días desde el primer depósito validado.\n"
        "• Para entrar en esa acumulación, el comprobante debe enviarse dentro de las 72 horas posteriores al depósito.\n"
        "• Después del 3.er depósito validado o una vez vencidos los 30 días, el upgrade requiere UN nuevo depósito que por sí solo alcance el monto mínimo completo del nuevo nivel.\n"
        "• Binomo y Stockity se gestionan por separado y sus depósitos nunca se suman entre sí.\n"
        "• Solo se contabilizan depósitos reportados y validados por este chat."
    )


def upgrade_info_keyboard(lang: str = "es") -> InlineKeyboardMarkup:
    """En confirmaciones de depósito muestra solo la información útil del upgrade."""
    label = "ℹ️ VIEW UPGRADE CONDITIONS" if lang == "en" else "ℹ️ VER CONDICIONES DE UPGRADE"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="upgrade_conditions")],
    ])


def _broker_preview_deposit(chat_id: int, broker: str, amount_cents: int, timely: bool):
    broker = _broker_norm(broker)
    state = _broker_get(chat_id, broker, create=True) or {}
    now = utcnow_naive()
    old_level = state.get("level") or VIP_LEVEL_NONE
    old_count = int(state.get("deposit_count") or 0)
    old_accum = int(state.get("upgrade_accum_cents") or 0)
    first = state.get("first_deposit_at")
    window_open = _broker_upgrade_window_open(state, now)
    accumulates = bool(timely and window_open)
    new_accum = old_accum + int(amount_cents) if accumulates else old_accum
    candidate = _vip_level_for_total_cents(new_accum if accumulates else amount_cents)
    new_level = _max_level(old_level, candidate)
    new_count = old_count + 1
    first_after = first or now
    window_closed_after = (
        new_count >= UPGRADE_ACCUM_MAX_DEPOSITS
        or now > first_after + timedelta(days=UPGRADE_ACCUM_WINDOW_DAYS)
    )
    return {
        "broker": broker,
        "amount_cents": int(amount_cents),
        "timely": bool(timely),
        "old_level": old_level,
        "new_level": new_level,
        "old_count": old_count,
        "new_count": new_count,
        "old_accum_cents": old_accum,
        "new_accum_cents": new_accum,
        "accumulates": accumulates,
        "first_after": first_after,
        "window_closed_after": window_closed_after,
        "new_validated_total_cents": int(state.get("validated_total_cents") or 0) + int(amount_cents),
    }


def _broker_account_summary(chat_id: int) -> str:
    parts = []
    for state in _broker_rows(chat_id):
        broker = _broker_label(state["broker"]).upper()
        tid = state.get("trading_id") or state.get("pending_trading_id") or "—"
        valid = "✅" if state.get("id_validated") else "⏳"
        parts.append(
            f"{broker}: {valid} ID {tid} · Nivel {_vip_level_label(state.get('level'), 'es')} · "
            f"Depósitos validados {state.get('deposit_count', 0)}"
        )
    return "\n".join(parts)


def _vip_get_state(chat_id: int, create: bool = False):
    try:
        with Session() as session:
            row = session.get(VIPAccessState, str(chat_id))
            if not row and create:
                row = VIPAccessState(
                    telegram_id=str(chat_id),
                    validated_total_cents=0,
                    level=VIP_LEVEL_NONE,
                    pending_access_keys="",
                    welcome_level=None,
                    updated_at=utcnow_naive(),
                )
                session.add(row)
                session.commit()
            if not row:
                return None
            return {
                "telegram_id": str(chat_id),
                "total_cents": int(row.validated_total_cents or 0),
                "level": row.level if row.level in VIP_LEVEL_RANK else _vip_level_for_total_cents(row.validated_total_cents or 0),
                "pending_keys": [x for x in (row.pending_access_keys or "").split(",") if x],
                "welcome_level": row.welcome_level or "",
            }
    except Exception as e:
        logging.warning("No pude leer estado VIP de %s: %s", chat_id, e)
        return None


def _vip_set_state(chat_id: int, total_cents: int, level: str, pending_keys=None, welcome_level=None):
    try:
        with Session() as session:
            row = session.get(VIPAccessState, str(chat_id))
            if not row:
                row = VIPAccessState(telegram_id=str(chat_id))
                session.add(row)
            row.validated_total_cents = max(0, int(total_cents or 0))
            row.level = level if level in VIP_LEVEL_RANK else _vip_level_for_total_cents(total_cents)
            if pending_keys is not None:
                row.pending_access_keys = ",".join(dict.fromkeys(k for k in pending_keys if k in VIP_ACCESS_CHANNELS))
            if welcome_level is not None:
                row.welcome_level = welcome_level
            row.updated_at = utcnow_naive()
            session.commit()
        return True
    except Exception as e:
        logging.warning("No pude actualizar estado VIP de %s: %s", chat_id, e)
        return False


def _vip_channel_keys_for_level(level: str):
    return list(VIP_LEVEL_CHANNEL_KEYS.get(level, []))


def _vip_new_channel_keys(old_level: str, new_level: str):
    old = set(_vip_channel_keys_for_level(old_level))
    return [k for k in _vip_channel_keys_for_level(new_level) if k not in old]


def _normalize_invite_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def _invite_token(value: str) -> str:
    raw = _normalize_invite_url(value)
    if "/+" in raw:
        return raw.rsplit("/+", 1)[-1]
    if "/joinchat/" in raw:
        return raw.rsplit("/joinchat/", 1)[-1]
    return ""


VIP_ACCESS_LINK_LOOKUP = {
    _normalize_invite_url(info["url"]): key for key, info in VIP_ACCESS_CHANNELS.items()
}
VIP_ACCESS_TOKEN_LOOKUP = {
    _invite_token(info["url"]): key for key, info in VIP_ACCESS_CHANNELS.items() if _invite_token(info["url"])
}
VIP_ACCESS_CHAT_ID_LOOKUP = {
    str(info.get("chat_id")): key
    for key, info in VIP_ACCESS_CHANNELS.items()
    if info.get("chat_id")
}


VIP_ACCESS_TITLE_ALIASES = {
    "vip_main": ("JT TRADERS TEAMS", "JT TRADERS TEAMS VIP", "VIP PRINCIPAL"),
    "crypto_basic": ("CANAL DE SEÑALES CRYPTOIDX", "SEÑALES CRYPTO IDX", "CRYPTO IDX BASICO", "CRYPTO IDX BÁSICO"),
    "module3": ("BINARY TEAMS MODULO 3", "BINARY TEAMS MÓDULO 3", "INTRODUCCION AL ANALISIS BURSATIL", "INTRODUCCIÓN AL ANÁLISIS BURSÁTIL"),
    "signals_premium": ("SEÑALES PREMIUM +300", "SENALES PREMIUM +300", "PREMIUM +300"),
    "ai_crypto": ("IA PREMIUM AUTOMATICAS CRYPTOIDX 24/7", "IA PREMIUM AUTOMÁTICAS CRYPTOIDX 24/7", "IA PREMIUM AUTOMATICA CRYPTO IDX 24/7"),
    "module4": ("BINARY TEAMS MODULO 4", "BINARY TEAMS MÓDULO 4", "SMART MONEY CONCEPT"),
    "fx_auto": ("DIVISAS AUTOMATICAS 24/7 PREMIUM", "DIVISAS AUTOMÁTICAS 24/7 PREMIUM", "DIVISAS AUTO PREMIUM"),
    "madness": ("MADNESS TRADING AVANZADO", "METODO ALGO Y LIT", "MÉTODO ALGO Y LIT"),
}


def _norm_title(value: str) -> str:
    raw = unicodedata.normalize("NFKD", (value or "").upper())
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]+", " ", raw).strip()


def _vip_learn_channel(access_key: str, chat) -> None:
    if not access_key or not chat:
        return
    try:
        with Session() as session:
            row = session.get(VIPChannelMap, access_key)
            if not row:
                row = VIPChannelMap(access_key=access_key)
                session.add(row)
            row.chat_id = str(chat.id)
            row.title = (getattr(chat, "title", None) or getattr(chat, "full_name", None) or "")[:250]
            row.updated_at = utcnow_naive(); session.commit()
    except Exception as e:
        logging.warning("No pude aprender chat_id VIP %s: %s", access_key, e)


def _vip_access_key_from_request(req) -> str:
    invite_obj = getattr(req, "invite_link", None)
    invite_link = _normalize_invite_url(getattr(invite_obj, "invite_link", None) or "")
    if invite_link in VIP_ACCESS_LINK_LOOKUP:
        key = VIP_ACCESS_LINK_LOOKUP[invite_link]
        _vip_learn_channel(key, getattr(req, "chat", None))
        return key
    token = _invite_token(invite_link)
    if token in VIP_ACCESS_TOKEN_LOOKUP:
        key = VIP_ACCESS_TOKEN_LOOKUP[token]
        _vip_learn_channel(key, getattr(req, "chat", None))
        return key

    chat = getattr(req, "chat", None)
    chat_id = str(getattr(chat, "id", "") or "")
    if chat_id and chat_id in VIP_ACCESS_CHAT_ID_LOOKUP:
        key = VIP_ACCESS_CHAT_ID_LOOKUP[chat_id]
        _vip_learn_channel(key, chat)
        return key
    if chat_id:
        try:
            with Session() as session:
                row = session.query(VIPChannelMap).filter(VIPChannelMap.chat_id == chat_id).first()
                if row and row.access_key in VIP_ACCESS_CHANNELS:
                    return row.access_key
        except Exception as e:
            logging.warning("No pude consultar mapa VIP por chat_id %s: %s", chat_id, e)

    title = _norm_title(getattr(chat, "title", None) or "")
    if title:
        for key, aliases in VIP_ACCESS_TITLE_ALIASES.items():
            norm_aliases = [_norm_title(x) for x in aliases]
            if any(a and (title == a or a in title or title in a) for a in norm_aliases):
                _vip_learn_channel(key, chat)
                return key
    return ""


def _vip_channel_allowed(level: str, access_key: str) -> bool:
    info = VIP_ACCESS_CHANNELS.get(access_key) or {}
    return bool(level in info.get("levels", ()))


def _vip_access_keyboard(level: str, lang: str, keys=None) -> InlineKeyboardMarkup:
    keys = list(keys if keys is not None else _vip_channel_keys_for_level(level))
    rows = []
    for key in keys:
        info = VIP_ACCESS_CHANNELS.get(key)
        if not info:
            continue
        label = info["name_es"] if lang == "es" else info["name_en"]
        rows.append([InlineKeyboardButton(f"🔐 {label}", url=info["url"])])
    # Mientras solicita accesos, solo se muestran los canales pendientes.
    # Soporte y menú vuelven en la bienvenida final al completar el proceso.
    return InlineKeyboardMarkup(rows)


def _vip_access_intro(level: str, lang: str, upgrade: bool = False) -> str:
    level_label = _vip_level_label(level, lang)
    if lang == "en":
        if upgrade:
            return (
                f"🔐 🎉 Congratulations! Your {level_label} level is now active.\n\n"
                "Use the buttons below to request access to the NEW channels unlocked by your level. "
                "The bot will approve your requests automatically when they come from this same Telegram account."
            )
        return (
            f"🔐 🎉 Congratulations! Your {level_label} level access is ready.\n\n"
            "Use the buttons below to request access to each channel included in your level. "
            "The bot will approve your requests automatically when they come from this same Telegram account."
        )
    if upgrade:
        return (
            f"🔐 🎉 ¡Felicidades! Tu nivel {level_label} ya está activo.\n\n"
            "Usa los botones de abajo para solicitar acceso a los NUEVOS canales desbloqueados por tu nivel. "
            "El bot aprobará automáticamente las solicitudes hechas desde esta misma cuenta de Telegram."
        )
    return (
        f"🔐 🎉 ¡Felicidades! Ya están listos tus accesos del nivel {level_label}.\n\n"
        "Usa los botones de abajo para solicitar acceso a cada canal incluido en tu nivel. "
        "El bot aprobará automáticamente las solicitudes hechas desde esta misma cuenta de Telegram."
    )


def _vip_level_summary(level: str, lang: str) -> str:
    lines = []
    for key in _vip_channel_keys_for_level(level):
        info = VIP_ACCESS_CHANNELS[key]
        name = info["name_es"] if lang == "es" else info["name_en"]
        desc = info["desc_es"] if lang == "es" else info["desc_en"]
        lines.append(f"• {name}\n  {desc}")
    if level == VIP_LEVEL_PRESTIGE:
        if lang == "es":
            lines.append("• Beneficios Prestige adicionales\n  Mentorías privadas, acompañamiento cercano y preparación para cuentas de fondeo. Forex automático: en construcción.")
        else:
            lines.append("• Additional Prestige benefits\n  Private mentoring, closer guidance and funded-account preparation. Automatic Forex: under development.")
    return "\n\n".join(lines)


def _vip_final_welcome_text(level: str, lang: str) -> str:
    label = _vip_level_label(level, lang)
    summary = _vip_level_summary(level, lang)
    if lang == "en":
        return (
            f"🎉 Welcome to JT TRADERS TEAMS — {label} level!\n\n"
            "Your requested accesses have been enabled. Here is a quick guide to what you now have and how to use it:\n\n"
            f"{summary}\n\n"
            "📌 Check the pinned instructions inside each channel before using the signals. Martingale is optional and increases risk."
        )
    return (
        f"🎉 ¡Bienvenida/o a JT TRADERS TEAMS — nivel {label}!\n\n"
        "Tus accesos solicitados ya fueron habilitados. Aquí tienes una guía rápida de lo que incluye tu nivel y cómo utilizarlo:\n\n"
        f"{summary}\n\n"
        "📌 Revisa las indicaciones fijadas dentro de cada canal antes de utilizar las señales. La Martingala es opcional y aumenta el riesgo."
    )


def _vip_mark_access_approved(chat_id: int, access_key: str):
    """Quita un acceso pendiente y devuelve (nivel, enviar_bienvenida)."""
    try:
        with Session() as session:
            row = session.get(VIPAccessState, str(chat_id))
            if not row:
                return VIP_LEVEL_NONE, False
            level = row.level if row.level in VIP_LEVEL_RANK else _vip_level_for_total_cents(row.validated_total_cents or 0)
            pending = [x for x in (row.pending_access_keys or "").split(",") if x]
            if access_key in pending:
                pending = [x for x in pending if x != access_key]
                row.pending_access_keys = ",".join(pending)
                row.updated_at = utcnow_naive()
            should_welcome = bool(not pending and level != VIP_LEVEL_NONE and (row.welcome_level or "") != level)
            if should_welcome:
                row.welcome_level = level
            session.commit()
            return level, should_welcome
    except Exception as e:
        logging.warning("No pude marcar acceso VIP aprobado para %s/%s: %s", chat_id, access_key, e)
        return VIP_LEVEL_NONE, False


def _vip_activation_message(level: str, total_cents: int, lang: str, upgraded: bool = False) -> str:
    label = _vip_level_label(level, lang)
    if lang == "en":
        prefix = "✅ Additional deposit confirmed." if upgraded else "✅ Deposit confirmed."
        return (
            f"{prefix}\n\nYour current level is {label}.\n"
            "Upgrades are calculated from validated deposits within the enabled level-update period."
        )
    prefix = "✅ Depósito adicional confirmado." if upgraded else "✅ Depósito confirmado."
    return (
        f"{prefix}\n\nTu nivel actual es {label}.\n"
        "Los upgrades se calculan según depósitos validados dentro del periodo habilitado para actualización de nivel."
    )


def _vip_insufficient_message(total_cents: int, lang: str) -> str:
    missing = max(0, VIP_LEVEL_THRESHOLDS_CENTS[VIP_LEVEL_BASIC] - int(total_cents or 0))
    if lang == "en":
        return (
            f"✅ I confirmed a validated total of USD {_usd(total_cents)}.\n\n"
            f"To activate the Basic level you need USD 50. You still need USD {_usd(missing)}. "
            "Access cannot be enabled until the minimum is completed. When you add the remaining amount, send me the new proof here."
        )
    return (
        f"✅ He confirmado un total validado de USD {_usd(total_cents)}.\n\n"
        f"Para activar el nivel Básico necesitas USD 50. Te faltan USD {_usd(missing)}. "
        "El acceso no puede habilitarse hasta completar el mínimo. Cuando agregues el valor restante, envíame aquí el nuevo comprobante."
    )


def _id_pending_review_message(lang: str) -> str:
    if lang == "en":
        return (
            "✅ I received the ID number. Before I leave it for validation, tell me which trading account it belongs to.\n\n"
            "Choose the broker below:"
        )
    return (
        "✅ Recibí el número de ID. Antes de dejarlo en validación necesito saber a qué cuenta de trading corresponde.\n\n"
        "Elige el broker aquí abajo:"
    )


def _has_pending_id_review(chat_id: int) -> bool:
    try:
        flow = _broker_flow_get(chat_id)
        if flow.get("pending_trading_id"):
            return True
        for state in _broker_rows(chat_id):
            if state.get("pending_trading_id"):
                return True
        with Session() as session:
            row = (
                session.query(BotEvent.event_type)
                .filter(
                    BotEvent.telegram_id == str(chat_id),
                    BotEvent.event_type.in_(["ID_SUBMITTED", "ID_VALIDATED", "ID_REJECTED"]),
                )
                .order_by(BotEvent.created_at.desc(), BotEvent.id.desc())
                .first()
            )
        return bool(row and row[0] == "ID_SUBMITTED")
    except Exception as e:
        logging.warning("No pude comprobar ID pendiente para %s: %s", chat_id, e)
        return False


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
GATILLO_ID_ERRADO = f"""❌ Tu ID no quedó vinculado correctamente.

Para tener acceso a mi comunidad VIP y a todas las herramientas, debes realizar el registro desde uno de mis enlaces oficiales.

Abre una ventana de incógnito en tu navegador, copia y pega el enlace de registro y utiliza un correo diferente que no hayas usado antes en esa plataforma.

Cuando termines, envíame aquí el nuevo ID para validarlo ANTES de realizar cualquier depósito.

BINOMO 👇
{ENLACE_REFERIDO}

STOCKITY 👇
{ENLACE_REFERIDO_STOCKITY}"""

GATILLO_ID_ERRADO_EN = f"""❌ Your ID was not linked correctly.

To access my VIP community and all the tools, you need to register through one of my official links.

Open an incognito window in your browser, copy and paste the registration link, and use a different email address that you have not used before on that platform.

When you finish, send me the new ID here so I can validate it BEFORE you make any deposit.

BINOMO 👇
{ENLACE_REFERIDO}

STOCKITY 👇
{ENLACE_REFERIDO_STOCKITY}"""

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
WELCOME_IMG = "bienvenidanuevasi.jpg"  # Reemplazar este archivo por la nueva imagen de bienvenida

MENSAJE_BIENVENIDA_ES = """👋 ¡Hola! Soy JOHAALETRADER.
Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable.
¿Lista o listo para registrarte y empezar a ganar?"""

MENSAJE_BIENVENIDA_EN = """👋 Hi! I’m JOHAALETRADER.
I’m here to help you start in binary options trading safely, with guidance and real profitability.
Ready to register and start earning?"""


def _telegram_display_name(user) -> str:
    """Nombre visible estable: @username si existe; de lo contrario nombre de Telegram."""
    if user is None:
        return "✨"
    username = (getattr(user, "username", None) or "").strip()
    if username:
        return f"@{username}"
    full_name = (getattr(user, "full_name", None) or "").strip()
    first_name = (getattr(user, "first_name", None) or "").strip()
    return full_name or first_name or "✨"


def _personalized_welcome(user, lang: str = "es") -> str:
    """Conserva el contenido de bienvenida y personaliza únicamente el saludo."""
    name = _telegram_display_name(user)
    if lang == "en":
        return (
            f"👋 Hi, {name}! I’m JOHAALETRADER.\n"
            "I’m here to help you start in binary options trading safely, with guidance and real profitability.\n"
            "Ready to register and start earning?"
        )
    return (
        f"👋 ¡Hola, {name}! Soy JOHAALETRADER.\n"
        "Estoy aquí para ayudarte a empezar en el mundo del trading de opciones binarias de forma segura, guiada y rentable.\n"
        "¿Lista o listo para registrarte y empezar a ganar?"
    )


ADMIN_ID_VALIDATED_ES = (
    "✅ Tu ID ha sido validado con éxito.\n\n"
    "Ya puedes realizar el depósito directamente en tu cuenta de trading.\n"
    "Cuando lo hagas, envíame aquí en este chat una captura del depósito para confirmar tu activación. 📸"
)
ADMIN_ID_VALIDATED_EN = (
    "✅ Your ID has been successfully validated.\n\n"
    "You can now make the deposit directly into your trading account.\n"
    "Once it is done, send me a screenshot of the deposit here in this chat so I can confirm your activation. 📸"
)
ADMIN_ACCOUNT_ACTIVE_ES = (
    "✅ Depósito confirmado.\n\n"
    "🎉 Tu cuenta está activa y tu acceso GRATUITO a mi comunidad VIP ha sido habilitado correctamente.\n"
    "¡Te doy la bienvenida a JT TRADERS TEAMS! 🚀"
)
ADMIN_ACCOUNT_ACTIVE_EN = (
    "✅ Deposit confirmed.\n\n"
    "🎉 Your account is active and your FREE access to my VIP community has been enabled successfully.\n"
    "Welcome to JT TRADERS TEAMS! 🚀"
)

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
BENEFICIOS_ES = """✨ Beneficios JT TRADERS TEAMS ✨

✅ Todos los niveles: acceso al VIP principal con educación/metodología completa + Binary Teams Módulo 3 (Introducción al Análisis Bursátil).
✅ Básico — desde 50 USD: 30–50 señales CRYPTO IDX por día, de lunes a viernes.
✅ Premium — desde 200 USD: +300 señales Premium de lunes a sábado, IA automática CRYPTO IDX 24/7 y Módulo 4 Smart Money Concept.
✅ Prestige — desde 500 USD: todo Premium + Divisas Automáticas 24/7, Madness Trading Avanzado ALGO & LIT, mentorías privadas, acompañamiento cercano y preparación para cuentas de fondeo. Forex automático: en construcción.

⚡️ La comunidad es GRATUITA: el dinero se deposita directamente en TU propia cuenta de trading. Las herramientas habilitadas dependen del nivel alcanzado.
⚠️ Las señales se operan con gestión de riesgo; Martingala 1/2 es opcional y aumenta la exposición.
"""

BENEFICIOS_EN = """✨ JT TRADERS TEAMS Benefits ✨

✅ Every level: access to the main VIP with complete education/methodology + Binary Teams Module 3 (Introduction to Market Analysis).
✅ Basic — from USD 50: 30–50 CRYPTO IDX signals per day, Monday to Friday.
✅ Premium — from USD 200: 300+ Premium signals Monday to Saturday, automatic CRYPTO IDX AI 24/7 and Module 4 Smart Money Concept.
✅ Prestige — from USD 500: everything in Premium + Automatic FX 24/7, Madness Advanced Trading ALGO & LIT, private mentoring, closer guidance and funded-account preparation. Automatic Forex: under development.

⚡️ The community is FREE: funds are deposited directly into YOUR own trading account. Enabled tools depend on the level reached.
⚠️ Signals should be used with risk management; Martingale 1/2 is optional and increases exposure.
"""

# === FUNCIONES DE MENSAJES PROGRAMADOS (usa lang por usuario) ===
async def _send_job_message(context: ContextTypes.DEFAULT_TYPE, text_es: str, text_en: str):
    chat_id, lang = context.job.data  # (chat_id, "es"/"en")
    if not _is_private_user_id(chat_id):
        return
    try:
        outbound = _personalize_referral_links(text_es if lang == "es" else text_en, chat_id)
        await context.bot.send_message(chat_id=chat_id, text=outbound, reply_markup=remarketing_keyboard(lang))
    except Exception as e:
        if _is_blocked_user_error(e):
            _cleanup_blocked_user_tasks(context, chat_id, source="legacy_scheduled_message")
            return
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
    """Recibe un ID válido y espera selección BINOMO/STOCKITY antes de guardarlo."""
    trading_id = _extract_candidate_trading_id(text_value)
    if not trading_id:
        return ""
    _broker_flow_set(chat_id, pending_trading_id=trading_id)
    _log_event(chat_id, "ID_SUBMITTED_PENDING_BROKER", trading_id)
    _tracking_fire_event(chat_id, "ID_SUBMITTED_PENDING_BROKER", trading_id)
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


async def _tracking_post(path: str, payload: dict, source: str = ""):
    """Envía datos al servicio JOHAALE-TRACKING sin poder interrumpir el bot."""
    if not (HAS_HTTPX and TRACKING_BASE_URL and TRACKING_SECRET):
        return None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.post(
                f"{TRACKING_BASE_URL}{path}",
                json=payload,
                headers={"X-Tracking-Secret": TRACKING_SECRET},
            )
        if response.status_code >= 400:
            logging.warning(
                "Tracking no aceptó %s (%s) origen=%s: HTTP %s",
                path, payload.get("telegram_id"), source or "n/a", response.status_code,
            )
            return None
        try:
            return response.json()
        except Exception:
            return {"ok": True}
    except Exception as e:
        logging.warning(
            "Tracking no disponible para %s (%s) origen=%s: %s",
            path, payload.get("telegram_id"), source or "n/a", e,
        )
        return None


async def _tracking_affiliate_summary(start_utc: datetime, end_utc: datetime):
    """Consulta al tracking el resumen real de Affiliate Top para el rango UTC indicado.

    Si tracking no está disponible, devuelve None y el reporte lo muestra como no disponible
    sin afectar ninguna otra función del bot.
    """
    if not (HAS_HTTPX and TRACKING_BASE_URL and TRACKING_SECRET):
        return None
    return await _tracking_post(
        "/internal/affiliate-summary",
        {
            "start_utc": start_utc.isoformat(),
            "end_utc": end_utc.isoformat(),
        },
        source="affiliate_summary",
    )


def _tracking_fire_event(chat_id: int, event_type: str, detail: str = ""):
    """Dispara un evento en segundo plano; si tracking falla, el bot sigue normal."""
    if not _is_private_user_id(chat_id):
        return
    if not (HAS_HTTPX and TRACKING_BASE_URL and TRACKING_SECRET):
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_tracking_post(
            "/internal/event",
            {
                "telegram_id": int(chat_id),
                "event": (event_type or "UNKNOWN")[:80],
                "detail": (detail or "")[:1500] or None,
            },
            source=event_type or "UNKNOWN",
        ))
    except RuntimeError:
        # Puede ocurrir únicamente si se llama fuera de un loop async. No afecta al bot.
        logging.info("Tracking omitido fuera del loop async para %s", chat_id)
    except Exception as e:
        logging.warning("No pude programar evento tracking %s para %s: %s", event_type, chat_id, e)


def _is_tracking_info_channel(chat) -> bool:
    """True solo para el canal informativo ES usado por la pauta."""
    if chat is None:
        return False
    target = str(INFO_CHANNEL_ID or "@JohaaleTrader_es").strip()
    try:
        if re.fullmatch(r"-?\d+", target):
            return int(chat.id) == int(target)
    except Exception:
        pass
    target_username = target.lstrip("@").lower()
    return bool(target_username and (getattr(chat, "username", None) or "").lower() == target_username)


def _normalize_channel_source(value: str | None) -> str:
    raw = (value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return "ADS" if raw in {"ADS", "AD", "PAID", "PUBLICIDAD"} else "ORGANIC_OTHER"


def _get_channel_source(chat_id: int) -> str:
    """Devuelve la atribución first-touch; sin marca ADS se considera Orgánico/Otros."""
    if not _is_private_user_id(chat_id):
        return "ORGANIC_OTHER"
    try:
        with Session() as session:
            row = session.get(ChannelSourceAttribution, str(chat_id))
            if row and row.source == "ADS":
                return "ADS"
    except Exception as e:
        logging.warning("No pude leer origen de canal para %s: %s", chat_id, e)
    return "ORGANIC_OTHER"


def _save_ads_click_token(chat_id: int, click_id: str) -> None:
    """Guarda el click_id confirmado del último acceso ADS del usuario."""
    if not _is_private_user_id(chat_id):
        return
    token = (click_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{20}", token):
        return
    try:
        with Session() as session:
            row = session.get(AdsClickAttribution, str(chat_id))
            if row:
                row.click_id = token
                row.updated_at = utcnow_naive()
            else:
                session.add(AdsClickAttribution(
                    telegram_id=str(chat_id),
                    click_id=token,
                    updated_at=utcnow_naive(),
                ))
            session.commit()
    except Exception as e:
        logging.warning("No pude guardar click ADS de %s: %s", chat_id, e)


def _get_ads_click_token(chat_id: int) -> str:
    """Devuelve el click_id ADS confirmado del usuario, si existe."""
    if not _is_private_user_id(chat_id):
        return ""
    try:
        with Session() as session:
            row = session.get(AdsClickAttribution, str(chat_id))
            token = (row.click_id or "").strip().lower() if row else ""
        return token if re.fullmatch(r"[a-f0-9]{20}", token) else ""
    except Exception as e:
        logging.warning("No pude leer click ADS de %s: %s", chat_id, e)
        return ""


def _url_with_query_param(url: str, key: str, value: str) -> str:
    """Añade/reemplaza un parámetro sin alterar el resto del enlace oficial."""
    try:
        parsed = urllib.parse.urlsplit(url)
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        replaced = False
        new_pairs = []
        for k, v in pairs:
            if k == key:
                if not replaced:
                    new_pairs.append((k, value))
                    replaced = True
            else:
                new_pairs.append((k, v))
        if not replaced:
            new_pairs.append((key, value))
        return urllib.parse.urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(new_pairs),
            parsed.fragment,
        ))
    except Exception:
        joiner = "&" if "?" in url else "?"
        return f"{url}{joiner}{urllib.parse.quote_plus(key)}={urllib.parse.quote_plus(value)}"


def _referral_links_for_user(chat_id: int) -> tuple[str, str]:
    """En ADS agrega la subcuenta individual; en orgánico conserva los enlaces actuales."""
    stockity = ENLACE_REFERIDO_STOCKITY
    binomo = ENLACE_REFERIDO
    if _get_channel_source(chat_id) != "ADS":
        return stockity, binomo

    token = _get_ads_click_token(chat_id)
    if not token:
        # ADS antiguo/sin token local: no inventamos una atribución individual.
        return stockity, binomo

    # Conserva la etiqueta histórica JTTRADERS y añade el click individual.
    subaccount = f"JTTRADERS_{token}"
    return (
        _url_with_query_param(stockity, "sa", subaccount),
        _url_with_query_param(binomo, "sa", subaccount),
    )


def _personalize_referral_links(text_value: str, chat_id: int) -> str:
    """Sustituye solo los dos enlaces oficiales cuando el usuario tiene click ADS confirmado."""
    value = text_value or ""
    if not value or not _is_private_user_id(chat_id):
        return value
    stockity, binomo = _referral_links_for_user(chat_id)
    if stockity == ENLACE_REFERIDO_STOCKITY and binomo == ENLACE_REFERIDO:
        return value

    # Idempotente: si un bloque ya fue personalizado para este usuario, no duplica el sufijo.
    if stockity != ENLACE_REFERIDO_STOCKITY and stockity not in value:
        value = value.replace(ENLACE_REFERIDO_STOCKITY, stockity)
    if binomo != ENLACE_REFERIDO and binomo not in value:
        value = value.replace(ENLACE_REFERIDO, binomo)
    return value


def _record_source_attribution_only(chat_id: int, detected_source: str, authoritative: bool = False) -> str:
    """Guarda la fuente first-touch/paid-touch SIN contar todavía un ingreso al canal.

    Se usa en la puerta ADS: el usuario ya quedó identificado por el deep-link del anuncio,
    pero ChannelJoinEvent solo se crea cuando Telegram confirma posteriormente que entró
    al canal. Una atribución ADS confirmada puede elevar un registro orgánico previo y
    nunca se degrada después.
    """
    if not _is_private_user_id(chat_id):
        return "ORGANIC_OTHER"
    detected_source = _normalize_channel_source(detected_source)
    now = utcnow_naive()
    try:
        with Session() as session:
            row = session.get(ChannelSourceAttribution, str(chat_id))
            if row:
                if row.source == "ADS":
                    final_source = "ADS"
                elif authoritative and detected_source == "ADS":
                    row.source = "ADS"
                    final_source = "ADS"
                else:
                    final_source = "ORGANIC_OTHER"
                row.last_seen_at = now
            else:
                final_source = detected_source
                session.add(ChannelSourceAttribution(
                    telegram_id=str(chat_id),
                    source=final_source,
                    first_seen_at=now,
                    last_seen_at=now,
                ))
            session.commit()
        return final_source
    except Exception as e:
        logging.warning("No pude guardar atribución previa al canal para %s: %s", chat_id, e)
        return detected_source


def _record_channel_join_source(chat_id: int, detected_source: str, invite_name: str = "", authoritative: bool = False) -> str:
    """Guarda origen del canal sin tocar `usuarios`.

    La respuesta del servicio JOHAALE-TRACKING es la autoridad para ADS porque puede
    reconocer el invite_link exacto aunque Telegram omita el nombre del enlace en
    ``chat_member``. Una atribución ADS nunca se degrada a orgánico.
    """
    if not _is_private_user_id(chat_id):
        return "ORGANIC_OTHER"
    detected_source = _normalize_channel_source(detected_source)
    now = utcnow_naive()
    try:
        with Session() as session:
            row = session.get(ChannelSourceAttribution, str(chat_id))
            if row:
                if row.source == "ADS":
                    final_source = "ADS"
                elif authoritative and detected_source == "ADS":
                    # Reconciliación: el tracking externo confirmó que el enlace usado
                    # corresponde a ADS aunque Telegram no haya enviado invite_name.
                    row.source = "ADS"
                    final_source = "ADS"
                else:
                    final_source = "ORGANIC_OTHER"
                row.last_seen_at = now
            else:
                final_source = detected_source
                session.add(ChannelSourceAttribution(
                    telegram_id=str(chat_id),
                    source=final_source,
                    first_seen_at=now,
                    last_seen_at=now,
                ))

            session.add(ChannelJoinEvent(
                telegram_id=str(chat_id),
                source=final_source,
                invite_name=(invite_name or "")[:255],
                created_at=now,
            ))
            session.commit()
        return final_source
    except Exception as e:
        logging.warning("No pude guardar origen de canal para %s: %s", chat_id, e)
        return detected_source


def _source_breakdown(user_ids) -> tuple[int, int]:
    """Cuenta ADS vs Orgánico/Otros para un conjunto de Telegram IDs."""
    ids = {str(x) for x in (user_ids or set()) if x and _is_private_user_id(x)}
    if not ids:
        return 0, 0
    ads_ids = set()
    try:
        with Session() as session:
            rows = (
                session.query(ChannelSourceAttribution.telegram_id)
                .filter(
                    ChannelSourceAttribution.telegram_id.in_(list(ids)),
                    ChannelSourceAttribution.source == "ADS",
                )
                .all()
            )
            ads_ids = {str(r[0]) for r in rows if r and r[0]}
    except Exception as e:
        logging.warning("No pude calcular desglose de origen: %s", e)
    return len(ads_ids), len(ids - ads_ids)


def _channel_join_source_metrics(start_utc: datetime, end_utc: datetime):
    """Personas únicas que ingresaron al canal durante el día, por origen."""
    all_ids = set()
    ads_ids = set()
    try:
        with Session() as session:
            rows = (
                session.query(ChannelJoinEvent.telegram_id, ChannelJoinEvent.source)
                .filter(
                    ChannelJoinEvent.created_at >= start_utc,
                    ChannelJoinEvent.created_at < end_utc,
                )
                .all()
            )
        for telegram_id, source in rows:
            if telegram_id and _is_private_user_id(telegram_id):
                uid = str(telegram_id)
                all_ids.add(uid)
                if source == "ADS":
                    ads_ids.add(uid)
    except Exception as e:
        logging.warning("No pude calcular ingresos al canal por origen: %s", e)
    return all_ids, ads_ids, all_ids - ads_ids


async def tracking_channel_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registra TODA alta al canal ES y distingue ADS del resto cuando Telegram entrega la marca."""
    change = getattr(update, "chat_member", None)
    chat = update.effective_chat
    if not change or not _is_tracking_info_channel(chat):
        return

    old_status = str(getattr(getattr(change, "old_chat_member", None), "status", "") or "").lower()
    new_status = str(getattr(getattr(change, "new_chat_member", None), "status", "") or "").lower()
    active_statuses = {"member", "administrator", "restricted"}
    if new_status not in active_statuses or old_status in active_statuses:
        return

    member = getattr(getattr(change, "new_chat_member", None), "user", None)
    if not member or not _is_private_user_id(getattr(member, "id", None)):
        return

    invite_obj = getattr(change, "invite_link", None)
    invite_link = (getattr(invite_obj, "invite_link", None) or "").strip()
    invite_name = (getattr(invite_obj, "name", None) or "").strip()

    # Nuevo enlace permanente de pauta: name=source-ADS.
    # Los antiguos track-JT-* también se consideran ADS para conservar compatibilidad.
    detected_source = "ADS" if (
        invite_name.upper().startswith("SOURCE-ADS")
        or invite_name.startswith("track-JT-")
    ) else "ORGANIC_OTHER"

    # El servicio de tracking puede reconocer el invite_link exacto aun cuando
    # Telegram omite invite_name. Por eso consultamos tracking ANTES de persistir
    # la clasificación local y usamos su respuesta como autoridad si está disponible.
    result = await _tracking_post(
        "/internal/channel-join",
        {
            "telegram_id": int(member.id),
            "invite_link": invite_link or None,
            "invite_name": invite_name or None,
            "source": detected_source,
            "username": getattr(member, "username", None),
            "first_name": getattr(member, "first_name", None),
        },
        source="channel_join",
    )
    authoritative_source = _normalize_channel_source(
        result.get("source") if isinstance(result, dict) else detected_source
    )
    final_source = _record_channel_join_source(
        member.id, authoritative_source, invite_name, authoritative=bool(result)
    )
    logging.info(
        "📢 Alta canal detectada: Telegram %s | origen=%s | invite=%s | link=%s",
        member.id, final_source, invite_name or "(sin marca)", "sí" if invite_link else "no",
    )


async def tracking_channel_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aprueba accesos VIP autorizados y conserva el tracking del canal informativo ES."""
    req = getattr(update, "chat_join_request", None)
    if not req:
        return

    member = getattr(req, "from_user", None)
    if not member or not _is_private_user_id(getattr(member, "id", None)):
        return

    # 1) ACCESOS VIP POR NIVEL
    access_key = _vip_access_key_from_request(req)
    if access_key:
        chat_id = int(member.id)
        stage = get_user_stage(chat_id)
        state = _vip_get_state(chat_id, create=False)
        access_info = VIP_ACCESS_CHANNELS.get(access_key) or {}
        access_name = access_info.get("name_es", access_key)

        # Usuarios nuevos: autorización estricta por nivel persistido.
        # Cuentas DEPOSITED anteriores a esta versión pueden entrar automáticamente
        # a los dos espacios comunes; los accesos específicos quedan pendientes para
        # revisión manual hasta migrar su total/nivel desde REVISAR DEPÓSITO.
        if state:
            authorized = stage == STAGE_DEPOSITED and _vip_channel_allowed(state.get("level"), access_key)
            legacy_manual = False
        else:
            authorized = stage == STAGE_DEPOSITED and access_key in ("vip_main", "module3")
            legacy_manual = stage == STAGE_DEPOSITED and not authorized

        if legacy_manual:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"⚠️ Solicitud VIP de cuenta antigua: {_telegram_display_name(member)} (ID: {chat_id}).\n"
                    f"Canal: {access_name}.\n"
                    "La cuenta está DEPOSITED pero aún no tiene nivel migrado en v7.10.21. "
                    "La solicitud quedó pendiente para revisión manual; usa GESTIONAR USUARIO → REVISAR DEPÓSITO para registrar el total/nivel."
                ),
                reply_markup=admin_user_quick_keyboard(chat_id),
            )
            return

        if not authorized:
            try:
                await context.bot.decline_chat_join_request(chat_id=req.chat.id, user_id=chat_id)
            except Exception as e:
                logging.warning("No pude rechazar solicitud VIP no autorizada de %s: %s", chat_id, e)
            lang = get_user_lang(chat_id)
            denial = (
                "🔒 Esta solicitud no puede aprobarse porque ese canal no está incluido en tu nivel activo. Si crees que tu depósito o nivel cambió, envíame el comprobante por este chat."
                if lang == "es" else
                "🔒 This request cannot be approved because that channel is not included in your active level. If your deposit or level changed, send me the proof here in this chat."
            )
            try:
                await context.bot.send_message(chat_id=chat_id, text=denial, reply_markup=support_keyboard(lang))
            except Exception:
                pass
            logging.info("🔒 Solicitud VIP rechazada: %s / %s / stage=%s", chat_id, access_key, stage)
            return

        try:
            await context.bot.approve_chat_join_request(chat_id=req.chat.id, user_id=chat_id)
        except Exception as e:
            logging.warning("No pude aprobar acceso VIP %s para %s: %s", access_key, chat_id, e)
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"⚠️ No pude aprobar automáticamente a {_telegram_display_name(member)} (ID: {chat_id}) en {access_name}.\n"
                        "Verifica que @JOHAALETRADER_bot sea administrador del canal con permiso para invitar/aprobar usuarios."
                    ),
                )
            except Exception:
                pass
            return

        _log_event(chat_id, "VIP_ACCESS_APPROVED", access_key)
        _tracking_fire_event(chat_id, "VIP_ACCESS_APPROVED", access_key)
        level, should_welcome = _vip_mark_access_approved(chat_id, access_key)
        logging.info("✅ Acceso VIP automático aprobado: %s / %s / nivel=%s", chat_id, access_key, level)

        # UNA sola bienvenida privada cuando se completaron todos los accesos
        # pendientes del nivel/upgrade actual. No se publica bienvenida por canal.
        if should_welcome:
            lang = get_user_lang(chat_id)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=_vip_final_welcome_text(level, lang),
                    reply_markup=support_keyboard(lang),
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logging.warning("Accesos VIP completos para %s, pero no pude enviar bienvenida: %s", chat_id, e)
        return

    # 2) TRACKING DEL CANAL INFORMATIVO ES — comportamiento anterior intacto.
    if not _is_tracking_info_channel(getattr(req, "chat", None)):
        chat = getattr(req, "chat", None)
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ SOLICITUD VIP NO MAPEADA\n\n"
                    f"Canal: {getattr(chat, 'title', None) or '(sin título)'}\n"
                    f"Chat ID: {getattr(chat, 'id', None)}\n"
                    f"Usuario: {_telegram_display_name(member)} (ID: {member.id})\n\n"
                    "La solicitud quedó pendiente. Este dato permite asociar el canal por ID sin depender del enlace de invitación."
                ),
            )
        except Exception:
            pass
        logging.warning("⚠️ Solicitud VIP no mapeada: chat=%s title=%s user=%s", getattr(chat, "id", None), getattr(chat, "title", None), member.id)
        return

    invite_obj = getattr(req, "invite_link", None)
    invite_link = (getattr(invite_obj, "invite_link", None) or "").strip()
    invite_name = (getattr(invite_obj, "name", None) or "").strip()
    detected_source = "ADS" if (
        invite_name.upper().startswith("SOURCE-ADS")
        or invite_name.startswith("track-JT-")
    ) else "ORGANIC_OTHER"

    try:
        await context.bot.approve_chat_join_request(chat_id=req.chat.id, user_id=member.id)
    except Exception as e:
        logging.warning("No pude aprobar join request de %s: %s", member.id, e)
        return

    result = await _tracking_post(
        "/internal/channel-join",
        {
            "telegram_id": int(member.id),
            "invite_link": invite_link or None,
            "invite_name": invite_name or None,
            "source": detected_source,
            "username": getattr(member, "username", None),
            "first_name": getattr(member, "first_name", None),
        },
        source="channel_join_request",
    )
    authoritative_source = _normalize_channel_source(
        result.get("source") if isinstance(result, dict) else detected_source
    )
    _record_channel_join_source(
        member.id, authoritative_source, invite_name, authoritative=bool(result)
    )


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


def _event_user_ids_with_detail(event_type: str, detail_value: str, start_utc: datetime, end_utc: datetime):
    """Usuarios únicos de un evento cuyo detail coincide exactamente."""
    try:
        with Session() as session:
            rows = (
                session.query(BotEvent.telegram_id)
                .filter(BotEvent.event_type == event_type)
                .filter(BotEvent.detail == detail_value)
                .filter(BotEvent.created_at >= start_utc, BotEvent.created_at < end_utc)
                .distinct()
                .all()
            )
        return {
            str(row[0]) for row in rows
            if row and row[0] and _is_private_user_id(row[0])
        }
    except Exception as e:
        logging.warning(
            "No pude consultar eventos %s detail=%s: %s",
            event_type, detail_value, e,
        )
        return set()


def _message_source_metrics(start_utc: datetime, end_utc: datetime) -> tuple[int, int]:
    """Cuenta mensajes (no solo personas) separados por ADS vs Orgánico/Otros."""
    try:
        with Session() as session:
            rows = (
                session.query(BotEvent.telegram_id)
                .filter(BotEvent.event_type == "MESSAGE")
                .filter(BotEvent.created_at >= start_utc, BotEvent.created_at < end_utc)
                .all()
            )
            ids = {str(r[0]) for r in rows if r and r[0] and _is_private_user_id(r[0])}
            ads_ids = set()
            if ids:
                ads_rows = (
                    session.query(ChannelSourceAttribution.telegram_id)
                    .filter(
                        ChannelSourceAttribution.telegram_id.in_(list(ids)),
                        ChannelSourceAttribution.source == "ADS",
                    )
                    .all()
                )
                ads_ids = {str(r[0]) for r in ads_rows if r and r[0]}
        ads_messages = sum(1 for r in rows if r and r[0] and str(r[0]) in ads_ids)
        organic_messages = sum(1 for r in rows if r and r[0] and str(r[0]) not in ads_ids and _is_private_user_id(r[0]))
        return ads_messages, organic_messages
    except Exception as e:
        logging.warning("No pude calcular mensajes por origen: %s", e)
        return 0, 0


def _daily_report_text(now_local=None, affiliate_summary=None) -> str:
    now_local = now_local or datetime.now(COLOMBIA_TZ)
    start_utc, end_utc = _colombia_day_utc_bounds(now_local)

    writers = _event_user_ids("MESSAGE", start_utc, end_utc)
    channel_welcome_es = _event_user_ids_with_detail(
        "CHANNEL_WELCOME_START", "canal_bienvenida", start_utc, end_utc
    )
    channel_welcome_en = _event_user_ids_with_detail(
        "CHANNEL_WELCOME_START", "canal_bienvenida_en", start_utc, end_utc
    )
    channel_welcome_starts = channel_welcome_es | channel_welcome_en
    ids_sent = _event_user_ids("ID_SUBMITTED", start_utc, end_utc)
    ids_validated = _event_user_ids("ID_VALIDATED", start_utc, end_utc)
    deposits_reported = _event_user_ids("DEPOSIT_REPORTED", start_utc, end_utc)
    activated = _event_user_ids("ACCOUNT_ACTIVATED", start_utc, end_utc)
    ads_gate_starts = _event_user_ids("ADS_GATE_START", start_utc, end_utc)
    registration_entry_starts = _event_user_ids("REGISTRATION_ENTRY_START", start_utc, end_utc)

    channel_join_ids, channel_join_ads_ids, channel_join_organic_ids = _channel_join_source_metrics(start_utc, end_utc)
    welcome_ads, welcome_organic = _source_breakdown(channel_welcome_starts)
    writers_ads, writers_organic = _source_breakdown(writers)
    ids_sent_ads, ids_sent_organic = _source_breakdown(ids_sent)
    ids_validated_ads, ids_validated_organic = _source_breakdown(ids_validated)
    deposits_reported_ads, deposits_reported_organic = _source_breakdown(deposits_reported)
    activated_ads, activated_organic = _source_breakdown(activated)
    registration_entry_ads, registration_entry_organic = _source_breakdown(registration_entry_starts)
    messages_ads, messages_organic = _message_source_metrics(start_utc, end_utc)

    # Affiliate Top: datos reales recibidos por el servicio de tracking.
    affiliate_ok = isinstance(affiliate_summary, dict) and bool(affiliate_summary.get("ok"))
    by_source = affiliate_summary.get("by_source", {}) if affiliate_ok else {}

    def _aff_counts(bucket: str):
        data = by_source.get(bucket, {}) if isinstance(by_source, dict) else {}
        return (
            int(data.get("REGISTRATION", 0) or 0),
            int(data.get("FIRST_DEPOSIT", 0) or 0),
            int(data.get("REDEPOSIT", 0) or 0),
        )

    aff_ads = _aff_counts("ADS")
    aff_organic = _aff_counts("ORGANIC_OTHER")
    aff_unattributed = _aff_counts("UNATTRIBUTED")

    if affiliate_ok:
        affiliate_ads_line = (
            f"📈 Affiliate Top: 📝 Registros {aff_ads[0]} | 💰 1er depósito {aff_ads[1]} | ♻️ Redepósitos {aff_ads[2]}\n"
        )
        affiliate_organic_line = (
            f"📈 Affiliate Top: 📝 Registros {aff_organic[0]} | 💰 1er depósito {aff_organic[1]} | ♻️ Redepósitos {aff_organic[2]}\n"
        )
        affiliate_unattributed_line = ""
        if any(aff_unattributed):
            affiliate_unattributed_line = (
                f"🔎 Affiliate sin atribuir: Registros {aff_unattributed[0]} | "
                f"1er depósito {aff_unattributed[1]} | Redepósitos {aff_unattributed[2]}\n"
            )
    else:
        affiliate_ads_line = "📈 Affiliate Top: ⚠️ no disponible\n"
        affiliate_organic_line = "📈 Affiliate Top: ⚠️ no disponible\n"
        affiliate_unattributed_line = ""

    no_id_users = set()
    try:
        with Session() as session:
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
                        no_id_users.add(str(uid))
    except Exception as e:
        logging.warning("No pude completar pendientes sin ID del reporte: %s", e)

    # Validado durante el día y todavía sin aviso/confirmación de depósito.
    waiting_users = ids_validated - deposits_reported - activated
    no_id_ads, no_id_organic = _source_breakdown(no_id_users)
    waiting_ads, waiting_organic = _source_breakdown(waiting_users)

    fecha = now_local.strftime("%d/%m/%Y")
    return (
        f"📊 REPORTE DIARIO — {fecha}\n\n"
        f"📣 ADS\n"
        f"🎯 Bot-puerta: {len(ads_gate_starts)} | 📥 Canal: {len(channel_join_ads_ids)}\n"
        f"🤖 Del canal al bot: {welcome_ads} | 🚀 Entrada registro: {registration_entry_ads}\n"
        f"👤 Escribieron: {writers_ads} | 💬 Mensajes: {messages_ads}\n"
        f"🆔 ID enviados: {ids_sent_ads} | ✅ Validados: {ids_validated_ads}\n"
        f"💳 Avisaron depósito: {deposits_reported_ads} | 🟢 Confirmados: {activated_ads}\n"
        f"{affiliate_ads_line}"
        f"⏳ Sin ID: {no_id_ads} | ID validado sin depósito: {waiting_ads}\n\n"
        f"🌱 ORGÁNICO / OTROS\n"
        f"📥 Canal: {len(channel_join_organic_ids)} | 🤖 Del canal al bot: {welcome_organic}\n"
        f"🚀 Entrada registro: {registration_entry_organic}\n"
        f"👤 Escribieron: {writers_organic} | 💬 Mensajes: {messages_organic}\n"
        f"🆔 ID enviados: {ids_sent_organic} | ✅ Validados: {ids_validated_organic}\n"
        f"💳 Avisaron depósito: {deposits_reported_organic} | 🟢 Confirmados: {activated_organic}\n"
        f"{affiliate_organic_line}"
        f"⏳ Sin ID: {no_id_organic} | ID validado sin depósito: {waiting_organic}\n\n"
        f"{affiliate_unattributed_line}"
        "ℹ️ Orgánico/Otros = toda persona sin atribución ADS confirmada.\n"
        "⏰ Corte: 11:00 p. m. Colombia."
    )


async def _daily_report_with_affiliate(now_local=None) -> str:
    """Construye el reporte sin hacer depender el bot del tracking externo."""
    now_local = now_local or datetime.now(COLOMBIA_TZ)
    start_utc, end_utc = _colombia_day_utc_bounds(now_local)
    affiliate_summary = await _tracking_affiliate_summary(start_utc, end_utc)
    return _daily_report_text(now_local, affiliate_summary)


def report_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Botón seguro de solo navegación al dashboard privado de ADS REPORTS."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 ABRIR DASHBOARD ADS", url=DASHBOARD_URL)
    ]])


async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Envía el corte diario al grupo de reportes; mientras no esté configurado, cae al ADMIN."""
    target_chat_id = REPORT_CHAT_ID if REPORT_CHAT_ID else ADMIN_ID
    try:
        await context.bot.send_message(
            chat_id=target_chat_id,
            text=await _daily_report_with_affiliate(),
            reply_markup=report_dashboard_keyboard() if target_chat_id == REPORT_CHAT_ID else None,
        )
    except Exception as e:
        logging.warning("No pude enviar reporte diario a %s: %s", target_chat_id, e)
        # Si el grupo fue mal configurado o el bot perdió permisos, Johanna no pierde el reporte.
        if target_chat_id != ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        "⚠️ No pude publicar el reporte diario en JOHAALETRADER · ADS REPORTS. "
                        "Te envío el reporte aquí como respaldo.\n\n"
                        + await _daily_report_with_affiliate()
                    ),
                )
            except Exception as fallback_error:
                logging.warning("Tampoco pude enviar respaldo del reporte al ADMIN: %s", fallback_error)


async def daily_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Conserva /reporte privado para Johanna."""
    if update.effective_user and update.effective_user.id == ADMIN_ID:
        await update.effective_message.reply_text(await _daily_report_with_affiliate())


async def report_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Devuelve el ID del grupo SOLO a Johanna; se registra antes del bloqueo global."""
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            "ℹ️ Usa /reportid dentro del grupo JOHAALETRADER · ADS REPORTS."
        )
        return
    title = getattr(chat, "title", None) or "(sin título)"
    await update.effective_message.reply_text(
        f"✅ Grupo detectado\n\nNombre: {title}\nREPORT_CHAT_ID = {chat.id}\n\n"
        "Este es el ID del grupo configurado para los reportes."
    )


async def report_group_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prueba manual desde el chat privado de Johanna hacia el grupo configurado."""
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    if not REPORT_CHAT_ID:
        await update.effective_message.reply_text(
            "⚠️ REPORT_CHAT_ID aún no está configurado. Primero usa /reportid dentro del grupo."
        )
        return
    try:
        await context.bot.send_message(
            chat_id=REPORT_CHAT_ID,
            text=await _daily_report_with_affiliate(),
            reply_markup=report_dashboard_keyboard(),
        )
        await update.effective_message.reply_text(
            "✅ Reporte de prueba enviado a JOHAALETRADER · ADS REPORTS."
        )
    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ No pude enviar el reporte al grupo configurado: {e}"
        )


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
    """Prioriza que la conversación continúe dentro de este chat."""
    if lang == "en":
        return [
            [InlineKeyboardButton("💬 I HAVE A QUESTION", callback_data="ask_here")],
            [InlineKeyboardButton("🏠 Back to main menu", callback_data="back_main_menu")],
        ]
    return [
        [InlineKeyboardButton("💬 TENGO UNA PREGUNTA", callback_data="ask_here")],
        [InlineKeyboardButton("🏠 Volver al menú principal", callback_data="back_main_menu")],
    ]

def support_keyboard(lang: str = "es") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(support_rows(lang))


def levels_keyboard(lang: str = "es") -> InlineKeyboardMarkup:
    """Estructura completa + soporte, usando el Telegraph oficial actualizado."""
    label = "📄 Full structure" if lang == "en" else "📄 Ver estructura completa"
    upgrade_label = "ℹ️ View upgrade conditions" if lang == "en" else "ℹ️ Ver condiciones de upgrade"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, url=TELEGRAPH_LEVELS_URL)],
        [InlineKeyboardButton(upgrade_label, callback_data="upgrade_conditions")],
        *support_rows(lang),
    ])

def personal_chat_keyboard(lang: str = "es") -> InlineKeyboardMarkup:
    """Acceso secundario al chat personal, reservado para casos que sí requieren atención directa."""
    label = "📩 MY PERSONAL CHAT" if lang == "en" else "📩 MI CHAT PERSONAL"
    back = "🏠 Back to main menu" if lang == "en" else "🏠 Volver al menú principal"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, url=SUPPORT_URL)],
        [InlineKeyboardButton(back, callback_data="back_main_menu")],
    ])

def remarketing_keyboard(lang: str = "es") -> InlineKeyboardMarkup:
    """Teclado exclusivo del remarketing: registro + soporte + regreso al menú."""
    register_text = "📝 I want to register" if lang == "en" else "📝 QUIERO REGISTRARME"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(register_text, callback_data="registrarme")],
        *support_rows(lang),
    ])

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
        [InlineKeyboardButton("👤 GESTIONAR USUARIO", callback_data="admin_panel_users")],
        [InlineKeyboardButton("🏠 /start — Inicio", callback_data="admin_panel_start")],
    ])


ADMIN_USER_PAGE_SIZE = 10
ADMIN_USER_MAX_PENDING = 50


def _admin_recent_users(limit: int = ADMIN_USER_MAX_PENDING):
    """Usuarios PRE/POST recientes pendientes de gestión; DEPOSITED no llena la cola."""
    try:
        safe_limit = max(1, min(int(limit), ADMIN_USER_MAX_PENDING))
        with Session() as session:
            rows = (
                session.query(Usuario, UserActivity.last_activity_at)
                .outerjoin(UserActivity, Usuario.telegram_id == UserActivity.telegram_id)
                .filter(
                    Usuario.telegram_id != str(ADMIN_ID),
                    or_(Usuario.stage == None, Usuario.stage.in_((STAGE_PRE, STAGE_POST))),
                )
                .order_by(UserActivity.last_activity_at.desc(), Usuario.fecha_registro.desc())
                .limit(safe_limit)
                .all()
            )
        result = []
        for user, last_activity in rows:
            try:
                cid = int(user.telegram_id)
            except Exception:
                continue
            if not _is_private_user_id(cid):
                continue
            stage = user.stage if user.stage in (STAGE_PRE, STAGE_POST) else STAGE_PRE
            result.append((cid, user.nombre or f"Usuario {cid}", stage, user.binomo_id or "", last_activity))
        return result
    except Exception as e:
        logging.warning("No pude listar usuarios pendientes para admin: %s", e)
        return []


def _admin_user_list_keyboard(rows, page: int = 0, total_count: int | None = None) -> InlineKeyboardMarkup:
    buttons = []
    stage_icon = {STAGE_PRE: "🟡", STAGE_POST: "🔵", STAGE_DEPOSITED: "🟢"}
    for chat_id, name, stage, _trading_id, _last_activity in rows:
        clean_name = re.sub(r"\s+", " ", str(name or "Usuario")).strip()[:26]
        icon = stage_icon.get(stage or STAGE_PRE, "⚪")
        buttons.append([InlineKeyboardButton(
            f"{icon} {clean_name}",
            callback_data=f"admin_user_open:{chat_id}",
        )])

    if total_count is not None:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ ANTERIOR", callback_data=f"admin_user_list_page:{page - 1}"))
        if (page + 1) * ADMIN_USER_PAGE_SIZE < total_count:
            nav.append(InlineKeyboardButton("SIGUIENTE ➡️", callback_data=f"admin_user_list_page:{page + 1}"))
        if nav:
            buttons.append(nav)

    buttons.extend([
        [InlineKeyboardButton("🔎 BUSCAR USUARIO", callback_data="admin_user_search")],
        [InlineKeyboardButton("↩️ VOLVER AL PANEL", callback_data="admin_user_panel")],
    ])
    return InlineKeyboardMarkup(buttons)


def _admin_user_actions_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Muestra solo la siguiente acción válida según la etapa actual."""
    stage = get_user_stage(chat_id)
    buttons = []
    if stage == STAGE_PRE:
        buttons.append([InlineKeyboardButton("✅ VALIDAR ID", callback_data=f"admin_user_validate:{chat_id}")])
        if _get_saved_trading_id(chat_id):
            buttons.append([InlineKeyboardButton("❌ ID ERRADO", callback_data=f"admin_user_reject:{chat_id}")])
    elif stage == STAGE_POST:
        buttons.append([InlineKeyboardButton("💰 REVISAR DEPÓSITO", callback_data=f"admin_user_deposit:{chat_id}")])
    elif stage == STAGE_DEPOSITED:
        buttons.append([InlineKeyboardButton("💰 REVISAR DEPÓSITO / SUBIR NIVEL", callback_data=f"admin_user_deposit:{chat_id}")])
    buttons.extend([
        [InlineKeyboardButton("🔎 BUSCAR OTRO", callback_data="admin_user_search")],
        [InlineKeyboardButton("👥 PENDIENTES RECIENTES", callback_data="admin_user_list")],
    ])
    return InlineKeyboardMarkup(buttons)


def admin_user_quick_keyboard(chat_id: int, event_kind: str = "") -> InlineKeyboardMarkup:
    """Acciones rápidas solo cuando el evento recibido aporta evidencia real.

    Las entradas al bot, /start y bienvenidas NO muestran VALIDAR ID por el simple
    hecho de que el usuario esté en PRE. VALIDAR ID aparece únicamente cuando el
    evento corresponde a un ID realmente enviado; ACTIVAR únicamente ante un
    comprobante recibido mientras el usuario está en POST.
    """
    stage = get_user_stage(chat_id)
    rows = []
    if event_kind == "id":
        rows.append([InlineKeyboardButton("✅ VALIDAR ID", callback_data=f"admin_user_validate:{chat_id}")])
        rows.append([InlineKeyboardButton("❌ ID ERRADO", callback_data=f"admin_user_reject:{chat_id}")])
    elif event_kind == "deposit_proof" and stage in (STAGE_POST, STAGE_DEPOSITED):
        label = "💰 REVISAR DEPÓSITO" if stage == STAGE_POST else "💰 REVISAR DEPÓSITO / SUBIR NIVEL"
        rows.append([InlineKeyboardButton(label, callback_data=f"admin_user_deposit:{chat_id}")])
    rows.append([InlineKeyboardButton("👤 GESTIONAR", callback_data=f"admin_user_open:{chat_id}")])
    return InlineKeyboardMarkup(rows)


def _admin_user_record(chat_id: int):
    try:
        with Session() as session:
            row = (
                session.query(Usuario.nombre, Usuario.binomo_id, Usuario.lang, Usuario.stage)
                .filter_by(telegram_id=str(chat_id))
                .first()
            )
        if not row:
            return None
        return {
            "chat_id": int(chat_id),
            "nombre": row[0] or f"Usuario {chat_id}",
            "trading_id": (row[1] or "").strip(),
            "lang": row[2] if row[2] in ("es", "en") else "es",
            "stage": row[3] if row[3] in (STAGE_PRE, STAGE_POST, STAGE_DEPOSITED) else STAGE_PRE,
        }
    except Exception as e:
        logging.warning("No pude leer usuario %s para panel admin: %s", chat_id, e)
        return None


async def _show_admin_user_list(context: ContextTypes.DEFAULT_TYPE, page: int = 0, text_prefix: str = ""):
    all_rows = _admin_recent_users(ADMIN_USER_MAX_PENDING)
    total = len(all_rows)
    max_page = max(0, (total - 1) // ADMIN_USER_PAGE_SIZE) if total else 0
    page = max(0, min(int(page), max_page))
    start_idx = page * ADMIN_USER_PAGE_SIZE
    page_rows = all_rows[start_idx:start_idx + ADMIN_USER_PAGE_SIZE]

    text_value = (text_prefix + "\n\n" if text_prefix else "") + (
        "👤 GESTIONAR USUARIO\n\n"
        "Solo aparecen procesos pendientes: 🟡 PRE y 🔵 POST.\n"
        "Las cuentas activadas desaparecen automáticamente de esta lista."
    )
    if total:
        text_value += f"\n\nPendientes: {total} de un máximo de {ADMIN_USER_MAX_PENDING} · Página {page + 1}/{max_page + 1}"
    else:
        text_value += "\n\n✅ No hay usuarios pendientes en la lista."
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=text_value,
        reply_markup=_admin_user_list_keyboard(page_rows, page=page, total_count=total),
    )


async def _show_admin_user(context: ContextTypes.DEFAULT_TYPE, chat_id: int, prefix: str = ""):
    record = _admin_user_record(chat_id)
    if not record:
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ No encontré al usuario {chat_id} en la base del bot.")
        return
    stage_label = {STAGE_PRE: "PRE — pendiente de validar ID", STAGE_POST: "POST — ID validado / esperando depósito", STAGE_DEPOSITED: "DEPOSITED — cuenta activa"}.get(record["stage"], record["stage"])
    trading = record["trading_id"] or "No registrado en el bot"
    vip_state = _vip_get_state(chat_id, create=False)
    vip_extra = ""
    broker_summary = _broker_account_summary(chat_id)
    if vip_state:
        vip_extra = (
            f"\nNivel VIP: {_vip_level_label(vip_state['level'], 'es')}"
            f"\nTotal referencia global: USD {_usd(vip_state['total_cents'])}"
        )
    if broker_summary:
        vip_extra += f"\n\n🏦 CUENTAS POR BROKER\n{broker_summary}"
    text_value = (prefix + "\n\n" if prefix else "") + (
        f"👤 {record['nombre']}\n"
        f"Telegram ID: {record['chat_id']}\n"
        f"Estado: {stage_label}\n"
        f"ID de trading: {trading}{vip_extra}"
    )
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=text_value,
        reply_markup=_admin_user_actions_keyboard(chat_id),
    )


async def _admin_finalize_id_validation(context: ContextTypes.DEFAULT_TYPE, chat_id: int, trading_id: str):
    trading_id = (trading_id or "").strip()
    if not re.fullmatch(r"\d{6,12}", trading_id):
        return False, "⚠️ El ID de trading debe contener entre 6 y 12 números."
    record = _admin_user_record(chat_id)
    if not record:
        return False, "⚠️ Ese usuario no está registrado en el bot."
    if record["stage"] == STAGE_DEPOSITED:
        return False, "ℹ️ Esa cuenta ya figura como activa (DEPOSITED)."
    if record["stage"] == STAGE_POST and _strict_validated_id_state(chat_id):
        return False, "ℹ️ Ese ID ya está validado. No reinicié la Serie B ni sus tiempos."

    try:
        with Session() as session:
            u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
            if not u:
                return False, "⚠️ Ese usuario no está registrado en el bot."
            u.binomo_id = trading_id
            session.commit()

        if not _has_submitted_id_evidence(chat_id, trading_id):
            _log_event(chat_id, "ID_SUBMITTED", trading_id)
            _tracking_fire_event(chat_id, "ID_SUBMITTED", trading_id)
        set_user_stage(chat_id, STAGE_POST)
        _log_event(chat_id, "ID_VALIDATED", f"ID={trading_id} | ADMIN_MANUAL")
        _tracking_fire_event(chat_id, "ID_VALIDATED", f"ID={trading_id} | ADMIN_MANUAL")
        _cancel_jobs_prefix(context, "A", chat_id)
        schedule_series_b(chat_id, context)

        lang = get_user_lang(chat_id)
        user_msg = ADMIN_ID_VALIDATED_ES if lang == "es" else ADMIN_ID_VALIDATED_EN
        try:
            await context.bot.send_message(chat_id=chat_id, text=user_msg)
        except Exception as e:
            logging.warning("ID validado manualmente para %s, pero no pude avisarle: %s", chat_id, e)
        return True, f"✅ ID {trading_id} validado manualmente. Serie A detenida y Serie B activada."
    except Exception as e:
        logging.exception("Error validando ID manual de %s", chat_id)
        return False, f"❌ No pude validar el ID: {e}"


async def _admin_reject_trading_id(context: ContextTypes.DEFAULT_TYPE, chat_id: int, notify_user: bool = True):
    """Marca el ID como errado sin mezclar campañas A/B ni perder la atribución ADS."""
    record = _admin_user_record(chat_id)
    if not record:
        return False, "⚠️ Ese usuario no está registrado en el bot."
    if record["stage"] == STAGE_DEPOSITED:
        return False, "⚠️ Esa cuenta ya está activa. No marqué el ID como errado."

    rejected_id = (record.get("trading_id") or "").strip()
    previous_stage = record["stage"]

    set_user_stage(chat_id, STAGE_PRE)
    _clear_saved_trading_id(chat_id)
    _log_event(chat_id, "ID_REJECTED", f"ID={rejected_id or 'SIN_ID'} | ADMIN")
    _tracking_fire_event(chat_id, "ID_REJECTED", f"ID={rejected_id or 'SIN_ID'} | ADMIN")
    _cancel_jobs_prefix(context, "B", chat_id)

    lang = get_user_lang(chat_id)
    if previous_stage == STAGE_POST:
        # Al volver desde POST, la Serie A anterior ya había sido cancelada al validar.
        # Este rechazo representa un nuevo intento de registro, por eso nace una nueva Serie A.
        schedule_series_a(chat_id, lang, context)
        campaign_note = "Serie B cancelada y Serie A reiniciada para el nuevo registro."
    else:
        # Si seguía PRE, conserva los tiempos originales de A; no reinicia el reloj.
        _sync_menu_campaign_for_stage(chat_id, lang, context)
        campaign_note = "Serie B no aplica y Serie A continúa con sus tiempos actuales."

    if notify_user:
        template = GATILLO_ID_ERRADO if lang == "es" else GATILLO_ID_ERRADO_EN
        outbound = _personalize_referral_links(template, chat_id)
        try:
            await context.bot.send_message(chat_id=chat_id, text=outbound, disable_web_page_preview=True)
        except Exception as e:
            logging.warning("ID rechazado para %s, pero no pude avisarle: %s", chat_id, e)

    return True, f"❌ ID rechazado. Usuario continúa en PRE. {campaign_note}"


async def _admin_apply_deposit_confirmation(context: ContextTypes.DEFAULT_TYPE, chat_id: int, pending: dict):
    """Confirma un monto revisado por Johanna, calcula nivel y entrega accesos sin decisiones silenciosas."""
    record = _admin_user_record(chat_id)
    if not record:
        return False, "⚠️ Ese usuario no está registrado en el bot."

    mode = pending.get("mode") or "add"
    amount_cents = int(pending.get("amount_cents") or 0)
    expected_previous = int(pending.get("previous_total_cents") or 0)
    if amount_cents <= 0:
        return False, "⚠️ El monto pendiente no es válido. Vuelve a usar REVISAR DEPÓSITO."

    current_state = _vip_get_state(chat_id, create=False)
    current_total = int((current_state or {}).get("total_cents") or 0)
    old_level = (current_state or {}).get("level") or VIP_LEVEL_NONE

    # Evita sumar dos veces el mismo depósito si se pulsa una confirmación vieja.
    if mode == "add" and current_total != expected_previous:
        return False, "⚠️ El total VIP cambió desde que preparaste esta confirmación. Vuelve a REVISAR DEPÓSITO para evitar duplicarlo."

    if mode == "set_total_existing":
        new_total = amount_cents
        old_level = VIP_LEVEL_NONE
        if record["stage"] == STAGE_DEPOSITED and new_total < VIP_LEVEL_THRESHOLDS_CENTS[VIP_LEVEL_BASIC]:
            return False, "⚠️ Esa persona ya estaba activa antes de este control. Para migrarla, el TOTAL confirmado debe ser al menos USD 50."
    else:
        new_total = current_total + amount_cents

    new_level = _vip_level_for_total_cents(new_total)
    rank_up = VIP_LEVEL_RANK.get(new_level, 0) > VIP_LEVEL_RANK.get(old_level, 0)
    new_keys = _vip_new_channel_keys(old_level, new_level) if rank_up else []
    existing_pending = list((current_state or {}).get("pending_keys") or [])
    if new_keys:
        allowed_now = set(_vip_channel_keys_for_level(new_level))
        carry_pending = [k for k in existing_pending if k in allowed_now]
        pending_keys = list(dict.fromkeys(carry_pending + new_keys))
    else:
        pending_keys = None

    if not _vip_set_state(chat_id, new_total, new_level, pending_keys=pending_keys):
        return False, "❌ No pude guardar el nuevo total VIP. No se modificó la activación."

    detail = (
        f"amount_usd={amount_cents / 100:.2f} | total_usd={new_total / 100:.2f} | "
        f"level={new_level} | mode={mode}"
    )
    _log_event(chat_id, "DEPOSIT_VALIDATED", detail)
    _tracking_fire_event(chat_id, "DEPOSIT_VALIDATED", detail)
    lang = get_user_lang(chat_id)

    # Todavía no llega al mínimo de Básico: conserva POST y no habilita ningún acceso.
    if new_level == VIP_LEVEL_NONE:
        if record["stage"] != STAGE_POST:
            set_user_stage(chat_id, STAGE_POST)
        user_msg = _vip_insufficient_message(new_total, lang)
        try:
            await context.bot.send_message(chat_id=chat_id, text=user_msg)
        except Exception as e:
            logging.warning("Depósito insuficiente guardado para %s, pero no pude avisarle: %s", chat_id, e)
        missing = VIP_LEVEL_THRESHOLDS_CENTS[VIP_LEVEL_BASIC] - new_total
        return True, (
            f"💰 Total validado: USD {_usd(new_total)}. Aún sin acceso; "
            f"faltan USD {_usd(missing)} para Básico. El usuario continúa en POST."
        )

    was_active = record["stage"] == STAGE_DEPOSITED
    first_activation = not was_active
    if first_activation:
        set_user_stage(chat_id, STAGE_DEPOSITED)
        _log_event(chat_id, "ACCOUNT_ACTIVATED", f"LEVEL={new_level} | TOTAL_USD={new_total / 100:.2f}")
        _tracking_fire_event(chat_id, "ACCOUNT_ACTIVATED", f"LEVEL={new_level} | TOTAL_USD={new_total / 100:.2f}")
    elif rank_up:
        _log_event(chat_id, "VIP_LEVEL_UPGRADED", f"{old_level}->{new_level} | TOTAL_USD={new_total / 100:.2f}")
        _tracking_fire_event(chat_id, "VIP_LEVEL_UPGRADED", f"{old_level}->{new_level} | TOTAL_USD={new_total / 100:.2f}")

    _cancel_jobs_prefix(context, "A", chat_id)
    _cancel_jobs_prefix(context, "B", chat_id)

    try:
        if first_activation or rank_up:
            activation_msg = _vip_activation_message(new_level, new_total, lang, upgraded=(was_active and rank_up))
            await context.bot.send_message(chat_id=chat_id, text=activation_msg, reply_markup=upgrade_info_keyboard(lang))
            if new_keys:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=_vip_access_intro(new_level, lang, upgrade=(was_active and rank_up)),
                    reply_markup=_vip_access_keyboard(new_level, lang, keys=new_keys),
                    disable_web_page_preview=True,
                )
        else:
            next_level, missing = _vip_next_level(new_level, new_total)
            if lang == "en":
                user_msg = (
                    f"✅ Additional deposit confirmed. Your current level remains {_vip_level_label(new_level, lang)}.\n\n"
                    "Upgrades are calculated from validated deposits within the enabled level-update period."
                )
            else:
                user_msg = (
                    f"✅ Depósito adicional confirmado. Tu nivel actual se mantiene en {_vip_level_label(new_level, lang)}.\n\n"
                    "Los upgrades se calculan según depósitos validados dentro del periodo habilitado para actualización de nivel."
                )
            await context.bot.send_message(chat_id=chat_id, text=user_msg, reply_markup=upgrade_info_keyboard(lang))
    except Exception as e:
        logging.warning("Depósito/nivel actualizado para %s, pero no pude enviar todos los avisos: %s", chat_id, e)

    admin_note = (
        f"✅ Total validado: USD {_usd(new_total)} · Nivel {_vip_level_label(new_level, 'es')}. "
        "Cuenta activa y fuera de campañas A/B."
    )
    if new_keys:
        admin_note += f" Se enviaron {len(new_keys)} acceso(s) nuevo(s) para aprobación automática."
    return True, admin_note


async def _admin_activate_user(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Compatibilidad con botones antiguos: la activación directa ya no existe sin registrar monto."""
    return False, "ℹ️ El flujo cambió: usa 💰 REVISAR DEPÓSITO, registra el monto confirmado y luego confirma el cálculo."


async def broker_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not update.effective_user or not _is_private_user_id(update.effective_user.id):
        return
    data = q.data or ""
    await q.answer()
    chat_id = update.effective_user.id
    lang = get_user_lang(chat_id)

    m = re.fullmatch(r"broker_id_select:(BINOMO|STOCKITY)", data)
    if m:
        broker = m.group(1)
        flow = _broker_flow_get(chat_id)
        trading_id = (flow.get("pending_trading_id") or "").strip()
        if not re.fullmatch(r"\d{6,12}", trading_id):
            await q.message.reply_text(
                "⚠️ Ya no encuentro un ID pendiente. Envíamelo nuevamente en texto." if lang == "es" else
                "⚠️ I can no longer find a pending ID. Please send it again as text.",
            )
            return

        _broker_seed_legacy_if_matching(chat_id, broker, trading_id)
        try:
            with Session() as session:
                row = session.get(BrokerAccountState, _broker_key(chat_id, broker))
                if not row:
                    row = BrokerAccountState(account_key=_broker_key(chat_id, broker), telegram_id=str(chat_id), broker=broker)
                    session.add(row)
                already_valid = (row.trading_id or "").strip() == trading_id and bool(row.id_validated)
                row.pending_trading_id = None if already_valid else trading_id
                row.updated_at = utcnow_naive(); session.commit()
            _broker_flow_set(chat_id, pending_trading_id="")
            _log_event(chat_id, "ID_SUBMITTED", f"BROKER={broker} | ID={trading_id}")
            _tracking_fire_event(chat_id, "ID_SUBMITTED", f"BROKER={broker} | ID={trading_id}")
        except Exception:
            logging.exception("No pude guardar ID por broker")
            await q.message.reply_text("⚠️ No pude guardar ese ID. Intenta nuevamente.")
            return

        if already_valid:
            msg = (
                f"✅ Ese ID de {_broker_label(broker)} ya estaba validado. Puedes continuar con tu depósito o enviar el comprobante si ya lo realizaste."
                if lang == "es" else
                f"✅ That {_broker_label(broker)} ID was already validated. You can continue with your deposit or send the proof if you already made it."
            )
            await q.message.reply_text(msg)
            return

        before_noon = datetime.now(COLOMBIA_TZ).hour < 12
        if lang == "en":
            msg = f"✅ Your {_broker_label(broker)} ID has been received and is pending validation."
            if before_noon:
                msg += "\n\nID validations begin at 12:00 p.m. Colombia time. You will receive confirmation here once reviewed."
        else:
            msg = f"✅ Tu ID de {_broker_label(broker)} fue recibido y quedó pendiente de validación."
            if before_noon:
                msg += "\n\nLas validaciones de ID se realizan a partir de las 12:00 p. m. hora Colombia. Recibirás la confirmación por este chat cuando sea revisado."
        await q.message.reply_text(msg)
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🆔 ID PENDIENTE · {_broker_label(broker).upper()}\nUsuario: {_telegram_display_name(update.effective_user)} (ID: {chat_id})\nID trading: {trading_id}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ VALIDAR ID", callback_data=f"admin_broker_validate:{chat_id}:{broker}")],
                    [InlineKeyboardButton("❌ ID ERRADO", callback_data=f"admin_broker_reject:{chat_id}:{broker}")],
                    [InlineKeyboardButton("👤 GESTIONAR", callback_data=f"admin_user_open:{chat_id}")],
                ]),
            )
        except Exception as e:
            logging.warning("No pude avisar ID broker al admin: %s", e)
        return

    m = re.fullmatch(r"broker_deposit_select:(BINOMO|STOCKITY)", data)
    if m:
        broker = m.group(1)
        state = _broker_get(chat_id, broker, create=False)
        if not state or not state.get("id_validated"):
            _broker_bind_legacy_validated_id(chat_id, broker)
            state = _broker_get(chat_id, broker, create=False)
        if not state or not state.get("id_validated"):
            await q.message.reply_text(
                f"⚠️ Primero necesito tener validado tu ID de {_broker_label(broker)}. Envíame el ID en texto y selecciónalo como {_broker_label(broker)} antes de revisar este depósito." if lang == "es" else
                f"⚠️ I first need your {_broker_label(broker)} ID to be validated. Send the ID as text and select {_broker_label(broker)} before this deposit is reviewed.",
            )
            return
        _broker_flow_set(chat_id, pending_deposit_broker=broker)
        msg = (
            f"✅ Perfecto. Dejé este depósito identificado como {_broker_label(broker)}. Lo revisaré y te confirmaré por aquí."
            if lang == "es" else
            f"✅ Perfect. I marked this deposit as {_broker_label(broker)}. I will review it and confirm here."
        )
        await q.message.reply_text(msg)
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"💰 DEPÓSITO IDENTIFICADO · {_broker_label(broker).upper()}\nUsuario: {_telegram_display_name(update.effective_user)} (ID: {chat_id})",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                    f"💰 REVISAR DEPÓSITO · {_broker_label(broker).upper()}",
                    callback_data=f"admin_broker_deposit:{chat_id}:{broker}",
                )]]),
            )
        except Exception:
            pass
        return


async def _admin_finalize_broker_id(context: ContextTypes.DEFAULT_TYPE, chat_id: int, broker: str):
    broker = _broker_norm(broker)
    state = _broker_get(chat_id, broker, create=False)
    pending_id = (state or {}).get("pending_trading_id") or ""
    if not re.fullmatch(r"\d{6,12}", pending_id):
        return False, "⚠️ Ya no encuentro un ID pendiente válido para ese broker."
    previous_validated = [b for b in _broker_validated_brokers(chat_id) if b != broker]
    try:
        with Session() as session:
            row = session.get(BrokerAccountState, _broker_key(chat_id, broker))
            row.trading_id = pending_id
            row.pending_trading_id = None
            row.id_validated = 1
            row.updated_at = utcnow_naive()
            u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
            if u:
                u.binomo_id = pending_id  # compatibilidad histórica: último ID validado
            session.commit()
        _log_event(chat_id, "ID_VALIDATED", f"BROKER={broker} | ID={pending_id}")
        _tracking_fire_event(chat_id, "ID_VALIDATED", f"BROKER={broker} | ID={pending_id}")
        stage = get_user_stage(chat_id)
        if stage == STAGE_PRE:
            set_user_stage(chat_id, STAGE_POST)
            _cancel_jobs_prefix(context, "A", chat_id)
            schedule_series_b(chat_id, context)
        elif stage == STAGE_POST and not previous_validated:
            _cancel_jobs_prefix(context, "A", chat_id)
            schedule_series_b(chat_id, context)
        lang = get_user_lang(chat_id)
        if lang == "en":
            msg = f"✅ Your {_broker_label(broker)} ID has been successfully validated.\n\nYou can now make the deposit in that same trading account. When done, send me the proof here."
        else:
            msg = f"✅ Tu ID de {_broker_label(broker)} fue validado correctamente.\n\nYa puedes realizar el depósito en esa misma cuenta de trading. Cuando lo hagas, envíame aquí el comprobante."
        await context.bot.send_message(chat_id=chat_id, text=msg)
        return True, f"✅ ID {_broker_label(broker)} validado: {pending_id}."
    except Exception as e:
        logging.exception("Error validando ID por broker")
        return False, f"❌ No pude validar el ID: {e}"


async def _admin_reject_broker_id(context: ContextTypes.DEFAULT_TYPE, chat_id: int, broker: str):
    broker = _broker_norm(broker)
    state = _broker_get(chat_id, broker, create=False)
    pending_id = (state or {}).get("pending_trading_id") or ""
    if not pending_id:
        return False, "⚠️ Ya no encuentro un ID pendiente para rechazar."
    try:
        with Session() as session:
            row = session.get(BrokerAccountState, _broker_key(chat_id, broker))
            row.pending_trading_id = None; row.updated_at = utcnow_naive(); session.commit()
        _log_event(chat_id, "ID_REJECTED", f"BROKER={broker} | ID={pending_id}")
        _tracking_fire_event(chat_id, "ID_REJECTED", f"BROKER={broker} | ID={pending_id}")
        stage = get_user_stage(chat_id)
        if stage != STAGE_DEPOSITED and not _broker_validated_brokers(chat_id):
            if stage == STAGE_POST:
                set_user_stage(chat_id, STAGE_PRE)
                _cancel_jobs_prefix(context, "B", chat_id)
                schedule_series_a(chat_id, get_user_lang(chat_id), context)
            else:
                set_user_stage(chat_id, STAGE_PRE)
                _cancel_jobs_prefix(context, "B", chat_id)
                _sync_menu_campaign_for_stage(chat_id, get_user_lang(chat_id), context)
        lang = get_user_lang(chat_id)
        template = GATILLO_ID_ERRADO if lang == "es" else GATILLO_ID_ERRADO_EN
        await context.bot.send_message(chat_id=chat_id, text=_personalize_referral_links(template, chat_id), disable_web_page_preview=True)
        return True, f"❌ ID {_broker_label(broker)} rechazado."
    except Exception as e:
        logging.exception("Error rechazando ID por broker")
        return False, f"❌ No pude rechazar el ID: {e}"


def _broker_deposit_preview_text(chat_id: int, broker: str, preview: dict) -> str:
    old_global = (_vip_get_state(chat_id, create=False) or {}).get("level") or VIP_LEVEL_NONE
    proposed_global = _max_level(old_global, preview.get("new_level") or VIP_LEVEL_NONE)
    mode = "ACUMULA dentro de la ventana" if preview.get("accumulates") else "NO acumula; se evalúa como depósito único"
    status = "CERRADA después de este depósito" if preview.get("window_closed_after") else "ABIERTA"
    return (
        f"💰 CONFIRMAR DEPÓSITO · {_broker_label(broker).upper()}\n\n"
        f"Monto: USD {_usd(preview['amount_cents'])}\n"
        f"Comprobante ≤72h: {'SÍ' if preview['timely'] else 'NO'}\n"
        f"Depósito validado nº: {preview['new_count']}\n"
        f"Regla: {mode}\n"
        f"Acumulación habilitada: USD {_usd(preview['new_accum_cents'])}\n"
        f"Ventana 3 depósitos / 30 días: {status}\n\n"
        f"Nivel en {_broker_label(broker)}: {_vip_level_label(preview['new_level'], 'es')}\n"
        f"Nivel general JT TRADERS: {_vip_level_label(proposed_global, 'es')}\n\n"
        "¿Confirmas este depósito validado?"
    )


async def _admin_apply_broker_deposit(context: ContextTypes.DEFAULT_TYPE, chat_id: int, broker: str, preview: dict):
    broker = _broker_norm(broker)
    current = _broker_get(chat_id, broker, create=False)
    if not current or not current.get("id_validated"):
        return False, "⚠️ Ese broker no tiene un ID validado."
    if int(current.get("deposit_count") or 0) != int(preview.get("old_count") or 0):
        return False, "⚠️ El contador de depósitos cambió. Revisa nuevamente para evitar duplicados."

    old_global_state = _vip_get_state(chat_id, create=False) or {}
    old_global = old_global_state.get("level") or VIP_LEVEL_NONE
    try:
        with Session() as session:
            row = session.get(BrokerAccountState, _broker_key(chat_id, broker))
            row.validated_total_cents = int(preview["new_validated_total_cents"])
            row.upgrade_accum_cents = int(preview["new_accum_cents"])
            row.validated_deposit_count = int(preview["new_count"])
            row.first_validated_deposit_at = preview.get("first_after") or utcnow_naive()
            row.level = preview.get("new_level") or VIP_LEVEL_NONE
            row.updated_at = utcnow_naive(); session.commit()
    except Exception as e:
        logging.exception("No pude aplicar depósito broker")
        return False, f"❌ No pude guardar el depósito: {e}"

    new_global = old_global
    for state in _broker_rows(chat_id):
        new_global = _max_level(new_global, state.get("level") or VIP_LEVEL_NONE)
    rank_up = VIP_LEVEL_RANK.get(new_global, 0) > VIP_LEVEL_RANK.get(old_global, 0)
    new_keys = _vip_new_channel_keys(old_global, new_global) if rank_up else []
    existing_pending = list(old_global_state.get("pending_keys") or [])
    pending_keys = list(dict.fromkeys(existing_pending + new_keys)) if new_keys else existing_pending
    global_total = max(
        int(old_global_state.get("total_cents") or 0),
        int(preview.get("new_accum_cents") or 0),
        VIP_LEVEL_THRESHOLDS_CENTS.get(new_global, 0),
    )
    _vip_set_state(chat_id, global_total, new_global, pending_keys=pending_keys)

    first_activation = old_global == VIP_LEVEL_NONE and new_global != VIP_LEVEL_NONE
    if first_activation:
        set_user_stage(chat_id, STAGE_DEPOSITED)
        _log_event(chat_id, "ACCOUNT_ACTIVATED", f"BROKER={broker} | LEVEL={new_global}")
        _tracking_fire_event(chat_id, "ACCOUNT_ACTIVATED", f"BROKER={broker} | LEVEL={new_global}")
    elif rank_up:
        _log_event(chat_id, "VIP_LEVEL_UPGRADED", f"BROKER={broker} | {old_global}->{new_global}")
        _tracking_fire_event(chat_id, "VIP_LEVEL_UPGRADED", f"BROKER={broker} | {old_global}->{new_global}")

    detail = f"BROKER={broker} | USD={preview['amount_cents']/100:.2f} | COUNT={preview['new_count']} | TIMELY={preview['timely']} | LEVEL={preview['new_level']}"
    _log_event(chat_id, "DEPOSIT_VALIDATED", detail)
    _tracking_fire_event(chat_id, "DEPOSIT_VALIDATED", detail)
    _broker_flow_set(chat_id, pending_deposit_broker="")

    if new_global != VIP_LEVEL_NONE:
        _cancel_jobs_prefix(context, "A", chat_id)
        _cancel_jobs_prefix(context, "B", chat_id)

    lang = get_user_lang(chat_id)
    broker_level = preview.get("new_level") or VIP_LEVEL_NONE
    if lang == "en":
        user_msg = (
            f"✅ {_broker_label(broker)} deposit confirmed.\n\n"
            f"Your current level on {_broker_label(broker)} is {_vip_level_label(broker_level, lang)}.\n"
            f"Your current JT TRADERS level is {_vip_level_label(new_global, lang)}.\n\n"
            "Upgrades are calculated from validated deposits within the enabled level-update period."
        )
    else:
        user_msg = (
            f"✅ Depósito de {_broker_label(broker)} confirmado.\n\n"
            f"Tu nivel actual en {_broker_label(broker)} es {_vip_level_label(broker_level, lang)}.\n"
            f"Tu nivel actual JT TRADERS es {_vip_level_label(new_global, lang)}.\n\n"
            "Los upgrades se calculan según depósitos validados dentro del periodo habilitado para actualización de nivel."
        )
    await context.bot.send_message(chat_id=chat_id, text=user_msg, reply_markup=upgrade_info_keyboard(lang))

    if new_keys:
        await context.bot.send_message(
            chat_id=chat_id,
            text=_vip_access_intro(new_global, lang, upgrade=(old_global != VIP_LEVEL_NONE)),
            reply_markup=_vip_access_keyboard(new_global, lang, keys=new_keys),
            disable_web_page_preview=True,
        )

    admin_result = (
        f"✅ DEPÓSITO CONFIRMADO · {_broker_label(broker).upper()}\n\n"
        f"💰 Monto validado: USD {_usd(preview['amount_cents'])}\n"
        f"👑 Nivel en {_broker_label(broker)}: {_vip_level_label(broker_level, 'es')}\n"
        f"⭐ Nivel general JT TRADERS: {_vip_level_label(new_global, 'es')}"
    )
    if new_keys:
        admin_result += (
            f"\n\n🔓 Habilitando {len(new_keys)} acceso(s) correspondiente(s) "
            f"al nivel {_vip_level_label(new_global, 'es')}."
        )
    else:
        admin_result += "\n\nℹ️ No hay accesos nuevos que habilitar; el nivel general se mantiene."
    return True, admin_result


async def _start_admin_broker_deposit(context: ContextTypes.DEFAULT_TYPE, chat_id: int, broker: str):
    broker = _broker_norm(broker)
    state = _broker_get(chat_id, broker, create=False)
    if not state or not state.get("id_validated"):
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ {_broker_label(broker)} no tiene un ID validado para este usuario.")
        return
    context.user_data["admin_user_action"] = {"action": "broker_deposit_amount", "chat_id": chat_id, "broker": broker}
    context.user_data.pop("admin_pending_broker_deposit", None)
    window = "ABIERTA" if _broker_upgrade_window_open(state) else "CERRADA"
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"💰 REVISAR DEPÓSITO · {_broker_label(broker).upper()}\n\n"
            f"Nivel actual de esta cuenta: {_vip_level_label(state.get('level'), 'es')}\n"
            f"Depósitos validados: {state.get('deposit_count', 0)}\n"
            f"Ventana acumulable: {window}\n\n"
            "Escribe el monto NUEVO que acabas de confirmar en USD."
        ),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ CANCELAR", callback_data=f"admin_user_open:{chat_id}")]]),
    )


async def admin_broker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    await q.answer()
    data = q.data or ""
    m = re.fullmatch(r"admin_broker_(validate|validate_confirm|reject|reject_confirm|deposit|timely|late|deposit_confirm):(\d+):(BINOMO|STOCKITY)", data)
    if not m:
        return
    action, raw_id, broker = m.groups()
    chat_id = int(raw_id)
    state = _broker_get(chat_id, broker, create=False)

    if action in ("validate", "reject"):
        pending_id = (state or {}).get("pending_trading_id") or ""
        if not pending_id:
            await context.bot.send_message(chat_id=ADMIN_ID, text="⚠️ Ya no hay un ID pendiente para esa cuenta.")
            return
        if action == "validate":
            text_value = f"✅ VALIDAR ID · {_broker_label(broker).upper()}\n\nID: {pending_id}\n¿Confirmas que ya verificaste que quedó correctamente vinculado?"
            callback = f"admin_broker_validate_confirm:{chat_id}:{broker}"
            button = "✅ SÍ, VALIDAR"
        else:
            text_value = f"❌ ID ERRADO · {_broker_label(broker).upper()}\n\nID: {pending_id}\n¿Confirmas el rechazo?"
            callback = f"admin_broker_reject_confirm:{chat_id}:{broker}"
            button = "❌ SÍ, ID ERRADO"
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=text_value,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(button, callback_data=callback)],
                [InlineKeyboardButton("↩️ CANCELAR", callback_data=f"admin_user_open:{chat_id}")],
            ]),
        )
        return

    if action == "validate_confirm":
        _, msg = await _admin_finalize_broker_id(context, chat_id, broker)
        await _show_admin_user(context, chat_id, msg)
        return
    if action == "reject_confirm":
        _, msg = await _admin_reject_broker_id(context, chat_id, broker)
        await _show_admin_user(context, chat_id, msg)
        return
    if action == "deposit":
        await _start_admin_broker_deposit(context, chat_id, broker)
        return

    if action in ("timely", "late"):
        pending = context.user_data.get("admin_pending_broker_deposit") or {}
        if int(pending.get("chat_id") or 0) != chat_id or pending.get("broker") != broker:
            await context.bot.send_message(chat_id=ADMIN_ID, text="⚠️ Esa revisión ya no está activa. Pulsa REVISAR DEPÓSITO nuevamente.")
            return
        timely = action == "timely"
        preview = _broker_preview_deposit(chat_id, broker, int(pending["amount_cents"]), timely)
        pending.update({"timely": timely, "preview": preview})
        context.user_data["admin_pending_broker_deposit"] = pending
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=_broker_deposit_preview_text(chat_id, broker, preview),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ CONFIRMAR DEPÓSITO", callback_data=f"admin_broker_deposit_confirm:{chat_id}:{broker}")],
                [InlineKeyboardButton("❌ CANCELAR", callback_data=f"admin_user_open:{chat_id}")],
            ]),
        )
        return

    if action == "deposit_confirm":
        pending = context.user_data.get("admin_pending_broker_deposit") or {}
        if int(pending.get("chat_id") or 0) != chat_id or pending.get("broker") != broker or not pending.get("preview"):
            await context.bot.send_message(chat_id=ADMIN_ID, text="⚠️ Esa confirmación ya no está activa.")
            return
        context.user_data.pop("admin_pending_broker_deposit", None)
        context.user_data.pop("admin_user_action", None)
        _, msg = await _admin_apply_broker_deposit(context, chat_id, broker, pending["preview"])
        # Mantiene limpio el chat administrativo: reutiliza la misma tarjeta de
        # confirmación y NO despliega la lista de pendientes.
        # IMPORTANTE: este callback usa `q`; `query` no existe en esta función.
        await _safe_edit_callback_message(q, msg)
        return


async def admin_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    data = query.data or ""

    if data in ("admin_user_list", "admin_user_panel"):
        context.user_data.pop("admin_user_lookup_mode", None)
        context.user_data.pop("admin_user_action", None)
        context.user_data.pop("admin_pending_deposit", None)
        if data == "admin_user_panel":
            await context.bot.send_message(chat_id=ADMIN_ID, text="🔐 PANEL ADMINISTRADOR\n\nElige una opción:", reply_markup=admin_panel_keyboard())
        else:
            await _show_admin_user_list(context)
        return

    if data == "admin_user_search":
        context.user_data["admin_user_lookup_mode"] = True
        context.user_data.pop("admin_user_action", None)
        context.user_data.pop("admin_pending_deposit", None)
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="🔎 Escribe el nombre visible de Telegram o el Telegram ID de la persona.",
        )
        return

    page_match = re.fullmatch(r"admin_user_list_page:(\d+)", data)
    if page_match:
        await _show_admin_user_list(context, page=int(page_match.group(1)))
        return

    m = re.fullmatch(r"admin_user_(open|validate|validate_confirm|reject|reject_confirm|deposit|deposit_confirm|activate|activate_confirm):(\d+)", data)
    if not m:
        return
    action, raw_id = m.groups()
    chat_id = int(raw_id)
    if not _is_private_user_id(chat_id):
        await context.bot.send_message(chat_id=ADMIN_ID, text="🛡️ Acción bloqueada: el destino no es un usuario privado.")
        return

    if action == "open":
        context.user_data.pop("admin_user_lookup_mode", None)
        context.user_data.pop("admin_user_action", None)
        context.user_data.pop("admin_pending_deposit", None)
        await _show_admin_user(context, chat_id)
        return

    if action == "validate":
        record = _admin_user_record(chat_id)
        if not record:
            await context.bot.send_message(chat_id=ADMIN_ID, text="⚠️ No encontré ese usuario.")
            return
        if record["stage"] == STAGE_DEPOSITED:
            await _show_admin_user(context, chat_id, "ℹ️ Esta persona ya tiene la cuenta activa.")
            return
        if record["stage"] == STAGE_POST and _strict_validated_id_state(chat_id):
            await _show_admin_user(context, chat_id, "ℹ️ El ID ya está validado. No se reinició la Serie B.")
            return
        saved_id = record["trading_id"]
        if saved_id:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"✅ VALIDAR ID — {record['nombre']}\n\n"
                    f"ID guardado: {saved_id}\n\n"
                    "¿Confirmas que este es el ID correcto que ya validaste?"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ SÍ, VALIDAR", callback_data=f"admin_user_validate_confirm:{chat_id}")],
                    [InlineKeyboardButton("❌ CANCELAR", callback_data=f"admin_user_open:{chat_id}")],
                ]),
            )
        else:
            context.user_data["admin_user_action"] = {"action": "validate_id", "chat_id": chat_id}
            context.user_data.pop("admin_user_lookup_mode", None)
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"✅ VALIDAR ID — {record['nombre']}\n\nEscribe ahora el ID de trading que ya verificaste (solo números).",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ CANCELAR", callback_data=f"admin_user_open:{chat_id}")
                ]]),
            )
        return

    if action == "validate_confirm":
        record = _admin_user_record(chat_id)
        if not record or not record["trading_id"]:
            await _show_admin_user(context, chat_id, "⚠️ Ya no encuentro un ID guardado para validar.")
            return
        ok, msg = await _admin_finalize_id_validation(context, chat_id, record["trading_id"])
        await _show_admin_user(context, chat_id, msg)
        return

    if action == "reject":
        record = _admin_user_record(chat_id)
        if not record:
            await context.bot.send_message(chat_id=ADMIN_ID, text="⚠️ No encontré ese usuario.")
            return
        if record["stage"] == STAGE_DEPOSITED:
            await _show_admin_user(context, chat_id, "⚠️ Esa cuenta ya está activa. No marqué el ID como errado.")
            return
        saved_id = (record.get("trading_id") or "").strip()
        if not saved_id:
            await _show_admin_user(context, chat_id, "⚠️ No encuentro un ID enviado para marcar como errado.")
            return
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"❌ ID ERRADO — {record['nombre']}\n\n"
                f"ID recibido: {saved_id}\n"
                "Se enviará automáticamente el mensaje de corrección al usuario, se limpiará ese ID y permanecerá en PRE.\n"
                "Serie B quedará cancelada y Serie A continuará correctamente.\n\n"
                "¿Confirmas?"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ SÍ, ID ERRADO", callback_data=f"admin_user_reject_confirm:{chat_id}")],
                [InlineKeyboardButton("↩️ CANCELAR", callback_data=f"admin_user_open:{chat_id}")],
            ]),
        )
        return

    if action == "reject_confirm":
        ok, msg = await _admin_reject_trading_id(context, chat_id, notify_user=True)
        await _show_admin_user(context, chat_id, msg)
        return

    if action in ("deposit", "activate"):
        record = _admin_user_record(chat_id)
        if not record:
            await context.bot.send_message(chat_id=ADMIN_ID, text="⚠️ No encontré ese usuario.")
            return

        validated_brokers = _broker_validated_brokers(chat_id)
        if validated_brokers:
            preferred = _broker_flow_get(chat_id).get("pending_deposit_broker")
            if preferred in validated_brokers:
                await _start_admin_broker_deposit(context, chat_id, preferred)
                return
            if len(validated_brokers) == 1:
                await _start_admin_broker_deposit(context, chat_id, validated_brokers[0])
                return
            rows = [[InlineKeyboardButton(
                f"💰 {_broker_label(b).upper()}", callback_data=f"admin_broker_deposit:{chat_id}:{b}"
            )] for b in validated_brokers]
            rows.append([InlineKeyboardButton("❌ CANCELAR", callback_data=f"admin_user_open:{chat_id}")])
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text="💰 ¿A qué broker corresponde este depósito? El usuario tiene más de una cuenta validada.",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return

        current_stage, _ = _repair_inconsistent_stage(chat_id)
        if current_stage in (STAGE_POST, STAGE_DEPOSITED):
            preferred = _broker_flow_get(chat_id).get("pending_deposit_broker")
            # Para usuarios v7.10.21/22 con estado VIP existente, no permitimos
            # volver al acumulador global: primero se identifica/migra el broker.
            if _vip_get_state(chat_id, create=False):
                if preferred in BROKERS and _broker_bind_legacy_validated_id(chat_id, preferred):
                    await _start_admin_broker_deposit(context, chat_id, preferred)
                    return
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        "🏦 Este usuario todavía no tiene el broker asociado a su cuenta anterior. "
                        "Pídele seleccionar BINOMO/STOCKITY en el mensaje del comprobante o reenviar su ID. "
                        "No sumé ningún depósito para evitar mezclar brokers."
                    ),
                    reply_markup=admin_user_quick_keyboard(chat_id),
                )
                return
        if current_stage not in (STAGE_POST, STAGE_DEPOSITED):
            await _show_admin_user(context, chat_id, "⚠️ Primero debes VALIDAR ID antes de revisar un depósito.")
            return
        if current_stage == STAGE_POST and not _strict_validated_id_state(chat_id):
            await _show_admin_user(context, chat_id, "⚠️ El ID todavía no tiene validación estricta. Primero usa VALIDAR ID.")
            return

        vip_state = _vip_get_state(chat_id, create=False)
        mode = "set_total_existing" if (current_stage == STAGE_DEPOSITED and not vip_state) else "add"
        previous_total = int((vip_state or {}).get("total_cents") or 0)
        context.user_data["admin_user_action"] = {
            "action": "deposit_amount",
            "chat_id": chat_id,
            "mode": mode,
            "previous_total_cents": previous_total,
        }
        context.user_data.pop("admin_user_lookup_mode", None)
        context.user_data.pop("admin_pending_deposit", None)
        if mode == "set_total_existing":
            instruction = (
                "⚠️ Esta cuenta ya estaba activa antes del nuevo control por niveles.\n"
                "Escribe el TOTAL validado actual en USD (incluyendo cualquier depósito nuevo), no solo la última recarga."
            )
        elif previous_total:
            instruction = (
                f"Total validado acumulado hasta ahora: USD {_usd(previous_total)}.\n"
                "Escribe únicamente el monto NUEVO que acabas de confirmar en USD."
            )
        else:
            instruction = "Escribe el monto del depósito que acabas de confirmar en USD."
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"💰 REVISAR DEPÓSITO — {record['nombre']}\n\n{instruction}\n\nEjemplos: 50 · 100 · 200 · 49.50",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ CANCELAR", callback_data=f"admin_user_open:{chat_id}")]]),
        )
        return

    if action == "deposit_confirm":
        pending_deposit = context.user_data.get("admin_pending_deposit") or {}
        if int(pending_deposit.get("chat_id") or 0) != chat_id:
            await _show_admin_user(context, chat_id, "⚠️ Esa confirmación de depósito ya no está activa. Pulsa REVISAR DEPÓSITO nuevamente.")
            return
        context.user_data.pop("admin_pending_deposit", None)
        context.user_data.pop("admin_user_action", None)
        ok, msg = await _admin_apply_deposit_confirmation(context, chat_id, pending_deposit)
        # Compatibilidad con el flujo anterior: muestra únicamente el resultado de
        # la validación en el mismo mensaje. La lista de pendientes se abre SOLO
        # desde Gestión de Usuarios / el menú de administrador.
        await _safe_edit_callback_message(query, msg)
        return

    if action == "activate_confirm":
        # Compatibilidad segura con un botón de confirmación antiguo que pudiera seguir visible.
        await _show_admin_user(
            context, chat_id,
            "ℹ️ La activación directa fue reemplazada por el control de monto. Usa 💰 REVISAR DEPÓSITO para calcular el nivel correctamente.",
        )
        return


async def admin_user_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa búsqueda/ID manual solo cuando el panel de gestión lo está esperando."""
    if not update.effective_user or update.effective_user.id != ADMIN_ID or not update.effective_message:
        return
    if update.effective_message.reply_to_message:
        return
    raw = (update.effective_message.text or "").strip()
    if not raw:
        return

    pending = context.user_data.get("admin_user_action") or {}
    if pending.get("action") == "broker_deposit_amount":
        chat_id = int(pending.get("chat_id"))
        broker = _broker_norm(pending.get("broker"))
        amount_cents = _parse_usd_to_cents(raw)
        if amount_cents is None:
            await update.effective_message.reply_text("⚠️ Escribe solo el monto en USD. Ejemplos: 50 · 100 · 200 · 49.50")
            from telegram.ext import ApplicationHandlerStop
            raise ApplicationHandlerStop
        context.user_data.pop("admin_user_action", None)
        context.user_data["admin_pending_broker_deposit"] = {
            "chat_id": chat_id, "broker": broker, "amount_cents": amount_cents
        }
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🕒 COMPROBANTE · {_broker_label(broker).upper()}\n\n"
                f"Monto confirmado: USD {_usd(amount_cents)}\n\n"
                "¿El usuario envió este comprobante dentro de las 72 horas posteriores al depósito?"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ SÍ · DENTRO DE 72H", callback_data=f"admin_broker_timely:{chat_id}:{broker}")],
                [InlineKeyboardButton("⏰ NO · FUERA DE 72H", callback_data=f"admin_broker_late:{chat_id}:{broker}")],
                [InlineKeyboardButton("❌ CANCELAR", callback_data=f"admin_user_open:{chat_id}")],
            ]),
        )
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop

    if pending.get("action") == "deposit_amount":
        chat_id = int(pending.get("chat_id"))
        amount_cents = _parse_usd_to_cents(raw)
        if amount_cents is None:
            await update.effective_message.reply_text("⚠️ Escribe solo el monto en USD. Ejemplos: 50 · 100 · 200 · 49.50")
            from telegram.ext import ApplicationHandlerStop
            raise ApplicationHandlerStop

        mode = pending.get("mode") or "add"
        previous_total = int(pending.get("previous_total_cents") or 0)
        if mode == "set_total_existing":
            new_total = amount_cents
            old_level = VIP_LEVEL_NONE
        else:
            state = _vip_get_state(chat_id, create=False)
            current_total = int((state or {}).get("total_cents") or 0)
            if current_total != previous_total:
                context.user_data.pop("admin_user_action", None)
                await update.effective_message.reply_text(
                    "⚠️ El total del usuario cambió mientras escribías. Vuelve a pulsar REVISAR DEPÓSITO para evitar duplicar valores."
                )
                from telegram.ext import ApplicationHandlerStop
                raise ApplicationHandlerStop
            new_total = previous_total + amount_cents
            old_level = (state or {}).get("level") or VIP_LEVEL_NONE

        new_level = _vip_level_for_total_cents(new_total)
        next_level, missing = _vip_next_level(new_level, new_total)
        if new_level == VIP_LEVEL_NONE:
            result_line = f"🚫 Aún sin acceso. Faltan USD {_usd(missing)} para Básico."
        else:
            result_line = f"🎯 Nivel resultante: {_vip_level_label(new_level, 'es')}"
            if next_level:
                result_line += f"\n➡️ Faltan USD {_usd(missing)} para {_vip_level_label(next_level, 'es')}."
            else:
                result_line += "\n🏆 Nivel máximo alcanzado."

        pending_deposit = {
            "chat_id": chat_id,
            "mode": mode,
            "amount_cents": amount_cents,
            "previous_total_cents": previous_total,
            "preview_total_cents": new_total,
            "old_level": old_level,
            "new_level": new_level,
        }
        context.user_data["admin_pending_deposit"] = pending_deposit
        context.user_data.pop("admin_user_action", None)
        label = "TOTAL ACTUAL" if mode == "set_total_existing" else "NUEVO DEPÓSITO"
        previous_line = (
            "Migración de cuenta activa anterior"
            if mode == "set_total_existing"
            else f"Total anterior: USD {_usd(previous_total)}"
        )
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"💰 CONFIRMAR DEPÓSITO\n\n"
                f"{label}: USD {_usd(amount_cents)}\n"
                f"{previous_line}\n"
                f"Total validado después: USD {_usd(new_total)}\n\n"
                f"{result_line}\n\n"
                "¿Confirmas este cálculo?"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ CONFIRMAR DEPÓSITO", callback_data=f"admin_user_deposit_confirm:{chat_id}")],
                [InlineKeyboardButton("❌ CANCELAR", callback_data=f"admin_user_open:{chat_id}")],
            ]),
        )
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop

    if pending.get("action") == "validate_id":
        chat_id = int(pending.get("chat_id"))
        trading_id = re.sub(r"\D", "", raw)
        if not re.fullmatch(r"\d{6,12}", trading_id):
            await update.effective_message.reply_text("⚠️ Envíame únicamente el ID de trading (6 a 12 números).")
            from telegram.ext import ApplicationHandlerStop
            raise ApplicationHandlerStop
        context.user_data.pop("admin_user_action", None)
        ok, msg = await _admin_finalize_id_validation(context, chat_id, trading_id)
        await _show_admin_user(context, chat_id, msg)
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop

    if context.user_data.get("admin_user_lookup_mode"):
        context.user_data.pop("admin_user_lookup_mode", None)
        query_text = raw.lstrip("@").strip()
        rows = []
        try:
            with Session() as session:
                q = session.query(Usuario)
                if re.fullmatch(r"\d+", query_text):
                    q = q.filter(Usuario.telegram_id == query_text)
                else:
                    q = q.filter(Usuario.nombre.ilike(f"%{query_text}%"))
                users = q.order_by(Usuario.fecha_registro.desc()).limit(12).all()
            for u in users:
                try:
                    cid = int(u.telegram_id)
                except Exception:
                    continue
                if cid == ADMIN_ID or not _is_private_user_id(cid):
                    continue
                rows.append((cid, u.nombre or f"Usuario {cid}", u.stage or STAGE_PRE, u.binomo_id or "", None))
        except Exception as e:
            logging.warning("No pude buscar usuario desde panel admin: %s", e)

        if rows:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🔎 Resultados para: {raw}",
                reply_markup=_admin_user_list_keyboard(rows),
            )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ No encontré coincidencias para: {raw}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔎 INTENTAR OTRA VEZ", callback_data="admin_user_search")]]),
            )
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop


async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    # Abrir el panel cancela únicamente una búsqueda/entrada manual pendiente de gestión.
    context.user_data.pop("admin_user_lookup_mode", None)
    context.user_data.pop("admin_user_action", None)
    context.user_data.pop("admin_pending_deposit", None)
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
        await context.bot.send_message(chat_id=ADMIN_ID, text=await _daily_report_with_affiliate())
    elif query.data == "admin_panel_users":
        await _show_admin_user_list(context)
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


def _is_blocked_user_error(exc) -> bool:
    """Detecta respuestas de Telegram que indican que el usuario ya no puede recibir mensajes."""
    msg = str(exc or "").lower()
    return (
        "bot was blocked by the user" in msg
        or "user is deactivated" in msg
    )


def _cleanup_blocked_user_tasks(context: ContextTypes.DEFAULT_TYPE, chat_id: int, source: str = ""):
    """Elimina tareas pendientes de un usuario que bloqueó el bot, sin borrar su historial."""
    if not _is_private_user_id(chat_id):
        return

    # Jobs en memoria: campañas A/B + IA pendiente.
    if context and context.job_queue:
        names = [f"AI_REPLY_{chat_id}"]
        names.extend(
            f"{prefix}_{step}_{chat_id}"
            for prefix in ("A", "B")
            for step in ("1h", "3h", "24h", "48h")
        )
        for name in names:
            try:
                for job in context.job_queue.get_jobs_by_name(name):
                    job.schedule_removal()
            except Exception:
                pass

    removed_campaigns = 0
    cleared_ai = False
    removed_activity = False
    try:
        with Session() as session:
            removed_campaigns = (
                session.query(CampaignJob)
                .filter(
                    CampaignJob.telegram_id == str(chat_id),
                    CampaignJob.sent_at.is_(None),
                )
                .delete(synchronize_session=False)
            )

            user = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
            if user and (user.ai_pending_text or user.ai_pending_message_id or user.ai_pending_due_at):
                user.ai_pending_text = None
                user.ai_pending_message_id = None
                user.ai_pending_due_at = None
                cleared_ai = True

            activity = session.get(UserActivity, str(chat_id))
            if activity:
                session.delete(activity)
                removed_activity = True

            session.commit()
    except Exception as e:
        logging.warning("No pude limpiar tareas del usuario bloqueado %s: %s", chat_id, e)
        return

    _log_event(chat_id, "BOT_BLOCKED", source or "telegram_forbidden")
    logging.info(
        "🧹 Usuario bloqueó el bot: %s | campañas pendientes eliminadas=%s | IA limpiada=%s | actividad removida=%s | origen=%s",
        chat_id, removed_campaigns, cleared_ai, removed_activity, source or "telegram_forbidden",
    )


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

    # Si el usuario ya envió un ID real y aún está pendiente de revisión, la Serie A
    # queda PAUSADA para no enviar recordatorios de registro contradictorios.
    # No borramos la campaña: cada job vencido se desplaza 1 hora y se reanuda
    # automáticamente si el ID es rechazado; si se valida, la Serie A se cancela.
    if series == "A" and _has_pending_id_review(chat_id):
        next_due = utcnow_naive() + timedelta(hours=1)
        try:
            with Session() as session:
                paused = session.get(CampaignJob, int(job_id))
                if paused and paused.sent_at is None:
                    paused.due_at = next_due
                    session.commit()
            _schedule_persistent_campaign_record(
                context.job_queue,
                {"id": int(job_id), "chat_id": chat_id, "series": series, "step": step, "lang": lang, "due_at": next_due},
            )
            logging.info("⏸️ Serie A pausada por ID pendiente: %s / %s", chat_id, step)
        except Exception as e:
            logging.warning("No pude pausar Serie A para %s: %s", chat_id, e)
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
    outbound = _personalize_referral_links(text_es if lang == "es" else text_en, chat_id)
    if not outbound:
        logging.warning("No existe texto de campaña %s %s para %s", series, step, chat_id)
        return

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=outbound,
            reply_markup=remarketing_keyboard(lang),
            disable_web_page_preview=True,
        )
        with Session() as session:
            sent_row = session.get(CampaignJob, int(job_id))
            if sent_row and sent_row.sent_at is None:
                sent_row.sent_at = utcnow_naive()
                session.commit()
        logging.info("✅ Campaña %s %s enviada a chat_id %s (lang=%s)", series, step, chat_id, lang)
    except Exception as e:
        if _is_blocked_user_error(e):
            _cleanup_blocked_user_tasks(
                context, chat_id, source=f"campaign_{series}_{step}"
            )
            return
        # Para errores transitorios distintos de bloqueo, no marcamos como enviado:
        # un reinicio podrá recuperarlo conservando su vencimiento original.
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
            reply_markup=remarketing_keyboard(lang),
        )
    except Exception as e:
        if _is_blocked_user_error(e):
            _cleanup_blocked_user_tasks(context, chat_id, source="legacy_series_b")
            return
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
            [InlineKeyboardButton("🚀 CREATE MY ACCOUNT", callback_data="registrarme")],
            [InlineKeyboardButton("💬 I HAVE A QUESTION", callback_data="ask_here")],
            [InlineKeyboardButton("✅ I already have an account", callback_data="ya_tengo_cuenta")],
            [InlineKeyboardButton("📊 Capital Management", callback_data="gestion_capital_en")],
            [InlineKeyboardButton("🎁 VIP Benefits", callback_data="beneficios_vip")],
            [InlineKeyboardButton("📊 Levels & Plans", callback_data="levels_plans_en")],
            [InlineKeyboardButton("📲 Channel in English", url=CANAL_EN)],
            [InlineKeyboardButton("📊 Results Channel", url=CANAL_RESULTADOS)],
            [InlineKeyboardButton("🌐 Social media", callback_data="redes_sociales")],
            [InlineKeyboardButton("📩 MY PERSONAL CHAT", url=SUPPORT_URL)],
            [InlineKeyboardButton("🇪🇸 Cambiar a Español", callback_data="set_lang_es")],
        ]
    else:
        kb = [
            [InlineKeyboardButton("🚀 QUIERO REGISTRARME", callback_data="registrarme")],
            [InlineKeyboardButton("💬 TENGO UNA PREGUNTA", callback_data="ask_here")],
            [InlineKeyboardButton("✅ Ya tengo cuenta", callback_data="ya_tengo_cuenta")],
            [InlineKeyboardButton("📊 Gestión de capital", callback_data="gestion_capital")],
            [InlineKeyboardButton("🎁 Beneficios VIP", callback_data="beneficios_vip")],
            [InlineKeyboardButton("📊 Niveles y Planes", callback_data="niveles_planes")],
            [InlineKeyboardButton("📲 Canal en Español", url=CANAL_ES)],
            [InlineKeyboardButton("📊 Canal de resultados", url=CANAL_RESULTADOS)],
            [InlineKeyboardButton("🌐 Redes sociales", callback_data="redes_sociales")],
            [InlineKeyboardButton("📩 MI CHAT PERSONAL", url=SUPPORT_URL)],
            [InlineKeyboardButton("🇺🇸 Switch to English", callback_data="set_lang_en")],
        ]
    return InlineKeyboardMarkup(kb)


def build_registration_entry_menu(lang: str = "es") -> InlineKeyboardMarkup:
    """Entrada mínima para CTAs de registro publicados fuera del bot.

    Reutiliza callbacks existentes: no cambia la lógica de registro ni del menú principal.
    """
    if lang == "en":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 CREATE MY ACCOUNT", callback_data="registrarme")],
            [InlineKeyboardButton("💬 I HAVE A QUESTION", callback_data="ask_here")],
            [InlineKeyboardButton("🏠 VIEW FULL MENU", callback_data="back_main_menu")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 QUIERO REGISTRARME", callback_data="registrarme")],
        [InlineKeyboardButton("💬 TENGO UNA PREGUNTA", callback_data="ask_here")],
        [InlineKeyboardButton("🏠 VER MENÚ COMPLETO", callback_data="back_main_menu")],
        [InlineKeyboardButton("🇺🇸 English", callback_data="set_lang_en")],
    ])


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

    lang = get_user_lang(chat_id)
    _touch_user_activity(chat_id, lang)

    # Deep links: ADS usa un token opaco (trk_...) y las bienvenidas del canal
    # conservan sus parámetros históricos. El token nunca se muestra ni se guarda
    # en los eventos locales del bot.
    start_param_raw = (context.args[0].strip() if context.args else "")
    start_param = start_param_raw.lower()

    # === PUERTA ADS: publicidad -> bot mínimo -> canal informativo ===
    # No muestra idioma, menú VIP ni activa campañas. La atribución ocurre ANTES
    # de que el usuario entre al canal, lo que permite reconocer después su alta
    # aunque Telegram no entregue invite_link/invite_name en chat_member.
    if start_param.startswith("trk_") and re.fullmatch(r"trk_[a-z0-9_-]{6,60}", start_param):
        set_user_lang(chat_id, nombre, "es")
        lang = "es"
        result = await _tracking_post(
            "/internal/ads-start",
            {
                "telegram_id": int(chat_id),
                "token": start_param_raw[4:],
                "username": getattr(update.effective_user, "username", None),
                "first_name": getattr(update.effective_user, "first_name", None),
            },
            source="ads_gate_start",
        )
        confirmed_source = _normalize_channel_source(
            result.get("source") if isinstance(result, dict) else None
        )
        if isinstance(result, dict) and result.get("ok"):
            final_source = _record_source_attribution_only(
                chat_id, confirmed_source, authoritative=True
            )
            _save_ads_click_token(chat_id, start_param_raw[4:])
        else:
            # Si tracking estuviera temporalmente caído, no inventamos ADS.
            # La puerta sigue funcionando y el bot principal no se interrumpe.
            final_source = _get_channel_source(chat_id)

        _log_event(chat_id, "ADS_GATE_START", final_source)
        # El BOT_START sale después de reclamar el token para que cualquier postback
        # posterior ya encuentre la fuente ADS asociada al Telegram ID.
        _tracking_fire_event(chat_id, "BOT_START", "ads_gate")

        first_name = (update.effective_user.first_name or nombre or "").strip() or "✨"
        safe_name = html.escape(first_name)
        texto_ads = (
            f"💜 <b>¡Hola, {safe_name}! Bienvenido.</b>\n\n"
            "Gracias por estar aquí. ✨\n"
            "<b>Solo te falta un paso para continuar.</b>\n\n"
            "Entra a mi <b>canal informativo oficial</b> y accede a contenido, operativas, resultados y novedades "
            "que pueden ayudarte a seguir avanzando en tu camino como trader. 🚀\n\n"
            "👇 <b>Toca el botón para entrar.</b>"
        )
        ads_gate_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("💜 QUIERO ENTRAR AL CANAL", url=CANAL_ES)
        ]])
        await update.message.reply_text(
            texto_ads,
            parse_mode=ParseMode.HTML,
            reply_markup=ads_gate_keyboard,
        )

        user = update.effective_user
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "📣 Entrada desde publicidad al bot-puerta: "
                f"{_telegram_display_name(user)} (ID: {user.id}) | origen={final_source}."
            ),
            reply_markup=admin_user_quick_keyboard(user.id),
        )
        return

    _tracking_fire_event(chat_id, "BOT_START", start_param or "normal")

    # === ENTRADA DIRECTA DE REGISTRO ===
# Español es la entrada predeterminada; el menú mínimo incluye solo un acceso a English.
# El callback set_lang_en reutiliza la bienvenida y el menú completos en inglés ya existentes.
    # Enlace oficial para CTAs externos: https://t.me/JOHAALETRADER_bot?start=registro_canal
    # Muestra solo bienvenida + REGISTRARME + MENÚ COMPLETO. El callback registrarme
    # conserva la personalización ADS existente mediante el click_id guardado.
    if start_param == "registro_canal":
        set_user_lang(chat_id, nombre, "es")
        lang = "es"
        _log_event(chat_id, "REGISTRATION_ENTRY_START", "registro_canal")
        _tracking_fire_event(chat_id, "REGISTRATION_ENTRY_START", "registro_canal")

        entry_keyboard = build_registration_entry_menu("es")
        try:
            with open(WELCOME_IMG, "rb") as img:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=InputFile(img),
                    caption=_personalized_welcome(update.effective_user, "es"),
                    reply_markup=entry_keyboard,
                )
        except FileNotFoundError:
            await context.bot.send_message(
                chat_id=chat_id,
                text=_personalized_welcome(update.effective_user, "es"),
                reply_markup=entry_keyboard,
            )

        # Conserva las campañas/etapa actuales sin reiniciar relojes existentes.
        _sync_menu_campaign_for_stage(chat_id, lang, context)

        user = update.effective_user
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "🚀 Entrada directa de registro al bot: "
                f"{_telegram_display_name(user)} (ID: {user.id}) | origen={_get_channel_source(chat_id)}."
            ),
            reply_markup=admin_user_quick_keyboard(user.id),
        )
        return

    # Deep links exclusivos de las bienvenidas de los canales ES / EN.
    # Cada origen fija el idioma correspondiente y NO altera el /start normal.
    if start_param in ("canal_bienvenida", "canal_bienvenida_en"):
        source_lang = "en" if start_param == "canal_bienvenida_en" else "es"
        set_user_lang(chat_id, nombre, source_lang)
        lang = source_lang

        first_name = (update.effective_user.first_name or nombre or "").strip() or "✨"
        safe_name = html.escape(first_name.upper())

        if lang == "en":
            texto_entrada = (
                f"💜✨ <b>HI, {safe_name}!</b> ✨💜\n\n"
                "Great to have you here. I see you’re coming from my English channel, and I’m here to guide you. 🚀\n\n"
                "<b>👇 Choose what you’d like to know:</b>"
            )
            source_label = "canal EN"
        else:
            texto_entrada = (
                f"💜✨ <b>¡HOLA, {safe_name}!</b> ✨💜\n\n"
                "Qué bueno tenerte aquí. Vienes desde mi canal informativo y estoy aquí para guiarte. 🚀\n\n"
                "<b>👇 Elige lo que quieres conocer:</b>"
            )
            source_label = "canal ES"

        _log_event(chat_id, "CHANNEL_WELCOME_START", start_param)
        _tracking_fire_event(chat_id, "CHANNEL_WELCOME_START", start_param)
        await update.message.reply_text(
            texto_entrada,
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(lang),
        )

        # Mantiene exactamente la campaña que corresponda a su etapa,
        # sin reiniciar relojes existentes si el usuario vuelve a tocar el enlace.
        _sync_menu_campaign_for_stage(chat_id, lang, context)

        user = update.effective_user
        mensaje_admin = (
            f"🚀 Llegó al bot desde la bienvenida del {source_label}: "
            f"{_telegram_display_name(user)} (ID: {user.id})."
        )
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=mensaje_admin,
            reply_markup=admin_user_quick_keyboard(user.id),
        )
        return

    # /start normal: conserva el comportamiento original.
    await update.message.reply_text("Elige tu idioma / Choose your language:", reply_markup=build_lang_picker())

    # Notificar admin
    user = update.effective_user
    mensaje_admin = f"🚨 El usuario {_telegram_display_name(user)} (ID: {user.id}) ejecutó /start (selección de idioma)."
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=mensaje_admin,
        reply_markup=admin_user_quick_keyboard(user.id),
    )

# Enviar bienvenida y menú después de elegir idioma
async def send_welcome_and_menu(chat_id: int, lang: str, context: ContextTypes.DEFAULT_TYPE, telegram_user=None):
    # Bienvenida con imagen si existe. El saludo usa @username o nombre visible de Telegram.
    welcome_text = _personalized_welcome(telegram_user, lang)
    try:
        with open(WELCOME_IMG, "rb") as img:
            await context.bot.send_photo(chat_id=chat_id, photo=InputFile(img), caption=welcome_text)
    except FileNotFoundError:
        await context.bot.send_message(chat_id=chat_id, text=welcome_text)

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

    if q.data == "upgrade_conditions":
        lang = get_user_lang(chat_id)
        await q.message.reply_text(upgrade_conditions_text(lang), reply_markup=support_keyboard(lang))
        return

    # Notificar interacción
    await notificar_interaccion(update, context)

    if q.data == "back_main_menu":
        lang = get_user_lang(chat_id)
        await q.message.reply_text(
            "👇 Elige una opción para continuar:" if lang == "es" else "👇 Choose an option to continue:",
            reply_markup=build_main_menu(lang),
        )
        return

    if q.data == "ask_here":
        lang = get_user_lang(chat_id)
        msg = (
            "Escribe tu pregunta aquí abajo en este chat 👇"
            if lang == "es" else
            "Write your question below in this chat 👇"
        )
        await q.message.reply_text(msg)
        return

    # --- Niveles y Planes (informativo) ---
    if q.data == "niveles_planes":
        texto = _personalize_referral_links(respuesta_niveles_es(), chat_id)
        await q.message.reply_text(texto, reply_markup=levels_keyboard("es"))
        return

    # --- Levels & Plans (EN) ---
    if q.data == "levels_plans_en":
        texto = _personalize_referral_links(respuesta_niveles_en(), chat_id)
        await q.message.reply_text(texto, reply_markup=levels_keyboard("en"))
        return


    # --- Acciones para imagen (ID vs depósito) — ES/EN ---
    lang = get_user_lang(chat_id)

    if q.data and q.data.startswith("IMG_IS_ID|"):
        msg = (
            (
                "Perfecto ✅\n"
                "Para poder validarlo necesito que me envíes el **ID en texto** (solo el número).\n"
                "📌 Ábrelo en Stockity o Binomo, cópialo y pégalo aquí 👇"
            )
            if lang == "es" else
            (
                "Perfect ✅\n"
                "To validate it, I need you to send me the **ID as text** (numbers only).\n"
                "📌 Open your Stockity or Binomo profile, copy the ID and paste it here 👇"
            )
        )
        await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        await send_admin_auto_log(context, update, "IMG_IS_ID", msg)
        return

    if q.data and q.data.startswith("IMG_IS_DEP|"):
        saved_id = _get_saved_trading_id(chat_id)
        if saved_id:
            msg = (
                (
                    "Perfecto ✅\n\n"
                    "Recibido. Estoy validando tu depósito ahora mismo.\n"
                    "Te escribiré de nuevo para confirmar y habilitar tu acceso 🎉"
                )
                if lang == "es" else
                (
                    "Perfect ✅\n\n"
                    "Received. I’m validating your deposit now.\n"
                    "I’ll message you again to confirm it and enable your access 🎉"
                )
            )
            await q.message.reply_text(msg)
            await send_admin_auto_log(context, update, "AUTO_IMG_DEPOSIT_VALIDATING", msg)
            return

        msg = (
            (
                "Perfecto ✅\n\n"
                "Recibido. Para continuar, envíame tu **ID de Stockity o Binomo en texto** (solo el número) y lo dejo en validación 👇"
            )
            if lang == "es" else
            (
                "Perfect ✅\n\n"
                "Received. To continue, send me your **Stockity or Binomo ID as text** (numbers only) and I’ll leave it for validation 👇"
            )
        )
        await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        await send_admin_auto_log(context, update, "AUTO_IMG_DEPOSIT_NEED_ID", msg)
        return

    if q.data and q.data.startswith("IMG_IS_OTHER|"):
        msg = (
            (
                "Listo ✅\n"
                "Dime qué necesitas exactamente (bono, retiros, ID o horarios) y escríbelo aquí abajo 👇"
            )
            if lang == "es" else
            (
                "Got it ✅\n"
                "Tell me exactly what you need help with (bonus, withdrawals, ID or schedules) and write it below 👇"
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
        await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
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
        await send_welcome_and_menu(chat_id, "es", context, q.from_user)
        return
    if q.data == "set_lang_en":
        set_user_lang(chat_id, q.from_user.full_name, "en")
        await q.message.reply_text("✅ Language switched to English.")
        await send_welcome_and_menu(chat_id, "en", context, q.from_user)
        return

    lang = get_user_lang(chat_id)

    if q.data == "registrarme":
        if lang == "es":
            await q.message.reply_text(_personalize_referral_links(MENSAJE_REGISTRARME_ES, chat_id))
            # Video SOLO en español
            await q.message.reply_video(
                video="BAACAgEAAxkBAAIBaGhdq0nQXi6B4N8uRwmaOHKkUarbAAIMBgACTgAB8UbIZIU9XTMCzjYE",
                caption="📹 Paso a paso en el vídeo",
            )
        else:
            await q.message.reply_text(_personalize_referral_links(MENSAJE_REGISTRARME_EN, chat_id))

    elif q.data == "ya_tengo_cuenta":
        _msg_account = MENSAJE_YA_TENGO_CUENTA_ES if lang=="es" else MENSAJE_YA_TENGO_CUENTA_EN
        await q.message.reply_text(_personalize_referral_links(_msg_account, chat_id), reply_markup=support_keyboard(lang))

    elif q.data == "gestion_capital":
        texto_gestion = (
            "📊 GESTIÓN DE CAPITAL\n\n"
            "Tengo dos modalidades disponibles y este proceso lo reviso personalmente contigo.\n\n"
            "• Modalidad 3 meses: desde 200 USD. Objetivo estimado de 20–30% mensual, sujeto a resultados del trading.\n"
            "• Modalidad 2 meses: desde 100 USD. La estructura planteada busca generar hasta 30 USD semanales, sujeto a resultados.\n\n"
            "⚠️ Son objetivos, NO ganancias garantizadas. El trading implica riesgo.\n\n"
            "Si te interesa, escríbeme directamente y te explico condiciones, disponibilidad y proceso 👇"
        )
        await q.message.reply_text(texto_gestion, reply_markup=personal_chat_keyboard(lang))

    elif q.data == "gestion_capital_en":
        texto_gestion = (
            "📊 CAPITAL MANAGEMENT\n\n"
            "I currently have two options, and I review this process with you personally.\n\n"
            "• 3-month option: from 200 USD. Estimated target of 20–30% per month, subject to trading results.\n"
            "• 2-month option: from 100 USD. The structure aims for up to 30 USD per week, subject to results.\n\n"
            "⚠️ These are targets, NOT guaranteed profits. Trading involves risk.\n\n"
            "If you are interested, message me directly so I can explain the current conditions and availability 👇"
        )
        await q.message.reply_text(texto_gestion, reply_markup=personal_chat_keyboard(lang))

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
https://t.me/JohaaleTrader_es""", reply_markup=support_keyboard(lang))
        else:
            await q.message.reply_text("""🌐 Social Media:

🔴 YouTube:
https://youtube.com/@johaalegria.trader?si=JemqmPes0Rz3WqEZ

🟣 Instagram:
https://www.instagram.com/johaale_trader?igsh=ZWI5dXNnaXN6aDNw

🎵 TikTok:
https://www.tiktok.com/@joha_binomo?_t=ZN-8xceLrp5GTe&_r=1

💬 Telegram:
https://t.me/JohaaleTrader_es""", reply_markup=support_keyboard(lang))

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
        if update.message is None or update.effective_user is None:
            return
        usuario = update.message.from_user
        chat_id = usuario.id
        nombre = f"@{usuario.username}" if usuario.username else (usuario.full_name or usuario.first_name or f"Usuario {chat_id}")
        lang = get_user_lang(chat_id)
        stage = get_user_stage(chat_id)
        visible_text = (update.message.text or update.message.caption or "").strip()
        candidate_id = _extract_candidate_trading_id(visible_text)

        # El botón de acción va pegado al mensaje/foto correcta para no obligar a buscar al usuario.
        action_rows = [[InlineKeyboardButton("✏️ Responder", callback_data=f"responder:{chat_id}:{update.message.message_id}")]]
        if update.message.photo and stage in (STAGE_POST, STAGE_DEPOSITED):
            deposit_label = "💰 REVISAR DEPÓSITO" if stage == STAGE_POST else "💰 REVISAR DEPÓSITO / SUBIR NIVEL"
            action_rows.append([InlineKeyboardButton(
                deposit_label,
                callback_data=f"admin_user_deposit:{chat_id}",
            )])
        elif candidate_id:
            # Primero el usuario identifica BINOMO/STOCKITY. Luego llega el bloque admin específico.
            pass
        action_rows.append([InlineKeyboardButton("👤 Gestionar usuario", callback_data=f"admin_user_open:{chat_id}")])
        admin_markup = InlineKeyboardMarkup(action_rows)

        if update.message.photo:
            cap = update.message.caption or ""
            stage_hint = "POST · pendiente de depósito" if stage == STAGE_POST else ("PRE · pendiente de validar ID" if stage == STAGE_PRE else "CUENTA ACTIVA")
            cap_final = f"📩 Foto de {nombre} (ID: {chat_id}) [lang={lang}]\nEstado: {stage_hint}\n\n{cap}"
            await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=update.message.photo[-1].file_id,
                caption=cap_final,
                reply_markup=admin_markup,
            )
        elif update.message.video:
            cap = update.message.caption or ""
            cap_final = f"📩 Video de {nombre} (ID: {chat_id}) [lang={lang}]\n\n{cap}"
            await context.bot.send_video(chat_id=ADMIN_ID, video=update.message.video.file_id, caption=cap_final, reply_markup=admin_markup)
        elif update.message.audio:
            cap = update.message.caption or ""
            cap_final = f"📩 Audio de {nombre} (ID: {chat_id}) [lang={lang}]\n\n{cap}"
            await context.bot.send_audio(chat_id=ADMIN_ID, audio=update.message.audio.file_id, caption=cap_final, reply_markup=admin_markup)
        elif update.message.voice:
            cap_final = f"📩 Nota de voz de {nombre} (ID: {chat_id}) [lang={lang}]"
            await context.bot.send_voice(chat_id=ADMIN_ID, voice=update.message.voice.file_id, caption=cap_final, reply_markup=admin_markup)
        else:
            mensaje_usuario = update.message.text or ""
            texto = (
                f"📩 Nuevo mensaje de {nombre} (ID: {chat_id}) [lang={lang}]:\n\n"
                f"🗨️ {mensaje_usuario}\n\n"
                "✏️ Puedes responder desde este mismo aviso."
            )
            await context.bot.send_message(chat_id=ADMIN_ID, text=texto, reply_markup=admin_markup)

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
                    _manual_outbound = _personalize_referral_links(update.message.text or "", destinatario_id)
                    await context.bot.send_message(
                        chat_id=destinatario_id,
                        text=_manual_outbound
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
                            _tracking_fire_event(destinatario_id, "ID_VALIDATED", f"ID={saved_id}")
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
                        # Desde v7.10.21 ningún texto manual puede saltarse el control de monto/nivel.
                        # Johanna puede seguir respondiendo manualmente, pero el estado DEPOSITED y los
                        # accesos solo se actualizan desde 💰 REVISAR DEPÓSITO.
                        current_stage, _ = _repair_inconsistent_stage(destinatario_id)
                        if current_stage in (STAGE_POST, STAGE_DEPOSITED):
                            await context.bot.send_message(
                                chat_id=ADMIN_ID,
                                text=(
                                    f"ℹ️ No cambié automáticamente el nivel/estado de {destinatario_id} por el texto manual. "
                                    "Usa 👤 GESTIONAR USUARIO → 💰 REVISAR DEPÓSITO para registrar el monto y calcular el nivel."
                                ),
                                reply_markup=admin_user_quick_keyboard(destinatario_id),
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=ADMIN_ID,
                                text=f"⚠️ No marqué DEPOSITED a {destinatario_id}: primero debe existir un ID realmente enviado y validado.",
                            )

                    elif (
                        (_norm(GATILLO_ID_ERRADO) in txtn)
                        or (_norm(GATILLO_ID_ERRADO_EN) in txtn)
                        or ("tu id esta errado" in txtn)
                        or ("tu id está errado" in txtn)
                        or ("tu id no quedo vinculado correctamente" in txtn)
                        or ("your id is incorrect" in txtn)
                        or ("your id is wrong" in txtn)
                        or ("your id was not linked correctly" in txtn)
                    ):
                        _ok, _msg = await _admin_reject_trading_id(
                            context, destinatario_id, notify_user=False
                        )
                        await context.bot.send_message(chat_id=ADMIN_ID, text=_msg)
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
# 5 minutos de prioridad para Johanna. Puede cambiarse en Railway con AI_WAIT_MINUTES.
try:
    AI_WAIT_MINUTES = max(1, int(os.getenv("AI_WAIT_MINUTES", "5")))
except Exception:
    AI_WAIT_MINUTES = 5
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
- Básico: desde 50 USD en la propia cuenta de trading. Incluye JT TRADERS TEAMS VIP principal (educación/metodología completa), Binary Teams Módulo 3 y canal de 30–50 señales CRYPTO IDX diarias de lunes a viernes.
- Premium: desde 200 USD. Incluye VIP principal + Módulo 3 + canal de +300 señales Premium de lunes a sábado (CRYPTO IDX, pares de divisas, índices sintéticos y Forex) + IA Premium Automática CRYPTO IDX 24/7 + Binary Teams Módulo 4 Smart Money Concept.
- Prestige: desde 500 USD. Incluye todo Premium + Divisas Automáticas 24/7 Premium + Madness Trading Avanzado ALGO & LIT + mentorías privadas, acompañamiento cercano y preparación para cuentas de fondeo. Forex automático está en construcción.
- Si preguntan con cuánto es ideal iniciar, explica que se puede empezar desde 50 USD en Básico. Normalmente recomiendo 200 USD o más si está dentro de las posibilidades del usuario porque Premium habilita una estructura mucho más amplia de señales y herramientas, sin prometer mejores resultados.
- Explica con buenas palabras que un capital más amplio da mayor margen operativo y más flexibilidad para aplicar gestión de riesgo y distribuir mejor las entradas. Eso puede ayudar a aprovechar mejor la estrategia y las herramientas, pero NO garantiza mejores resultados ni ganancias. Nunca digas que más inversión asegura más rentabilidad.
- Si un usuario tiene menos de 50 USD, no negocies una excepción ni prometas acceso: el nivel Básico solo se habilita al completar al menos 50 USD. Si ya envió un depósito incompleto, debe completar el faltante y enviar el nuevo comprobante.

BROKERS Y REGLAS DE UPGRADE
- Binomo y Stockity se gestionan por separado. Los depósitos de brokers distintos NUNCA se suman entre sí para subir de nivel.
- Cada broker tiene su propio ID validado, depósitos, contador y nivel. El nivel general de la comunidad es el nivel más alto alcanzado individualmente en cualquiera de las cuentas. Registrar un segundo broker nunca baja el nivel que el usuario ya tenía.
- Los primeros 3 depósitos validados de una misma cuenta/broker pueden acumularse para subir de nivel.
- Esa ventana de acumulación dura 30 días desde el primer depósito validado.
- Para entrar en la acumulación, el comprobante debe enviarse dentro de las 72 horas posteriores al depósito.
- Al completarse el 3.er depósito o vencer los 30 días, los depósitos posteriores ya no se suman: un upgrade exige un nuevo depósito único que por sí solo alcance el monto mínimo completo del nuevo nivel.
- Solo cuentan depósitos que el usuario reportó y Johanna validó.
- No bombardees al usuario con cálculos de cuánto le falta. Si pregunta por estas reglas, explícalas de forma breve y remite al botón «ℹ️ VER CONDICIONES DE UPGRADE».

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

SEÑALES — CANALES DE TELEGRAM
- Básico: canal CRYPTO IDX limitado con 30–50 señales diarias de lunes a viernes. Se toma la entrada en el minuto exacto indicado, con expiración de 1 minuto. Martingala 1 y 2 son opcionales y aumentan el riesgo.
- Premium/Prestige: canal de Señales Premium con +300 señales de lunes a sábado entre CRYPTO IDX, pares de divisas, índices sintéticos y Forex. Se toma la entrada en el minuto exacto indicado, con expiración de 1 minuto. Martingala 1 y 2 son opcionales.
- Premium/Prestige: IA Premium Automática CRYPTO IDX 24/7. La entrada se toma en el minuto inmediatamente siguiente al minuto en que llega la alerta, con expiración de 1 minuto.
- Prestige: Divisas Automáticas 24/7 Premium. La entrada se toma en el minuto inmediatamente siguiente a la alerta, con expiración de 1 minuto.
- Nunca presentes Martingala como garantía de recuperación ni de ganancia.

INTERFAZ VISUAL PRIVADA DE JOHAALETRADER
- La interfaz/software visual que Johanna utiliza en sus lives es una herramienta privada de uso interno y NO se entrega a miembros de la comunidad.
- Esa interfaz requiere programación, instalación, configuración, mantenimiento y actualizaciones propias.
- Los miembros NO pierden las señales por no tener la interfaz: reciben las señales operativas directamente dentro de Telegram mediante los canales de Señales Premium/CRYPTO IDX y los bots automáticos 24/7 incluidos según su nivel.
- Si preguntan “¿por qué no me dieron acceso al bot/interfaz/software que usas?”, explica de forma profesional que Telegram permite recibir las señales de manera más simple, estable y accesible desde cualquier dispositivo, sin instalaciones ni configuraciones adicionales.

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


def _is_live_info_query(texto: str) -> bool:
    """Detecta una consulta real sobre horarios/plataformas LIVE, no una simple mención."""
    t = _norm(texto or "").strip()
    if not t:
        return False

    # Mensajes muy cortos que claramente funcionan como solicitud de información.
    if t in {
        "live", "live?", "en vivo", "en vivo?", "directo", "directo?",
        "horario", "horario?", "horarios", "horarios?",
        "live hoy", "live hoy?", "en vivo hoy", "en vivo hoy?",
        "live schedule", "live schedule?",
    }:
        return True

    explicit_queries = (
        # Español — hora/cuándo
        "a que hora te conectas", "a qué hora te conectas",
        "a que hora haces live", "a qué hora haces live",
        "a que hora es el live", "a qué hora es el live",
        "a que hora estas en vivo", "a qué hora estás en vivo",
        "a que hora sales en vivo", "a qué hora sales en vivo",
        "a que hora haces directo", "a qué hora haces directo",
        "a que hora transmites", "a qué hora transmites",
        "cuando te conectas", "cuándo te conectas",
        "cuando hay live", "cuándo hay live",
        "cuando es el live", "cuándo es el live",
        "cuando haces live", "cuándo haces live",
        "cuando haces directo", "cuándo haces directo",
        "cuando estas en vivo", "cuándo estás en vivo",
        "cuando transmites", "cuándo transmites",
        "proximo live", "próximo live", "siguiente live",

        # Español — hoy/mañana
        "hay live hoy", "hay en vivo hoy", "hay directo hoy",
        "tienes live hoy", "tienes en vivo hoy",
        "haces live hoy", "estas en vivo hoy", "estás en vivo hoy",
        "vas a hacer live hoy", "vas a estar en vivo hoy",
        "haras live hoy", "harás live hoy",
        "live mañana", "live manana", "hay live mañana", "hay live manana",

        # Español — dónde/cómo entrar
        "donde haces live", "dónde haces live",
        "por donde haces live", "por dónde haces live",
        "donde transmites", "dónde transmites",
        "por donde transmites", "por dónde transmites",
        "donde es el live", "dónde es el live",
        "como entro al live", "cómo entro al live",
        "como me conecto al live", "cómo me conecto al live",
        "quiero entrar al live", "quiero conectarme al live",
        "horario de live", "horarios de live",
        "horario del live", "horarios del live",
        "horario de tus lives", "horarios de tus lives",

        # English
        "what time do you go live", "what time are you live",
        "what time is the live", "what time is your live",
        "when do you go live", "when are you live",
        "when is the live", "when is your live",
        "do you go live today", "are you live today",
        "do you have a live today", "is there a live today",
        "where do you go live", "where is the live",
        "how do i join the live", "how can i join the live",
        "next live", "next live?",
    )
    if any(q in t for q in explicit_queries):
        return True

    # Formas compactas naturales: "hoy live?", "mañana en vivo?", etc.
    live_terms = ("live", "en vivo", "directo", "transmision", "transmisión", "stream", "streaming")
    time_terms = ("hoy", "mañana", "manana", "esta noche", "tonight", "today", "tomorrow")
    if any(term in t for term in live_terms) and any(term in t for term in time_terms):
        # Solo si el texto parece pedir información, no relatar una experiencia.
        narrative_markers = (
            "te vi", "estuve", "estaba", "opere", "operé", "operando",
            "gracias por", "me gusto", "me gustó", "me encanto", "me encantó",
            "sali", "salí", "gané", "gane", "ganancias", "profit",
            "i saw", "i watched", "thanks for", "i was", "i made profit",
        )
        if not any(marker in t for marker in narrative_markers):
            return True

    return False


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

    # ---- Live / conexión ----
    # Solo responde horarios/plataformas cuando el usuario realmente los consulta.
    # Menciones narrativas como "te vi en live" o "operé contigo en vivo" NO disparan este bloque.
    if _is_live_info_query(texto):
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

    # LIVE solo cuando existe una consulta real; una simple mención no cuenta como intención.
    if _is_live_info_query(texto):
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
        "📊 Señales JT TRADERS\n\n"
        "🟢 Básico: 30–50 señales CRYPTO IDX diarias de lunes a viernes. Se toman en el minuto exacto indicado, con expiración de 1 minuto.\n\n"
        "🔵 Premium / 🟣 Prestige: +300 señales Premium de lunes a sábado entre CRYPTO IDX, pares de divisas, índices sintéticos y Forex; además IA Premium Automática CRYPTO IDX 24/7.\n\n"
        "🟣 Prestige también incluye Divisas Automáticas 24/7.\n\n"
        "⏱ En las señales automáticas 24/7 la entrada se toma al minuto siguiente de recibir la alerta. MG1/MG2 son opcionales y aumentan el riesgo."
    )


def respuesta_senales_en() -> str:
    return (
        "📊 JT TRADERS Signals\n\n"
        "🟢 Basic: 30–50 CRYPTO IDX signals per day, Monday to Friday. Enter at the exact indicated minute with 1-minute expiry.\n\n"
        "🔵 Premium / 🟣 Prestige: 300+ Premium signals Monday to Saturday across CRYPTO IDX, currency pairs, synthetic indices and Forex, plus Premium Automatic CRYPTO IDX AI 24/7.\n\n"
        "🟣 Prestige also includes Automatic FX 24/7.\n\n"
        "⏱ For automatic 24/7 signals, enter on the minute immediately after the alert. MG1/MG2 are optional and increase risk."
    )


def respuesta_bot_ia_es() -> str:
    return (
        "🤖 IA Premium Automática CRYPTO IDX 24/7\n\n"
        "Está incluida desde el nivel Premium y se utiliza directamente dentro de Telegram. Cuando llega una alerta, la entrada se toma al minuto siguiente, con expiración de 1 minuto. MG1/MG2 son opcionales y aumentan el riesgo.\n\n"
        "📌 Importante: la interfaz visual que ves en mis lives es una herramienta privada de uso interno. No necesitas instalarla para recibir las señales: tus accesos se habilitan directamente en Telegram para que puedas utilizarlos desde cualquier dispositivo, sin instalaciones, configuraciones ni actualizaciones adicionales."
    )


def respuesta_bot_ia_en() -> str:
    return (
        "🤖 Premium Automatic CRYPTO IDX AI 24/7\n\n"
        "It is included from the Premium level and is used directly inside Telegram. When an alert arrives, enter on the following minute with 1-minute expiry. MG1/MG2 are optional and increase risk.\n\n"
        "📌 Important: the visual interface you see in my live sessions is a private internal tool. You do not need to install it to receive the signals: your access is enabled directly in Telegram so you can use it from any device without extra installations, setup or updates."
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
            "💰 El mínimo para activar el nivel Básico es 50 USD. Con menos de 50 USD todavía no se habilita acceso; debes completar el valor faltante.\n\n"
            "Desde 50 USD, Básico incluye el VIP principal, Módulo 3 y 30–50 señales CRYPTO IDX diarias de lunes a viernes. Desde 200 USD, Premium habilita +300 señales, IA automática CRYPTO IDX 24/7 y Módulo 4 Smart Money Concept.\n\n"
            "El depósito siempre queda en tu propia cuenta de trading. 🚀"
            if lang == "es" else
            "💰 The minimum required to activate the Basic level is USD 50. With less than USD 50, access is not enabled yet; the remaining amount must be completed.\n\n"
            "From USD 50, Basic includes the main VIP, Module 3 and 30–50 CRYPTO IDX signals per day Monday to Friday. From USD 200, Premium unlocks 300+ signals, automatic CRYPTO IDX AI 24/7 and Module 4 Smart Money Concept.\n\n"
            "The deposit always stays in your own trading account. 🚀"
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
        "💜 Mi comunidad es totalmente GRATIS. No pagas membresía: la inversión se deposita directamente en TU propia cuenta de trading.\n\n"
        "🟢 Básico — desde 50 USD\n"
        "🎓 Curso incluido:\n"
        "• Binary Teams Módulo 3 — Introducción al Análisis Bursátil.\n"
        "📚 JT TRADERS TEAMS VIP: educación y metodología completa.\n"
        "📈 30–50 señales CRYPTO IDX diarias, de lunes a viernes.\n\n"
        "🔵 Premium — desde 200 USD\n"
        "🎓 Cursos incluidos:\n"
        "• Binary Teams Módulo 3 — Introducción al Análisis Bursátil.\n"
        "• Binary Teams Módulo 4 — Smart Money Concept.\n"
        "🚀 +300 señales Premium de lunes a sábado: CRYPTO IDX, pares de divisas, índices sintéticos y Forex.\n"
        "🤖 IA Premium Automática CRYPTO IDX 24/7.\n"
        "📚 JT TRADERS TEAMS VIP incluido.\n\n"
        "🟣 Prestige — desde 500 USD\n"
        "🎓 Cursos incluidos:\n"
        "• Binary Teams Módulo 3 — Introducción al Análisis Bursátil.\n"
        "• Binary Teams Módulo 4 — Smart Money Concept.\n"
        "• Madness Trading Avanzado — metodología ALGO & LIT.\n"
        "🚀 Incluye absolutamente todo Premium.\n"
        "💹 Divisas Automáticas 24/7 Premium.\n"
        "👩‍🏫 Mentorías privadas, acompañamiento cercano y preparación para cuentas de fondeo Forex.\n"
        "🤖 Forex automático: en construcción.\n\n"
        "⚠️ IMPORTANTE: primero regístrate con uno de mis enlaces y ANTES de depositar envíame tu ID para verificar que quedó correctamente vinculado conmigo.\n\n"
        f"🔗 Stockity — opción principal:\n{ENLACE_REFERIDO_STOCKITY}\n\n"
        f"🔗 Binomo — opción secundaria:\n{ENLACE_REFERIDO}\n\n"
        "🚀 Haz tu registro, envíame tu ID y te indico el siguiente paso."
    )


def respuesta_niveles_en() -> str:
    return (
        "📊 JT TRADERS Levels\n\n"
        "💜 My community is completely FREE. There is no membership fee: the investment is deposited directly into YOUR own trading account.\n\n"
        "🟢 Basic — from USD 50\n"
        "🎓 Course included:\n"
        "• Binary Teams Module 3 — Introduction to Market Analysis.\n"
        "📚 JT TRADERS TEAMS Main VIP: education and complete methodology.\n"
        "📈 30–50 CRYPTO IDX signals per day, Monday to Friday.\n\n"
        "🔵 Premium — from USD 200\n"
        "🎓 Courses included:\n"
        "• Binary Teams Module 3 — Introduction to Market Analysis.\n"
        "• Binary Teams Module 4 — Smart Money Concept.\n"
        "🚀 300+ Premium signals Monday to Saturday: CRYPTO IDX, currency pairs, synthetic indices and Forex.\n"
        "🤖 Premium Automatic CRYPTO IDX AI 24/7.\n"
        "📚 JT TRADERS TEAMS Main VIP included.\n\n"
        "🟣 Prestige — from USD 500\n"
        "🎓 Courses included:\n"
        "• Binary Teams Module 3 — Introduction to Market Analysis.\n"
        "• Binary Teams Module 4 — Smart Money Concept.\n"
        "• Madness Advanced Trading — ALGO & LIT methodology.\n"
        "🚀 Includes absolutely everything in Premium.\n"
        "💹 Premium Automatic FX 24/7.\n"
        "👩‍🏫 Private mentoring, closer guidance and Forex funded-account preparation.\n"
        "🤖 Automatic Forex: under development.\n\n"
        "⚠️ IMPORTANT: register with one of my links first and BEFORE depositing, send me your ID so I can verify that it is correctly linked to me.\n\n"
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
        "📌 Envíame aquí tu **ID de Stockity o Binomo** (solo el número) y lo dejo en validación 👇"
    )

def respuesta_where_send_id_es() -> str:
    return (
        "Sí ✅ Puedes enviarme tu **ID por aquí mismo** (solo el número) y lo dejo en validación 👇"
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
- Si preguntan por niveles/planes/inversión mínima, comienza aclarando que mi comunidad es GRATIS y que el dinero se deposita directamente en la PROPIA cuenta de trading. Muestra Básico/Premium/Prestige con emojis, SIN asteriscos alrededor de los nombres, usando la estructura oficial vigente de canales y herramientas. Incluye Stockity primero y Binomo segundo y recalca que ANTES de depositar deben enviarme el ID para validarlo conmigo.
- Si preguntan cuánto es el mínimo, con cuánto recomiendo empezar, si 50 USD está bien o cuál es la diferencia entre 50 y 200: explica claramente que 50 USD corresponde al Básico y habilita VIP principal + Módulo 3 + 30–50 señales CRYPTO IDX diarias de lunes a viernes. La recomendación habitual es 200 USD o más si está dentro de sus posibilidades porque Premium habilita +300 señales Premium de lunes a sábado, IA Automática CRYPTO IDX 24/7 y Módulo 4 Smart Money Concept. Puedes añadir que un capital mayor da más margen para gestión de riesgo, pero NUNCA lo presentes como garantía de mejores resultados o ganancias.
- FORMATO DE ENLACES: nunca uses Markdown tipo [texto](URL). Si incluyes Stockity/Binomo, usa EXACTAMENTE bloques separados. En español: "🔗 Stockity — opción principal:" + URL en la línea siguiente, una línea en blanco, luego "🔗 Binomo — opción secundaria:" + URL en la línea siguiente. En inglés: "🔗 Stockity — primary option:" + URL, línea en blanco, luego "🔗 Binomo — secondary option:" + URL. Stockity siempre primero y Binomo después.
- Los ejemplos reales de Johanna sirven para aprender vocabulario, ritmo y conocimiento. No generalices una excepción claramente individual.
- Si el caso realmente necesita revisión personal de Johanna porque es una excepción de cuenta, restricción, bloqueo o falta un dato que solo ella puede verificar, comienza tu respuesta EXACTAMENTE con [[PERSONAL_CHAT]]. No uses esa marca en preguntas normales que puedas resolver con la información disponible.

LÍMITES IMPORTANTES
- No inventes información, promociones, cupos, resultados ni horarios exactos no confirmados.
- No prometas ganancias ni resultados garantizados.
- No confirmes ID, depósito, afiliación, pago ni acceso VIP.
- Para ID y comprobantes: pide que los envíen primero AQUÍ MISMO en este chat para poder continuar el proceso sin sacarlos de la conversación.
- Para gestión de capital, VPN/restricción de país, bloqueos o casos extraordinarios de una cuenta específica: deriva a Johanna usando el chat de validación. Si preguntan por menos de 50 USD, explica que no se habilita acceso hasta completar el mínimo de 50 USD; solo deriva si pide una excepción especial.
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
    personal_review = False
    if not answer:
        personal_review = True
        answer = (
            "Este caso necesito revisarlo personalmente contigo. 👇"
            if lang == "es" else
            "I need to review this case personally with you. 👇"
        )
    else:
        marker = "[[PERSONAL_CHAT]]"
        if answer.lstrip().startswith(marker):
            personal_review = True
            answer = answer.lstrip()[len(marker):].lstrip()

    # Verificación final inmediatamente antes de enviar, por si Johanna respondió mientras se generaba la respuesta.
    latest = _get_pending_ai(chat_id)
    if not latest or str(latest.get("message_id") or "") != expected_message_id:
        return

    try:
        answer = _personalize_referral_links(answer, chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=answer,
            reply_markup=personal_chat_keyboard(lang) if personal_review else support_keyboard(lang),
            disable_web_page_preview=True,
        )
        _clear_pending_ai_db(chat_id)
        _append_ai_exchange(chat_id, question, answer)
        await _send_scheduled_ai_admin_log(context, chat_id, question, answer)
    except Exception as e:
        if _is_blocked_user_error(e):
            _cleanup_blocked_user_tasks(context, chat_id, source="delayed_ai")
            return
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
    chat_id = update.effective_chat.id if update.effective_chat else 0
    text_value = _personalize_referral_links((text_value or "").strip(), chat_id)
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
    """Responde todas las partes conocidas y deja solo lo restante para IA a los 5 min."""
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
            block = _id_pending_review_message(lang)
            await update.effective_message.reply_text(block, reply_markup=_broker_selection_keyboard("id", lang))
            handled.append(intent); continue

        if intent == "DEPOSITO":
            _log_event(chat_id, "DEPOSIT_REPORTED", texto)
            _tracking_fire_event(chat_id, "DEPOSIT_REPORTED", texto)
            stage_now = get_user_stage(chat_id)
            if stage_now == STAGE_DEPOSITED:
                block = (
                    "💳 Perfecto. Envíame aquí la captura del depósito adicional y la revisaré según las condiciones de actualización de nivel."
                    if lang == "es" else
                    "💳 Perfect. Send me the screenshot of the additional deposit and I’ll review it under the level-update conditions."
                )
            elif stage_now == STAGE_POST:
                block = (
                    "💳 Perfecto. Envíame aquí el comprobante de depósito/activación para revisar el monto y habilitar el nivel que corresponda."
                    if lang == "es" else
                    "💳 Perfect. Send me the deposit/activation proof here so I can review the amount and enable the corresponding level."
                )
            else:
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
        # vuelva a repetir niveles/bonos/registro a los 5 minutos.
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

    # En POST una foto se toma como comprobante inicial; en DEPOSITED se trata como
    # depósito adicional para posible subida de nivel. En ambos casos Johanna revisa
    # el monto y el bot calcula el nivel antes de habilitar accesos.
    if update.message and update.message.photo:
        caption = (update.message.caption or "").strip()
        current_stage = get_user_stage(chat_id)
        if current_stage in (STAGE_POST, STAGE_DEPOSITED):
            _log_event(chat_id, "DEPOSIT_REPORTED", caption or "PHOTO_PROOF")
            _tracking_fire_event(chat_id, "DEPOSIT_REPORTED", caption or "PHOTO_PROOF")
            brokers = _broker_validated_brokers(chat_id)
            if len(brokers) > 1:
                _broker_flow_set(chat_id, pending_deposit_broker="")
                qtxt = (
                    "✅ Recibido. Para revisar correctamente este depósito necesito saber a cuál de tus cuentas corresponde. Elige el broker:"
                    if lang == "es" else
                    "✅ Received. To review this deposit correctly, tell me which account it belongs to. Choose the broker:"
                )
                await update.message.reply_text(qtxt, reply_markup=_broker_selection_keyboard("deposit", lang))
                log_intent = "DEPOSIT_PROOF_BROKER_REQUIRED"
            elif len(brokers) == 1:
                _broker_flow_set(chat_id, pending_deposit_broker=brokers[0])
                qtxt = (
                    f"✅ Recibido. Estoy revisando tu depósito de {_broker_label(brokers[0])}. Te confirmaré por este chat cuando quede validado."
                    if lang == "es" else
                    f"✅ Received. I’m reviewing your {_broker_label(brokers[0])} deposit. I’ll confirm here once it is validated."
                )
                await update.message.reply_text(qtxt)
                log_intent = f"DEPOSIT_PROOF_{brokers[0]}"
            else:
                # Usuario de una versión anterior: antes de sumar cualquier depósito
                # debe identificar a qué broker pertenece su cuenta validada.
                _broker_flow_set(chat_id, pending_deposit_broker="")
                qtxt = (
                    "✅ Recibido. Para separar correctamente tus depósitos necesito identificar el broker de esta cuenta. Elige BINOMO o STOCKITY:"
                    if lang == "es" else
                    "✅ Received. To keep your deposits separated correctly, I need to identify the broker for this account. Choose BINOMO or STOCKITY:"
                )
                await update.message.reply_text(qtxt, reply_markup=_broker_selection_keyboard("deposit", lang))
                log_intent = "DEPOSIT_PROOF_LEGACY_BROKER_REQUIRED"
            await send_admin_auto_log(context, update, log_intent, qtxt)
            return

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
        await _send_user_blocks(update, msg)
        await send_admin_auto_log(context, update, "REGISTRO", msg)
        return

    if intent == "YA_REGISTRE":
        msg = _immediate_block("YA_REGISTRE", lang)
        await update.message.reply_text(msg)
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
        await send_admin_auto_log(context, update, "AUTO_MIN50", msg)
        return

    if intent == "DEPOSITO":
        _log_event(chat_id, "DEPOSIT_REPORTED", texto)
        _tracking_fire_event(chat_id, "DEPOSIT_REPORTED", texto)
        stage_now = get_user_stage(chat_id)
        if stage_now == STAGE_DEPOSITED:
            msg = (
                "Perfecto ✅\n\nEnvíame aquí la captura del depósito adicional. La revisaré según las condiciones de actualización de nivel y te confirmaré el resultado."
                if lang == "es" else
                "Perfect ✅\n\nSend me the screenshot of the additional deposit. I’ll review it under the level-update conditions and confirm the result."
            )
        elif stage_now == STAGE_POST:
            msg = (
                "Perfecto ✅\n\nEnvíame aquí tu comprobante de depósito/activación (foto o captura). Revisaré el monto y te confirmaré el nivel que queda habilitado."
                if lang == "es" else
                "Perfect ✅\n\nSend me your deposit/activation proof here (photo or screenshot). I’ll review the amount and confirm which level is enabled."
            )
        else:
            msg = (
                "Perfecto ✅\n\nEnvíame aquí tu comprobante de depósito/activación (foto o captura) y también tu ID de Stockity o Binomo en texto para validarlo y habilitar tu acceso."
                if lang == "es" else
                "Perfect ✅\n\nSend me your deposit/activation proof and your Stockity/Binomo ID as text so it can be validated and your access enabled."
            )
        await update.message.reply_text(msg)
        await send_admin_auto_log(context, update, "AUTO_DEPOSIT_CONFIRM", msg)
        return

    if intent == "ID_SUBMIT":
        _record_submitted_trading_id(chat_id, texto, context)
        msg = _id_pending_review_message(lang)
        await update.message.reply_text(msg, reply_markup=_broker_selection_keyboard("id", lang))
        await send_admin_auto_log(context, update, "ID_SUBMIT_PENDING_BROKER", msg)
        return

    if intent in ("GESTION_CAPITAL", "VPN", "PAIS"):
        msg = _immediate_block(intent, lang)
        await update.message.reply_text(msg, reply_markup=personal_chat_keyboard(lang))
        await send_admin_auto_log(context, update, intent, msg)
        return

    if intent in ("NEXT_STEP", "WHERE_SEND_ID", "NIVELES", "LIVE", "ID", "BENEFICIOS", "SENALES", "BOT_IA"):
        msg = _immediate_block(intent, lang)
        if msg:
            if intent == "LIVE":
                keyboard = live_keyboard(lang)
            elif intent in ("NIVELES", "BENEFICIOS", "SENALES", "BOT_IA"):
                keyboard = support_keyboard(lang)
            else:
                # NEXT_STEP / WHERE_SEND_ID / ID forman parte del flujo de registro.
                keyboard = None
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
            _direct_outbound = _personalize_referral_links(mensaje, chat_id)
            await context.bot.send_message(chat_id=chat_id, text=_direct_outbound)
            _learn_direct_admin_message(chat_id, mensaje, "text")
            _cancel_pending_ai(context, chat_id, manual_reply=mensaje)
            await update.message.reply_text("✅ Mensaje enviado con éxito.")
        else:
            await update.message.reply_text("⚠️ No se pudo enviar nada. Revisa el contenido.")
    except Exception as e:
        if "chat_id" in locals() and _is_blocked_user_error(e):
            _cleanup_blocked_user_tasks(context, chat_id, source="admin_direct_send")
            await update.message.reply_text(
                "🚫 Ese usuario bloqueó el bot. Limpié automáticamente sus campañas, IA pendiente y actividad de envíos."
            )
            return
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
            if _is_blocked_user_error(e):
                _cleanup_blocked_user_tasks(context, chat_id, source="live_broadcast")
                failed += 1
                logging.info("Aviso LIVE no entregado a %s: usuario bloqueó el bot", chat_id)
                await asyncio.sleep(0.06)
                continue

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
                except Exception as retry_error:
                    if _is_blocked_user_error(retry_error):
                        _cleanup_blocked_user_tasks(context, chat_id, source="live_broadcast_retry")
                        failed += 1
                        logging.info("Aviso LIVE no entregado a %s: usuario bloqueó el bot", chat_id)
                        await asyncio.sleep(0.06)
                        continue
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
                        reply_markup=remarketing_keyboard(lang),
                        disable_web_page_preview=True,
                    )
                else:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_file_id,
                        caption=outbound_text if outbound_text else None,
                        reply_markup=remarketing_keyboard(lang),
                    )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=outbound_text,
                    reply_markup=remarketing_keyboard(lang),
                    disable_web_page_preview=True,
                )
            sent += 1
            if lang == "en":
                sent_en += 1
            else:
                sent_es += 1
        except Exception as e:
            if _is_blocked_user_error(e):
                _cleanup_blocked_user_tasks(context, chat_id, source="marketing_broadcast")
                failed += 1
                logging.info("Marketing no entregado a %s: usuario bloqueó el bot", chat_id)
            else:
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

    # Tracking de altas al canal ES. El enlace especial ADS es permanente;
    # el enlace público normal del canal continúa funcionando como Orgánico/Otros.
    app.add_handler(
        ChatJoinRequestHandler(tracking_channel_join_request),
        group=-95,
    )
    # ChatMember registra todas las altas/reingresos detectados en el canal ES.
    app.add_handler(
        ChatMemberHandler(tracking_channel_member_update, ChatMemberHandler.CHAT_MEMBER),
        group=-90,
    )

    # EXCEPCIÓN ADMINISTRATIVA SEGURA: permite a Johanna conocer el ID del grupo de reportes.
    # Debe ejecutarse ANTES del bloqueo global y solo responde al ADMIN_ID.
    app.add_handler(
        CommandHandler("reportid", report_id_command, filters=filters.User(ADMIN_ID)),
        group=-110,
    )

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
    app.add_handler(CommandHandler("reportegrupo", report_group_test_command))
    app.add_handler(CommandHandler("version", version_command))

    # Gestión manual de usuarios: solo intercepta texto cuando el panel está esperando
    # una búsqueda o un ID de trading. Tiene prioridad sobre borradores y respuestas genéricas.
    app.add_handler(
        MessageHandler(filters.User(ADMIN_ID) & filters.TEXT & ~filters.COMMAND, admin_user_text_input),
        group=-5,
    )

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
    app.add_handler(CallbackQueryHandler(admin_user_callback, pattern="^admin_user_"))
    app.add_handler(CallbackQueryHandler(admin_broker_callback, pattern="^admin_broker_"))
    app.add_handler(CallbackQueryHandler(broker_user_callback, pattern="^broker_(?:id|deposit)_select:"))

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
    app.run_polling(allowed_updates=Update.ALL_TYPES)
