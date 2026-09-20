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

BOT_VERSION = "v7.10.58-20260920-ACTIVE-STATUS-CTA-MULTIQUESTION-GUARDS"
# v7.10.58: estado activo manda para Básico/Premium/Prestige, CTA por intención real y guardias multi-pregunta sin borrar otras respuestas.
# v7.10.57: mapea el Chat ID real de CRYPTO IDX Básico para aprobar solicitudes VIP.
# v7.10.56: guardias duras de nivel activo + interfaz LIVE; evita degradar miembros activos por montos hipotéticos y corrige respuestas del panel privado.
# v7.10.55: orquestación IA por estado + intención, aislamiento factual/promos, idioma EN garantizado y multi-pregunta natural.
# v7.10.54: corrige routing semántico de la interfaz mostrada en lives y evita confundirla con bots entregables por nivel.
# v7.10.53: jerarquía de profundidad IA: multi-pregunta más compacta, organización no rígida y menos relleno.
# v7.10.52: IA más concisa, sin redundancias/relleno y formación Binary Teams entendida como ruta progresiva.
# v7.10.50: conocimiento IA filtrado por tema, preguntas múltiples naturales y ejemplos solo relevantes.
# v7.10.49: mejora selección contextual: responde solo el tema preguntado, relaciona fuentes de señales y evita información/horarios innecesarios.
# v7.10.48: refuerza organización natural en 2 sesiones ~40 min, ~5 operaciones por sesión y precisión +300 señales/día lunes-sábado.
# v7.10.47: pulido de estilo IA, tuteo, sesiones ~40 min, limpieza Markdown y botón de niveles contextual.
# v7.10.45: promo lookup inmediato ampliado, guard factual Premium y espera IA máxima de 4 min.
# v7.10.27: conserva los flujos operativos de v7.10.26 y corrige
# enrutamiento contextual de IA, primer depósito y accesos VIP secuenciales.
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
            _append_ai_exchange(chat_id, pregunta, respuesta, assistant_source="auto")
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


class VIPInviteOverride(Base):
    """Enlaces VIP seguros creados por el propio bot (persisten entre deploys)."""
    __tablename__ = "vip_invite_override"
    access_key = Column(String, primary_key=True)
    invite_url = Column(Text)
    chat_id    = Column(String, index=True)
    updated_at = Column(DateTime, default=utcnow_naive, index=True)


class VIPAccessPause(Base):
    """Pausa persistente entre tandas de accesos para evitar límites de Telegram."""
    __tablename__ = "vip_access_pause"
    telegram_id = Column(String, primary_key=True)
    level       = Column(String, default="NONE", index=True)
    due_at      = Column(DateTime, index=True)
    updated_at  = Column(DateTime, default=utcnow_naive, index=True)


class PromoCodeConfig(Base):
    """Códigos promocionales editables desde el panel administrador.

    Se mantiene en una tabla independiente para no tocar el esquema histórico de
    usuarios. Los recordatorios enviados también quedan persistidos para evitar
    duplicados después de un redeploy de Railway.
    """
    __tablename__ = "promo_code_config"
    config_key          = Column(String, primary_key=True)
    code_100            = Column(String, default="TOP1_JOHATRADER")
    code_70             = Column(String, default="TOP_1JOHAALE")
    expires_on          = Column(String, default="2026-09-30")
    reminder_3d_for     = Column(String)
    reminder_expiry_for = Column(String)
    updated_at          = Column(DateTime, default=utcnow_naive, index=True)


engine = create_engine(DATABASE_URL, echo=False)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# === CÓDIGOS PROMOCIONALES DINÁMICOS ===
PROMO_CONFIG_KEY = "ACTIVE"
PROMO_DEFAULT_100 = "TOP1_JOHATRADER"
PROMO_DEFAULT_70 = "TOP_1JOHAALE"
PROMO_DEFAULT_EXPIRY = "2026-09-30"


def _promo_get_config():
    """Devuelve la configuración promocional vigente y la crea si aún no existe."""
    try:
        with Session() as session:
            row = session.get(PromoCodeConfig, PROMO_CONFIG_KEY)
            if not row:
                row = PromoCodeConfig(
                    config_key=PROMO_CONFIG_KEY,
                    code_100=PROMO_DEFAULT_100,
                    code_70=PROMO_DEFAULT_70,
                    expires_on=PROMO_DEFAULT_EXPIRY,
                )
                session.add(row)
                session.commit()
            return {
                "code_100": (row.code_100 or PROMO_DEFAULT_100).strip(),
                "code_70": (row.code_70 or PROMO_DEFAULT_70).strip(),
                "expires_on": (row.expires_on or PROMO_DEFAULT_EXPIRY).strip(),
                "reminder_3d_for": (row.reminder_3d_for or "").strip(),
                "reminder_expiry_for": (row.reminder_expiry_for or "").strip(),
            }
    except Exception as e:
        logging.warning("No pude leer códigos promocionales dinámicos: %s", e)
        return {
            "code_100": PROMO_DEFAULT_100,
            "code_70": PROMO_DEFAULT_70,
            "expires_on": PROMO_DEFAULT_EXPIRY,
            "reminder_3d_for": "",
            "reminder_expiry_for": "",
        }


def _promo_set_config(*, code_100=None, code_70=None, expires_on=None):
    """Actualiza solo los campos indicados y reinicia avisos al cambiar vencimiento."""
    with Session() as session:
        row = session.get(PromoCodeConfig, PROMO_CONFIG_KEY)
        if not row:
            row = PromoCodeConfig(
                config_key=PROMO_CONFIG_KEY,
                code_100=PROMO_DEFAULT_100,
                code_70=PROMO_DEFAULT_70,
                expires_on=PROMO_DEFAULT_EXPIRY,
            )
            session.add(row)
        if code_100 is not None:
            row.code_100 = str(code_100).strip()
        if code_70 is not None:
            row.code_70 = str(code_70).strip()
        if expires_on is not None:
            new_expiry = str(expires_on).strip()
            if new_expiry != (row.expires_on or "").strip():
                row.reminder_3d_for = None
                row.reminder_expiry_for = None
            row.expires_on = new_expiry
        row.updated_at = utcnow_naive()
        session.commit()


def _promo_parse_expiry(value: str):
    raw = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except Exception:
            pass
    return None


def _promo_expiry_display(value: str) -> str:
    d = _promo_parse_expiry(value)
    return d.strftime("%d/%m/%Y") if d else (value or "Sin fecha")


def _promo_ai_context(lang: str = "es") -> str:
    cfg = _promo_get_config()
    expiry_date = _promo_parse_expiry(cfg["expires_on"])
    expiry = _promo_expiry_display(cfg["expires_on"])
    today = datetime.now(COLOMBIA_TZ).date() if "COLOMBIA_TZ" in globals() else datetime.now().date()
    expired = bool(expiry_date and expiry_date < today)
    if lang == "en":
        if expired:
            return (
                f"PROMO STATUS: configured codes expired on {expiry}. DO NOT present them as active. "
                "Tell the user the promotion needs to be confirmed/updated before providing a code."
            )
        return (
            "CURRENT PROMOTIONAL CODES (authoritative, override old examples):\n"
            f"- 100% first-deposit bonus: {cfg['code_100']} (first deposit only, one-time use).\n"
            f"- 70% subsequent-deposit bonus: {cfg['code_70']} (subsequent deposits).\n"
            f"- Current expiry date: {expiry}."
        )
    if expired:
        return (
            f"ESTADO PROMO: los códigos configurados vencieron el {expiry}. NO los presentes como activos. "
            "Indica que la promoción debe confirmarse/actualizarse antes de entregar un código."
        )
    return (
        "CÓDIGOS PROMOCIONALES ACTUALES (fuente autoritativa, manda sobre ejemplos antiguos):\n"
        f"- Bono 100% primer depósito: {cfg['code_100']} (solo primer depósito, un solo uso).\n"
        f"- Bono 70% depósitos posteriores: {cfg['code_70']}.\n"
        f"- Fecha de vencimiento vigente: {expiry}."
    )


def _contains_active_promo_code(texto: str) -> bool:
    t = _norm(texto or "") if "_norm" in globals() else str(texto or "").lower()
    cfg = _promo_get_config()
    return any((code and str(code).lower() in t) for code in (cfg.get("code_100"), cfg.get("code_70")))


def _promo_admin_text() -> str:
    cfg = _promo_get_config()
    return (
        "🎟 CÓDIGOS PROMOCIONALES\n\n"
        f"💯 100% primer depósito: {cfg['code_100']}\n"
        f"🔥 70% depósitos posteriores: {cfg['code_70']}\n"
        f"📅 Vencimiento: {_promo_expiry_display(cfg['expires_on'])}\n\n"
        "Puedes cambiar los códigos o la fecha sin desplegar un MAIN nuevo."
    )


def _promo_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ CAMBIAR CÓDIGO 100%", callback_data="admin_panel_promo_code100")],
        [InlineKeyboardButton("✏️ CAMBIAR CÓDIGO 70%", callback_data="admin_panel_promo_code70")],
        [InlineKeyboardButton("📅 CAMBIAR VENCIMIENTO", callback_data="admin_panel_promo_expiry")],
        [InlineKeyboardButton("↩️ VOLVER AL PANEL", callback_data="admin_panel_promo_back")],
    ])


def _promo_mark_reminder(field: str, expiry_key: str):
    if field not in {"reminder_3d_for", "reminder_expiry_for"}:
        return
    try:
        with Session() as session:
            row = session.get(PromoCodeConfig, PROMO_CONFIG_KEY)
            if not row:
                return
            setattr(row, field, expiry_key)
            row.updated_at = utcnow_naive()
            session.commit()
    except Exception as e:
        logging.warning("No pude marcar recordatorio promo: %s", e)


async def _check_promo_expiry_reminders(bot):
    """Avisa a Johanna 3 días antes y el día exacto, una sola vez por vencimiento."""
    cfg = _promo_get_config()
    expiry = _promo_parse_expiry(cfg.get("expires_on"))
    if not expiry:
        return
    today = datetime.now(COLOMBIA_TZ).date() if "COLOMBIA_TZ" in globals() else datetime.now().date()
    days = (expiry - today).days
    expiry_key = expiry.isoformat()
    if days == 3 and cfg.get("reminder_3d_for") != expiry_key:
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ RECORDATORIO DE CÓDIGOS PROMOCIONALES\n\n"
                    f"Tus códigos promocionales vencen en 3 días, el {_promo_expiry_display(expiry_key)}.\n"
                    "Prepara los nuevos códigos y luego actualízalos desde ⚙️ MENÚ ADMIN → 🎟 CÓDIGOS PROMO."
                ),
            )
            _promo_mark_reminder("reminder_3d_for", expiry_key)
        except Exception as e:
            logging.warning("No pude enviar recordatorio promo 3 días: %s", e)
    elif days == 0 and cfg.get("reminder_expiry_for") != expiry_key:
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🚨 CÓDIGOS PROMOCIONALES · VENCEN HOY\n\n"
                    f"La fecha configurada es {_promo_expiry_display(expiry_key)}. Crea los nuevos códigos y actualízalos desde el panel administrador."
                ),
            )
            _promo_mark_reminder("reminder_expiry_for", expiry_key)
        except Exception as e:
            logging.warning("No pude enviar recordatorio promo de vencimiento: %s", e)


async def promo_expiry_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    await _check_promo_expiry_reminders(context.bot)


def schedule_promo_expiry_reminder(application):
    if not application.job_queue:
        return
    try:
        for job in application.job_queue.get_jobs_by_name("PROMO_EXPIRY_REMINDER"):
            job.schedule_removal()
    except Exception:
        pass
    application.job_queue.run_daily(
        promo_expiry_reminder_job,
        time=dt_time(hour=9, minute=0, tzinfo=COLOMBIA_TZ),
        name="PROMO_EXPIRY_REMINDER",
    )


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

# Los accesos VIP se validan por Telegram ID y nivel. Cuando el enlace genera
# solicitud, el bot la aprueba; si un enlace existente admite entrada directa,
# el handler chat_member detecta el ingreso y continúa el flujo secuencial.
VIP_ACCESS_CHANNELS = {
    "vip_main": {
        "name_es": "JT TRADERS TEAMS · VIP Principal",
        "name_en": "JT TRADERS TEAMS · Main VIP",
        "url": "https://t.me/+k1--4ts-vnc4ODUx",
        # ID real confirmado en prueba Telegram 18/09/2026.
        "chat_id": -1001946870620,
        "levels": (VIP_LEVEL_BASIC, VIP_LEVEL_PREMIUM, VIP_LEVEL_PRESTIGE),
        "desc_es": "Comunidad principal dividida por temas, con educación y metodología completa.",
        "desc_en": "Main community organized by topics, with education and the complete methodology.",
    },
    "crypto_basic": {
        "name_es": "Señales CRYPTO IDX · Básico",
        "name_en": "CRYPTO IDX Signals · Basic",
        "url": "https://t.me/+xYyDG-g72tM2OWJh",
        # ID real confirmado por solicitud Telegram 20/09/2026 (título: CRYPTO IDX VIP).
        "chat_id": -1002098088816,
        "levels": (VIP_LEVEL_BASIC,),
        "desc_es": "30–50 señales CRYPTO IDX de lunes a viernes. Entrada en el minuto exacto indicado, expiración de 1 minuto y hasta Martingala 2 opcional.",
        "desc_en": "30–50 CRYPTO IDX signals Monday to Friday. Enter at the exact indicated minute, 1-minute expiry, with optional Martingale up to level 2.",
    },
    "module3": {
        "name_es": "Binary Teams · Módulo 3 — Introducción al Análisis Bursátil",
        "name_en": "Binary Teams · Module 3 — Introduction to Market Analysis",
        "url": "https://t.me/+imlcZTiobAs0YTJh",
        # ID real confirmado en prueba Telegram 18/09/2026.
        "chat_id": -1001898859946,
        "levels": (VIP_LEVEL_BASIC, VIP_LEVEL_PREMIUM, VIP_LEVEL_PRESTIGE),
        "desc_es": "Curso de introducción al análisis bursátil para todos los niveles.",
        "desc_en": "Introduction to market analysis course for every level.",
    },
    "signals_premium": {
        "name_es": "Señales Premium +300",
        "name_en": "Premium Signals +300",
        # Enlace reconfirmado por Johanna el 18/09/2026.
        "url": "https://t.me/+fe5N2iolLGk0ZjBh",
        "levels": (VIP_LEVEL_PREMIUM, VIP_LEVEL_PRESTIGE),
        "desc_es": "+300 señales AL DÍA de lunes a sábado entre CRYPTO IDX, pares de divisas, índices sintéticos y Forex. Entrada en el minuto exacto indicado, expiración de 1 minuto y hasta Martingala 2 opcional.",
        "desc_en": "300+ signals PER DAY Monday to Saturday across CRYPTO IDX, currency pairs, synthetic indices and Forex. Enter at the exact indicated minute, 1-minute expiry, with optional Martingale up to level 2.",
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
        # ID real confirmado en prueba Telegram 18/09/2026.
        "chat_id": -1001929893768,
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

# Orden de entrega y bienvenida: VIP principal -> educación -> señales/bots.
VIP_LEVEL_CHANNEL_KEYS = {
    VIP_LEVEL_NONE: [],
    VIP_LEVEL_BASIC: ["vip_main", "module3", "crypto_basic"],
    VIP_LEVEL_PREMIUM: ["vip_main", "module3", "module4", "signals_premium", "ai_crypto"],
    VIP_LEVEL_PRESTIGE: ["vip_main", "module3", "module4", "madness", "signals_premium", "ai_crypto", "fx_auto"],
}

# Para evitar el mensaje de Telegram "demasiados intentos", el flujo hace una
# pausa automática tras 4 accesos confirmados consecutivos y luego continúa.
try:
    VIP_ACCESS_BATCH_SIZE = max(1, int(os.getenv("VIP_ACCESS_BATCH_SIZE", "4")))
except Exception:
    VIP_ACCESS_BATCH_SIZE = 4
try:
    VIP_ACCESS_PAUSE_MINUTES = max(1, int(os.getenv("VIP_ACCESS_PAUSE_MINUTES", "5")))
except Exception:
    VIP_ACCESS_PAUSE_MINUTES = 5


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
    if (state.get("level") or VIP_LEVEL_NONE) == VIP_LEVEL_PRESTIGE:
        return False
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
        "• Después del tercer depósito validado o una vez vencidos los 30 días, el upgrade requiere UN nuevo depósito que por sí solo alcance el monto mínimo completo del nuevo nivel.\n"
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
        new_level == VIP_LEVEL_PRESTIGE
        or new_count >= UPGRADE_ACCUM_MAX_DEPOSITS
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


def _vip_invite_override(access_key: str) -> str:
    try:
        with Session() as session:
            row = session.get(VIPInviteOverride, access_key)
            return (row.invite_url or "").strip() if row else ""
    except Exception as e:
        logging.warning("No pude leer override de enlace VIP %s: %s", access_key, e)
        return ""


def _vip_effective_invite_url(access_key: str) -> str:
    override = _vip_invite_override(access_key)
    if override:
        return override
    return ((VIP_ACCESS_CHANNELS.get(access_key) or {}).get("url") or "").strip()


def _vip_store_invite_override(access_key: str, chat_id: int, invite_url: str) -> bool:
    invite_url = (invite_url or "").strip()
    if not invite_url:
        return False
    try:
        with Session() as session:
            row = session.get(VIPInviteOverride, access_key)
            if not row:
                row = VIPInviteOverride(access_key=access_key)
                session.add(row)
            row.invite_url = invite_url
            row.chat_id = str(chat_id)
            row.updated_at = utcnow_naive()
            session.commit()
        return True
    except Exception as e:
        logging.warning("No pude guardar enlace VIP seguro %s: %s", access_key, e)
        return False


async def _vip_ensure_request_link(bot, access_key: str, *, notify_admin: bool = False) -> str:
    """Crea una sola vez un enlace del BOT que siempre requiera aprobación.

    Se usa especialmente para Señales Premium +300, cuyo enlace histórico se
    comportó como ingreso directo en Telegram. No revoca enlaces de otros admins.
    """
    existing = _vip_invite_override(access_key)
    if existing:
        return existing
    channel_id = _vip_mapped_chat_id(access_key)
    if not channel_id:
        return ""
    info = VIP_ACCESS_CHANNELS.get(access_key) or {}
    name = ("JT " + (info.get("name_es") or access_key))[:32]
    try:
        link = await bot.create_chat_invite_link(
            chat_id=channel_id,
            name=name,
            creates_join_request=True,
        )
        invite_url = (getattr(link, "invite_link", None) or "").strip()
        if invite_url and _vip_store_invite_override(access_key, channel_id, invite_url):
            logging.info("🔐 Enlace VIP con solicitud creado por el bot: %s / %s", access_key, channel_id)
            if notify_admin:
                try:
                    await bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            "🔐 ENLACE VIP SEGURO CREADO\n\n"
                            f"Canal: {info.get('name_es') or access_key}\n"
                            f"Chat ID: {channel_id}\n\n"
                            "Desde ahora el bot usará un enlace propio que exige solicitud de acceso."
                        ),
                    )
                except Exception:
                    pass
            return invite_url
    except Exception as e:
        logging.warning("No pude crear enlace VIP con solicitud para %s/%s: %s", access_key, channel_id, e)
    return ""


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
    "vip_main": ("JT TRADERS TEAMS", "JT TRADERS TEAMS VIP", "JT TRADERS VIP", "VIP PRINCIPAL"),
    "crypto_basic": ("CANAL DE SEÑALES CRYPTOIDX", "SEÑALES CRYPTO IDX", "CRYPTO IDX BASICO", "CRYPTO IDX BÁSICO"),
    "module3": ("BINARY TEAMS MODULO 3", "BINARY TEAMS MÓDULO 3", "BINARY TEAMS 3", "INTRODUCCION AL ANALISIS BURSATIL", "INTRODUCCIÓN AL ANÁLISIS BURSÁTIL"),
    "signals_premium": ("SEÑALES PREMIUM +300", "SENALES PREMIUM +300", "SEÑALES PREMIUM", "SENALES PREMIUM", "PREMIUM +300"),
    "ai_crypto": ("IA PREMIUM AUTOMATICAS CRYPTOIDX 24/7", "IA PREMIUM AUTOMÁTICAS CRYPTOIDX 24/7", "IA PREMIUM AUTOMATICA CRYPTO IDX 24/7"),
    "module4": ("BINARY TEAMS MODULO 4", "BINARY TEAMS MÓDULO 4", "BINARY TEAMS 4", "SMART MONEY CONCEPT"),
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


def _vip_access_key_from_chat(chat) -> str:
    """Identifica un canal VIP por chat_id aprendido/estático o por título.

    Sirve también para enlaces que Telegram admite de forma directa y por eso
    no generan ChatJoinRequest.
    """
    if not chat:
        return ""
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

    return _vip_access_key_from_chat(getattr(req, "chat", None))


def _vip_channel_allowed(level: str, access_key: str) -> bool:
    info = VIP_ACCESS_CHANNELS.get(access_key) or {}
    return bool(level in info.get("levels", ()))


def _vip_access_keyboard(level: str, lang: str, keys=None) -> InlineKeyboardMarkup:
    """Entrega un solo acceso por vez para reducir ráfagas/rate limits de Telegram."""
    keys = list(keys if keys is not None else _vip_channel_keys_for_level(level))
    rows = []
    if keys:
        key = keys[0]
        info = VIP_ACCESS_CHANNELS.get(key)
        if info:
            label = info["name_es"] if lang == "es" else info["name_en"]
            rows.append([InlineKeyboardButton(f"🔐 {label}", url=_vip_effective_invite_url(key))])
    return InlineKeyboardMarkup(rows)

def _vip_access_intro(level: str, lang: str, upgrade: bool = False) -> str:
    level_label = _vip_level_label(level, lang)
    if lang == "en":
        if upgrade:
            return (
                f"🔐 🎉 Congratulations! Your {level_label} level is now active.\n\n"
                "I’ll unlock your NEW channels one by one. Request access to the channel shown below; "
                "as soon as it is approved, I’ll send you the next one automatically."
            )
        return (
            f"🔐 🎉 Congratulations! Your {level_label} level access is ready.\n\n"
            "I’ll unlock your channels one by one. Request access to the channel shown below; "
            "as soon as it is approved, I’ll send you the next one automatically."
        )
    if upgrade:
        return (
            f"🔐 🎉 ¡Felicidades! Tu nivel {level_label} ya está activo.\n\n"
            "Voy a habilitarte los NUEVOS canales uno por uno. Solicita acceso al canal que aparece abajo; "
            "apenas quede aprobado, te enviaré automáticamente el siguiente."
        )
    return (
        f"🔐 🎉 ¡Felicidades! Ya están listos tus accesos del nivel {level_label}.\n\n"
        "Voy a habilitarte los canales uno por uno. Solicita acceso al canal que aparece abajo; "
        "apenas quede aprobado, te enviaré automáticamente el siguiente."
    )

def _vip_level_summary(level: str, lang: str) -> str:
    keys = _vip_channel_keys_for_level(level)
    education_keys = [k for k in ("module3", "module4", "madness") if k in keys]
    signal_keys = [k for k in ("crypto_basic", "signals_premium", "ai_crypto", "fx_auto") if k in keys]

    def item(key):
        info = VIP_ACCESS_CHANNELS[key]
        name = info["name_es"] if lang == "es" else info["name_en"]
        desc = info["desc_es"] if lang == "es" else info["desc_en"]
        return f"• {name}\n  {desc}"

    blocks = []
    if "vip_main" in keys:
        blocks.append(("👑 VIP PRINCIPAL" if lang == "es" else "👑 MAIN VIP") + "\n" + item("vip_main"))
    if education_keys:
        blocks.append(("🎓 EDUCACIÓN" if lang == "es" else "🎓 EDUCATION") + "\n" + "\n".join(item(k) for k in education_keys))
    if signal_keys:
        blocks.append(("📊 SEÑALES Y AUTOMATIZACIÓN" if lang == "es" else "📊 SIGNALS & AUTOMATION") + "\n" + "\n".join(item(k) for k in signal_keys))
    if level == VIP_LEVEL_PRESTIGE:
        extra = (
            "⭐ BENEFICIOS PRESTIGE ADICIONALES\n• Mentorías privadas\n• Acompañamiento cercano\n• Preparación para cuentas de fondeo\n• Forex automático: en construcción."
            if lang == "es" else
            "⭐ ADDITIONAL PRESTIGE BENEFITS\n• Private mentoring\n• Closer guidance\n• Funded-account preparation\n• Automatic Forex: under development."
        )
        blocks.append(extra)
    return "\n\n".join(blocks)


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


def _vip_pending_keys(chat_id: int):
    """Devuelve los accesos aún pendientes, conservando el orden del nivel."""
    try:
        state = _vip_get_state(chat_id, create=False) or {}
        pending = [x for x in (state.get("pending_keys") or []) if x in VIP_ACCESS_CHANNELS]
        level = state.get("level") or VIP_LEVEL_NONE
        order = _vip_channel_keys_for_level(level)
        return [k for k in order if k in pending]
    except Exception as e:
        logging.warning("No pude leer accesos VIP pendientes de %s: %s", chat_id, e)
        return []


def _vip_get_pause(chat_id: int):
    try:
        with Session() as session:
            row = session.get(VIPAccessPause, str(chat_id))
            if not row or not row.due_at:
                return None
            return {"level": row.level or VIP_LEVEL_NONE, "due_at": row.due_at}
    except Exception as e:
        logging.warning("No pude leer pausa VIP de %s: %s", chat_id, e)
        return None


def _vip_set_pause(chat_id: int, level: str, due_at: datetime):
    try:
        with Session() as session:
            row = session.get(VIPAccessPause, str(chat_id))
            if not row:
                row = VIPAccessPause(telegram_id=str(chat_id))
                session.add(row)
            row.level = level or VIP_LEVEL_NONE
            row.due_at = due_at
            row.updated_at = utcnow_naive()
            session.commit()
        return True
    except Exception as e:
        logging.warning("No pude guardar pausa VIP de %s: %s", chat_id, e)
        return False


def _vip_clear_pause(chat_id: int):
    try:
        with Session() as session:
            row = session.get(VIPAccessPause, str(chat_id))
            if row:
                session.delete(row)
                session.commit()
    except Exception as e:
        logging.warning("No pude limpiar pausa VIP de %s: %s", chat_id, e)


def _vip_pause_keyboard(lang: str) -> InlineKeyboardMarkup:
    label = "▶️ CONTINUAR MIS ACCESOS" if lang == "es" else "▶️ CONTINUE MY ACCESS"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="vip_continue_access")]])


def _vip_schedule_resume(context: ContextTypes.DEFAULT_TYPE, chat_id: int, due_at: datetime):
    if not context.job_queue:
        return
    name = f"VIP_ACCESS_RESUME_{chat_id}"
    try:
        for job in context.job_queue.get_jobs_by_name(name):
            job.schedule_removal()
    except Exception:
        pass
    delay = max(1, int((due_at - utcnow_naive()).total_seconds()))
    context.job_queue.run_once(
        _vip_resume_access_job,
        when=delay,
        data={"chat_id": int(chat_id)},
        name=name,
    )


async def _vip_resume_access_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = int(data.get("chat_id") or 0)
    if not _is_private_user_id(chat_id):
        return
    pause = _vip_get_pause(chat_id)
    if not pause:
        return
    due_at = pause.get("due_at")
    if due_at and due_at > utcnow_naive() + timedelta(seconds=1):
        _vip_schedule_resume(context, chat_id, due_at)
        return
    _vip_clear_pause(chat_id)
    state = _vip_get_state(chat_id, create=False) or {}
    level = state.get("level") or pause.get("level") or VIP_LEVEL_NONE
    if level == VIP_LEVEL_NONE:
        return
    await _vip_send_next_or_welcome(
        context,
        chat_id,
        level,
        get_user_lang(chat_id),
        just_completed="",
        should_welcome=False,
        bypass_pause=True,
    )


async def recover_pending_vip_access_pauses(application):
    """Recupera pausas VIP después de un redeploy de Railway."""
    try:
        with Session() as session:
            rows = session.query(VIPAccessPause).filter(VIPAccessPause.due_at.isnot(None)).all()
            pending = [(int(r.telegram_id), r.due_at) for r in rows if _is_private_user_id(r.telegram_id)]
    except Exception as e:
        logging.warning("No pude recuperar pausas VIP: %s", e)
        return
    for chat_id, due_at in pending:
        if due_at <= utcnow_naive():
            due_at = utcnow_naive() + timedelta(seconds=2)
        try:
            # application.job_queue expone la misma interfaz usada por Context.job_queue.
            name = f"VIP_ACCESS_RESUME_{chat_id}"
            for job in application.job_queue.get_jobs_by_name(name):
                job.schedule_removal()
            application.job_queue.run_once(
                _vip_resume_access_job,
                when=max(1, int((due_at - utcnow_naive()).total_seconds())),
                data={"chat_id": chat_id},
                name=name,
            )
        except Exception as e:
            logging.warning("No pude reprogramar pausa VIP de %s: %s", chat_id, e)


def _vip_activation_message(level: str, total_cents: int, lang: str, upgraded: bool = False) -> str:
    label = _vip_level_label(level, lang)
    if lang == "en":
        prefix = "✅ Additional deposit confirmed." if upgraded else "✅ Deposit confirmed."
        if level == VIP_LEVEL_PRESTIGE:
            return f"{prefix}\n\nYour current JT TRADERS TEAMS level is {label}."
        return (
            f"{prefix}\n\nYour current level is {label}.\n"
            "Upgrades are calculated from validated deposits within the enabled level-update period."
        )
    prefix = "✅ Depósito adicional confirmado." if upgraded else "✅ Depósito confirmado."
    if level == VIP_LEVEL_PRESTIGE:
        return f"{prefix}\n\nTu nivel actual en JT TRADERS TEAMS es {label}."
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

MENSAJE_B_3H_ES = """💰 Si este será tu primer depósito, tienes disponible un bono promocional del 100%.

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

MENSAJE_B_3H_EN = """💰 If this is your first deposit, a 100% promotional bonus is currently available.

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

MENSAJE_YA_TENGO_CUENTA_ES = """Si ya tienes una cuenta, primero envíame el ID para verificar si quedó registrada correctamente con mi enlace.

No hagas un depósito nuevo hasta que te confirme la validación. Si la cuenta no está vinculada conmigo, te indico el registro correcto de una nueva cuenta."""

MENSAJE_YA_TENGO_CUENTA_EN = """If you already have an account, first send me the account ID so I can verify whether it was correctly registered through my link.

Do not make a new deposit until I confirm the validation. If the account is not linked to me, I’ll explain the correct process for registering a new one."""

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

# Gestión de capital — texto informativo del BOTÓN del menú.
# Las consultas libres sobre gestión/VPN siguen escalándose directamente a Johanna.
GESTION_CAPITAL_BUTTON_ES = """📊 GESTIÓN DE CAPITAL

La gestión de cuenta/capital la manejo personalmente y cada caso se acuerda de forma individual.

🔹 Modalidad 3 meses — desde 200 USD
Objetivo estimado de 20–30% mensual, sujeto al resultado real de la operativa. Al finalizar el tercer mes se liquida el ciclo según resultados y se devuelve el capital correspondiente.

🔹 Modalidad 2 meses — desde 100 USD
Estructura orientativa de hasta 30 USD semanales durante 2 meses, sujeta a resultados.

🤝 También podemos acordar otras modalidades según el capital, el plazo, los objetivos y las condiciones de cada caso. Cualquier modalidad adicional se define conmigo directamente antes de iniciar.

⚠️ Estas cifras son objetivos, no ganancias garantizadas. El trading implica riesgo y puede haber pérdidas. Mi compromiso es gestionar con enfoque de preservación del capital, respetar al máximo lo acordado y explicarte claramente las condiciones antes de comenzar.

📩 Para revisar tu caso y acordar la modalidad adecuada, escríbeme directamente en mi chat personal."""

GESTION_CAPITAL_BUTTON_EN = """📊 CAPITAL MANAGEMENT

I handle account/capital management personally, and each case is agreed individually.

🔹 3-month option — from USD 200
Estimated target of 20–30% per month, subject to actual trading results. At the end of the third month, the cycle is settled according to results and the corresponding capital is returned.

🔹 2-month option — from USD 100
Indicative structure of up to USD 30 per week for 2 months, subject to results.

🤝 Other arrangements can also be agreed depending on capital, timeframe, objectives and the conditions of each case. Any additional arrangement is defined with me directly before starting.

⚠️ These figures are targets, not guaranteed profits. Trading involves risk and losses can occur. My commitment is to manage with a capital-preservation focus, respect the agreed terms as closely as possible, and explain the conditions clearly before starting.

📩 To review your case and agree on the right arrangement, message me directly in my personal chat."""

# Beneficios (ES/EN)
BENEFICIOS_ES = """✨ Beneficios JT TRADERS TEAMS ✨

✅ Básico — desde 50 USD: formación Binary Teams Módulos 1 al 3 + VIP principal + 30–50 señales CRYPTO IDX diarias de lunes a viernes.
✅ Premium — desde 200 USD: formación Binary Teams Módulos 1 al 4 (Módulo 4 Smart Money Concept), material de apoyo, sesiones/acompañamiento, Software Premium Anticipado con +300 señales AL DÍA de lunes a sábado e IA CRYPTO IDX 24/7.
✅ Prestige — desde 500 USD: todo Premium + Madness Trading Avanzado ALGO & LIT + bot IA de pares de divisas 24/7 + mentorías privadas, acompañamiento cercano y preparación para cuentas de fondeo.

⚡️ La comunidad es GRATUITA: el dinero se deposita directamente en TU propia cuenta de trading. Las herramientas habilitadas dependen del nivel alcanzado.
⚠️ Las entradas se realizan manualmente con gestión de riesgo; MG1/MG2 son opcionales.
"""

BENEFICIOS_EN = """✨ JT TRADERS TEAMS Benefits ✨

✅ Basic — from USD 50: Binary Teams Modules 1–3 + main VIP + 30–50 CRYPTO IDX signals per day, Monday to Friday.
✅ Premium — from USD 200: Binary Teams Modules 1–4 (Module 4 Smart Money Concept), support materials, live sessions/guidance, Premium Anticipated Software with 300+ signals PER DAY Monday to Saturday, and CRYPTO IDX AI 24/7.
✅ Prestige — from USD 500: everything in Premium + Madness Advanced Trading ALGO & LIT + 24/7 currency-pair AI bot + private mentoring, closer guidance and funded-account preparation.

⚡️ The community is FREE: funds are deposited directly into YOUR own trading account. Enabled tools depend on the level reached.
⚠️ Entries are taken manually with risk management; MG1/MG2 are optional.
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


def _vip_mapped_chat_id(access_key: str):
    """Devuelve el chat_id conocido de un acceso VIP (estático o aprendido)."""
    info = VIP_ACCESS_CHANNELS.get(access_key) or {}
    if info.get("chat_id"):
        try:
            return int(info["chat_id"])
        except Exception:
            pass
    try:
        with Session() as session:
            row = session.get(VIPChannelMap, access_key)
            if row and row.chat_id:
                return int(row.chat_id)
    except Exception as e:
        logging.warning("No pude leer chat_id VIP aprendido para %s: %s", access_key, e)
    return None


def _vip_is_active_member_status(status: str) -> bool:
    return str(status or "").lower() in {"member", "administrator", "creator", "restricted"}


async def _vip_reconcile_known_memberships(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Quita de pendientes accesos en los que el usuario ya es miembro.

    Esto evita que una prueba/reingreso se quede congelada en un canal que el
    usuario ya había obtenido antes. Solo consulta canales cuyo chat_id ya es
    conocido por configuración o por una solicitud previa.
    """
    welcomed = False
    while True:
        pending = _vip_pending_keys(chat_id)
        if not pending:
            return welcomed
        access_key = pending[0]
        channel_id = _vip_mapped_chat_id(access_key)
        if not channel_id:
            return welcomed
        try:
            member_state = await context.bot.get_chat_member(chat_id=channel_id, user_id=chat_id)
            status = getattr(member_state, "status", "")
        except Exception as e:
            logging.info("No pude verificar membresía existente %s/%s: %s", chat_id, access_key, e)
            return welcomed

        if not _vip_is_active_member_status(status):
            return welcomed

        _log_event(chat_id, "VIP_ACCESS_ALREADY_MEMBER", access_key)
        _tracking_fire_event(chat_id, "VIP_ACCESS_ALREADY_MEMBER", access_key)
        level, should_welcome = _vip_mark_access_approved(chat_id, access_key)
        welcomed = welcomed or should_welcome
        logging.info(
            "♻️ Acceso VIP ya existente reconciliado: %s / %s / nivel=%s",
            chat_id, access_key, level
        )


async def _vip_notify_admin_completed(context: ContextTypes.DEFAULT_TYPE, chat_id: int, level: str):
    """Avisa a Johanna cuando el usuario completó TODOS los accesos de su nivel."""
    try:
        display = str(chat_id)
        username = ""
        try:
            chat = await context.bot.get_chat(chat_id)
            full_name = " ".join(x for x in [getattr(chat, "first_name", None), getattr(chat, "last_name", None)] if x).strip()
            username = (getattr(chat, "username", None) or "").strip()
            display = (f"@{username}" if username else full_name) or str(chat_id)
        except Exception:
            pass
        keys = _vip_channel_keys_for_level(level)
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "✅ INGRESO VIP COMPLETADO\n\n"
                f"Usuario: {display}\n"
                f"Telegram ID: {chat_id}\n"
                f"Nivel JT TRADERS TEAMS: {_vip_level_label(level, 'es')}\n"
                f"Accesos completados: {len(keys)}/{len(keys)}\n\n"
                "🎉 El usuario ya completó todos los accesos y recibió su mensaje final de bienvenida."
            ),
        )
    except Exception as e:
        logging.warning("No pude avisar al admin del ingreso VIP completo de %s: %s", chat_id, e)


async def _vip_send_next_or_welcome(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    level: str,
    lang: str,
    *,
    just_completed: str = "",
    should_welcome: bool = False,
    bypass_pause: bool = False,
):
    """Continúa el flujo VIP con UN solo botón, pausa anti-flood o bienvenida."""
    reconciled_welcome = await _vip_reconcile_known_memberships(context, chat_id)
    should_welcome = should_welcome or reconciled_welcome
    remaining = _vip_pending_keys(chat_id)

    if remaining:
        # Tras una tanda de accesos consecutivos hacemos una pausa persistente.
        # Esto reduce el riesgo de que Telegram responda "demasiados intentos".
        all_keys = _vip_channel_keys_for_level(level)
        completed_count = max(0, len(all_keys) - len(remaining))
        should_pause_now = (
            not bypass_pause
            and bool(just_completed)
            and len(all_keys) > VIP_ACCESS_BATCH_SIZE
            and completed_count == VIP_ACCESS_BATCH_SIZE
        )
        if should_pause_now:
            due_at = utcnow_naive() + timedelta(minutes=VIP_ACCESS_PAUSE_MINUTES)
            _vip_set_pause(chat_id, level, due_at)
            _vip_schedule_resume(context, chat_id, due_at)
            wait_text = (
                f"✅ Ya tienes {completed_count} accesos confirmados.\n\n"
                f"⏳ Telegram puede limitar varias incorporaciones consecutivas. Para evitar que te aparezca «demasiados intentos», haré una pausa de {VIP_ACCESS_PAUSE_MINUTES} minutos y luego te enviaré automáticamente el siguiente acceso.\n\n"
                "Si vuelves más tarde, puedes usar el botón CONTINUAR MIS ACCESOS y retomarás exactamente desde donde quedaste."
                if lang == "es" else
                f"✅ You already have {completed_count} confirmed accesses.\n\n"
                f"⏳ Telegram may temporarily limit several consecutive joins. To reduce the chance of a “too many attempts” message, I’ll pause for {VIP_ACCESS_PAUSE_MINUTES} minutes and then automatically send your next access.\n\n"
                "If you come back later, use CONTINUE MY ACCESS and you’ll resume exactly where you left off."
            )
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=wait_text,
                    reply_markup=_vip_pause_keyboard(lang),
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logging.warning("No pude avisar pausa VIP a %s: %s", chat_id, e)
            return

        # Si existe una pausa aún vigente por una ejecución anterior, no mandamos
        # otro enlace hasta que venza o el usuario use CONTINUAR después del plazo.
        if not bypass_pause:
            pause = _vip_get_pause(chat_id)
            if pause and pause.get("due_at") and pause["due_at"] > utcnow_naive():
                _vip_schedule_resume(context, chat_id, pause["due_at"])
                return

        _vip_clear_pause(chat_id)
        next_key = remaining[0]
        next_info = VIP_ACCESS_CHANNELS.get(next_key) or {}
        next_name = next_info.get("name_es") if lang == "es" else next_info.get("name_en")
        if not next_name:
            next_name = next_key
        text_value = (
            f"✅ Acceso confirmado. Ahora solicita el siguiente: {next_name}."
            if lang == "es" else
            f"✅ Access confirmed. Now request the next one: {next_name}."
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text_value,
                reply_markup=_vip_access_keyboard(level, lang, keys=[next_key]),
                disable_web_page_preview=True,
            )
        except Exception as e:
            logging.warning(
                "No pude enviar el siguiente acceso VIP a %s tras %s: %s",
                chat_id, just_completed or "(sin clave)", e
            )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        "⚠️ FLUJO VIP DETENIDO\n\n"
                        f"Usuario ID: {chat_id}\n"
                        f"Acceso completado: {just_completed or '(sin clave)'}\n"
                        f"Siguiente acceso: {next_key}\n"
                        f"Error: {str(e)[:700]}"
                    ),
                )
            except Exception:
                pass
        return

    _vip_clear_pause(chat_id)
    # Si acabamos de retirar el último pendiente, enviamos cierre aunque el
    # welcome_level hubiese quedado marcado en una prueba anterior.
    if just_completed or should_welcome:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=_vip_final_welcome_text(level, lang),
                reply_markup=support_keyboard(lang),
                disable_web_page_preview=True,
            )
            await _vip_notify_admin_completed(context, chat_id, level)
        except Exception as e:
            logging.warning("Accesos VIP completos para %s, pero no pude enviar bienvenida: %s", chat_id, e)


async def _vip_finalize_confirmed_membership(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    access_key: str,
    *,
    source: str,
):
    """Marca un acceso únicamente cuando Telegram confirma membresía real.

    Es idempotente: si el canal ya no estaba pendiente no avanza dos veces.
    """
    pending_before = _vip_pending_keys(chat_id)
    if access_key not in pending_before:
        logging.info(
            "ℹ️ Membresía VIP ya procesada/no pendiente: %s / %s / source=%s",
            chat_id, access_key, source
        )
        return False

    event_name = "VIP_ACCESS_APPROVED" if source == "join_request" else "VIP_ACCESS_DIRECT_JOIN"
    _log_event(chat_id, event_name, access_key)
    _tracking_fire_event(chat_id, event_name, access_key)
    _prune_pending_ai_after_operation(
        context, chat_id, ["VIP_ACCESS"], reason=f"acceso VIP confirmado: {access_key}"
    )
    level, should_welcome = _vip_mark_access_approved(chat_id, access_key)
    logging.info(
        "✅ Membresía VIP confirmada: %s / %s / nivel=%s / source=%s",
        chat_id, access_key, level, source
    )
    await _vip_send_next_or_welcome(
        context,
        chat_id,
        level,
        get_user_lang(chat_id),
        just_completed=access_key,
        should_welcome=should_welcome,
    )
    return True


async def cleanup_vip_membership_service_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Protege privacidad en espacios VIP y mantiene vacío el General de Premium +300.

    1) En cualquier grupo VIP conocido elimina avisos de alta/baja que exponen nombres.
    2) En Señales Premium +300 elimina CUALQUIER mensaje nuevo que Telegram coloque
       dentro del tema General (thread_id=1), para que ese tema no vuelva a acumular
       contenido visible. No toca ningún otro tema de señales.

    Importante: el Bot API no permite recorrer/borrar de forma retroactiva todo el
    historial antiguo del tema General; esta rutina evita nueva acumulación.
    """
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return

    access_key = _vip_access_key_from_chat(chat)
    if not access_key:
        return

    joined = list(getattr(msg, "new_chat_members", None) or [])
    left = getattr(msg, "left_chat_member", None)
    membership_service = bool(joined or left)

    try:
        thread_id = int(getattr(msg, "message_thread_id", 0) or 0)
    except Exception:
        thread_id = 0
    premium_general = access_key == "signals_premium" and thread_id == 1

    if not membership_service and not premium_general:
        return

    try:
        await context.bot.delete_message(chat_id=chat.id, message_id=msg.message_id)
        logging.info(
            "🧹 Privacidad VIP: mensaje eliminado chat=%s access=%s thread=%s message_id=%s tipo=%s",
            chat.id,
            access_key,
            thread_id,
            msg.message_id,
            "GENERAL_PREMIUM" if premium_general else ("JOIN" if joined else "LEAVE"),
        )
    except Exception as e:
        logging.warning(
            "No pude eliminar mensaje de privacidad VIP: chat=%s access=%s thread=%s message_id=%s error=%s",
            getattr(chat, "id", None),
            access_key,
            thread_id,
            getattr(msg, "message_id", None),
            e,
        )
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ LIMPIEZA PRIVACIDAD VIP\n\n"
                    f"No pude borrar un mensaje en: {getattr(chat, 'title', None) or access_key}\n"
                    f"Chat ID: {getattr(chat, 'id', None)}\n"
                    f"Tema/Thread ID: {thread_id or 'sin thread'}\n"
                    f"Mensaje ID: {getattr(msg, 'message_id', None)}\n\n"
                    "Revisa que JOHAALETRADER_bot conserve el permiso para eliminar mensajes."
                ),
            )
        except Exception:
            pass


async def tracking_channel_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirma membresías VIP reales y conserva el tracking del canal informativo ES."""
    change = getattr(update, "chat_member", None)
    chat = update.effective_chat
    if not change or not chat:
        return

    old_status = str(getattr(getattr(change, "old_chat_member", None), "status", "") or "").lower()
    new_status = str(getattr(getattr(change, "new_chat_member", None), "status", "") or "").lower()
    if not _vip_is_active_member_status(new_status) or _vip_is_active_member_status(old_status):
        return

    member = getattr(getattr(change, "new_chat_member", None), "user", None)
    if not member or not _is_private_user_id(getattr(member, "id", None)):
        return

    # 1) VIP: este evento es la confirmación definitiva de que el usuario YA
    # pertenece al canal. Funciona tanto tras aprobar una solicitud como en un
    # ingreso directo. No dependemos de via_join_request, porque Telegram puede
    # omitir/variar ese indicador según el tipo de enlace.
    access_key = _vip_access_key_from_chat(chat)
    if access_key:
        chat_id = int(member.id)
        # Señales Premium +300 mostró comportamiento de ingreso directo con el
        # enlace histórico. Al conocer el chat_id real, creamos y persistimos un
        # enlace propio del bot que SIEMPRE exige solicitud para futuros usuarios.
        if access_key == "signals_premium":
            await _vip_ensure_request_link(context.bot, access_key, notify_admin=True)
        stage = get_user_stage(chat_id)
        state = _vip_get_state(chat_id, create=False)
        if state:
            authorized = stage == STAGE_DEPOSITED and _vip_channel_allowed(state.get("level"), access_key)
        else:
            authorized = stage == STAGE_DEPOSITED and access_key in ("vip_main", "module3")

        if not authorized:
            removed = False
            remove_error = ""
            try:
                # Si alguien usa un enlace viejo/compartido que permite entrada directa,
                # retiramos el acceso. Unban inmediato permite que luego pueda solicitar
                # correctamente si llega a tener el nivel correspondiente.
                await context.bot.ban_chat_member(chat_id=chat.id, user_id=chat_id)
                try:
                    await context.bot.unban_chat_member(chat_id=chat.id, user_id=chat_id, only_if_banned=True)
                except Exception:
                    pass
                removed = True
            except Exception as e:
                remove_error = str(e)[:500]
                logging.warning("No pude retirar ingreso VIP no autorizado %s/%s: %s", chat_id, access_key, e)
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        "⚠️ INGRESO VIP NO AUTORIZADO\n\n"
                        f"Canal: {getattr(chat, 'title', None) or access_key}\n"
                        f"Chat ID: {getattr(chat, 'id', None)}\n"
                        f"Usuario: {_telegram_display_name(member)} (ID: {chat_id})\n\n"
                        + (
                            "🛡️ El bot retiró automáticamente al usuario del canal."
                            if removed else
                            "⚠️ No pude retirarlo automáticamente. Revisa el permiso de expulsar/restringir usuarios del bot."
                        )
                        + (f"\nError: {remove_error}" if remove_error else "")
                    ),
                )
            except Exception:
                pass
            logging.warning("⚠️ Membresía VIP no autorizada: %s / %s", chat_id, access_key)
            return

        await _vip_finalize_confirmed_membership(
            context,
            chat_id,
            access_key,
            source="member_update",
        )
        return

    # Si el usuario está en pleno flujo VIP y Telegram confirma un alta en un
    # canal que aún no reconocemos, avisamos al admin en vez de fallar en silencio.
    pending_user = _vip_pending_keys(int(member.id))
    if pending_user and not _is_tracking_info_channel(chat):
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ ALTA VIP NO MAPEADA\n\n"
                    f"Canal: {getattr(chat, 'title', None) or '(sin título)'}\n"
                    f"Chat ID: {getattr(chat, 'id', None)}\n"
                    f"Usuario: {_telegram_display_name(member)} (ID: {member.id})\n"
                    f"Pendientes esperados: {', '.join(pending_user)}"
                ),
            )
        except Exception:
            pass
        logging.warning(
            "⚠️ Alta VIP no mapeada durante flujo: chat=%s title=%s user=%s",
            getattr(chat, "id", None), getattr(chat, "title", None), member.id
        )
        return

    # 2) TRACKING DEL CANAL INFORMATIVO ES — comportamiento anterior intacto.
    if not _is_tracking_info_channel(chat):
        return

    invite_obj = getattr(change, "invite_link", None)
    invite_link = (getattr(invite_obj, "invite_link", None) or "").strip()
    invite_name = (getattr(invite_obj, "name", None) or "").strip()

    detected_source = "ADS" if (
        invite_name.upper().startswith("SOURCE-ADS")
        or invite_name.startswith("track-JT-")
    ) else "ORGANIC_OTHER"

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
    """Aprueba solicitudes VIP; el flujo avanza al confirmar membresía real."""
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

        # Guardamos siempre el ID real del canal recibido por Telegram. Así, desde
        # la primera solicitud ya no dependemos del texto del título ni del token.
        _vip_learn_channel(access_key, getattr(req, "chat", None))
        if access_key == "signals_premium":
            await _vip_ensure_request_link(context.bot, access_key, notify_admin=True)
        logging.info(
            "🔎 Solicitud VIP mapeada: user=%s access=%s chat_id=%s title=%s",
            chat_id,
            access_key,
            getattr(getattr(req, "chat", None), "id", None),
            getattr(getattr(req, "chat", None), "title", None),
        )

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
                    "La cuenta está DEPOSITED pero aún no tiene nivel migrado. "
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

        # No damos por completado el acceso únicamente porque approveChatJoinRequest
        # devolvió True. Primero comprobamos si Telegram ya convirtió al usuario en
        # miembro. Si todavía muestra “Unirme al canal”, dejamos el acceso pendiente
        # hasta recibir ChatMemberUpdated al completar realmente el ingreso.
        active_now = False
        try:
            member_state = await context.bot.get_chat_member(chat_id=req.chat.id, user_id=chat_id)
            active_now = _vip_is_active_member_status(getattr(member_state, "status", ""))
        except Exception as e:
            logging.info(
                "Solicitud VIP aprobada %s/%s; no pude verificar membresía inmediata: %s",
                chat_id, access_key, e
            )

        if active_now:
            await _vip_finalize_confirmed_membership(
                context,
                chat_id,
                access_key,
                source="join_request",
            )
        else:
            lang = get_user_lang(chat_id)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "✅ Tu solicitud fue aprobada. Si Telegram te muestra el botón «Unirme al canal», tócalo para completar el ingreso. En cuanto Telegram confirme que ya entraste, te enviaré automáticamente el siguiente acceso."
                        if lang == "es" else
                        "✅ Your request was approved. If Telegram shows “Join Channel”, tap it to complete the join. As soon as Telegram confirms you are inside, I’ll automatically send your next access."
                    ),
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logging.warning("Solicitud VIP aprobada para %s, pero no pude avisar al usuario: %s", chat_id, e)
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
    """Único CTA para casos que realmente requieren atención personal."""
    label = "📩 CHAT WITH ME PERSONALLY" if lang == "en" else "📩 HABLAR CONMIGO EN MI CHAT PERSONAL"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, url=SUPPORT_URL)],
    ])

def _ai_needs_levels_button(question: str, current_level: str = VIP_LEVEL_NONE, current_stage: str = None) -> bool:
    """Muestra el CTA de niveles solo cuando la intención REAL lo justifica.

    Para cualquier miembro activo (Básico/Premium/Prestige), su nivel guardado manda:
    preguntar por SUS señales, bots, cursos, un depósito adicional o la interfaz del live
    no debe disparar automáticamente el botón de niveles. El CTA se reserva para una
    consulta explícita/general de niveles o un escenario claramente hipotético de otra persona.
    """
    t = _norm(question or "")
    if not t:
        return False

    explicit_general = any(x in t for x in (
        "que niveles", "qué niveles", "niveles disponibles", "todos los niveles",
        "diferencia entre niveles", "diferencia de niveles", "comparar niveles",
        "comparacion de niveles", "comparación de niveles", "planes disponibles",
        "ver niveles", "quiero ver los niveles", "muestrame los niveles", "muéstrame los niveles",
        "estructura de niveles", "what levels", "available levels", "compare levels", "show me the levels",
    ))
    hypothetical_other = _is_hypothetical_other_person(question) or any(x in t for x in (
        "para alguien", "una persona nueva", "un usuario nuevo", "alguien nuevo",
        "for someone", "new user", "a new user",
    ))

    active_member = (current_stage == STAGE_DEPOSITED and current_level != VIP_LEVEL_NONE)
    if active_member:
        # El usuario ya tiene un nivel real. No mostramos niveles por sus propias
        # herramientas, montos o depósitos; solo cuando pide comparar/ver niveles
        # o está hablando claramente de otra persona.
        return bool(explicit_general or hypothetical_other)

    currency_amount = bool(re.search(
        r"(?:\$\s*\d{2,5}(?:[.,]\d{1,2})?|\b\d{2,5}(?:[.,]\d{1,2})?\s*(?:usd|dolares|dólares)\b)", t
    ))
    bare_level_amount = bool(re.search(
        r"\b(?:con|entro con|entrar con|deposito de|depósito de|depositar|capital de)\s+\$?\s*\d{2,5}(?:[.,]\d{1,2})?\b", t
    ))
    workflow_amount = bool(re.search(
        r"\b(?:tengo|cuento con|dispongo de|tendria|tendría)\s+\$?\s*\d{2,5}(?:[.,]\d{1,2})?\b", t
    )) and any(x in t for x in (
        "que me toca", "qué me toca", "que hago", "qué hago", "como sigo", "cómo sigo",
        "por donde empiezo", "por dónde empiezo", "what do i do", "what next", "how do i start",
    ))
    level_related_detail = any(x in t for x in (
        "premium", "prestige", "basico", "básico", "nivel", "que incluye", "qué incluye",
        "beneficios", "madness", "modulo", "módulo", "curso", "formacion", "formación",
    ))
    explicit_level_planning = (
        (currency_amount or bare_level_amount) and level_related_detail
    ) or explicit_general

    direct = (
        "nivel", "niveles", "plan", "planes", "que incluye", "qué incluye",
        "que recibo", "qué recibo", "diferencia", "cuanto necesito", "cuánto necesito",
        "cuanto cuesta", "cuánto cuesta", "cuanto debo depositar", "cuánto debo depositar",
        "acceso al bot", "quiero el bot", "puedo tener el bot", "bot es posible",
        "acceso a las señales", "acceso a las senales",
        "con 50", "con 100", "con 200", "con 300", "con 500",
    )
    if hypothetical_other or explicit_level_planning or workflow_amount or any(x in t for x in direct):
        return True
    return t.strip() in {"basico", "básico", "premium", "prestige"}


def ai_context_keyboard(question: str, lang: str = "es", chat_id: int = None):
    """CTA contextual mínimo: respeta etapa + nivel real antes de mostrar niveles."""
    current_level = VIP_LEVEL_NONE
    current_stage = None
    if chat_id is not None:
        try:
            current_level = (_vip_get_state(chat_id, create=False) or {}).get("level") or VIP_LEVEL_NONE
            current_stage = get_user_stage(chat_id)
        except Exception:
            current_level = VIP_LEVEL_NONE
            current_stage = None
    rows = []
    if _ai_needs_levels_button(question, current_level=current_level, current_stage=current_stage):
        label = "📊 VIEW MY COMMUNITY LEVELS" if lang == "en" else "📊 MIRA LOS NIVELES DE MI COMUNIDAD"
        callback = "levels_plans_en" if lang == "en" else "niveles_planes"
        rows.append([InlineKeyboardButton(label, callback_data=callback)])
    return InlineKeyboardMarkup(rows) if rows else None


def _is_bonus_or_promo_mention(texto: str) -> bool:
    """Reconoce menciones naturales de bonos/códigos/promociones sin exigir la palabra bono."""
    t = _norm(texto or "")
    terms = (
        "bono", "bonos", "bonus", "100%", "70%",
        "codigo promocional", "codigos promocionales", "codigo de promocion", "codigos de promocion",
        "cuales son los codigos", "que codigos tienes", "codigos activos", "codigo activo",
        "promocion", "promociones", "promo", "promos", "promotional code", "promo code", "promo codes",
    )
    return any(term in t for term in terms) or _contains_active_promo_code(texto)


def _is_simple_bonus_lookup(texto: str) -> bool:
    """Solo códigos/bonos activos; recomendaciones y condiciones pasan a IA."""
    t = _norm(texto or "")
    nuanced = (
        "recomiend", "conviene", "deberia", "debería", "condicion", "condición", "requisito", "volumen",
        "mover", "liberar", "retiro", "retirar", "ganancia", "gestionar", "gestion", "gestión", "riesgo",
        "cuanto tarda", "cuánto tarda", "meses", "que pasa", "qué pasa",
    )
    if any(x in t for x in nuanced):
        return False
    simple = (
        "que bonos", "qué bonos", "bonos activos", "bono activo", "codigo de bono", "código de bono",
        "codigo bono", "código bono", "cual es el bono", "cuál es el bono", "bono 100", "bono 70",
        "codigo promocional", "código promocional", "codigos promocionales", "códigos promocionales",
        "cuales son los codigos", "cuáles son los códigos", "que codigos tienes", "qué códigos tienes",
        "codigos activos", "códigos activos", "codigo activo", "código activo",
        "promos activas", "promociones activas", "promociones vigentes", "que promociones", "qué promociones", "que promos", "qué promos",
        "promo activa", "promocion activa", "promoción activa",
    )
    return any(x in t for x in simple) or t.strip() in {"bono", "bonos", "bonus", "promo", "promos", "promocion", "promoción", "promociones"} or _contains_active_promo_code(texto)


def _is_simple_levels_lookup(texto: str) -> bool:
    """Consulta general de niveles; montos/casos concretos pasan a IA."""
    t = _norm(texto or "")
    contextual = (
        "recomiend", "conviene", "diferencia", "con 50", "con 100", "con 200", "con 300", "con 500",
        "fondeando", "depositando", "acceso", "bot", "señal", "senal", "software", "para mi", "me sirve",
    )
    if any(x in t for x in contextual):
        return False
    return any(x in t for x in ("que niveles", "qué niveles", "niveles disponibles", "planes disponibles")) or t.strip() in {"niveles", "planes"}


def _is_vip_access_rate_limit_query(texto: str, vip_flow_active: bool = False) -> bool:
    """Detecta el límite de Telegram sin confundirlo con login de un broker.

    Si el usuario está en pleno flujo VIP, frases ambiguas como “no me deja
    ingresar, demasiados intentos” se interpretan usando ese contexto real.
    """
    t = _norm(texto or "")
    limit_terms = (
        "demasiados intentos", "muchos intentos", "demasiado intentos",
        "intentalo mas tarde", "inténtalo más tarde", "intente mas tarde",
        "try again later", "too many attempts", "too many tries",
    )
    if not any(_norm(x) in t for x in limit_terms):
        return False

    strong_access_terms = (
        "telegram", "canal", "canales", "acceso", "accesos", "enlace", "enlaces",
        "unirme", "unir", "solicitud", "solicitar", "vip", "grupo", "grupos",
    )
    if any(x in t for x in strong_access_terms):
        return True

    # Evita secuestrar un error explícito de login/contraseña del broker.
    broker_terms = (
        "binomo", "stockity", "broker", "contraseña", "contrasena", "password",
        "correo", "email", "iniciar sesion", "inicio de sesion", "login",
    )
    if any(x in t for x in broker_terms):
        return False

    ambiguous_join_terms = ("ingresar", "entrar", "no me deja", "intentar", "intento")
    return bool(vip_flow_active and any(x in t for x in ambiguous_join_terms))


def _vip_rate_limit_message(lang: str = "es") -> str:
    if lang == "en":
        return (
            "⏳ That ‘too many attempts’ notice is coming from Telegram after several channel joins or access requests in a short period. "
            "It is not related to your Binomo/Stockity password or your broker account.\n\n"
            f"Your confirmed accesses are not lost. I’ll pause the remaining access flow for about {VIP_ACCESS_PAUSE_MINUTES} minutes and then continue from the next pending channel automatically. "
            "If Telegram still shows the same notice after the pause, wait a little longer before trying again.\n\n"
            "You can also use CONTINUE MY ACCESS after the pause to resume exactly where you left off. ✅"
        )
    return (
        "⏳ Ese aviso de «demasiados intentos» viene de Telegram cuando se hacen varias solicitudes o ingresos a canales en poco tiempo. "
        "No tiene relación con tu contraseña ni con tu cuenta de Binomo o Stockity.\n\n"
        f"Tus accesos ya confirmados no se pierden. Voy a pausar los accesos restantes aproximadamente {VIP_ACCESS_PAUSE_MINUTES} minutos y después continuaré automáticamente desde el siguiente canal pendiente. "
        "Si Telegram todavía muestra el mismo aviso al terminar la pausa, espera un poco más antes de volver a intentarlo.\n\n"
        "También puedes usar CONTINUAR MIS ACCESOS después de la pausa para retomar exactamente donde quedaste. ✅"
    )


def _ai_runtime_context(chat_id: int, lang: str = "es") -> str:
    """Contexto operativo REAL y de solo lectura para decisiones de la IA.

    Expone etapa, estado de validación, brokers y nivel sin modificar ningún dato.
    La IA debe decidir el siguiente paso a partir de este estado antes de usar la
    base comercial/general.
    """
    stage_now = get_user_stage(chat_id)
    if lang == "en":
        lines = [f"Bot stage: {stage_now}"]
        stage_labels = {
            STAGE_PRE: "PRE = registration/ID validation not completed yet",
            STAGE_POST: "POST = ID validated; waiting for deposit/proof",
            STAGE_DEPOSITED: "DEPOSITED = account active; do not restart registration/ID",
        }
        lines.append(stage_labels.get(stage_now, stage_now))
    else:
        lines = [f"Etapa del bot: {stage_now}"]
        stage_labels = {
            STAGE_PRE: "PRE = registro/validación de ID todavía no completados",
            STAGE_POST: "POST = ID validado; pendiente depósito/comprobante",
            STAGE_DEPOSITED: "DEPOSITED = cuenta activa; no reiniciar registro/ID",
        }
        lines.append(stage_labels.get(stage_now, stage_now))

    try:
        pending_review = _has_pending_id_review(chat_id)
        strict_valid = _strict_validated_id_state(chat_id)
        if lang == "en":
            lines.append(f"ID currently pending admin review: {'yes' if pending_review else 'no'}")
            lines.append(f"Strict validated-ID evidence: {'yes' if strict_valid else 'no'}")
        else:
            lines.append(f"ID actualmente pendiente de revisión admin: {'sí' if pending_review else 'no'}")
            lines.append(f"Evidencia estricta de ID validado: {'sí' if strict_valid else 'no'}")
    except Exception:
        pass

    try:
        rows = _broker_rows(chat_id)
        if rows:
            broker_parts = []
            for state in rows:
                label = _broker_label(state.get("broker"))
                if state.get("id_validated"):
                    status = "validated" if lang == "en" else "validado"
                elif state.get("pending_trading_id"):
                    status = "pending review" if lang == "en" else "pendiente de revisión"
                else:
                    status = "not validated" if lang == "en" else "no validado"
                broker_parts.append(f"{label}: ID {status}; {_vip_level_label(state.get('level'), lang)}")
            lines.append(("Broker/account states: " if lang == "en" else "Estado por broker/cuenta: ") + "; ".join(broker_parts))
    except Exception:
        pass

    try:
        with Session() as session:
            row = session.query(Usuario.nombre).filter(Usuario.telegram_id == str(chat_id)).first()
            visible_name = (row[0] or "").strip() if row else ""
        if visible_name:
            if lang == "en":
                lines.append(f"Visible user name: {visible_name}. Use it only occasionally when natural.")
            else:
                lines.append(f"Nombre visible del usuario: {visible_name}. Puedes usarlo ocasionalmente si suena natural; no lo repitas en cada respuesta.")
    except Exception:
        pass

    try:
        state = _vip_get_state(chat_id, create=False) or {}
        level = state.get("level") or VIP_LEVEL_NONE
        if level != VIP_LEVEL_NONE:
            lines.append(("Active JT TRADERS TEAMS level: " if lang == "en" else "Nivel JT TRADERS TEAMS activo: ") + _vip_level_label(level, lang))
            if level == VIP_LEVEL_PREMIUM:
                lines.append("Included AI bots: CRYPTO IDX 24/7" if lang == "en" else "Bots IA incluidos en su nivel: CRYPTO IDX 24/7")
            elif level == VIP_LEVEL_PRESTIGE:
                lines.append("Included AI bots: CRYPTO IDX 24/7 + currency pairs 24/7" if lang == "en" else "Bots IA incluidos en su nivel: CRYPTO IDX 24/7 + pares de divisas 24/7")
            pending = _vip_pending_keys(chat_id)
            if pending:
                names = []
                for key in pending:
                    info = VIP_ACCESS_CHANNELS.get(key) or {}
                    name = info.get("name_en") if lang == "en" else info.get("name_es")
                    names.append(name or key)
                lines.append(("Pending VIP accesses: " if lang == "en" else "Accesos VIP pendientes: ") + "; ".join(names))
            else:
                lines.append("Pending VIP accesses: none" if lang == "en" else "Accesos VIP pendientes: ninguno")
            pause = _vip_get_pause(chat_id)
            if pause and pause.get("due_at") and pause["due_at"] > utcnow_naive():
                seconds_left = max(1, int((pause["due_at"] - utcnow_naive()).total_seconds()))
                minutes_left = max(1, (seconds_left + 59) // 60)
                lines.append(
                    f"Telegram anti-limit pause active: about {minutes_left} min remaining"
                    if lang == "en" else
                    f"Pausa anti-límite de Telegram activa: aproximadamente {minutes_left} min restantes"
                )
    except Exception as e:
        logging.info("No pude construir contexto operativo IA para %s: %s", chat_id, e)
    return "\n".join(lines)

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
        [InlineKeyboardButton("🎟 CÓDIGOS PROMO", callback_data="admin_panel_promos")],
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
    _prune_pending_ai_after_operation(
        context, chat_id, ["DEPOSITO"], reason="depósito validado / nivel actualizado"
    )
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
            await context.bot.send_message(
                chat_id=chat_id,
                text=activation_msg,
                reply_markup=None if new_level == VIP_LEVEL_PRESTIGE else upgrade_info_keyboard(lang),
            )
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
                if new_level == VIP_LEVEL_PRESTIGE:
                    user_msg = f"✅ Additional deposit confirmed. Your JT TRADERS TEAMS level remains {_vip_level_label(new_level, lang)}."
                else:
                    user_msg = (
                        f"✅ Additional deposit confirmed. Your current level remains {_vip_level_label(new_level, lang)}.\n\n"
                        "Upgrades are calculated from validated deposits within the enabled level-update period."
                    )
            else:
                if new_level == VIP_LEVEL_PRESTIGE:
                    user_msg = f"✅ Depósito adicional confirmado. Tu nivel en JT TRADERS TEAMS se mantiene en {_vip_level_label(new_level, lang)}."
                else:
                    user_msg = (
                        f"✅ Depósito adicional confirmado. Tu nivel actual se mantiene en {_vip_level_label(new_level, lang)}.\n\n"
                        "Los upgrades se calculan según depósitos validados dentro del periodo habilitado para actualización de nivel."
                    )
            await context.bot.send_message(
                chat_id=chat_id,
                text=user_msg,
                reply_markup=None if new_level == VIP_LEVEL_PRESTIGE else upgrade_info_keyboard(lang),
            )
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
        _prune_pending_ai_after_operation(
            context, chat_id, ["ID_SUBMIT"], reason=f"ID {broker} validado"
        )
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
        _prune_pending_ai_after_operation(
            context, chat_id, ["ID_SUBMIT"], reason=f"ID {broker} rechazado"
        )
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
    global_state = _vip_get_state(chat_id, create=False) or {}
    current_global = global_state.get("level") or VIP_LEVEL_NONE
    proposed_global = _max_level(current_global, preview.get("new_level") or VIP_LEVEL_NONE)
    if proposed_global == VIP_LEVEL_PRESTIGE:
        return (
            f"💰 CONFIRMAR DEPÓSITO · {_broker_label(broker).upper()}\n\n"
            f"Monto: USD {_usd(preview['amount_cents'])}\n"
            f"Depósito validado nº: {preview['new_count']}\n\n"
            f"Nivel JT asociado a esta cuenta: {_vip_level_label(preview['new_level'], 'es')}\n"
            f"Nivel general JT TRADERS TEAMS: {_vip_level_label(proposed_global, 'es')}\n\n"
            "¿Confirmas este depósito validado?"
        )
    mode = "ACUMULA dentro de la ventana" if preview.get("accumulates") else "NO acumula; se evalúa como depósito único"
    status = "CERRADA después de este depósito" if preview.get("window_closed_after") else "ABIERTA"
    proof_line = (
        "Comprobante ≤72h: NO APLICA (primer depósito)"
        if int(preview.get("old_count") or 0) == 0 else
        f"Comprobante ≤72h: {'SÍ' if preview['timely'] else 'NO'}"
    )
    return (
        f"💰 CONFIRMAR DEPÓSITO · {_broker_label(broker).upper()}\n\n"
        f"Monto: USD {_usd(preview['amount_cents'])}\n"
        f"{proof_line}\n"
        f"Depósito validado nº: {preview['new_count']}\n"
        f"Regla: {mode}\n"
        f"Acumulación habilitada: USD {_usd(preview['new_accum_cents'])}\n"
        f"Ventana 3 depósitos / 30 días: {status}\n\n"
        f"Nivel JT que habilita esta cuenta {_broker_label(broker)}: {_vip_level_label(preview['new_level'], 'es')}\n"
        f"Nivel general JT TRADERS TEAMS: {_vip_level_label(proposed_global, 'es')}\n\n"
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
    _prune_pending_ai_after_operation(
        context, chat_id, ["DEPOSITO"], reason=f"depósito {broker} validado / upgrade evaluado"
    )
    _broker_flow_set(chat_id, pending_deposit_broker="")

    if new_global != VIP_LEVEL_NONE:
        _cancel_jobs_prefix(context, "A", chat_id)
        _cancel_jobs_prefix(context, "B", chat_id)

    lang = get_user_lang(chat_id)
    broker_level = preview.get("new_level") or VIP_LEVEL_NONE
    if lang == "en":
        if new_global == VIP_LEVEL_PRESTIGE:
            user_msg = (
                f"✅ {_broker_label(broker)} deposit confirmed.\n\n"
                f"Your current level in my JT TRADERS TEAMS community is {_vip_level_label(new_global, lang)}."
            )
        else:
            user_msg = (
                f"✅ {_broker_label(broker)} deposit confirmed.\n\n"
                f"Your current level in my JT TRADERS TEAMS community is {_vip_level_label(new_global, lang)}.\n\n"
                "Upgrades are calculated from validated deposits within the enabled level-update period."
            )
    else:
        if new_global == VIP_LEVEL_PRESTIGE:
            user_msg = (
                f"✅ Depósito de {_broker_label(broker)} confirmado.\n\n"
                f"Tu nivel actual en mi comunidad JT TRADERS TEAMS es {_vip_level_label(new_global, lang)}."
            )
        else:
            user_msg = (
                f"✅ Depósito de {_broker_label(broker)} confirmado.\n\n"
                f"Tu nivel actual en mi comunidad JT TRADERS TEAMS es {_vip_level_label(new_global, lang)}.\n\n"
                "Los upgrades se calculan según depósitos validados dentro del periodo habilitado para actualización de nivel."
            )
    await context.bot.send_message(
        chat_id=chat_id,
        text=user_msg,
        reply_markup=None if new_global == VIP_LEVEL_PRESTIGE else upgrade_info_keyboard(lang),
    )

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
        f"👑 Nivel JT asociado a la cuenta {_broker_label(broker)}: {_vip_level_label(broker_level, 'es')}\n"
        f"⭐ Nivel general JT TRADERS TEAMS: {_vip_level_label(new_global, 'es')}"
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
    global_level = (_vip_get_state(chat_id, create=False) or {}).get("level") or VIP_LEVEL_NONE
    if global_level == VIP_LEVEL_PRESTIGE:
        review_text = (
            f"💰 REVISAR DEPÓSITO · {_broker_label(broker).upper()}\n\n"
            f"Nivel actual JT TRADERS TEAMS: {_vip_level_label(global_level, 'es')}\n"
            f"Depósitos validados en esta cuenta: {state.get('deposit_count', 0)}\n\n"
            "Escribe el monto NUEVO que acabas de confirmar en USD."
        )
    else:
        window = "ABIERTA" if _broker_upgrade_window_open(state) else "CERRADA"
        review_text = (
            f"💰 REVISAR DEPÓSITO · {_broker_label(broker).upper()}\n\n"
            f"Nivel JT asociado a esta cuenta: {_vip_level_label(state.get('level'), 'es')}\n"
            f"Depósitos validados: {state.get('deposit_count', 0)}\n"
            f"Ventana acumulable: {window}\n\n"
            "Escribe el monto NUEVO que acabas de confirmar en USD."
        )
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=review_text,
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

    promo_edit = context.user_data.get("admin_promo_edit")
    if promo_edit:
        if promo_edit in ("code100", "code70"):
            if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", raw):
                await update.effective_message.reply_text("⚠️ Código inválido. Usa solo letras, números, guion o guion bajo, sin espacios.")
                from telegram.ext import ApplicationHandlerStop
                raise ApplicationHandlerStop
            if promo_edit == "code100":
                _promo_set_config(code_100=raw)
            else:
                _promo_set_config(code_70=raw)
        elif promo_edit == "expiry":
            expiry = _promo_parse_expiry(raw)
            if not expiry:
                await update.effective_message.reply_text("⚠️ Fecha inválida. Usa DD/MM/AAAA o AAAA-MM-DD. Ejemplo: 30/09/2026")
                from telegram.ext import ApplicationHandlerStop
                raise ApplicationHandlerStop
            _promo_set_config(expires_on=expiry.isoformat())
        context.user_data.pop("admin_promo_edit", None)
        await context.bot.send_message(chat_id=ADMIN_ID, text="✅ Configuración promocional actualizada.\n\n" + _promo_admin_text(), reply_markup=_promo_admin_keyboard())
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop

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
        state = _broker_get(chat_id, broker, create=False) or {}
        old_count = int(state.get("deposit_count") or 0)
        pending_dep = {"chat_id": chat_id, "broker": broker, "amount_cents": amount_cents}

        # Primer depósito: 72 h todavía no aplica. Tampoco aplica cuando el usuario
        # ya está en Prestige o este depósito por sí solo alcanza Prestige (USD 500+),
        # porque no existe un nivel superior al cual subir.
        current_global = (_vip_get_state(chat_id, create=False) or {}).get("level") or VIP_LEVEL_NONE
        account_level = state.get("level") or VIP_LEVEL_NONE
        skip_72h = (
            old_count == 0
            or current_global == VIP_LEVEL_PRESTIGE
            or account_level == VIP_LEVEL_PRESTIGE
            or amount_cents >= VIP_LEVEL_THRESHOLDS_CENTS[VIP_LEVEL_PRESTIGE]
        )
        if skip_72h:
            preview = _broker_preview_deposit(chat_id, broker, amount_cents, True)
            pending_dep.update({"timely": True, "preview": preview})
            context.user_data["admin_pending_broker_deposit"] = pending_dep
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=_broker_deposit_preview_text(chat_id, broker, preview),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ CONFIRMAR DEPÓSITO", callback_data=f"admin_broker_deposit_confirm:{chat_id}:{broker}")],
                    [InlineKeyboardButton("❌ CANCELAR", callback_data=f"admin_user_open:{chat_id}")],
                ]),
            )
        else:
            context.user_data["admin_pending_broker_deposit"] = pending_dep
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
    context.user_data.pop("admin_promo_edit", None)
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
    elif query.data == "admin_panel_promos":
        context.user_data.pop("admin_promo_edit", None)
        await context.bot.send_message(chat_id=ADMIN_ID, text=_promo_admin_text(), reply_markup=_promo_admin_keyboard())
    elif query.data == "admin_panel_promo_code100":
        context.user_data["admin_promo_edit"] = "code100"
        await context.bot.send_message(chat_id=ADMIN_ID, text="✏️ Escribe el NUEVO código del bono 100% (primer depósito).")
    elif query.data == "admin_panel_promo_code70":
        context.user_data["admin_promo_edit"] = "code70"
        await context.bot.send_message(chat_id=ADMIN_ID, text="✏️ Escribe el NUEVO código del bono 70% (depósitos posteriores).")
    elif query.data == "admin_panel_promo_expiry":
        context.user_data["admin_promo_edit"] = "expiry"
        await context.bot.send_message(chat_id=ADMIN_ID, text="📅 Escribe la nueva fecha de vencimiento. Formato: DD/MM/AAAA (ejemplo 30/09/2026).")
    elif query.data == "admin_panel_promo_back":
        context.user_data.pop("admin_promo_edit", None)
        await context.bot.send_message(chat_id=ADMIN_ID, text="🔐 PANEL ADMINISTRADOR\n\nElige una opción:", reply_markup=admin_panel_keyboard())
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
        if step == "3h":
            cfg = _promo_get_config()
            expiry = _promo_parse_expiry(cfg.get("expires_on"))
            today = datetime.now(COLOMBIA_TZ).date()
            promo_active = not expiry or expiry >= today
            if promo_active:
                return (
                    f"💰 Si este será tu primer depósito, tienes disponible un bono del 100% con el código {cfg['code_100']}.\n\n"
                    "Tu registro ya está validado: estás a un solo paso de activar tu acceso. Aprovecha tu depósito, completa la activación y empieza con formación, señales y herramientas según tu nivel.\n\n"
                    "✅ Cuando lo hagas, escríbeme Ya deposité y continuamos de inmediato.",
                    f"💰 If this is your first deposit, you currently have a 100% bonus available with code {cfg['code_100']}.\n\n"
                    "Your registration is already validated. Complete your deposit and start accessing the education, signals and tools included in your level.\n\n"
                    "✅ Once done, message me I deposited and we will continue immediately.",
                )
            return (
                "💰 Tu registro ya está validado: estás a un solo paso de activar tu acceso. Completa tu depósito y empieza con formación, señales y herramientas según tu nivel.\n\n"
                "✅ Cuando lo hagas, escríbeme Ya deposité y continuamos de inmediato.",
                "💰 Your registration is already validated. Complete your deposit to activate the education, signals and tools included in your level.\n\n"
                "✅ Once done, message me I deposited and we will continue immediately.",
            )
        mapping = {
            "1h": (MENSAJE_B_1H_ES, MENSAJE_B_1H_EN),
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
    es, en = _campaign_text_pair("B", "3h")
    await _send_job_message_B(context, es, en)


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
            [InlineKeyboardButton("🎁 Benefits, Levels & Plans", callback_data="levels_plans_en")],
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
            [InlineKeyboardButton("🎁 Beneficios, Niveles y Planes", callback_data="niveles_planes")],
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

    if q.data == "vip_continue_access":
        lang = get_user_lang(chat_id)
        state = _vip_get_state(chat_id, create=False) or {}
        level = state.get("level") or VIP_LEVEL_NONE
        remaining = _vip_pending_keys(chat_id)
        if level == VIP_LEVEL_NONE or not remaining:
            await q.message.reply_text(
                "✅ No tienes accesos VIP pendientes." if lang == "es" else "✅ You have no pending VIP access.",
                reply_markup=support_keyboard(lang),
            )
            return
        pause = _vip_get_pause(chat_id)
        if pause and pause.get("due_at") and pause["due_at"] > utcnow_naive():
            seconds_left = max(1, int((pause["due_at"] - utcnow_naive()).total_seconds()))
            minutes_left = max(1, (seconds_left + 59) // 60)
            await q.message.reply_text(
                (
                    f"⏳ Aún estamos dentro de la pausa de seguridad de Telegram. Espera aproximadamente {minutes_left} min y te enviaré automáticamente el siguiente acceso."
                    if lang == "es" else
                    f"⏳ We are still inside Telegram’s safety pause. Wait about {minutes_left} min and I’ll automatically send the next access."
                ),
                reply_markup=_vip_pause_keyboard(lang),
            )
            _vip_schedule_resume(context, chat_id, pause["due_at"])
            return
        _vip_clear_pause(chat_id)
        await _vip_send_next_or_welcome(
            context,
            chat_id,
            level,
            lang,
            just_completed="",
            should_welcome=False,
            bypass_pause=True,
        )
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
        await q.message.reply_text(_personalize_referral_links(_msg_account, chat_id))

    elif q.data == "gestion_capital":
        # El botón informa las modalidades y termina siempre en atención personal.
        await q.message.reply_text(GESTION_CAPITAL_BUTTON_ES, reply_markup=personal_chat_keyboard("es"))

    elif q.data == "gestion_capital_en":
        # Same rule in English: information + direct personal handoff.
        await q.message.reply_text(GESTION_CAPITAL_BUTTON_EN, reply_markup=personal_chat_keyboard("en"))

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
                        _append_ai_exchange(destinatario_id, original_question or "", manual_reply_text, assistant_source="manual")
                else:
                    _cancel_pending_ai(context, destinatario_id, manual_reply=manual_reply_text, original_question=original_question)

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

                if response_type == "voice":
                    # La transcripción, si existe, queda en memoria interna; no se muestra
                    # en el chat administrativo para evitar ruido visual.
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="✅ Audio enviado al usuario correctamente."
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
# v7.10.46: prioridad para Johanna mantiene un máximo de 4 minutos.
# Si Railway conserva una variable antigua AI_WAIT_MINUTES=5, el código la limita a 4.
# Se permiten valores enteros menores (por ejemplo 3) sin superar el máximo actual.
try:
    AI_WAIT_MINUTES = max(1, min(4, int(float(os.getenv("AI_WAIT_MINUTES", "4")))))
except Exception:
    AI_WAIT_MINUTES = 4
AI_WAIT_SECONDS = AI_WAIT_MINUTES * 60
AI_HISTORY_MAX_MESSAGES = 16
# Si Johanna respondió personalmente hace poco, los siguientes mensajes se tratan
# como continuación de ESA conversación antes de disparar respuestas fijas por palabras clave.
try:
    AI_MANUAL_CONTEXT_MINUTES = max(10, int(os.getenv("AI_MANUAL_CONTEXT_MINUTES", "60")))
except Exception:
    AI_MANUAL_CONTEXT_MINUTES = 60

JOHA_KNOWLEDGE = os.getenv("JOHA_KNOWLEDGE", "").strip() or f"""
INFORMACIÓN OFICIAL DE JOHAALETRADER / JT TRADERS TEAMS

IDENTIDAD Y PRINCIPIO DE RESPUESTA
- Responde siempre en primera persona como Johanna / JOHAALETRADER. Nunca digas que eres una IA, un bot de atención o un asistente virtual.
- Atiendes prospectos y miembros actuales.
- La prioridad es comprender la intención completa y responder SOLO lo necesario para esa pregunta. No descargues toda la información disponible sobre un tema.
- Antes de responder, identifica mentalmente el OBJETO preguntado (curso/formación, señales, bot, nivel, depósito, promo, acceso, horarios, etc.) y limita la respuesta a ese ámbito. El nombre de un nivel o un monto puede ser solo contexto y NO autoriza a enumerar beneficios de otras categorías.
- Si la pregunta es específica sobre un elemento, responde ese elemento y, si hace falta, la alternativa de la MISMA categoría. No añadas señales, bots, cursos o beneficios ajenos solo porque pertenezcan al mismo nivel.
- Si la pregunta es amplia (“qué incluye”, “qué recibo”, “beneficios”), entonces sí resume las categorías principales del nivel.
- Si la persona hace una pregunta corta, normalmente bastan 1–3 frases. Amplía solo cuando lo pida o cuando sea imprescindible para evitar un error.
- No asumas género. Usa lenguaje neutral: "si estás empezando", "cuando completes", "tú realizas la entrada", etc.
- Tutea SIEMPRE a la persona: usa “tú / te / tu / tus”. No uses “usted / su / sus” para dirigirte al usuario. Si el nombre visible está disponible, puedes usarlo ocasionalmente cuando suene natural, pero no en cada respuesta.

REGISTRO Y ACCESO
- El acceso a la comunidad JT TRADERS TEAMS es GRATUITO. No existe una membresía adicional que se pague a Johanna.
- El usuario deposita/invierte en SU PROPIA cuenta de trading. Ese capital sigue en su cuenta.
- El requisito para entrar a la comunidad es que la cuenta de Binomo o Stockity quede registrada correctamente con uno de los enlaces oficiales de Johanna y que el ID sea validado ANTES del depósito.
- Stockity es la opción principal y Binomo la opción secundaria cuando corresponda mostrar ambos enlaces.
- Cuando debas mostrar enlaces usa este formato, sin Markdown oculto:
  🔗 Stockity — opción principal:
  {ENLACE_REFERIDO_STOCKITY}

  🔗 Binomo — opción secundaria:
  {ENLACE_REFERIDO}
- En inglés usa "🔗 Stockity — primary option:" y "🔗 Binomo — secondary option:".
- Nunca confirmes por tu cuenta que un ID, afiliación, depósito o acceso quedó validado; eso depende del sistema/validación de Johanna.
- Cuando la persona pregunte de forma natural “¿qué me toca?”, “¿qué hago ahora?”, “¿cómo sigo?”, “¿por dónde empiezo?” o equivalente, interpreta primero que está preguntando por el SIGUIENTE PASO del flujo, no automáticamente por beneficios, nivel o promociones. Usa el ESTADO OPERATIVO REAL: PRE → registro correcto + envío/validación de ID antes de depositar; POST → ID validado, corresponde depósito en esa misma cuenta + comprobante; DEPOSITED → no reinicies registro/ID, continúa desde su nivel/acceso real.
- Si en esa pregunta de siguiente paso aparece un monto, puedes mencionar DESPUÉS del paso operativo qué nivel correspondería a ese capital, pero no conviertas el monto en una explicación de bonos ni en una lista de beneficios. Para los detalles del nivel existe el botón de niveles.
- Si ya existe un ID enviado y pendiente de revisión, no pidas otro registro ni otro ID y no indiques depositar todavía: informa brevemente que primero debe terminar la validación del ID.

NIVELES DENTRO DE JT TRADERS TEAMS
- El nivel pertenece a la comunidad JT TRADERS TEAMS, NO al broker. Se determina por el capital/deposito validado que la persona mantiene en su propia cuenta de trading.
- Básico: desde 50 USD hasta 199.99 USD.
- Premium: desde 200 USD hasta 499.99 USD.
- Prestige: desde 500 USD.
- Si preguntan por un monto concreto PARA SABER EL NIVEL o preguntan de forma amplia qué incluye, responde el nivel correspondiente y un resumen útil de sus beneficios. Ejemplo factual: 300 USD = Premium.
- Si el monto aparece dentro de una pregunta ESPECÍFICA sobre un curso, señal, bot u otra herramienta, úsalo solo para ubicar el nivel y responde únicamente ese tema; no conviertas el monto en una excusa para listar beneficios no preguntados.
- Si preguntan por todos los niveles, sí puedes compararlos.
- Un monto por sí solo NO significa “háblame del bono” ni “enumera beneficios”. El sentido lo define la intención completa: siguiente paso, nivel, curso, señales, depósito, etc.

FORMACIÓN / CURSOS
- Básico recibe Binary Teams Módulos 1, 2 y 3.
- Premium recibe EXACTAMENTE Binary Teams Módulos 1, 2, 3 y 4. NO recibe Madness Trading Avanzado.
- Prestige recibe Binary Teams Módulos 1 al 4 y además Madness Trading Avanzado — método ALGO & LIT.
- HECHO CRÍTICO: nunca describas Premium como “todos los módulos”, “todos los cursos” o “formación completa” si esa frase puede incluir Madness. Para Premium di “Binary Teams Módulos 1 al 4”. Madness es EXCLUSIVO de Prestige.
- Binary Teams Módulo 1: introducción al manejo de la plataforma, especialmente Binomo; IQ Option tiene funcionamiento similar para esta introducción.
- Binary Teams Módulo 2: introducción al análisis bursátil.
- Binary Teams Módulo 3: continuación/tercera parte del análisis bursátil.
- Binary Teams Módulo 4: Smart Money Concept.
- Los Módulos 1 al 4 de Binary Teams forman una RUTA PROGRESIVA de aprendizaje: parten de fundamentos/nivel principiante y avanzan gradualmente hasta contenido más avanzado, culminando en Smart Money Concept en el Módulo 4. Este es conocimiento conceptual, NO una frase fija: cuando la pregunta trate de cursos, formación o nivel de aprendizaje, explícalo con palabras naturales y variadas (por ejemplo, desde fundamentos hasta avanzado), sin repetir siempre "de cero a pro" ni convertirlo en slogan.
- Madness Trading Avanzado: formación avanzada con método ALGO & LIT.
- La formación incluye además material de estudio/apoyo según nivel, PDFs/guías, audiolibros, tablas de plan de trading y gestión de riesgo, sesiones en vivo y acompañamiento de la comunidad.
- No enumeres todos los módulos si no hace falta. Si preguntan por la formación de Premium o Prestige, puedes resumirla como una ruta progresiva desde fundamentos/principiante hasta avanzado y mencionar el Módulo 4 Smart Money Concept cuando aporte contexto.
- Si preguntan específicamente "qué cursos/formación tengo" en Premium o Prestige, después de indicar los módulos añade en UNA frase breve que también hay material de estudio/apoyo, PDFs/guías, audiolibros, plan de trading, gestión de riesgo, sesiones en vivo y acompañamiento según el nivel. No lo añadas si la pregunta solo busca confirmar un curso concreto.

SEÑALES Y SOFTWARE PREMIUM ANTICIPADO
- Básico: 30–50 señales CRYPTO IDX diarias, de lunes a viernes.
- Premium y Prestige: Software Premium Anticipado con MÁS DE 300 SEÑALES AL DÍA, de lunes a sábado.
- Esas +300 señales abarcan CRYPTO IDX, pares de divisas, índices sintéticos y Forex.
- El Software Premium Anticipado entrega una LISTA ANTICIPADA Y PREDETERMINADA de señales con el minuto exacto en que se tomará cada entrada. NO describas estas señales como “enviadas manualmente”.
- El listado se distribuye durante gran parte del día; normalmente comienza alrededor de las 7:00 a. m. y se extiende aproximadamente hasta las 10:00 p. m. hora Colombia. Presenta ese horario como habitual/aproximado, no como una promesa invariable.
- Esto permite que una persona con horario ocupado elija dentro de la lista el momento en que puede operar.
- En las señales del Software Premium Anticipado se entra en el minuto exacto indicado por la señal, con expiración de 1 minuto. La persona realiza la entrada en su cuenta; eso NO significa que las señales sean enviadas manualmente.
- Cuando compares el Software Premium Anticipado con un bot IA, evita la frase “ejecutar manualmente” porque puede confundirse con la forma de ENTREGA de la señal. Expresa simplemente que la persona toma/realiza la entrada en su propia cuenta. Reserva la explicación “no opera automáticamente” para cuando realmente pregunten si el bot ejecuta operaciones.
- Cuando pregunten de forma general por las SEÑALES disponibles en un nivel, considera todas las fuentes que realmente generan señales dentro de ese nivel y separa su frecuencia: en Premium/Prestige están las +300 del Software Premium Anticipado (lunes a sábado) y también las alertas del bot IA correspondiente 24/7. No mezcles aquí cursos ni otros beneficios que no sean señales.
- Si preguntan específicamente por el Software Premium Anticipado, responde solo sobre ese software; si preguntan específicamente por el bot IA, responde solo sobre el bot.

BOTS IA 24/7
- Los bots IA NO están disponibles para todos los niveles.
- Premium: bot IA CRYPTO IDX 24/7.
- Prestige: bot IA CRYPTO IDX 24/7 + bot IA de pares de divisas 24/7.
- "Automático" significa que el sistema GENERA Y ENVÍA ALERTAS automáticamente 24/7. NO abre ni ejecuta operaciones automáticamente dentro de la cuenta del usuario.
- El bot NO abre ni ejecuta operaciones dentro de la cuenta. La persona realiza la entrada en su propia cuenta, manteniendo el control de gestión de riesgo, capital y plan de trading.
- Para una alerta del bot IA 24/7: la alerta indica ACTIVO + DIRECCIÓN (compra o venta) y la entrada se toma al MINUTO SIGUIENTE de recibirse la alerta. Ejemplo: alerta en minuto 10 → entrada en minuto 11. La expiración es siempre de 1 minuto. NO digas “tú decides cuándo entrar”, porque el momento de entrada sí está definido por la alerta.
- MG1 y MG2 son opcionales; nunca digas que son obligatorios.
- Si alguien pregunta solamente si puede tener el bot, limita la respuesta a disponibilidad por nivel. No expliques funcionamiento, panel o registro salvo que lo pregunte.
- Si el ESTADO OPERATIVO indica Premium y preguntan qué/cuántos bots tiene la persona, menciona CRYPTO IDX 24/7. Si indica Prestige y preguntan qué/cuántos bots tiene, menciona SIEMPRE los dos: CRYPTO IDX 24/7 + pares de divisas 24/7.
- Si la conversación inmediatamente anterior habla de bots y luego preguntan "cuando me llega una señal, ¿qué hago?", interpreta que se refiere a la alerta del bot y puedes decir brevemente "si te refieres a las alertas de los bots..." antes de explicar minuto +1 y expiración de 1 minuto.

PANEL / INTERFAZ QUE JOHANNA MUESTRA EN LIVE
- La interfaz visual/panel que Johanna utiliza en sus lives es una herramienta privada de uso interno.
- No es lo que se instala o entrega a los miembros: mantenerla requeriría instalación, configuración, mantenimiento y actualizaciones, principalmente en computador.
- Los miembros reciben las MISMAS señales operativas correspondientes por Telegram, lo que permite usarlas cómodamente desde celular o cualquier dispositivo, sin instalar ni configurar esa interfaz.
- REFERENCIA SEMÁNTICA IMPORTANTE: si alguien dice “el bot que muestras en los lives”, “el programa que usas en vivo”, “eso que se ve en tu pantalla”, “el software que muestras” o una frase equivalente, interpreta que se refiere a ESTA interfaz visual privada, aunque use la palabra “bot”, salvo que nombre de forma explícita el bot IA CRYPTO IDX 24/7 o el bot IA de pares de divisas 24/7.
- No presentes esta interfaz como beneficio de Premium o Prestige ni digas que se obtiene subiendo de nivel. No se entrega a usuarios.
- SOLO explica esta diferencia si el usuario pregunta por el panel/interfaz/herramienta que ve en live o si pregunta si recibirá exactamente ese software. No la metas en cada pregunta general sobre bots.
- Cuando sí lo pregunten, incluye brevemente el motivo práctico: el panel requiere instalación/configuración/actualizaciones en computador; Telegram evita depender de un solo equipo y permite recibir las señales desde cualquier dispositivo y lugar.

GESTIÓN DE RIESGO Y MARTINGALA — METODOLOGÍA DE JOHANNA
- MG1 y MG2 son opcionales. Se pueden utilizar con una gestión de riesgo bien calculada y capital suficiente, pero no garantizan recuperación.
- La referencia habitual de Johanna es trabajar normalmente con un máximo cercano al 2% del capital para TODA la secuencia, no 2% en cada entrada.
- Con capital alto, por ejemplo por encima de 1,000 USD, puede usarse incluso 1% si cubre cómodamente la secuencia. Un 3% es una gestión más agresiva y no es la recomendación normal.
- Método porcentual orientativo: calcular 1–2% del capital y dividir ese presupuesto total en 6 o 7 unidades. La entrada inicial usa 1 unidad, MG1 usa 2 unidades y MG2 puede usar 3–4 unidades según el objetivo de la secuencia. No conviertas esto en una receta rígida; explica el cálculo solo si el usuario pregunta cómo distribuir la gestión.
- Ejemplo educativo: 400 USD × 2% = 8 USD de riesgo total para la secuencia; 8/7 ≈ 1.14 USD por unidad. No prometas que esta distribución garantiza recuperar o quedar en profit.
- Como referencia de plan, Johanna prioriza limitar pérdidas y buscar una relación favorable entre riesgo y objetivo; nunca presentes objetivos de ganancia como garantizados.
- Si preguntan simplemente "¿puedo usar martingala?", responde breve: sí, MG1/MG2 son opcionales y deben quedar dentro de la gestión total de riesgo.

TIEMPO / HORARIOS / PERSONAS QUE TRABAJAN TODO EL DÍA
- Si alguien pregunta cómo organizar sus horarios para operar, dice que trabaja todo el día o que tiene poco tiempo, responde BREVE y práctico; no conviertas la respuesta en una receta fija.
- Como conocimiento de referencia de Johanna existen AL MENOS DOS sesiones de unos 40 minutos y unas 5 operaciones bien seleccionadas por sesión. Son referencias disponibles, NO datos obligatorios que deban aparecer cada vez que se mencione poco tiempo.
- Si organización/tiempo es SOLO una parte de una pregunta múltiple, normalmente basta UNA frase natural sobre organizarse en sesiones cortas, apoyarse en las señales y evitar estar pendiente todo el día. No desarrolles 40 minutos + 5 operaciones + cantidad de señales salvo que la persona pida específicamente duración, número de operaciones, rutina o una organización detallada.
- Si la consulta está centrada específicamente en cómo organizar el tiempo para operar, entonces sí puedes concretar de forma natural la referencia de al menos dos sesiones de unos 40 minutos y, cuando aporte valor, unas 5 operaciones bien seleccionadas por sesión.
- Puede apoyarse en las señales del Software Premium Anticipado y en las herramientas disponibles para elegir oportunidades dentro del tiempo que tenga, siempre manteniendo su plan de trading y gestión de riesgo. No recites la cantidad de señales si no la preguntaron.
- Si el usuario no dio horarios concretos, NO inventes momentos como “a primera hora”, “en el almuerzo”, “durante el descanso” o “por la noche”. Di simplemente que elija momentos que se ajusten a su disponibilidad. Solo propone franjas concretas si el usuario las dio o las pidió expresamente.
- Tutea en la respuesta: “ajustadas a tu horario”, “a tu rutina”, “cuando tengas disponibilidad”.
- El Software Premium Anticipado distribuye señales normalmente desde aproximadamente 7 a. m. hasta 10 p. m. y el bot IA CRYPTO IDX funciona 24/7. Usa esos horarios SOLO si preguntan por disponibilidad/franjas concretas; en una duda simple de organización basta con explicar que hay flexibilidad para elegir momentos compatibles con la rutina.
- Puedes destacar la flexibilidad de horario del trading, pero NO prometas libertad financiera, dejar el empleo, mayor efectividad por operar a determinada hora ni ganancias determinadas.
- Para una pregunta simple sobre organización/horarios, normalmente bastan 2–3 frases que integren de forma NATURAL lo relevante: al menos 2 sesiones de ~40 minutos, unas 5 operaciones seleccionadas por sesión, apoyo en las señales y gestión de riesgo. NO copies una frase fija ni enumeres estos puntos si no hace falta.

CUENTAS EXISTENTES / ANTIGUAS
- Nunca trates igual "mi cuenta fue registrada contigo", "creo que fue contigo" y "no fue con tu enlace".
- Si la persona dice que la cuenta fue registrada con Johanna, o no está segura, el siguiente paso es pedir el ID para VALIDAR primero. No debe depositar de nuevo hasta que esa vinculación sea confirmada.
- Si después de validar el ID está correctamente vinculada, se continúa con el depósito/comprobante/nivel desde el estado real de la cuenta. No se crea otra cuenta innecesariamente.
- Si la persona confirma expresamente que la cuenta vieja NO fue registrada con los enlaces de Johanna: si tiene saldo, primero debe retirarlo; después cerrar/eliminar la cuenta anterior; para el NUEVO REGISTRO debe abrir una ventana de incógnito, entrar por uno de los enlaces oficiales y usar un correo diferente que no haya sido utilizado antes en esa plataforma. La ventana de incógnito es especialmente para el registro; después puede iniciar sesión normalmente.
- Tras crear la nueva cuenta debe enviar el nuevo ID ANTES de depositar.
- Si la cuenta pertenece a una persona de confianza/familiar, debe ser genuinamente de esa persona: datos reales, documento real y medios de depósito/retiro a nombre del titular.
- No menciones escenarios de familiar si el usuario no está hablando de eso.

BROKERS Y UPGRADES
- Binomo y Stockity se gestionan por separado. Sus depósitos NUNCA se suman entre sí.
- Cada broker tiene su propio ID validado, depósitos y contador interno.
- El nivel global de la comunidad es el más alto alcanzado individualmente en cualquiera de las cuentas y nunca baja por añadir otro broker.
- Los primeros 3 depósitos validados de una misma cuenta/broker pueden acumularse para subir de nivel dentro de una ventana de 30 días desde el primer depósito validado.
- Para entrar en esa acumulación, cada comprobante posterior debe reportarse dentro de las 72 horas del depósito.
- Después del tercer depósito validado o al vencer los 30 días, un upgrade exige un nuevo depósito único que por sí solo alcance el mínimo completo del nuevo nivel.
- Solo cuentan depósitos reportados y validados.

BONOS — REGLAS GENERALES
- AISLAMIENTO ESTRICTO: bonos, códigos, 70%, 100%, volumen y condiciones de retiro SOLO pueden aparecer si la pregunta pendiente menciona de forma explícita bonos/promociones/códigos o pregunta directamente por ellos. Un monto aislado, una duda de nivel o una pregunta de siguiente paso NO autoriza a hablar de promociones, aunque el historial o ejemplos anteriores sí las mencionen.
- Los códigos vigentes y la fecha actual se suministran en el CONTEXTO PROMOCIONAL ACTUAL; esa información manda sobre ejemplos antiguos.
- Bono 100%: solo primer depósito y un solo uso.
- Bono 70%: depósitos posteriores.
- Si preguntan únicamente qué bonos/códigos están activos, responde solo porcentaje + uso + código + fecha de vencimiento. No expliques volumen/retiro si no lo preguntaron.
- Si preguntan si conviene usar bono: puede tener sentido para una operativa de mediano a largo plazo; si la persona necesitará retirar pronto, normalmente es más sencillo operar sin bono.
- Con un bono activo se debe cumplir el volumen de operativas exigido por la promoción para liberar el bono y convertirlo en capital retirable.
- Si se solicita un retiro mientras el bono sigue activo, normalmente la promoción puede cancelarse y también pueden anularse ganancias asociadas al bono. Las condiciones pueden variar según la promoción vigente, así que indica revisar la sección de bonos de la plataforma cuando corresponda.
- No inventes un volumen exacto si no está confirmado en la promoción actual.

PREGUNTAS MÚLTIPLES Y DEPENDENCIAS
- Si llegan varias preguntas seguidas, agrúpalas y responde todas, pero primero identifica dependencias.
- Si una decisión depende de un dato que aún no está validado, NO asumas ese dato. Resuelve primero el requisito pendiente.
- Ejemplo: "tengo cuenta de hace meses / creo que fue contigo / quiero depositar 300 / no sé si usar bono / quiero retirar pronto" → primero pide el ID para verificar si esa cuenta está vinculada. No asumas que corresponde bono 70%, ni que ya puede depositar, hasta resolver esa validación. Puedes añadir brevemente que si piensa retirar en pocos días probablemente le convenga no activar bono, pero deja claro que primero hay que validar la cuenta.
- Si el usuario envía varias frases cortadas dentro de la ventana de 4 minutos, interprétalas como una misma conversación cuando sean continuidad clara.
- Si el mensaje actual abre claramente un caso hipotético/de otra persona (por ejemplo "si una persona...", "si otra persona...", "si alguien..."), responde ese caso como tema nuevo y NO arrastres una validación personal de ID/cuenta anterior salvo que el propio mensaje la conecte explícitamente.

LIVES
- Los lives públicos suelen realizarse de lunes a sábado.
- Normalmente hay una sesión alrededor de las 5:00 p. m. hora Colombia y una sesión nocturna que puede variar entre 8:00 p. m., 8:30 p. m. o 9:00 p. m.
- Algunos sábados puede no haber transmisión.
- Las sesiones privadas VIP se anuncian previamente dentro del canal VIP.

ACCESOS VIP EN TELEGRAM
- Si Telegram muestra "demasiados intentos" durante solicitudes/ingresos a canales VIP, interprétalo como un límite temporal de Telegram. Los accesos ya confirmados no se pierden.
- El bot puede pausar aproximadamente {VIP_ACCESS_PAUSE_MINUTES} minutos y continuar desde el siguiente acceso pendiente.
- No conviertas un problema de ingreso a canales de Telegram en un problema de contraseña, correo, KYC o broker.

CASOS QUE SIEMPRE VAN A JOHANNA
- Gestión de capital/cuenta: siempre al chat personal de Johanna.
- Problemas de disponibilidad por país, plataforma restringida/no disponible o imposibilidad de registrarse por ubicación: siempre al chat personal de Johanna. No expliques métodos para alterar ubicación ni menciones herramientas para hacerlo.
- Casos extraordinarios de cuenta que requieren comprobar un estado real: al chat personal.
- Chat personal: {SUPPORT_URL}

LÍMITES
- No inventes información, promociones, resultados, estados de cuenta ni validaciones.
- No prometas ganancias ni resultados garantizados.
- No solicites contraseñas, códigos 2FA, seed phrases ni credenciales sensibles.
- No indiques usar documentos o identidad ajena como si fueran propios.
- Si falta un dato oficial, dilo con naturalidad; no rellenes huecos.
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
    """Recupera SOLO ejemplos realmente relacionados con la consulta actual.

    Los ejemplos sirven para tono y continuidad, nunca para arrastrar contenido de
    otro tema ni para convertir respuestas recientes en una plantilla repetitiva.
    """
    try:
        limit = max(2, min(limit, 12))
        with Session() as session:
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
                candidates = (
                    relevant_query
                    .order_by(JohannaExample.created_at.desc())
                    .limit(max(30, limit * 4))
                    .all()
                )
                # Exige coincidencia temática suficiente. Nunca completamos con
                # ejemplos recientes no relacionados: eso contaminaba respuestas.
                threshold = 2 if len(keywords) >= 3 else 1
                scored = []
                for row in candidates:
                    hay = _norm(f"{row.user_text or ''} {row.response_text or ''}")
                    score = sum(1 for kw in keywords if _norm(kw) in hay)
                    if score >= threshold:
                        scored.append((score, row))
                scored.sort(key=lambda item: (item[0], item[1].created_at or datetime.min), reverse=True)
                relevant = [row for _score, row in scored[:min(limit, 4)]]

        parts = []
        total = 0
        for r in relevant:
            q = (r.user_text or "").strip()
            a = (r.response_text or "").strip()
            if not a:
                continue
            piece = (f"USUARIO: {q}\n" if q else "") + f"JOHANNA: {a}"
            if total + len(piece) > 6000:
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


async def _transcribe_telegram_audio(context: ContextTypes.DEFAULT_TYPE, file_id: str, speaker: str = "Johanna") -> str:
    """Transcribe una nota de voz/audio de Telegram para conservar el contexto real.

    Es una capa auxiliar: si falla, el envío principal nunca se rompe.
    """
    if not (HAS_HTTPX and OPENAI_API_KEY and file_id):
        return ""
    try:
        tg_file = await context.bot.get_file(file_id)
        raw = bytes(await tg_file.download_as_bytearray())
        if not raw:
            return ""

        mp3_bytes = await asyncio.to_thread(_convert_voice_to_mp3, raw)
        if not mp3_bytes:
            logging.warning("Transcripción de audio omitida: Railway necesita ffmpeg (o imageio-ffmpeg) para convertir el audio.")
            return ""
        if len(mp3_bytes) > 25 * 1024 * 1024:
            logging.warning("Audio demasiado grande para transcripción (>25 MB).")
            return ""

        files_payload = {"file": ("telegram_audio.mp3", mp3_bytes, "audio/mpeg")}
        data_payload = {
            "model": OPENAI_TRANSCRIBE_MODEL,
            "prompt": (
                f"Conversación entre un usuario y Johanna / JOHAALETRADER. Hablante actual: {speaker}. "
                "Términos frecuentes: JT TRADERS TEAMS, Stockity, Binomo, CRYPTO IDX, martingala, "
                "señales, Premium, Prestige, ID, depósito, gestión de cuenta, gestión de capital, Telegram, canales VIP."
            ),
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
        logging.warning("No pude transcribir audio de Telegram (%s): %s", speaker, e)
        return ""


async def _transcribe_admin_voice(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> str:
    """Transcribe una respuesta de voz de Johanna para contexto y aprendizaje."""
    return await _transcribe_telegram_audio(context, file_id, speaker="Johanna")


async def _transcribe_user_audio(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> str:
    """Transcribe voz/audio recibido del usuario para que la IA pueda seguir la conversación."""
    return await _transcribe_telegram_audio(context, file_id, speaker="Usuario")


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


def _append_ai_exchange(chat_id: int, user_text: str, assistant_text: str, assistant_source: str = "auto"):
    """Guarda el intercambio y su origen sin cambiar el esquema de BD.

    assistant_source puede ser manual, ai o auto. Las versiones antiguas del
    historial sin estos metadatos siguen siendo compatibles.
    """
    if not user_text and not assistant_text:
        return
    history = _load_ai_history(chat_id)
    stamp = utcnow_naive().isoformat(timespec="seconds")
    if user_text and user_text != "(sin texto)":
        history.append({"role": "user", "content": str(user_text)[:1800], "ts": stamp})
    if assistant_text:
        history.append({
            "role": "assistant",
            "content": str(assistant_text)[:2200],
            "source": str(assistant_source or "auto"),
            "ts": stamp,
        })
    _save_ai_history(chat_id, history)


def _history_as_text(chat_id: int) -> str:
    history = _load_ai_history(chat_id)[-12:]
    parts = []
    for item in history:
        role_value = item.get("role")
        source = str(item.get("source") or "").lower()
        if role_value == "user":
            role = "USUARIO"
        elif source == "manual":
            role = "JOHANNA (RESPUESTA PERSONAL REAL)"
        elif source == "ai":
            role = "JOHANNA (RESPUESTA IA ANTERIOR)"
        else:
            role = "JOHANNA / BOT"
        content = str(item.get("content") or "").strip()
        if content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


def _has_recent_manual_conversation(chat_id: int, minutes: int = None) -> bool:
    """True si Johanna respondió personalmente hace poco en este mismo chat."""
    minutes = int(minutes or AI_MANUAL_CONTEXT_MINUTES)
    cutoff = utcnow_naive() - timedelta(minutes=minutes)
    for item in reversed(_load_ai_history(chat_id)):
        if item.get("role") != "assistant" or str(item.get("source") or "").lower() != "manual":
            continue
        raw_ts = str(item.get("ts") or "").strip()
        if not raw_ts:
            # Registros antiguos no permiten saber si la conversación sigue activa.
            continue
        try:
            ts = datetime.fromisoformat(raw_ts)
        except Exception:
            continue
        return ts >= cutoff
    return False


def _decode_pending_payload(raw_value: str):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return {"messages": [], "message_ids": [], "answered_topics": []}
    try:
        data = json.loads(raw_value)
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            raw_messages = [str(x) for x in data.get("messages", []) if str(x).strip()]
            raw_ids = [str(x) for x in (data.get("message_ids") or [])]
            # Mantener alineación con mensajes incluso para payloads v2 sin IDs.
            if len(raw_ids) < len(raw_messages):
                raw_ids.extend([""] * (len(raw_messages) - len(raw_ids)))
            elif len(raw_ids) > len(raw_messages):
                raw_ids = raw_ids[-len(raw_messages):]
            return {
                "messages": raw_messages,
                "message_ids": raw_ids,
                "answered_topics": [str(x) for x in data.get("answered_topics", []) if str(x).strip()],
            }
    except Exception:
        pass
    # Compatibilidad con pendientes creados por versiones anteriores.
    return {"messages": [raw_value], "message_ids": [""], "answered_topics": []}


def _encode_pending_payload(messages, answered_topics, message_ids=None):
    clean_messages = [str(x)[:5000] for x in messages if str(x).strip()][-8:]
    raw_ids = [str(x) for x in (message_ids or [])]
    if len(raw_ids) < len(clean_messages):
        raw_ids.extend([""] * (len(clean_messages) - len(raw_ids)))
    elif len(raw_ids) > len(clean_messages):
        raw_ids = raw_ids[-len(clean_messages):]
    return json.dumps(
        {
            "v": 3,
            "messages": clean_messages,
            "message_ids": raw_ids,
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
        payload = {"messages": [], "message_ids": [], "answered_topics": []}
        if u.ai_pending_text and u.ai_pending_due_at and u.ai_pending_due_at >= utcnow_naive():
            payload = _decode_pending_payload(u.ai_pending_text)
        payload["messages"].append(text_value.strip())
        payload["message_ids"].append(str(message_id))
        payload["answered_topics"].extend(answered_topics)
        u.ai_pending_text = _encode_pending_payload(
            payload["messages"], payload["answered_topics"], payload["message_ids"]
        )
        u.ai_pending_message_id = str(message_id)
        u.ai_pending_due_at = due_at
        session.commit()
    return due_at


def _replace_pending_ai_edited_message(chat_id: int, message_id: int, new_text: str) -> bool:
    """Reemplaza una pregunta pendiente por su edición sin reiniciar el reloj de IA."""
    edited = (new_text or "").strip()
    if not edited:
        return False
    mid = str(message_id)
    try:
        with Session() as session:
            u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
            if not u or not u.ai_pending_text or not u.ai_pending_due_at:
                return False
            payload = _decode_pending_payload(u.ai_pending_text)
            messages = list(payload.get("messages") or [])
            message_ids = list(payload.get("message_ids") or [])
            if len(message_ids) < len(messages):
                message_ids.extend([""] * (len(messages) - len(message_ids)))

            idx = None
            for i, saved_mid in enumerate(message_ids):
                if saved_mid == mid:
                    idx = i
                    break
            # Compatibilidad v2: el último pendiente tenía su ID en la columna histórica.
            if idx is None and str(u.ai_pending_message_id or "") == mid and messages:
                idx = len(messages) - 1
                message_ids[idx] = mid
            if idx is None:
                return False

            messages[idx] = edited
            u.ai_pending_text = _encode_pending_payload(
                messages, payload.get("answered_topics") or [], message_ids
            )
            # IMPORTANTE: conserva ai_pending_due_at. Editar no reinicia los 4 minutos.
            session.commit()
            return True
    except Exception as e:
        logging.warning("No pude actualizar mensaje editado pendiente IA de %s: %s", chat_id, e)
        return False


def _get_pending_ai(chat_id: int):
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not u or not u.ai_pending_text or not u.ai_pending_due_at:
            return None
        payload = _decode_pending_payload(u.ai_pending_text)
        return {
            "text": "\n".join(payload["messages"]).strip(),
            "messages": payload["messages"],
            "message_ids": payload.get("message_ids") or [],
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


def _cancel_pending_ai(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    manual_reply: str = "",
    original_question: str = "",
):
    _cancel_ai_job(context, chat_id)
    pending_text = _clear_pending_ai_db(chat_id)
    question = (pending_text or original_question or "").strip()
    if manual_reply:
        # Una respuesta de Johanna siempre queda como contexto REAL aunque la IA
        # pendiente ya no exista (por ejemplo, respuestas a conversaciones directas).
        _append_ai_exchange(chat_id, question, manual_reply, assistant_source="manual")
    elif question:
        _append_ai_exchange(chat_id, question, "", assistant_source="auto")
    return pending_text


def _pending_ai_question_tail(text_value: str) -> str:
    """Extrae una duda conversacional que venga pegada a una acción operativa.

    Ejemplo: ``Te envío mi ID 123456789 y cuánto tarda la validación`` ->
    ``cuánto tarda la validación``. Si no hay una duda reconocible, devuelve vacío.
    """
    raw = (text_value or "").strip()
    if not raw:
        return ""
    # Preferimos el último marcador interrogativo para evitar devolver de nuevo el
    # prefijo operativo (ID, depósito, solicitud de acceso, etc.).
    patterns = (
        r"(?:^|[;,.!\n]\s*|\s+y\s+)(¿?\s*(?:qué|que|cómo|como|cuánto|cuanto|cuándo|cuando|dónde|donde|por\s+qué|por\s+que)\b.*)$",
        r"(?:^|[;,.!\n]\s*|\s+y\s+)(¿?\s*(?:puedo|podría|podria|debo|tengo\s+que|quiero\s+saber|me\s+gustaría\s+saber|me\s+gustaria\s+saber)\b.*)$",
    )
    for pattern in patterns:
        m = re.search(pattern, raw, re.IGNORECASE | re.DOTALL)
        if m:
            tail = (m.group(1) or "").strip(" ,.;:-")
            if len(_norm(tail)) >= 4:
                return tail
    return ""


def _clean_ai_text_after_operations(text_value: str, resolved_topics=None) -> str:
    """Quita de una IA pendiente solo acciones operativas YA atendidas.

    Conserva preguntas reales del usuario aunque hayan llegado en el mismo mensaje.
    Esta función NO decide estados ni valida nada: solo evita que OpenAI vuelva a
    contestar un ID, depósito o acceso que el motor del bot ya procesó.
    """
    raw = (text_value or "").strip()
    if not raw:
        return ""
    topics = {str(x).upper() for x in (resolved_topics or []) if str(x).strip()}
    if not topics:
        return raw

    units = [x.strip() for x in re.split(r"\n+", raw) if x.strip()] or [raw]
    kept = []
    for unit in units:
        norm = _norm(unit)
        tail = _pending_ai_question_tail(unit)

        # ID ya recibido/validado: elimina número y frases de entrega/registro,
        # pero conserva una duda pegada al mismo texto.
        if "ID_SUBMIT" in topics:
            candidate = _extract_candidate_trading_id(unit)
            id_action = bool(candidate) and (
                unit.strip().isdigit()
                or any(k in norm for k in (
                    "te envio mi id", "te mando mi id", "te paso mi id", "aqui esta mi id",
                    "este es mi id", "mi id es", "ya me registre", "ya me registré",
                    "me registre", "me registré", "ya hice el registro",
                ))
            )
            pure_registered = norm in {
                "ya me registre", "me registre", "ya estoy registrado", "ya estoy registrada",
                "ya hice el registro", "ya realice el registro",
            }
            if id_action or pure_registered:
                if tail:
                    kept.append(tail)
                continue

        # Cuenta antigua/vinculación ya atendida por el motor.
        if "YA_TENGO_CUENTA" in topics and _looks_like_existing_account_query(unit):
            if tail:
                kept.append(tail)
            continue

        # Depósito/redepósito ya atendido por el motor.
        if "DEPOSITO" in topics and _is_deposit_report_intent(unit):
            if tail:
                kept.append(tail)
            continue

        # Acceso VIP ya confirmado. Solo elimina afirmaciones operativas claras,
        # nunca preguntas sobre canales, señales o funcionamiento del VIP.
        if "VIP_ACCESS" in topics:
            access_done = any(k in norm for k in (
                "ya entre al canal", "ya entré al canal", "ya me uni", "ya me uní",
                "ya solicite acceso", "ya solicité acceso", "mande la solicitud",
                "mandé la solicitud", "acceso listo", "ya pude entrar",
            ))
            if access_done:
                if tail:
                    kept.append(tail)
                continue

        kept.append(unit)

    # Deduplica sin alterar el orden natural.
    cleaned = []
    seen = set()
    for item in kept:
        key = _norm(item)
        if key and key not in seen:
            seen.add(key)
            cleaned.append(item.strip())
    return "\n".join(cleaned).strip()


def _prune_pending_ai_after_operation(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    resolved_topics,
    reason: str = "",
):
    """Limpia residuos operativos sin borrar preguntas conversacionales pendientes.

    Mantiene el vencimiento original de 4 minutos. Si queda una pregunta válida,
    reprograma la misma IA para el tiempo que faltaba; si solo quedaba la acción
    operativa ya resuelta, elimina el pendiente por completo.
    """
    pending = _get_pending_ai(chat_id)
    if not pending:
        return ""

    topics = [str(x).upper() for x in (resolved_topics or []) if str(x).strip()]
    cleaned_messages = []
    cleaned_message_ids = []
    original_messages = list(pending.get("messages") or [])
    original_ids = list(pending.get("message_ids") or [])
    if len(original_ids) < len(original_messages):
        original_ids.extend([""] * (len(original_messages) - len(original_ids)))
    changed = False
    for idx, original in enumerate(original_messages):
        cleaned = _clean_ai_text_after_operations(original, topics)
        if cleaned != (original or "").strip():
            changed = True
        if cleaned:
            cleaned_messages.append(cleaned)
            cleaned_message_ids.append(original_ids[idx] if idx < len(original_ids) else "")

    if not changed:
        return (pending.get("text") or "").strip()

    _cancel_ai_job(context, chat_id)
    if not cleaned_messages:
        _clear_pending_ai_db(chat_id)
        logging.info(
            "🧹 IA pendiente operativa eliminada para %s · topics=%s · %s",
            chat_id, ",".join(topics), reason or "sin motivo",
        )
        return ""

    answered = list(pending.get("answered_topics") or []) + topics
    due_at = pending.get("due_at") or (utcnow_naive() + timedelta(seconds=AI_WAIT_SECONDS))
    message_id = str(pending.get("message_id") or "")
    try:
        with Session() as session:
            u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
            if u:
                u.ai_pending_text = _encode_pending_payload(cleaned_messages, answered, cleaned_message_ids)
                u.ai_pending_message_id = message_id or u.ai_pending_message_id
                u.ai_pending_due_at = due_at
                session.commit()
    except Exception as e:
        logging.warning("No pude depurar IA pendiente de %s: %s", chat_id, e)
        return "\n".join(cleaned_messages).strip()

    if context.job_queue:
        delay = max(2, int((due_at - utcnow_naive()).total_seconds()))
        context.job_queue.run_once(
            delayed_ai_reply,
            when=delay,
            data={"chat_id": chat_id, "message_id": message_id},
            name=f"AI_REPLY_{chat_id}",
        )
    logging.info(
        "🧠 IA pendiente depurada para %s · conserva pregunta · topics=%s · %s",
        chat_id, ",".join(topics), reason or "sin motivo",
    )
    return "\n".join(cleaned_messages).strip()


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


def _is_explicit_where_send_id_query(texto: str) -> bool:
    """Solo detecta una pregunta/acción clara sobre DÓNDE enviar el ID.

    Evita falsos positivos como “mañana te enviaré la ID, pero entonces no hará falta...”,
    que es una continuación de conversación y no una consulta de ubicación.
    """
    t = _norm(texto or "")
    if not re.search(r"\bid\b", t):
        return False
    location_patterns = (
        r"(?:por donde|a donde|donde|por aqui|por aca)\b.{0,45}\bid\b",
        r"\bid\b.{0,45}(?:por donde|a donde|donde|por aqui|por aca)\b",
    )
    if any(re.search(p, t) for p in location_patterns):
        return True
    # Formas explícitas de pregunta/confirmación de envío, no futuros narrativos.
    explicit_send = (
        r"(?:te envio|te mando|puedo enviar|puedo mandarte|debo enviar|debo mandarte)\b.{0,35}\bid\b",
        r"\bid\b.{0,35}(?:te lo envio|te lo mando|lo envio por aqui|lo mando por aqui)",
    )
    return any(re.search(p, t) for p in explicit_send)


def _personal_escalation_intent(texto: str):
    """Detecta temas que deben salir de la IA y pasar directo a Johanna.

    Está deliberadamente por encima de las FAQs y de la IA diferida. Incluye
    formas naturales/abreviadas de hablar de gestión ("me interesa la gestión",
    "hacer gestión", "tu gestión"), además de VPN/restricción de país y casos
    particulares de cuenta que requieren revisión real.

    No confunde "gestión de riesgo" con gestión de cuenta/capital.
    """
    t = _norm(texto or "")
    if not t:
        return None

    # VPN / proxy: siempre revisión personal; nunca instrucciones para evadir restricciones.
    if re.search(r"\b(vpn|proxy)\b", t):
        return "VPN"

    # Restricciones/disponibilidad por país.
    country_patterns = (
        r"\b(?:restriccion|restricciones|bloqueo|error|problema)\b.{0,35}\b(?:pais|country)\b",
        r"\b(?:pais|country)\b.{0,35}\b(?:restringido|restriccion|bloqueado|no disponible|no soportado|no permitido|unsupported|not available)\b",
        r"\b(?:no disponible|no esta disponible|no esta habilitado|no funciona|esta bloqueado|esta restringido|not available|unsupported)\b.{0,40}\b(?:pais|country|plataforma|platform)\b",
        r"\b(?:registrarme|registro|registrar|sign up|register)\b.{0,45}\b(?:no me abre|no abre|restringido|restringida|no disponible|no aparece disponible|not available|restricted)\b",
        r"\b(?:plataforma|platform)\b.{0,35}\b(?:no me abre|no abre|restringida|restringido|no disponible|not available|restricted)\b",
        r"\b(?:pais|country)\b.{0,45}\b(?:no me deja|no permite|impide)\b.{0,35}\b(?:registrar|registrarme|entrar|usar|abrir|plataforma|platform|register|sign up)\b",
        r"\b(?:no me abre|no abre|no me aparece disponible|no aparece disponible|no esta disponible|no está disponible)\b.{0,45}\b(?:plataforma|platform|binomo|stockity)\b",
    )
    if any(re.search(p, t) for p in country_patterns):
        return "PAIS"

    # Casos particulares de cuenta que necesitan comprobar un estado real.
    account_review_patterns = (
        r"\b(?:revisar|revisa|revises|verificar|verifica|chequear|chequea)\b.{0,30}\bmi cuenta\b",
        r"\bmi cuenta\b.{0,35}\b(?:bloqueada|bloqueado|suspendida|suspendido|restringida|restringido|cerrada|cerrado)\b",
        r"\b(?:bloquearon|suspendieron|cerraron|restringieron)\b.{0,30}\bmi cuenta\b",
        r"\b(?:problema|caso|inconveniente)\b.{0,25}\b(?:con|de)\b.{0,15}\bmi cuenta\b",
        r"\bmi cuenta\b.{0,25}\b(?:no funciona|no abre|no me deja|tiene un problema|tiene problema)\b",
    )
    if any(re.search(p, t) for p in account_review_patterns):
        return "CUENTA_PERSONAL"

    # Gestión de riesgo es una consulta educativa normal, no una gestión de cuenta.
    risk_management = any(x in t for x in (
        "gestion de riesgo", "gestion del riesgo", "manejo de riesgo",
        "gestionar riesgo", "gestionar el riesgo", "risk management",
    ))

    # Frases inequívocas de gestión de capital/cuenta.
    strong_management = (
        "gestion de capital", "gestionar capital", "manejo de capital",
        "gestion de cuenta", "gestionar mi cuenta", "gestiones mi cuenta",
        "manejo de cuenta", "manejar mi cuenta", "manejes mi cuenta",
        "operar mi cuenta", "operes mi cuenta", "administrar mi cuenta", "administres mi cuenta",
        "inversion contigo", "enviarte capital", "capital management", "manage my capital",
        "investment with you", "gestion conmigo", "gestion contigo", "gestionar contigo",
        "me ayudas a gestionar", "me ayudarias a gestionar", "gestionar con bono",
        "tu gestionaras", "usted gestionara", "vas a gestionar mi cuenta", "me vas a gestionar",
        "me gestionaras", "tu gestionarias", "usted gestionaria",
    )
    if any(x in t for x in strong_management):
        return "GESTION_CAPITAL"

    # Formas naturales que usan simplemente "la gestión" porque el contexto ya está claro.
    # Ej.: "Cuéntame, me interesa la gestión", "no sé si registrarme o hacer gestión".
    generic_management_signal = bool(re.search(
        r"\b(?:gestion|gestionar|gestionarme|gestionas|gestiones|gestionaria|gestionarias|gestionara|gestionaras)\b",
        t,
    ))
    generic_context = any(x in t for x in (
        "me interesa", "interesa la gestion", "quiero gestion", "quiero la gestion",
        "hacer gestion", "sobre la gestion", "de la gestion", "tu gestion",
        "como funciona la gestion", "como es la gestion", "cuentame", "explicame",
        "modalidad", "modalidades", "acordar", "acuerdo", "contigo", "mi cuenta", "capital",
    ))
    if generic_management_signal and generic_context and not risk_management:
        return "GESTION_CAPITAL"

    return None


def _is_deposit_report_intent(texto: str) -> bool:
    """Reconoce que el usuario YA realizó un depósito, incluido un redepósito.

    Cubre lenguaje natural como "hice otro depósito", "hice un nuevo depósito",
    "volví a depositar", "deposité otra vez" y errores ortográficos frecuentes
    como "hise otro deposito". No confunde preguntas/futuros del tipo
    "¿puedo hacer otro depósito?" o "voy a depositar" con un depósito ya hecho.
    """
    t = _norm(texto or "")
    if not t:
        return False

    completed_patterns = (
        # Primer depósito o redepósito expresado como acción ya realizada.
        r"\b(?:ya\s+)?(?:hice|hise|ise|realice|realize|efectue)\b.{0,28}\b(?:otro|nuevo|adicional|un\s+nuevo)?\s*(?:deposito|pago)\b",
        r"\b(?:ya\s+)?(?:deposite|depositamos)\b(?:.{0,22}\b(?:otra\s+vez|de\s+nuevo|otro|adicional|nuevo))?\b",
        r"\b(?:volvi|volvimos)\s+a\s+(?:depositar|hacer\s+un\s+deposito)\b",
        r"\b(?:acabo|acabamos)\s+de\s+(?:depositar|hacer\s+(?:otro|un\s+nuevo|un)?\s*deposito)\b",
        r"\b(?:otro|nuevo|adicional)\s+(?:deposito|pago)\b.{0,18}\b(?:listo|hecho|realizado|enviado)\b",
        r"\b(?:ya\s+)?(?:pague|pago\s+hecho)\b",
    )
    if any(re.search(p, t) for p in completed_patterns):
        return True

    # Frases históricas ya soportadas.
    return any(k in t for k in (
        "ya deposite", "ya hice el deposito", "deposito listo", "ya esta el deposito",
        "ya me llego el deposito", "ya me llego el pago", "i deposited",
        "deposit done", "i made the deposit", "i made another deposit",
        "i deposited again", "another deposit done",
    ))


def _is_min_50_intent(texto: str) -> bool:
    """Detecta montos realmente menores al mínimo sin confundir 30 con 300.

    Las versiones anteriores usaban coincidencias de subcadenas como
    ``"depositar 30" in texto`` y por eso ``depositar 300`` podía activar MIN_50.
    Aquí los números se comparan como tokens completos.
    """
    raw = (texto or "").strip()
    t = _norm(raw)
    if not t:
        return False

    explicit = (
        "no tengo 50", "no tengo cincuenta", "puedo con menos", "puedo iniciar con menos",
        "puedo empezar con menos", "puedo depositar menos", "puedo depositar con menos",
        "puedo depositar menos de 50", "menos de 50", "menos de cincuenta",
        "deposito minimo", "depósito mínimo", "monto minimo", "monto mínimo",
        "minimo de deposito", "mínimo de depósito",
    )
    if any(x in t for x in explicit):
        return True

    # Solo 10/20/30/40 como valores completos. El (?!\\d) evita 30 -> 300.
    amount = re.search(r"(?<!\d)(10|20|30|40)(?:[.,]0+)?(?!\d)", t)
    if not amount:
        return False
    context_terms = (
        "tengo", "solo tengo", "puedo con", "con ", "deposit", "iniciar", "empezar",
        "dolar", "dólar", "usd", "$",
    )
    return any(x in t for x in context_terms)


def _is_hypothetical_other_person(texto: str) -> bool:
    """True cuando la pregunta habla claramente de otra persona/caso general."""
    t = _norm(texto or "")
    markers = (
        "si otra persona", "si una persona", "para otra persona", "una persona que",
        "alguien que", "si alguien", "en el caso de otra persona", "hipoteticamente",
        "hipotéticamente", "another person", "if someone", "if another person",
    )
    return any(x in t for x in markers)


def _question_depends_on_account_validation(texto: str) -> bool:
    """Indica si la duda actual depende de validar una cuenta antigua propia."""
    if _is_hypothetical_other_person(texto):
        return False
    t = _norm(texto or "")
    if _looks_like_existing_account_query(texto):
        return True
    terms = (
        "quiero depositar", "voy a depositar", "puedo depositar", "hacer deposito", "hacer depósito",
        "mi cuenta", "esa cuenta", "esta cuenta", "mi id", "el id",
        "activar mi", "activar la cuenta", "seguir con el deposito", "seguir con el depósito",
    )
    return any(x in t for x in terms)


def _recent_account_validation_required(chat_id: int, minutes: int = 20) -> bool:
    """Detecta una solicitud reciente de validar una cuenta vieja aún sin ID nuevo.

    Se usa solo como contexto conversacional y nunca cambia el estado real del usuario.
    """
    cutoff = utcnow_naive() - timedelta(minutes=max(1, int(minutes)))
    history = _load_ai_history(chat_id)
    marker_index = -1
    for idx, item in enumerate(history):
        raw_ts = str(item.get("ts") or "").strip()
        if raw_ts:
            try:
                if datetime.fromisoformat(raw_ts) < cutoff:
                    continue
            except Exception:
                pass
        if item.get("role") != "assistant":
            continue
        content = _norm(str(item.get("content") or ""))
        if any(x in content for x in (
            "enviame primero el id de esa cuenta", "envíame primero el id de esa cuenta",
            "primero enviame el id", "primero envíame el id",
            "send me the id of that account first", "send me the account id first",
        )):
            marker_index = idx

    if marker_index < 0:
        return False

    # Si después de ese punto el usuario ya envió un ID candidato, la dependencia
    # deja de estar pendiente para efectos conversacionales.
    for item in history[marker_index + 1:]:
        if item.get("role") == "user" and _extract_candidate_trading_id(str(item.get("content") or "")):
            return False
        if item.get("role") == "assistant":
            c = _norm(str(item.get("content") or ""))
            if any(x in c for x in ("id validado", "id correctamente validado", "id successfully validated")):
                return False
    return True


def _looks_like_existing_account_query(texto: str) -> bool:
    t = _norm(texto or "")
    terms = (
        "ya tengo una cuenta", "ya tengo cuenta", "cuenta vieja", "cuenta antigua",
        "cuenta de hace meses", "cuenta de hace tiempo", "cuenta registrada contigo",
        "cuenta registrada con tu enlace", "la registre contigo", "la registré contigo",
        "fue contigo", "fue registrada contigo", "fue registrado contigo",
        "creo que fue registrada contigo", "creo que fue registrado contigo",
        "no se si fue registrada contigo", "no sé si fue registrada contigo",
        "no fue con tu enlace", "no fue contigo", "already have an account",
        "old account", "registered with you", "registered through your link",
    )
    return any(x in t for x in terms)


def _existing_account_relation(texto: str) -> str:
    """Clasifica lo que el usuario afirma sobre una cuenta antigua.

    Devuelve NOT_LINKED, UNCERTAIN, LINKED o GENERIC. La incertidumbre se evalúa
    antes que LINKED para no convertir "creo que fue contigo" en confirmación.
    """
    t = _norm(texto or "")
    uncertain = (
        "creo que fue contigo", "creo que fue registrada contigo", "creo que fue registrado contigo",
        "creo que la registre contigo", "creo que la registré contigo",
        "no estoy seguro", "no estoy segura", "no se si fue contigo", "no sé si fue contigo",
        "no se si fue registrada contigo", "no sé si fue registrada contigo",
        "no recuerdo si fue contigo", "quizas fue contigo", "quizás fue contigo",
        "i think it was with you", "not sure if", "i don't remember if",
    )
    if any(x in t for x in uncertain):
        return "UNCERTAIN"
    not_linked = (
        "no fue con tu enlace", "no fue contigo", "no la registre contigo", "no la registré contigo",
        "no esta registrada contigo", "no está registrada contigo", "no fue registrada con tu enlace",
        "not through your link", "wasn't registered with you", "was not registered with you",
    )
    if any(x in t for x in not_linked):
        return "NOT_LINKED"
    linked = (
        "registrada contigo", "registrado contigo", "la registre contigo", "la registré contigo",
        "fue contigo", "con tu enlace", "registered with you", "through your link",
    )
    if any(x in t for x in linked):
        return "LINKED"
    return "GENERIC"


def _existing_account_reply(texto: str, lang: str, chat_id: int) -> str:
    relation = _existing_account_relation(texto)
    if relation == "NOT_LINKED":
        if lang == "en":
            return (
                "If that old account was not registered through my link, you need a new account correctly linked to me. "
                "If it has funds, withdraw them first and then close/delete the old account. For the new registration, open an incognito browser window, use one of my links and a different email address. "
                "When you finish, send me the new ID BEFORE depositing.\n\n"
                f"🔗 Stockity — primary option:\n{ENLACE_REFERIDO_STOCKITY}\n\n"
                f"🔗 Binomo — secondary option:\n{ENLACE_REFERIDO}"
            )
        return (
            "Si esa cuenta vieja no fue registrada con mi enlace, necesitas crear una nueva correctamente vinculada conmigo. "
            "Si tiene saldo, retíralo primero y luego elimina/cierra la cuenta anterior. Para el nuevo registro abre una ventana de incógnito, entra con uno de mis enlaces y usa un correo diferente. "
            "Cuando termines, envíame el nuevo ID ANTES de depositar.\n\n"
            f"🔗 Stockity — opción principal:\n{ENLACE_REFERIDO_STOCKITY}\n\n"
            f"🔗 Binomo — opción secundaria:\n{ENLACE_REFERIDO}"
        )
    if lang == "en":
        return (
            "Send me the ID of that account first so I can verify whether it is correctly linked to me. "
            "Do not make a new deposit in that account until I confirm the validation; if it checks out, we continue from there."
        )
    return (
        "Envíame primero el ID de esa cuenta para verificar si está correctamente vinculada conmigo. "
        "No hagas un depósito nuevo en esa cuenta hasta que te confirme la validación; si está todo bien, continuamos desde ahí."
    )


def _ai_dependency_context(question: str, chat_id: int, lang: str = "es") -> str:
    """Prioriza validaciones previas solo cuando la pregunta ACTUAL depende de ellas.

    Así una cuenta antigua pendiente sigue bloqueando "quiero depositar 300", pero
    una pregunta nueva e hipotética como "si una persona está empezando..." no
    arrastra el ID de la conversación anterior.
    """
    direct_existing = _looks_like_existing_account_query(question)
    relation = _existing_account_relation(question) if direct_existing else ""
    recent_dependency = (
        _recent_account_validation_required(chat_id)
        and _question_depends_on_account_validation(question)
    )
    if not direct_existing and not recent_dependency:
        return ""

    if relation == "NOT_LINKED":
        return (
            "PRIORIDAD DE DEPENDENCIA: el usuario confirmó que su cuenta vieja NO está vinculada. "
            "Primero explica el registro correcto de una nueva cuenta (retirar saldo si existe → cerrar/eliminar cuenta anterior → registro en incógnito con otro correo → enviar nuevo ID antes de depositar). "
            "No asumas todavía qué bono corresponde ni que puede depositar hasta validar el nuevo ID."
            if lang == "es" else
            "DEPENDENCY PRIORITY: the user confirmed the old account is NOT linked. Explain the correct new-registration process first (withdraw funds if any → close/delete old account → incognito registration with a different email → send new ID before depositing). Do not assume a bonus or deposit can proceed before ID validation."
        )
    return (
        "PRIORIDAD DE DEPENDENCIA: la vinculación de la cuenta antigua todavía NO está validada. "
        "El siguiente paso obligatorio es pedir el ID y validarlo. No asumas qué bono corresponde, no recomiendes hacer el depósito todavía y no saltes a activación. "
        "Esta prioridad aplica solo a la duda actual relacionada con ESA cuenta; si el mensaje actual es una pregunta hipotética o cambia de tema, responde el nuevo tema sin arrastrar el ID anterior."
        if lang == "es" else
        "DEPENDENCY PRIORITY: the old account linkage is NOT validated yet. The required next step is to request and validate the ID. Do not assume a bonus, do not tell them to deposit yet, and do not jump to activation. This priority applies only to the current question about THAT account; if the current message is hypothetical or changes topic, answer the new topic without dragging the old ID forward."
    )


def detect_intent_es(texto: str) -> str:
    t = _norm(texto)

    # Los temas personales/sensibles tienen prioridad incluso sobre "tengo una duda".
    personal_intent = _personal_escalation_intent(texto)
    if personal_intent:
        return personal_intent

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
        "capital management", "manage my capital", "investment with you",
        "gestion de cuenta", "gestión de cuenta", "gestionar mi cuenta", "gestiones mi cuenta",
        "me ayudas a gestionar", "me ayudarías a gestionar", "me ayudarias a gestionar",
        "gestionar con bono", "gestionar con un bono", "manejar mi cuenta", "manejes mi cuenta",
        "operar mi cuenta", "operes mi cuenta", "administrar mi cuenta", "administres mi cuenta",
        "tu gestionaras", "tú gestionarás", "usted gestionara", "usted gestionará",
        "vas a gestionar mi cuenta", "me vas a gestionar", "me gestionaras", "me gestionarás",
        "tu gestionarias", "tú gestionarías", "usted gestionaria", "usted gestionaría"
    ]):
        return "GESTION_CAPITAL"

    # ---- Cuenta existente / antigua ----
    if _looks_like_existing_account_query(texto):
        return "YA_TENGO_CUENTA"

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
    if _is_min_50_intent(texto):
        return "MIN_50"

    # ---- Depósito realizado / redepósito / acceso VIP ----
    # Un usuario ya activo que diga "hice otro depósito" debe entrar al flujo
    # operativo de depósito adicional, nunca esperar a la IA diferida.
    if _is_deposit_report_intent(texto) or any(k in t for k in [
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
"ya cree cuenta", "ya creé cuenta", "ya hice el registro", "ya realice el registro", "ya realicé el registro",
    ]):
        return "YA_REGISTRE"

# ---- Siguiente paso / qué sigue ----
    if any(k in t for k in [
        "que sigue", "qué sigue", "que paso sigue", "qué paso sigue", "paso sigue",
        "y ahora que", "y ahora qué", "entonces que sigue", "entonces qué sigue",
        "ok gracias entonces", "ok gracias", "ya me registre que hago", "ya me registré que hago",
        "que hago ahora", "qué hago ahora", "siguiente paso",
        "que me toca", "qué me toca", "como sigo", "cómo sigo",
        "por donde empiezo", "por dónde empiezo", "como empiezo", "cómo empiezo",
        "what do i do", "what next", "what's next", "how do i continue", "how do i start"
    ]):
        return "NEXT_STEP"

    # Si el mensaje contiene un ID (número) en cualquier parte (prioridad alta)
    m_id = re.search(r"\b\d{6,12}\b", t)
    if m_id:
        return "ID_SUBMIT"

    # ---- Dónde enviar el ID / te envío el ID ----
    if _is_explicit_where_send_id_query(texto):
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

    if _is_bonus_or_promo_mention(texto):
        return "BONO"

    # ID debe ser una palabra/token real. Evita falsos positivos como “comunidad” + “cómo”,
    # que antes podía disparar por error la respuesta fija de ID.
    if re.search(r"\bid\b", t) and any(k in t for k in ["donde", "como", "encuentro", "ver", "buscar", "ubico", "aparece"]):
        return "ID"

    if any(k in t for k in ["retiro", "retirar", "withdraw", "rechaz", "rechazo", "deneg", "no me deja retirar"]):
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

    # Escalamiento personal tiene prioridad semántica. Se añade además de otros
    # temas para que cualquier pregunta mixta (registro + gestión, ID + gestión,
    # etc.) termine directamente en el chat personal de Johanna.
    personal_intent = _personal_escalation_intent(texto)
    if personal_intent:
        _add_intent(found, personal_intent)

    # ID numérico: solo si el mensaje realmente parece un envío de ID.
    # Evita confundir capitales, montos, fechas u otros números con un ID de trading.
    if _extract_candidate_trading_id(texto):
        _add_intent(found, "ID_SUBMIT")

    if any(k in t for k in [
        "gestion de capital", "gestión de capital", "gestionar capital", "manejas capital",
        "manejo de capital", "inversion contigo", "inversión contigo", "enviarte capital",
        "capital management", "manage my capital", "investment with you",
        "gestion de cuenta", "gestión de cuenta", "gestionar mi cuenta", "gestiones mi cuenta",
        "me ayudas a gestionar", "me ayudarías a gestionar", "me ayudarias a gestionar",
        "gestionar con bono", "gestionar con un bono", "manejar mi cuenta", "manejes mi cuenta",
        "operar mi cuenta", "operes mi cuenta", "administrar mi cuenta", "administres mi cuenta",
        "tu gestionaras", "tú gestionarás", "usted gestionara", "usted gestionará",
        "vas a gestionar mi cuenta", "me vas a gestionar", "me gestionaras", "me gestionarás",
        "tu gestionarias", "tú gestionarías", "usted gestionaria", "usted gestionaría",
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

    if _is_min_50_intent(texto):
        _add_intent(found, "MIN_50")

    if _is_deposit_report_intent(texto) or any(k in t for k in [
        "ya active", "ya activé", "dame acceso", "habilitar acceso", "acceso vip",
    ]):
        _add_intent(found, "DEPOSITO")

    # Diferenciamos cuenta existente/antigua de un registro recién realizado.
    if _looks_like_existing_account_query(texto):
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
        "que me toca", "qué me toca", "como sigo", "cómo sigo", "por donde empiezo", "por dónde empiezo",
        "como empiezo", "cómo empiezo", "what do i do", "what next", "what's next", "next step",
        "how do i continue", "how do i start",
    ]):
        _add_intent(found, "NEXT_STEP")

    if _is_explicit_where_send_id_query(texto) or ("id" in t and "where do i send" in t):
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

    if re.search(r"\bnivel\b", t) or any(k in t for k in [
        "niveles", "nivel basico", "nivel básico", "nivel premium", "nivel prestige", "planes", "plan basico",
        "plan básico", "plan premium", "plan prestige", "cuanto necesito para entrar", "cuánto necesito para entrar",
        "cuanto debo depositar", "cuánto debo depositar", "inversion minima", "inversión mínima", "minimum investment",
        "cuanto cuesta", "cuánto cuesta", "cuanto vale", "cuánto vale", "que niveles tienes", "qué niveles tienes",
        "niveles disponibles", "planes disponibles", "cuanto hay que invertir", "cuánto hay que invertir",
        "como hago mi inversion", "cómo hago mi inversión", "levels", "plans", "basic level", "premium level", "prestige level",
    ]):
        _add_intent(found, "NIVELES")

    if _is_bonus_or_promo_mention(texto):
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
        "📊 Las señales dependen de tu nivel dentro de JT TRADERS TEAMS.\n\n"
        "🟢 Básico: 30–50 señales CRYPTO IDX diarias de lunes a viernes.\n"
        "🔵 Premium/🟣 Prestige: Software Premium Anticipado con +300 señales AL DÍA de lunes a sábado, distribuidas normalmente desde la mañana hasta la noche, entre CRYPTO IDX, pares de divisas, índices sintéticos y Forex.\n\n"
        "🤖 Premium añade bot IA CRYPTO IDX 24/7; Prestige añade además bot IA de pares de divisas 24/7. Las alertas son automáticas, pero las entradas se toman manualmente."
    )


def respuesta_senales_en() -> str:
    return (
        "📊 Signals depend on your level inside JT TRADERS TEAMS.\n\n"
        "🟢 Basic: 30–50 CRYPTO IDX signals per day, Monday to Friday.\n"
        "🔵 Premium/🟣 Prestige: Premium Anticipated Software with 300+ signals PER DAY Monday to Saturday, normally distributed from morning through evening across CRYPTO IDX, currency pairs, synthetic indices and Forex.\n\n"
        "🤖 Premium adds a CRYPTO IDX AI bot 24/7; Prestige also adds a 24/7 currency-pair AI bot. Alerts are automatic, but entries are taken manually."
    )


def respuesta_bot_ia_es() -> str:
    return (
        "Sí 😊 Mis bots IA están disponibles desde Premium dentro de JT TRADERS TEAMS. "
        "Premium incluye CRYPTO IDX 24/7 y Prestige añade también el bot de pares de divisas 24/7. "
        "El nivel depende del capital que mantengas en tu propia cuenta de trading."
    )


def respuesta_bot_ia_en() -> str:
    return (
        "Yes 😊 My AI bots are available from the Premium level inside JT TRADERS TEAMS. "
        "Premium includes CRYPTO IDX 24/7, and Prestige also adds the 24/7 currency-pair bot. "
        "Your level depends on the capital you keep in your own trading account."
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
            "Desde 50 USD, Básico incluye Binary Teams Módulos 1–3 + VIP principal + 30–50 señales CRYPTO IDX diarias de lunes a viernes. Desde 200 USD, Premium habilita Módulos 1–4, +300 señales AL DÍA de lunes a sábado y bot IA CRYPTO IDX 24/7.\n\n"
            "El depósito siempre queda en tu propia cuenta de trading. 🚀"
            if lang == "es" else
            "💰 The minimum required to activate the Basic level is USD 50. With less than USD 50, access is not enabled yet; the remaining amount must be completed.\n\n"
            "From USD 50, Basic includes Binary Teams Modules 1–3 + main VIP + 30–50 CRYPTO IDX signals per day Monday to Friday. From USD 200, Premium unlocks Modules 1–4, 300+ signals PER DAY Monday to Saturday and the CRYPTO IDX AI bot 24/7.\n\n"
            "The deposit always stays in your own trading account. 🚀"
        )
    if intent in ("VPN", "PAIS"):
        return (
            "🌎 Este caso prefiero revisarlo directamente contigo porque depende de la disponibilidad de la plataforma en tu país. Escríbeme a mi chat personal y lo revisamos. 👇"
            if lang == "es" else
            "🌎 I prefer to review this directly with you because it depends on platform availability in your country. Message me in my personal chat and we’ll check it. 👇"
        )
    if intent == "GESTION_CAPITAL":
        return (
            "📊 Este tema prefiero hablarlo directamente contigo porque cada gestión depende del caso. Escríbeme a mi chat personal y lo revisamos juntos. 👇"
            if lang == "es" else
            "📊 I prefer to discuss this directly with you because each management case is different. Message me in my personal chat and we’ll review it together. 👇"
        )
    if intent == "CUENTA_PERSONAL":
        return (
            "🔐 Este caso necesito revisarlo personalmente contigo porque depende del estado real de tu cuenta. Escríbeme a mi chat personal y lo vemos directamente. 👇"
            if lang == "es" else
            "🔐 I need to review this personally with you because it depends on the real status of your account. Message me in my personal chat and we’ll check it directly. 👇"
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
        "💜 Mi comunidad es GRATUITA: el depósito se realiza directamente en tu propia cuenta de trading.\n\n"
        "🟢 Básico — desde 50 USD\n"
        "🎓 Binary Teams Módulos 1 al 3: plataforma + introducción/análisis bursátil.\n"
        "📈 30–50 señales CRYPTO IDX diarias, de lunes a viernes.\n\n"
        "🔵 Premium — desde 200 USD\n"
        "🎓 Binary Teams Módulos 1 al 4; el Módulo 4 es Smart Money Concept.\n"
        "📚 Material de apoyo, sesiones y acompañamiento.\n"
        "🚀 Software Premium Anticipado con +300 señales AL DÍA de lunes a sábado: CRYPTO IDX, pares de divisas, índices sintéticos y Forex.\n"
        "🤖 Bot IA CRYPTO IDX 24/7.\n\n"
        "🟣 Prestige — desde 500 USD\n"
        "🚀 Incluye todo Premium.\n"
        "🎓 Añade Madness Trading Avanzado — método ALGO & LIT.\n"
        "🤖 Añade bot IA de pares de divisas 24/7.\n"
        "👩‍🏫 Mentorías privadas, acompañamiento cercano y preparación para cuentas de fondeo.\n\n"
        "📌 El nivel pertenece a mi comunidad JT TRADERS TEAMS y depende del capital que mantengas en tu propia cuenta de trading."
    )


def respuesta_niveles_en() -> str:
    return (
        "📊 JT TRADERS Levels\n\n"
        "💜 My community is FREE: the deposit is made directly into your own trading account.\n\n"
        "🟢 Basic — from USD 50\n"
        "🎓 Binary Teams Modules 1–3: platform basics + introduction/market analysis.\n"
        "📈 30–50 CRYPTO IDX signals per day, Monday to Friday.\n\n"
        "🔵 Premium — from USD 200\n"
        "🎓 Binary Teams Modules 1–4; Module 4 is Smart Money Concept.\n"
        "📚 Support materials, live sessions and guidance.\n"
        "🚀 Premium Anticipated Software with 300+ signals PER DAY Monday to Saturday: CRYPTO IDX, currency pairs, synthetic indices and Forex.\n"
        "🤖 CRYPTO IDX AI bot 24/7.\n\n"
        "🟣 Prestige — from USD 500\n"
        "🚀 Includes everything in Premium.\n"
        "🎓 Adds Madness Advanced Trading — ALGO & LIT method.\n"
        "🤖 Adds a 24/7 currency-pair AI bot.\n"
        "👩‍🏫 Private mentoring, closer guidance and funded-account preparation.\n\n"
        "📌 The level belongs to my JT TRADERS TEAMS community and depends on the capital you keep in your own trading account."
    )

def respuesta_bono_es() -> str:
    cfg = _promo_get_config()
    expiry = _promo_parse_expiry(cfg["expires_on"])
    today = datetime.now(COLOMBIA_TZ).date()
    if expiry and expiry < today:
        return "🎁 La promoción configurada ya llegó a su fecha de vencimiento. Prefiero confirmar los códigos vigentes antes de darte uno."
    return (
        "🎁 Tengo dos códigos promocionales activos:\n\n"
        f"💯 100% primer depósito: {cfg['code_100']}\n"
        f"🔥 70% depósitos posteriores: {cfg['code_70']}\n\n"
        f"📅 Vigentes hasta {_promo_expiry_display(cfg['expires_on'])}."
    )


def respuesta_bono_en() -> str:
    cfg = _promo_get_config()
    expiry = _promo_parse_expiry(cfg["expires_on"])
    today = datetime.now(COLOMBIA_TZ).date()
    if expiry and expiry < today:
        return "🎁 The configured promotion has reached its expiry date. I prefer to confirm the current codes before giving you one."
    return (
        "🎁 I currently have two active promo codes:\n\n"
        f"💯 100% first deposit: {cfg['code_100']}\n"
        f"🔥 70% subsequent deposits: {cfg['code_70']}\n\n"
        f"📅 Valid through {_promo_expiry_display(cfg['expires_on'])}."
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
        "🆔 ¿Dónde encuentro mi ID de Stockity o Binomo?\n\n"
        "1) Entra a tu cuenta (app o web).\n"
        "2) Ve a tu perfil / ajustes (icono de usuario).\n"
        "3) Busca el campo ID o User ID y cópialo.\n\n"
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



def _recent_manual_context_text(chat_id: int, limit: int = 10) -> str:
    """Texto reciente escrito/dicho realmente por Johanna, solo para validar acuerdos."""
    parts = []
    for item in reversed(_load_ai_history(chat_id)):
        if item.get("role") != "assistant" or str(item.get("source") or "").lower() != "manual":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            parts.append(content)
        if len(parts) >= max(1, int(limit)):
            break
    return "\n".join(reversed(parts))


def _manual_context_supports_management_commitment(chat_id: int) -> bool:
    """Solo una respuesta REAL y positiva de Johanna puede respaldar un acuerdo de gestión."""
    t = _norm(_recent_manual_context_text(chat_id, limit=10))
    if not t:
        return False
    negative = (
        "no voy a gestionar", "no gestiono", "no puedo gestionar", "no manejo cuentas",
        "no voy a manejar", "no voy a administrar", "no voy a operar", "no creare", "no crearé",
    )
    if any(_norm(x) in t for x in negative):
        return False
    positive_patterns = (
        r"\b(?:yo )?(?:voy a|me encargo de|puedo|hare|haré)\b.{0,80}\b(?:gestionar|manejar|administrar|operar|crear)\b",
        r"\b(?:gestionar|manejar|administrar|operar|crear)\b.{0,80}\b(?:yo lo hago|yo me encargo|conmigo|quedamos|acordamos)\b",
    )
    return any(re.search(pat, t, re.IGNORECASE | re.DOTALL) for pat in positive_patterns)


def _conversation_evidence_text(chat_id: int) -> str:
    """Evidencia fiable para afirmaciones: usuario + respuestas manuales reales de Johanna."""
    parts = []
    for item in _load_ai_history(chat_id)[-16:]:
        role = item.get("role")
        source = str(item.get("source") or "").lower()
        if role == "user" or (role == "assistant" and source == "manual"):
            content = str(item.get("content") or "").strip()
            if content:
                parts.append(content)
    return "\n".join(parts)


def _ai_answer_context_guard(answer: str, question: str, chat_id: int, lang: str) -> tuple[str, bool]:
    """Evita que la IA invente acuerdos/acciones personales no respaldados por el historial.

    Devuelve (respuesta, personal_review). Se aplica DESPUÉS del modelo porque una
    instrucción de prompt por sí sola no basta para impedir todos los falsos acuerdos.
    """
    answer = (answer or "").strip()
    if not answer:
        return answer, False

    an = _norm(answer)
    qn = _norm(question or "")
    reliable_history = _norm(_conversation_evidence_text(chat_id))
    evidence = f"{qn}\n{reliable_history}"

    # Compromisos sensibles: la IA no puede prometer que Johanna va a gestionar,
    # administrar, operar o crear una cuenta salvo que Johanna lo haya dicho realmente.
    commitment_phrases = (
        "procedere a gestionar", "procederé a gestionar", "voy a gestionar tu cuenta",
        "gestionare tu cuenta", "gestionaré tu cuenta", "administrare tu cuenta",
        "administraré tu cuenta", "voy a administrar tu cuenta", "operare tu cuenta",
        "operaré tu cuenta", "voy a operar tu cuenta", "creare tu cuenta", "crearé tu cuenta",
        "voy a crear tu cuenta", "yo creare la cuenta", "yo crearé la cuenta",
    )
    generic_commitment = bool(re.search(
        r"\b(?:voy a|procedere a|procederé a|me encargare de|me encargaré de|yo voy a|yo puedo)\b.{0,90}\b(?:gestionar|manejar|administrar|operar|crear)\b",
        an, re.IGNORECASE | re.DOTALL
    ))
    unsupported_management_commitment = (
        (any(_norm(p) in an for p in commitment_phrases) or generic_commitment)
        and not _manual_context_supports_management_commitment(chat_id)
    )

    # No afirmar como hecho que el usuario acaba de completar pasos solo porque el
    # stage interno diga PRE/POST/DEPOSITED. El estado operativo no sustituye la conversación.
    status_claims = (
        "has completado tu registro", "completaste tu registro", "ya completaste tu registro",
        "al haber completado tu registro", "has realizado el deposito", "has realizado el depósito",
        "realizaste el deposito", "realizaste el depósito", "ya realizaste el deposito",
        "ya realizaste el depósito",
    )
    status_evidence_terms = (
        "ya me registre", "ya me registré", "complete el registro", "completé el registro",
        "ya deposite", "ya deposité", "hice el deposito", "hice el depósito",
        "deposito confirmado", "depósito confirmado", "registro completado",
    )
    unsupported_status_claim = (
        any(_norm(p) in an for p in status_claims)
        and not any(_norm(p) in evidence for p in status_evidence_terms)
    )

    if not (unsupported_management_commitment or unsupported_status_claim):
        return answer, False

    # Si el historial no respalda ese acuerdo, es preferible una aclaración breve
    # y humana a inventar un paso del proceso.
    if lang == "en":
        safe = (
            "[[PERSONAL_CHAT]] I understand what you mean 😊 but I don’t want to assume or change what we may have already agreed about account management. "
            "Before you register again or send an ID, message me directly and I’ll continue from the exact point we left off."
        )
    else:
        safe = (
            "[[PERSONAL_CHAT]] Entiendo lo que me dices 😊, pero no quiero asumir ni cambiar lo que podamos haber acordado sobre la gestión. "
            "Antes de volver a registrarte o enviarme un ID, escríbeme directamente y continuamos exactamente desde el punto en que quedamos."
        )
    return safe, True


def _is_reaction_only_message(text_value: str) -> bool:
    """True para mensajes breves formados solo por emojis/reacciones, sin palabras ni números."""
    value = (text_value or "").strip()
    if not value or len(value) > 40:
        return False
    if re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]", value):
        return False
    emoji_like = 0
    for ch in value:
        if ch.isspace() or ch in "!?¿¡.,;:()[]{}'\"-_+*=~/\\|":
            continue
        cat = unicodedata.category(ch)
        code = ord(ch)
        if cat in ("So", "Sk", "Mn", "Cf") or code >= 0x1F000:
            emoji_like += 1
            continue
        return False
    return emoji_like > 0


def _strip_redundant_ai_greeting(answer: str, question: str, history_text: str, lang: str) -> str:
    """Evita que la IA vuelva a saludar en cada turno de una conversación ya iniciada."""
    value = (answer or "").strip()
    if not value or not (history_text or "").strip():
        return value
    qn = _norm(question or "")
    if re.match(r"^(hola|holi|hello|hey|buenas|buenos|buen dia|buenas tardes|buenas noches|hi)\b", qn):
        return value
    if lang == "en":
        value = re.sub(r"^\s*(?:hello|hi|hey)\s*[!,.]?\s*(?:[👋😊🙂✨]+\s*)?", "", value, count=1, flags=re.I)
    else:
        value = re.sub(r"^\s*¡?(?:hola|holi|buenas)\s*[!,.]?\s*(?:[👋😊🙂✨]+\s*)?", "", value, count=1, flags=re.I)
    return value.strip()


def _neutralize_ai_gender(answer: str, lang: str = "es") -> str:
    """Evita género asumido en segunda persona sin volver artificial la respuesta."""
    value = (answer or "").strip()
    if not value or lang != "es":
        return value
    replacements = (
        (r"\bpara un principiante\b", "si estás empezando"),
        (r"\bpara una principiante\b", "si estás empezando"),
        (r"\bsi eres (?:un |una )?nuev[oa]\b", "si estás empezando"),
        (r"\bsi eres nuev[oa] en esto\b", "si estás empezando"),
        (r"\bmantenerte enfocad[oa]\b", "mantener tu enfoque"),
        (r"\bte mantendr[aá]s enfocad[oa]\b", "mantendrás tu enfoque"),
        (r"\bte mantendr[aá] enfocad[oa]\b", "te ayudará a mantener tu enfoque"),
        (r"\bte ayudar[aá] a mantenerte enfocad[oa]\b", "te ayudará a mantener tu enfoque"),
        (r"\bpara mantenerte enfocad[oa]\b", "para mantener tu enfoque"),
        (r"\bdebes estar atent[oa]\b", "debes prestar atención"),
        (r"\bmantente atent[oa]\b", "presta atención"),
        (r"\best[aá] atento[oa]?\b", "presta atención"),
        (r"\bcuando est[eé]s list[oa]\b", "cuando quieras continuar"),
        (r"\bsi est[aá]s list[oa]\b", "si quieres continuar"),
        (r"\bsi ya est[aá]s registrad[oa]\b", "si ya completaste el registro"),
        (r"\btú mismo\b", "directamente"),
        (r"\btu mismo\b", "directamente"),
        (r"\btú misma\b", "directamente"),
        (r"\btu misma\b", "directamente"),
        (r"\bpor ti mismo\b", "directamente"),
        (r"\bpor ti misma\b", "directamente"),
    )
    for pattern, repl in replacements:
        value = re.sub(pattern, repl, value, flags=re.I)
    return value.strip()


def _clean_ai_plain_text_format(answer: str) -> str:
    """Elimina Markdown decorativo porque las respuestas IA se envían como texto plano."""
    value = (answer or "").strip()
    if not value:
        return value
    # Bold/italic/code generados por el modelo: conservar contenido, quitar marcadores.
    value = re.sub(r"\*\*([^*\n]+?)\*\*", r"\1", value)
    value = re.sub(r"__([^_\n]+?)__", r"\1", value)
    value = re.sub(r"`([^`\n]+?)`", r"\1", value)
    value = re.sub(r"^\s*#{1,6}\s+", "", value, flags=re.MULTILINE)
    return value.strip()


def _time_management_style_guard(answer: str, question: str, lang: str = "es") -> str:
    """Guardia ligera para tiempo/organización sin reescribir la respuesta como plantilla.

    La redacción queda a cargo del modelo. Solo elimina franjas concretas inventadas
    cuando el usuario no dio ni pidió horarios específicos.
    """
    value = (answer or "").strip()
    if not value or lang != "es":
        return value
    q = _norm(question or "")
    time_topic = (
        any(x in q for x in ("horario", "horarios", "organizarme", "organizar", "rutina", "tiempo", "trabajo todo el dia", "solo tengo un rato"))
        and any(x in q for x in ("operar", "trading", "senal", "bot", "operaciones"))
    )
    if not time_topic:
        return value

    # Si la persona sí indicó o pidió una franja, respetamos la respuesta del modelo.
    explicit_time = any(x in q for x in (
        "primera hora", "manana", "madrugada", "mediodia", "almuerzo", "descanso",
        "tarde", "noche", "antes del trabajo", "despues del trabajo", "a. m.", "p. m.",
        "que hora", "que horario", "franja",
    )) or bool(re.search(r"\b(?:am|pm)\b|\b(?:[01]?\d|2[0-3])[:.]?[0-5]\d\b", q))
    if explicit_time:
        return value

    invented = (
        r"a primera hora|durante tu descanso|durante el descanso|en tu descanso|"
        r"en el almuerzo|durante el almuerzo|a la hora del almuerzo|por la noche|"
        r"antes de trabajar|despues del trabajo|después del trabajo"
    )
    value = re.sub(
        rf"(?:{invented})(?:\s*(?:,|o|y)\s*(?:{invented}))*",
        "cuando tengas disponibilidad",
        value,
        flags=re.I,
    )
    value = re.sub(r"(?:cuando tengas disponibilidad)(?:\s*(?:,|o|y)\s*cuando tengas disponibilidad)+", "cuando tengas disponibilidad", value, flags=re.I)
    return value.strip()

def _trim_generic_ai_closer(answer: str, lang: str = "es") -> str:
    """Quita solo cierres de atención al cliente que no aportan contenido."""
    value = (answer or "").strip()
    if not value:
        return value
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", value) if p.strip()]
    if len(paragraphs) <= 1:
        return value
    last = _norm(paragraphs[-1])
    if lang == "en":
        generic = (
            "if you have more questions", "if you need more", "i am here to help",
            "i'm here to help", "how would you like to proceed", "what would you like to do",
        )
    else:
        generic = (
            "si tienes mas preguntas", "si tienes alguna otra pregunta", "si necesitas mas",
            "si necesitas alguna", "estoy aqui para ayudarte", "estoy aqui para apoyarte",
            "como deseas proceder", "cómo deseas proceder", "que te gustaria hacer", "qué te gustaría hacer",
            "no dudes en escribirme",
        )
    if any(last.startswith(_norm(x)) for x in generic):
        paragraphs.pop()
    value = "\n\n".join(paragraphs).strip()
    # Elimina cierres evaluativos/genéricos que no añaden información nueva.
    # No toca contenido factual ni CTA útiles.
    if lang == "en":
        filler_endings = (
            r"Both options are (?:excellent|great|effective),? but (?:they|their dynamics?) (?:are|is) different[.!]?",
            r"I hope (?:this|that) helps[.!]?",
        )
    else:
        filler_endings = (
            r"Ambas opciones son (?:excelentes|muy buenas|buenas|efectivas),? pero (?:tienen enfoques diferentes|su dinámica es diferente|funcionan de forma diferente)[.!]?",
            r"Esto te ofrece (?:un )?(?:excelente|gran) (?:soporte|apoyo)(?: tanto para aprender como para operar)?[.!]?",
            r"¡?Espero que (?:esto|te) (?:te )?(?:sirva|ayude)!?[.!]?",
        )
    for pattern in filler_endings:
        value = re.sub(r"(?:\s*\n?\s*)" + pattern + r"\s*$", "", value, flags=re.I).strip()

    # Limpieza general de frases motivacionales/evaluativas que no añaden información.
    # Se eliminan aunque aparezcan en medio de una respuesta; no se reemplazan por una plantilla.
    if lang == "es":
        filler_sentences = (
            r"Esto te permite tener un control total[.!]?",
            r"¡?Te va a encantar aprender a tu ritmo!?[.!]?",
            r"Así podrás aprender y operar al mismo tiempo, aprovechando al máximo tu tiempo[.!]?",
            r"Aunque ambas (?:opciones|estrategias) (?:te ofrecen oportunidades para operar|son útiles|son efectivas),? (?:su dinámica|la dinámica|su forma de entrega) es diferente[.!]?",
        )
    else:
        filler_sentences = (
            r"This gives you total control[.!]?",
            r"You(?:'|’)ll love learning at your own pace[.!]?",
            r"This way you can learn and trade at the same time while making the most of your time[.!]?",
        )
    for pattern in filler_sentences:
        value = re.sub(r"(?:^|(?<=[.!?])\s+|\n+)" + pattern + r"(?=\s|$)", " ", value, flags=re.I).strip()
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _ai_known_fact_guard(answer: str, question: str, lang: str = "es") -> str:
    """Corrige contradicciones factuales muy concretas sin convertir la IA en plantilla."""
    value = (answer or "").strip()
    if not value:
        return value
    q = _norm(question or "")
    if lang == "es":
        value = re.sub(r"(?:más de\s*)?\+?300\s+señales\s+semanales", "+300 señales al día, de lunes a sábado", value, flags=re.I)
        value = re.sub(r"300\+?\s+señales\s+semanales", "+300 señales al día, de lunes a sábado", value, flags=re.I)

        # Nunca dejar "+300 señales" como cantidad ambigua: la cifra oficial es AL DÍA.
        pat_300 = re.compile(r"((?:más de\s*)?\+?300\s+señales)(?!\s+(?:al\s+d[ií]a|diarias?))", re.I)
        def _daily_300(m):
            tail = value[m.end():m.end()+45]
            if re.search(r"de\s+lunes\s+a\s+s[aá]bado", tail, re.I):
                return m.group(1) + " al día"
            return m.group(1) + " al día, de lunes a sábado"
        value = pat_300.sub(_daily_300, value)

        # Si el modelo ya dijo “al día/diarias” pero omitió los días, completa el dato oficial.
        value = re.sub(
            r"((?:más de\s*)?\+?300\s+señales\s+(?:al\s+d[ií]a|diarias?))(?![^.\n]{0,60}de\s+lunes\s+a\s+s[aá]bado)",
            r"\1, de lunes a sábado",
            value, flags=re.I,
        )
        # Si el mismo dato de disponibilidad ya apareció, elimina una frase posterior
        # que solo lo repita sin aportar nada nuevo.
        if len(re.findall(r"de\s+lunes\s+a\s+s[aá]bado", value, flags=re.I)) > 1:
            value = re.sub(
                r"\s*(?:Estas|Las)\s+señales\s+(?:est[aá]n\s+)?disponibles\s+de\s+lunes\s+a\s+s[aá]bado\.?",
                "", value, count=1, flags=re.I,
            ).strip()

        # Software Premium Anticipado: nunca describir la ENTREGA de sus señales como manual.
        # La entrada la realiza la persona, pero la lista de señales es anticipada y predeterminada.
        if any(x in q for x in ("senal", "senales", "software premium", "premium anticipado")):
            value = re.sub(
                r"(?:las\s+)?señales(?:\s+premium)?\s+(?:son|se\s+env[ií]an|son\s+enviadas)\s+manualmente",
                "las señales del Software Premium Anticipado son anticipadas y vienen predeterminadas con el minuto exacto de entrada",
                value, flags=re.I,
            )
            value = re.sub(
                r"enviadas\s+manualmente",
                "anticipadas y predeterminadas con el minuto exacto de entrada",
                value, flags=re.I,
            )
            # En comparaciones señal vs bot, evita que "manual" parezca describir la ENTREGA.
            # La operación la realiza la persona, pero la frase más clara es decir que toma la entrada en su cuenta.
            value = re.sub(
                r"(?:debes|tienes\s+que)\s+(?:ejecutar|realizar|tomar)(?:la|\s+la\s+entrada)?\s+manualmente\s+en\s+tu\s+cuenta",
                "la entrada la realizas en tu cuenta",
                value, flags=re.I,
            )
            value = re.sub(
                r"(?:la\s+entrada|cada\s+entrada)\s+(?:se\s+)?(?:ejecuta|realiza|toma)\s+manualmente\s+en\s+tu\s+cuenta",
                "la entrada la realizas en tu cuenta",
                value, flags=re.I,
            )

        # Guardia factual de formación: Premium termina en Binary Teams Módulo 4; Madness es solo Prestige.
        if "premium" in _norm(value):
            value = re.sub(
                r"(Premium[^.\n]{0,180}?)(?:todos\s+los\s+m[oó]dulos(?:\s+de\s+formaci[oó]n)?|todos\s+los\s+cursos|formaci[oó]n\s+completa)(?!\s+Binary\s+Teams\s+M[oó]dulos?\s+1)",
                lambda m: m.group(1) + "Binary Teams Módulos 1 al 4",
                value, flags=re.I
            )
        # Refuerzo para preguntas SOLO de Premium: aunque el modelo separe “Premium” y
        # “todos los módulos” en frases distintas, no puede dejar esa afirmación ambigua.
        if "premium" in q and "prestige" not in q:
            value = re.sub(
                r"\btodos\s+los\s+m[oó]dulos(?:\s+de\s+formaci[oó]n)?\b",
                "Binary Teams Módulos 1 al 4",
                value, flags=re.I,
            )
            value = re.sub(r"\btodos\s+los\s+cursos\b", "Binary Teams Módulos 1 al 4", value, flags=re.I)
            value = re.sub(r"\bformaci[oó]n\s+completa\b", "formación Binary Teams Módulos 1 al 4", value, flags=re.I)

        # Si la pregunta es amplia sobre QUÉ INCLUYE Premium, no omitir el material educativo de apoyo.
        premium_broad = "premium" in q and any(x in q for x in (
            "que incluye", "que recibo", "beneficios", "que trae", "incluye premium", "recibo con premium",
        ))
        if premium_broad and not re.search(
            r"(?:material de (?:estudio|apoyo)|pdf|audiolibro|plan de trading|gesti[oó]n de riesgo)",
            value, re.I
        ):
            value = value.rstrip() + (
                " También incluye material de estudio y apoyo, como PDFs, audiolibros y tablas de plan de trading y gestión de riesgo."
            )

        if "bot" in q or "automatic" in q or "automático" in q or "automatico" in q:
            value = re.sub(r"(?:está|esta) disponible para todos los miembros de mi comunidad", "está disponible desde el nivel Premium dentro de mi comunidad", value, flags=re.I)
            value = re.sub(r"(?:opera|operar|ejecuta|ejecutar) (?:las )?operaciones? automáticamente", "genera y envía alertas automáticamente; la persona realiza la entrada en su propia cuenta", value, flags=re.I)
            value = re.sub(
                r"(?:tú|tu)\s+decides\s+(?:cu[aá]ndo|cuando)\s+(?:realizar|tomar|hacer|ejecutar)\s+(?:la\s+)?entrada",
                "la entrada se toma al minuto siguiente de recibir la alerta", value, flags=re.I,
            )
    else:
        value = re.sub(r"300\+?\s+signals\s+(?:per\s+week|weekly)", "300+ signals per day, Monday to Saturday", value, flags=re.I)
        if "premium" in _norm(value):
            value = re.sub(
                r"(Premium[^.\n]{0,180}?)(?:all\s+(?:training\s+)?modules|all\s+courses|complete\s+training)(?!\s+Binary\s+Teams\s+Modules?\s+1)",
                lambda m: m.group(1) + "Binary Teams Modules 1–4",
                value, flags=re.I
            )
        if "bot" in q or "automatic" in q:
            value = re.sub(r"available to all members of my community", "available from the Premium level in my community", value, flags=re.I)
            value = re.sub(r"you decide when to (?:enter|take|place|execute) (?:the )?(?:entry|trade)", "you take the entry on the minute immediately after the alert", value, flags=re.I)
    return value.strip()


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
        runtime_context = _ai_runtime_context(chat_id, lang)
        dependency_context = _ai_dependency_context(question, chat_id, lang)
        history_text = _history_as_text(chat_id)
        q_norm = _norm(question or "")
        # v7.10.56: el nivel REAL persistido manda sobre cualquier monto escrito en chat.
        # Un miembro activo nunca puede ser "degradado" por una cifra hipotética o aislada.
        try:
            _active_vip_state = _vip_get_state(chat_id, create=False) or {}
            current_active_level = _active_vip_state.get("level") or VIP_LEVEL_NONE
        except Exception:
            current_active_level = VIP_LEVEL_NONE
        hypothetical_other_person = any(x in q_norm for x in (
            "si una persona", "si alguien", "para otra persona", "para alguien", "una persona nueva",
            "un usuario nuevo", "alguien nuevo", "otra persona", "if someone", "another person",
            "a new user", "new user",
        ))
        explicit_additional_deposit = any(x in q_norm for x in (
            "si deposito", "si depósito", "que pasa si deposito", "qué pasa si deposito",
            "deposito mas", "depósito más", "deposito otro", "depósito otro",
            "otro deposito", "otro depósito", "deposito adicional", "depósito adicional", "vuelvo a depositar",
            "quiero depositar", "voy a depositar", "los deposito", "lo deposito", "depositarlos", "depositarlo",
            "los meto", "los ingreso", "los pongo", "recargo", "recargar",
            "deposit more", "additional deposit", "another deposit", "top up", "should i deposit", "if i deposit",
        ))
        active_member = (stage == STAGE_DEPOSITED and current_active_level != VIP_LEVEL_NONE)

        # v7.10.55: ORQUESTADOR IA. Primero clasifica la intención semántica del
        # conjunto pendiente usando el modelo; después selecciona solo los bloques
        # factuales necesarios. Si el planificador falla, conserva heurísticas locales.
        planner = {}
        planner_intents = set()
        planner_primary = ""
        planner_amount = None
        try:
            planner_payload = {
                "model": OPENAI_MODEL,
                "instructions": (
                    "Classify the user's pending Telegram message(s) for a trading-community support workflow. "
                    "Return ONLY one compact JSON object, no markdown and no explanation. "
                    "Allowed intents: next_step, registration, level, broad_benefits, courses, signals, ai_bot, "
                    "live_panel, time_management, risk, existing_account, broker_upgrade, promo, live_schedule, "
                    "vip_access, personal_review, other. Use ALL intents that are actually asked. "
                    "Important semantics: phrases such as 'qué me toca', 'qué hago ahora', 'cómo sigo', 'por dónde empiezo' "
                    "normally mean next_step unless the user explicitly asks which level/benefits. "
                    "'the bot/program/software you show in your live/on screen' means live_panel unless CRYPTO IDX 24/7 "
                    "or the currency-pair AI bot is explicitly named. A money amount alone does NOT mean promo. "
                    "Set promo only when bonuses/promo codes/70%/100% are explicitly asked. "
                    "Schema: {\"primary\":\"intent\",\"intents\":[\"...\"],\"amount_usd\":number_or_null,"
                    "\"multi_question\":true_or_false}."
                ),
                "input": (
                    f"USER SELECTED LANGUAGE: {lang}\n"
                    f"BOT STAGE: {stage}\n"
                    f"REAL OPERATIONAL CONTEXT:\n{runtime_context}\n\n"
                    f"RECENT HISTORY (context only):\n{history_text[-3000:] if history_text else '(none)'}\n\n"
                    f"PENDING MESSAGE(S):\n{question.strip()}"
                ),
                "max_output_tokens": 180,
                "store": False,
            }
            async with httpx.AsyncClient(timeout=25) as client:
                planner_resp = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": "Bearer " + OPENAI_API_KEY, "Content-Type": "application/json"},
                    json=planner_payload,
                )
            if planner_resp.status_code == 200:
                planner_raw = _responses_api_text(planner_resp.json()).strip()
                planner_raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", planner_raw, flags=re.I | re.S).strip()
                match_json = re.search(r"\{.*\}", planner_raw, flags=re.S)
                if match_json:
                    parsed = json.loads(match_json.group(0))
                    if isinstance(parsed, dict):
                        planner = parsed
                        allowed = {
                            "next_step", "registration", "level", "broad_benefits", "courses", "signals",
                            "ai_bot", "live_panel", "time_management", "risk", "existing_account",
                            "broker_upgrade", "promo", "live_schedule", "vip_access", "personal_review", "other",
                        }
                        planner_intents = {str(x).strip() for x in (planner.get("intents") or []) if str(x).strip() in allowed}
                        planner_primary = str(planner.get("primary") or "").strip()
                        if planner_primary not in allowed:
                            planner_primary = ""
                        raw_amount = planner.get("amount_usd")
                        if isinstance(raw_amount, (int, float)) and 0 < float(raw_amount) <= 1000000:
                            planner_amount = float(raw_amount)
            else:
                logging.info("Planificador IA devolvió %s; uso heurísticas locales", planner_resp.status_code)
        except Exception as e:
            logging.info("Planificador IA no disponible; uso heurísticas locales: %s", e)

        explicit_bonus = _is_bonus_or_promo_mention(question)
        explicit_level_question = any(x in q_norm for x in (
            "que nivel", "qué nivel", "cual nivel", "cuál nivel", "nivel me toca", "nivel tendria",
            "nivel tendría", "que incluye", "qué incluye", "beneficios", "what level", "which level", "what is included",
        ))
        next_step_heuristic = (
            any(x in q_norm for x in (
                "que me toca", "qué me toca", "que hago ahora", "qué hago ahora", "que hago", "qué hago",
                "como sigo", "cómo sigo", "por donde empiezo", "por dónde empiezo", "como empiezo", "cómo empiezo",
                "what do i do", "what next", "what's next", "how do i continue", "how do i start",
            ))
            and not explicit_level_question
        )
        if next_step_heuristic:
            planner_intents.add("next_step")
            if not planner_primary or planner_primary == "other":
                planner_primary = "next_step"

        live_reference = any(x in q_norm for x in ("live", "en vivo", "en vivos", "transmision", "directo", "on live", "livestream"))
        shown_tool_reference = any(x in q_norm for x in (
            "que muestras", "que usas", "que utilizas", "que se ve", "que aparece", "muestras en los",
            "en tu pantalla", "de tu pantalla", "you show", "you use", "on your screen",
        ))
        tool_reference = any(x in q_norm for x in ("bot", "software", "programa", "sistema", "herramienta", "panel", "interfaz", "pantalla", "tool"))
        explicit_member_ai_bot = any(x in q_norm for x in (
            "bot ia", "ia crypto", "crypto idx 24/7", "bot de crypto", "bot crypto", "bot de pares",
            "pares de divisas 24/7", "ai bot", "currency pair bot", "automatic alert", "alerta automatica",
        ))
        panel_topic = ("live_panel" in planner_intents) or (live_reference and shown_tool_reference and tool_reference and not explicit_member_ai_bot)
        if panel_topic:
            planner_intents.add("live_panel")
            if planner_primary in ("", "other", "ai_bot") and not explicit_member_ai_bot:
                planner_primary = "live_panel"

        strong_current_topic = bool(planner_intents - {"other"}) or any(x in q_norm for x in (
            "premium", "prestige", "basico", "nivel", "curso", "modulo", "formacion", "senal",
            "bot", "crypto idx", "registro", "deposito", "bono", "live", "panel", "interfaz",
            "riesgo", "organizar", "poco tiempo", "cuenta antigua", "acceso vip",
        ))
        ambiguous_followup = (not strong_current_topic) and len(q_norm.split()) <= 14 and any(x in q_norm for x in (
            "eso", "ese ", "esa ", "esos ", "esas ", "y como", "y que", "y cuando",
            "como funciona", "como se usa", "y el ", "y la ", "tambien", "entonces",
        ))
        scope_norm = q_norm + ("\n" + _norm(history_text[-3000:]) if ambiguous_followup and history_text else "")
        multi_pending = bool(planner.get("multi_question")) or question.count("?") >= 2 or len([x for x in re.split(r"[\n\r]+", question or "") if x.strip()]) >= 2

        amount_usd = planner_amount
        if amount_usd is None:
            amount_match = re.search(
                r"(?:\$\s*)?(\d{2,5}(?:[.,]\d{1,2})?)(?:\s*(?:usd|dolares|dólares))?",
                q_norm,
            )
            if amount_match and (explicit_level_question or next_step_heuristic or any(x in q_norm for x in ("tengo ", "cuento con", "dispongo de", "deposit"))):
                try:
                    amount_usd = float(amount_match.group(1).replace(",", "."))
                except Exception:
                    amount_usd = None

        knowledge_text = (JOHA_KNOWLEDGE or "").strip()
        section_titles = [
            "IDENTIDAD Y PRINCIPIO DE RESPUESTA",
            "REGISTRO Y ACCESO",
            "NIVELES DENTRO DE JT TRADERS TEAMS",
            "FORMACIÓN / CURSOS",
            "SEÑALES Y SOFTWARE PREMIUM ANTICIPADO",
            "BOTS IA 24/7",
            "PANEL / INTERFAZ QUE JOHANNA MUESTRA EN LIVE",
            "GESTIÓN DE RIESGO Y MARTINGALA — METODOLOGÍA DE JOHANNA",
            "TIEMPO / HORARIOS / PERSONAS QUE TRABAJAN TODO EL DÍA",
            "CUENTAS EXISTENTES / ANTIGUAS",
            "BROKERS Y UPGRADES",
            "BONOS — REGLAS GENERALES",
            "PREGUNTAS MÚLTIPLES Y DEPENDENCIAS",
            "LIVES",
            "ACCESOS VIP EN TELEGRAM",
            "CASOS QUE SIEMPRE VAN A JOHANNA",
            "LÍMITES",
        ]
        heading_pattern = r"(?m)^(" + "|".join(re.escape(x) for x in section_titles) + r")\s*$"
        heading_matches = list(re.finditer(heading_pattern, knowledge_text))
        knowledge_sections = {}
        for idx, match in enumerate(heading_matches):
            stop = heading_matches[idx + 1].start() if idx + 1 < len(heading_matches) else len(knowledge_text)
            knowledge_sections[match.group(1)] = knowledge_text[match.start():stop].strip()

        broad_benefits = ("broad_benefits" in planner_intents) or any(x in q_norm for x in (
            "que incluye", "qué incluye", "que recibo", "qué recibo", "que trae", "beneficios",
            "todo lo que incluye", "que ofrece", "what is included", "what do i get",
        ))
        next_step_topic = "next_step" in planner_intents or next_step_heuristic
        level_topic = "level" in planner_intents or broad_benefits or explicit_level_question
        course_topic = "courses" in planner_intents or any(x in scope_norm for x in (
            "curso", "cursos", "formacion", "modulo", "binary teams", "madness", "smart money", "algo & lit", "audiolibro", "material de estudio",
        ))
        signal_topic = "signals" in planner_intents or any(x in scope_norm for x in ("senal", "senales", "software premium", "premium anticipado", "lista de senales", "signals"))
        bot_topic = ("ai_bot" in planner_intents or explicit_member_ai_bot) and not (panel_topic and not explicit_member_ai_bot)
        time_topic = "time_management" in planner_intents or any(x in scope_norm for x in (
            "organizarme", "organizar", "poco tiempo", "solo tengo un rato", "trabajo todo el dia", "cuanto tiempo", "rutina para operar", "2 horas", "dos horas",
        ))
        risk_topic = "risk" in planner_intents or any(x in scope_norm for x in ("gestion de riesgo", "martingala", "mg1", "mg2", "sobreoper", "cuantas operaciones"))
        account_topic = "existing_account" in planner_intents or any(x in scope_norm for x in ("cuenta antigua", "cuenta vieja", "ya tengo cuenta", "cuenta existente", "vinculada", "registrada contigo"))
        registration_topic = "registration" in planner_intents or account_topic or next_step_topic or any(x in scope_norm for x in ("registrarme", "registro", "enlace", "validar id", "id de binomo", "id de stockity"))
        broker_topic = "broker_upgrade" in planner_intents or any(x in scope_norm for x in ("upgrade", "subir de nivel", "otro deposito", "depositos acumul", "mismo broker", "dos brokers"))
        bonus_topic = bool(explicit_bonus)
        live_topic = "live_schedule" in planner_intents or any(x in q_norm for x in ("horario del live", "cuando es el live", "a que hora el live", "tiktok live", "youtube live"))
        vip_access_topic = "vip_access" in planner_intents or any(x in scope_norm for x in ("acceso vip", "canal vip", "demasiados intentos", "solicitud de acceso", "entrar al canal"))
        personal_topic = "personal_review" in planner_intents or any(x in scope_norm for x in ("gestion de cuenta", "gestion de capital", "plataforma no disponible", "restriccion por pais"))

        selected_titles = {"IDENTIDAD Y PRINCIPIO DE RESPUESTA", "LÍMITES"}
        if registration_topic:
            selected_titles.add("REGISTRO Y ACCESO")
        if level_topic or (next_step_topic and amount_usd is not None):
            selected_titles.add("NIVELES DENTRO DE JT TRADERS TEAMS")
        if course_topic or broad_benefits:
            selected_titles.add("FORMACIÓN / CURSOS")
        if signal_topic or broad_benefits:
            selected_titles.add("SEÑALES Y SOFTWARE PREMIUM ANTICIPADO")
        if bot_topic or broad_benefits or (
            signal_topic and (
                any(x in q_norm for x in ("premium", "prestige", "nivel"))
                or (active_member and current_active_level in (VIP_LEVEL_PREMIUM, VIP_LEVEL_PRESTIGE))
            )
        ):
            selected_titles.add("BOTS IA 24/7")
        if panel_topic:
            selected_titles.add("PANEL / INTERFAZ QUE JOHANNA MUESTRA EN LIVE")
        if time_topic:
            selected_titles.add("TIEMPO / HORARIOS / PERSONAS QUE TRABAJAN TODO EL DÍA")
            selected_titles.add("GESTIÓN DE RIESGO Y MARTINGALA — METODOLOGÍA DE JOHANNA")
        elif risk_topic:
            selected_titles.add("GESTIÓN DE RIESGO Y MARTINGALA — METODOLOGÍA DE JOHANNA")
        if account_topic:
            selected_titles.add("CUENTAS EXISTENTES / ANTIGUAS")
        if broker_topic:
            selected_titles.add("BROKERS Y UPGRADES")
        if bonus_topic:
            selected_titles.add("BONOS — REGLAS GENERALES")
        if live_topic:
            selected_titles.add("LIVES")
        if vip_access_topic:
            selected_titles.add("ACCESOS VIP EN TELEGRAM")
        if personal_topic:
            selected_titles.add("CASOS QUE SIEMPRE VAN A JOHANNA")
        if multi_pending:
            selected_titles.add("PREGUNTAS MÚLTIPLES Y DEPENDENCIAS")
        if broad_benefits:
            selected_titles.update({
                "NIVELES DENTRO DE JT TRADERS TEAMS", "FORMACIÓN / CURSOS",
                "SEÑALES Y SOFTWARE PREMIUM ANTICIPADO", "BOTS IA 24/7",
            })

        if knowledge_sections:
            ordered_sections = [knowledge_sections[t] for t in section_titles if t in selected_titles and t in knowledge_sections]
            scoped_knowledge = "\n\n".join(ordered_sections).strip()
        else:
            scoped_knowledge = knowledge_text
        if not scoped_knowledge:
            scoped_knowledge = knowledge_text

        real_examples = _johanna_examples_as_text(question=question, limit=4, lang=lang)
        if next_step_topic or panel_topic or (not bonus_topic and _is_bonus_or_promo_mention(real_examples)):
            real_examples = ""

        allow_optional_recurring_bonus_offer = bool(
            active_member and current_active_level == VIP_LEVEL_PRESTIGE and explicit_additional_deposit
        )
        promo_context = _promo_ai_context(lang) if bonus_topic else ""
        if bonus_topic:
            promo_context_for_prompt = promo_context
        elif allow_optional_recurring_bonus_offer:
            promo_context_for_prompt = (
                "(PROMO DETAILS NOT REQUESTED: do not give a code or conditions. You may only offer to explain the current recurring-deposit bonus if the user wants it.)"
                if lang == "en" else
                "(DETALLES PROMO NO SOLICITADOS: no entregues código ni condiciones. Solo puedes ofrecer explicar el bono vigente para depósitos posteriores si la persona lo desea.)"
            )
        else:
            promo_context_for_prompt = (
                "(PROMOTIONS BLOCKED: not requested in the pending message)" if lang == "en"
                else "(PROMOCIONES BLOQUEADAS: no fueron solicitadas en el mensaje pendiente)"
            )

        decision_lines = []
        if not bonus_topic and not allow_optional_recurring_bonus_offer:
            decision_lines.append(
                "PROMO ISOLATION: Do not mention bonuses, promo codes, 70%, 100%, turnover or bonus withdrawal conditions."
                if lang == "en" else
                "AISLAMIENTO PROMO: no menciones bonos, códigos, 70%, 100%, volumen ni condiciones de retiro por bono."
            )
        elif allow_optional_recurring_bonus_offer and not bonus_topic:
            decision_lines.append(
                "OPTIONAL RECURRING BONUS: because an active Prestige member is explicitly asking about an additional deposit, you may briefly offer to explain the current recurring-deposit bonus. Do NOT provide a code or bonus conditions unless asked."
                if lang == "en" else
                "BONO RECURRENTE OPCIONAL: como un miembro Prestige activo pregunta explícitamente por un depósito adicional, puedes ofrecer brevemente explicar el bono vigente para depósitos posteriores. NO entregues código ni condiciones del bono salvo que lo pidan."
            )
        if active_member and not hypothetical_other_person:
            decision_lines.append(
                (f"AUTHORITATIVE ACTIVE STATUS: this is an ACTIVE member. Their real JT TRADERS TEAMS level is {_vip_level_label(current_active_level, lang)}. This persisted status overrides isolated amounts and generic examples. Never answer as if they were unregistered/new, never recalculate their CURRENT level from a number written in chat, and when they ask about their own signals/bots/courses/benefits, describe only what their CURRENT level actually includes unless they explicitly request a comparison.")
                if lang == "en" else
                (f"ESTADO ACTIVO AUTORITATIVO: esta persona es un miembro ACTIVO. Su nivel real en JT TRADERS TEAMS es {_vip_level_label(current_active_level, lang)}. Este estado persistido manda sobre montos aislados y ejemplos genéricos. Nunca respondas como si no estuviera registrada o fuera nueva, nunca recalcules su nivel ACTUAL desde una cifra escrita en el chat y, cuando pregunte por sus propias señales/bots/cursos/beneficios, describe únicamente lo que realmente incluye su nivel ACTUAL salvo que pida explícitamente una comparación.")
            )
        try:
            pending_id_review = _has_pending_id_review(chat_id)
        except Exception:
            pending_id_review = False

        prospective_level = VIP_LEVEL_NONE
        if amount_usd is not None:
            try:
                prospective_level = _vip_level_for_total_cents(int(round(float(amount_usd) * 100)))
            except Exception:
                prospective_level = VIP_LEVEL_NONE

        if next_step_topic:
            if pending_id_review:
                decision_lines.append(
                    "NEXT STEP: the user already sent an ID and it is pending validation. Do not ask them to register/send another ID and do not tell them to deposit yet; briefly say validation must finish first."
                    if lang == "en" else
                    "SIGUIENTE PASO: el usuario ya envió un ID y está pendiente de validación. No pidas otro registro/ID ni indiques depositar todavía; explica brevemente que primero debe terminar la validación."
                )
            elif stage == STAGE_PRE:
                decision_lines.append(
                    "NEXT STEP (PRE): first explain that they must register a Binomo or Stockity account with my official link and send the ID for validation BEFORE depositing. Include the official registration links because they are the necessary next action, unless the message says they are already registered with me. If they already have an account registered with me, ask them to send that ID for validation. Do not jump to deposit."
                    if lang == "en" else
                    "SIGUIENTE PASO (PRE): primero explica que debe registrar una cuenta de Binomo o Stockity con mi enlace oficial y enviarme el ID para validarlo ANTES de depositar. Incluye los enlaces oficiales porque son la acción necesaria, salvo que el mensaje diga que ya tiene una cuenta registrada conmigo. Si ya tiene una cuenta registrada conmigo, debe enviarme ese ID para validarlo. No saltes al depósito."
                )
            elif stage == STAGE_POST:
                decision_lines.append(
                    "NEXT STEP (POST): the ID is already validated. Do not repeat registration or ask for the ID again. The next step is the deposit in that validated account and then sending the proof here."
                    if lang == "en" else
                    "SIGUIENTE PASO (POST): el ID ya está validado. No repitas registro ni pidas el ID otra vez. El siguiente paso es realizar el depósito en esa cuenta validada y luego enviar aquí el comprobante."
                )
            elif stage == STAGE_DEPOSITED:
                if current_active_level != VIP_LEVEL_NONE:
                    decision_lines.append(
                        (f"NEXT STEP (ACTIVE MEMBER): the account is already active at {_vip_level_label(current_active_level, lang)}. Never restart registration/ID and never answer from a new-user flow. If the message explicitly means an additional deposit, keep the current level as authoritative until that deposit is validated; only then can upgrade rules change it. Prestige is already the maximum level.")
                        if lang == "en" else
                        (f"SIGUIENTE PASO (MIEMBRO ACTIVO): la cuenta ya está activa en {_vip_level_label(current_active_level, lang)}. Nunca reinicies registro/ID ni respondas desde un flujo de persona nueva. Si el mensaje habla explícitamente de un depósito adicional, conserva el nivel actual como autoritativo hasta que ese depósito sea validado; solo después pueden aplicar las reglas de upgrade. Prestige ya es el nivel máximo.")
                    )
                else:
                    decision_lines.append(
                        "NEXT STEP (DEPOSITED): the account is already active. Never restart registration/ID. Continue from the real access state and do not infer a new level from an isolated amount."
                        if lang == "en" else
                        "SIGUIENTE PASO (DEPOSITED): la cuenta ya está activa. Nunca reinicies registro/ID. Continúa desde el estado real de accesos y no infieras un nivel nuevo desde un monto aislado."
                    )
            if amount_usd is not None and prospective_level != VIP_LEVEL_NONE:
                if active_member and not hypothetical_other_person:
                    # Estado real > monto aislado. Para miembros activos el monto NO redefine el nivel.
                    if current_active_level == VIP_LEVEL_PRESTIGE:
                        decision_lines.append(
                            (f"ACTIVE PRESTIGE OVERRIDE: this user is already Prestige, the highest JT TRADERS TEAMS level. USD {amount_usd:g} must NOT relabel them as Basic/Premium. If it is an additional deposit, the level remains Prestige and no new tools are unlocked; the funds remain in their own trading account for their trading. You may briefly offer the currently available recurring-deposit bonus only as an optional follow-up, without giving a code unless asked.")
                            if lang == "en" else
                            (f"PRIORIDAD PRESTIGE ACTIVO: esta persona YA es Prestige, el nivel más alto de JT TRADERS TEAMS. USD {amount_usd:g} NO puede reclasificarla como Básico/Premium. Si es un depósito adicional, se mantiene Prestige y no desbloquea herramientas nuevas; el capital queda en su propia cuenta para su operativa. Puedes ofrecer brevemente revisar el bono vigente para depósitos posteriores como opción, sin dar código salvo que lo pida.")
                        )
                    else:
                        decision_lines.append(
                            (f"ACTIVE LEVEL OVERRIDE: the user's real active level is {_vip_level_label(current_active_level, lang)}. USD {amount_usd:g} does NOT change that level just because it was mentioned in chat. If it is an additional deposit, any upgrade only exists after the deposit is actually validated under the upgrade rules.")
                            if lang == "en" else
                            (f"PRIORIDAD DE NIVEL ACTIVO: el nivel real actual es {_vip_level_label(current_active_level, lang)}. USD {amount_usd:g} NO cambia ese nivel solo por mencionarlo en el chat. Si es un depósito adicional, cualquier upgrade existe únicamente después de validar realmente el depósito según las reglas de upgrade.")
                        )
                else:
                    decision_lines.append(
                        f"AMOUNT CONTEXT: USD {amount_usd:g} would correspond to {_vip_level_label(prospective_level, lang)} inside JT TRADERS TEAMS. Mention that briefly AFTER the next-step instruction, without listing all benefits; the levels button can show details."
                        if lang == "en" else
                        f"CONTEXTO DE MONTO: USD {amount_usd:g} correspondería a {_vip_level_label(prospective_level, lang)} dentro de JT TRADERS TEAMS. Menciónalo brevemente DESPUÉS del siguiente paso, sin listar todos los beneficios; el botón de niveles muestra los detalles."
                    )
        elif explicit_level_question and amount_usd is not None and prospective_level != VIP_LEVEL_NONE:
            if active_member and not hypothetical_other_person:
                decision_lines.append(
                    (f"ACTIVE LEVEL QUESTION: the user's REAL current level is {_vip_level_label(current_active_level, lang)}. Do not replace it with the level that USD {amount_usd:g} would give a new user. Mention the hypothetical amount-level mapping only if the wording clearly asks a hypothetical comparison.")
                    if lang == "en" else
                    (f"CONSULTA CON NIVEL ACTIVO: el nivel REAL actual de esta persona es {_vip_level_label(current_active_level, lang)}. No lo reemplaces por el nivel que USD {amount_usd:g} daría a una persona nueva. Solo menciona la equivalencia hipotética monto→nivel si la redacción pide claramente una comparación hipotética.")
                )
            else:
                decision_lines.append(
                    f"LEVEL QUESTION: USD {amount_usd:g} corresponds to {_vip_level_label(prospective_level, lang)}. Answer the level asked; do not turn it into promo advice."
                    if lang == "en" else
                    f"CONSULTA DE NIVEL: USD {amount_usd:g} corresponde a {_vip_level_label(prospective_level, lang)}. Responde el nivel solicitado; no lo conviertas en recomendación de bonos."
                )
        if panel_topic and not explicit_member_ai_bot:
            decision_lines.append(
                "LIVE PANEL: the tool shown on screen in lives is Johanna's private internal interface. It is not delivered at any level. Members receive the corresponding same operational signals through Telegram for easier use on any device."
                if lang == "en" else
                "PANEL DEL LIVE: la herramienta que se muestra en pantalla en los lives es la interfaz privada de uso interno de Johanna. No se entrega en ningún nivel. Los miembros reciben por Telegram las señales operativas correspondientes para usarlas con facilidad desde cualquier dispositivo."
            )
        if bot_topic:
            decision_lines.append(
                "AI BOT FACT: alerts are generated automatically 24/7, but the bot does not operate the user's account. The entry time is NOT freely chosen: take the entry on the minute immediately after the alert."
                if lang == "en" else
                "HECHO BOT IA: las alertas se generan automáticamente 24/7, pero el bot no opera la cuenta. El momento de entrada NO se elige libremente: la entrada se toma al minuto siguiente de recibir la alerta."
            )
        if multi_pending:
            decision_lines.append(
                "MULTI-QUESTION: answer every pending question once, in arrival order, using short paragraphs and proportional depth. Do not let one topic contaminate another."
                if lang == "en" else
                "MULTIPREGUNTA: responde cada duda pendiente una sola vez, en el orden de llegada, con párrafos cortos y profundidad proporcional. No dejes que un tema contamine otro."
            )
        decision_context = "\n".join(f"- {x}" for x in decision_lines) or ("- No special decision constraint." if lang == "en" else "- Sin restricción especial adicional.")

        already_answered = already_answered or []
        answered_note = ", ".join(already_answered) if already_answered else ("none" if lang == "en" else "ninguno")
        system = f"""
{language_instruction}

Eres la voz digital de Johanna, conocida como JOHAALETRADER / JT TRADERS TEAMS.
Habla SIEMPRE en primera persona como Johanna. Nunca digas que eres una IA, un bot de atención o un asistente virtual.

OBJETIVO PRINCIPAL
- Conversa de forma humana, natural y contextual. NO respondas como una FAQ rígida ni copies la base de conocimiento como plantilla.
- La base oficial contiene HECHOS que debes comprender y aplicar según la pregunta; redacta libremente con palabras naturales.
- NO completes huecos con suposiciones sobre cómo funciona una herramienta. Si la base no dice que algo es manual, automático, instantáneo, personalizado, etc., no lo inventes. Distingue siempre entre CÓMO SE GENERA/ENTREGA una señal y CÓMO la persona ejecuta la entrada.
- RESPUESTA MÍNIMA SUFICIENTE: contesta exactamente lo que preguntaron y termina. No anticipes preguntas futuras ni descargues todo lo que sabes del tema.
- ECONOMÍA DE LENGUAJE: cada dato factual debe aparecer UNA sola vez por respuesta salvo que repetirlo sea indispensable para resolver otra pregunta distinta. Si ya dijiste "lunes a sábado", "24/7", un monto, un nivel o un requisito, no vuelvas a reformular el mismo dato en la frase siguiente.
- Evita preámbulos que solo repiten la pregunta (por ejemplo, "la diferencia principal radica en...") cuando puedes ir directamente a la diferencia. Evita también frases de relleno/evaluación sin información nueva como "ambas opciones son excelentes", "esto te ofrece muchas oportunidades", "esto te permite tener un control total", "te va a encantar", "aprovechando al máximo tu tiempo", "es una gran opción" o equivalentes.
- En comparaciones, explica directamente la diferencia concreta entre A y B en uno o dos bloques breves; no añadas una conclusión genérica si la comparación ya quedó clara.
- ORDEN DE DECISIÓN OBLIGATORIO: 1) ESTADO OPERATIVO REAL del usuario, 2) intención completa del mensaje pendiente, 3) conocimiento oficial relevante. No inviertas ese orden. El estado determina qué paso corresponde; las palabras sueltas no.
- FILTRO DE RELEVANCIA: antes de redactar, separa cada duda pendiente, identifica su categoría (formación/cursos, señales, bots, niveles, registro, depósito, promos, horarios, acceso, etc.) y responde SOLO con los hechos necesarios para ESA duda.
- HISTORIAL NO AUTORITATIVO: una respuesta IA/BOT anterior puede estar equivocada o pertenecer a otro tema. Úsala solo para continuidad conversacional; los hechos válidos vienen del ESTADO OPERATIVO REAL + CONOCIMIENTO OFICIAL + PRIORIDAD DE DECISIÓN ACTUAL.
- PROMOCIONES CERRADAS POR DEFECTO: si la prioridad actual dice que promociones están bloqueadas, está PROHIBIDO mencionar bonos, códigos, 70%, 100%, volumen o retiro por bono aunque aparezcan en el historial o en ejemplos antiguos.
- El bloque de conocimiento que recibes ya está filtrado por temas relevantes. NO tienes que mencionar todo lo que aparece allí: úsalo como referencia factual, no como checklist.
- PRINCIPIO DE MISMA CATEGORÍA: si preguntan por un curso, responde sobre cursos; si preguntan por señales, responde sobre las fuentes de señales; si preguntan por bots, responde sobre bots. Solo cruza categorías cuando sea necesario para contestar correctamente o cuando la pregunta sea amplia sobre beneficios/qué incluye.
- REFERENCIAS A LO QUE SE VE EN LIVE: si el usuario habla del “bot/software/programa/herramienta que muestras o usas en vivo”, resuelve primero esa referencia como la interfaz visual privada del live. NO la conviertas en un bot de Premium/Prestige ni en un beneficio por nivel, salvo que la persona nombre explícitamente CRYPTO IDX 24/7 o el bot de pares de divisas 24/7.
- Un monto o el nombre “Premium/Prestige/Básico” NO significa automáticamente “dime todos los beneficios”. Si la pregunta es específica, el nivel/monto solo sirve para ubicar la respuesta.
- “Qué me toca / qué hago / cómo sigo / por dónde empiezo” es lenguaje de FLUJO/SIGUIENTE PASO salvo que el mensaje diga explícitamente “qué nivel” o pregunte beneficios. Responde desde el estado operativo y luego, si hay monto, menciona el nivel de forma breve. No conviertas esto en una plantilla: redacta natural según la conversación.
- MIEMBROS ACTIVOS: si ESTADO OPERATIVO REAL indica DEPOSITED + Básico/Premium/Prestige, ese nivel es la verdad actual. Nunca lo reemplaces por el nivel teórico de un monto mencionado. Responde sobre SUS herramientas/beneficios desde ese nivel; un depósito adicional solo puede cambiar el nivel después de validarse según las reglas de upgrade. Si ya es Prestige, no existe un nivel superior.
- CTA DE NIVELES: para un miembro activo no sugieras “MIRA LOS NIVELES” por preguntar por sus propias señales, bots, cursos, interfaz del live o un depósito adicional. Ese CTA solo aporta si pide explícitamente ver/comparar niveles o plantea el caso de otra persona.
- Pregunta simple: normalmente 1–3 frases y preferiblemente 20–55 palabras. NO conviertas una duda sencilla en una lista de 4–5 puntos. Si el usuario no pidió pasos/lista/guía, NO numeres la respuesta.
- Responde en TEXTO PLANO: no uses Markdown decorativo (**negritas**, __subrayados__, títulos con # ni `código`) porque Telegram mostrará esos símbolos literalmente en este flujo.
- Si una explicación necesita más detalle porque el usuario lo pidió, puedes ampliarla.

CONTINUIDAD Y COMPRENSIÓN
- Lee el historial reciente y el MENSAJE PENDIENTE como una conversación real.
- Entiende errores ortográficos fuertes, abreviaciones, palabras recortadas y frases sin signos de interrogación usando contexto; no corrijas al usuario ni te burles.
- Si el usuario dice "eso", "ese nivel", "y qué recibo", "entonces", etc., resuelve la referencia con el contexto reciente SOLO cuando sea clara.
- Si la referencia es ambigua, haz una sola pregunta breve de aclaración; no inventes.
- No vuelvas a saludar con "Hola" en cada turno. Saluda solo si el usuario saluda o si realmente es el primer intercambio.
- NO asumas género, aunque el nombre parezca masculino o femenino. Evita "nuevo/nueva", "enfocado/enfocada", "atento/atenta", "listo/lista" y equivalentes dirigidos al usuario. Reformula de manera neutra: "si estás empezando", "mantener tu enfoque", "presta atención", "cuando quieras continuar".
- TUTEA SIEMPRE: usa tú/te/tu/tus. No trates al usuario de “usted” ni uses “su/sus” para dirigirte directamente a esa persona. Si el nombre visible aparece en el contexto, puedes usarlo ocasionalmente cuando quede natural, no en cada respuesta.
- No cierres por costumbre con "si tienes más preguntas", "estoy aquí para ayudarte", "¿cómo deseas proceder?" u otros cierres genéricos. Úsalos solo si aportan algo real.
- Usa emojis con moderación. Si el usuario manda solo emojis/reacciones, responde como máximo con una reacción breve y no inventes emociones o intención de compra.

VARIAS PREGUNTAS / MENSAJES SEGUIDOS
- Lee el conjunto completo antes de responder. El usuario puede enviar 2, 3, 4 o más mensajes durante la espera de 4 minutos.
- Separa mentalmente cada pregunta o intención y respóndelas TODAS en el mismo mensaje, en el mismo orden en que llegaron. Si son temas distintos, usa párrafos cortos separados; no hace falta numerarlos salvo que ayude de verdad.
- No des a cada subpregunta la misma profundidad por obligación. En una consulta múltiple, responde cada parte con la MÍNIMA profundidad necesaria: una parte secundaria puede resolverse en una sola frase si con eso queda contestada.
- No mezcles datos de una pregunta dentro de otra: por ejemplo, una duda sobre un curso no necesita señales; una duda de organización puede mencionar las señales como apoyo sin recitar cantidades o beneficios que no fueron preguntados.
- Responde todas las dudas pendientes, pero identifica primero si una depende de otra.
- Si una respuesta depende de un dato todavía no validado, NO asumas ese dato. Resuelve primero el requisito pendiente y después responde lo que sí pueda contestarse sin inventar.
- Temas ya atendidos automáticamente antes de llamarte: {answered_note}. No los repitas salvo una referencia mínima necesaria.
- Responde únicamente a lo que aparece en MENSAJE(S) PENDIENTE(S); el historial sirve para contexto, no para reabrir preguntas ya contestadas.
- CAMBIO DE TEMA: si el pendiente empieza un caso hipotético o habla explícitamente de "otra persona / una persona / alguien", no arrastres al nuevo tema un ID, depósito o cuenta personal pendiente de una conversación anterior.

ESTILO Y CTA
- Cercano, positivo, motivador, persuasivo y directo, sin exageraciones ni promesas engañosas. La naturalidad sale de adaptar el lenguaje a la conversación, no de añadir frases motivacionales de relleno.
- Antes de cerrar la respuesta, revisa mentalmente cada oración: si repite una idea ya dicha, solo parafrasea la pregunta o no aporta un hecho/acción útil, elimínala.
- No uses listas largas para una duda simple. Si el usuario NO pidió "pasos", "lista" o "guía", responde en prosa breve y NO uses numeración; usa lista solo si realmente la pidió o es imprescindible para claridad.
- Para dudas sobre falta de tiempo/organización/horarios de trading, tienes como referencia de fondo: al menos dos sesiones de unos 40 minutos, alrededor de 5 operaciones bien seleccionadas por sesión, apoyo en las señales disponibles, plan de trading y gestión de riesgo. Son DATOS DISPONIBLES, no una receta que debas recitar. Si organización es una subpregunta dentro de varias, resuélvela normalmente en UNA frase breve; usa cifras concretas de tiempo/operaciones solo si la consulta está centrada en la organización o pide esos detalles. No inventes momentos del día si el usuario no los dio.
- No repitas enlaces/CTA si ya se enviaron recientemente. Muestra registro, niveles u otro CTA solo cuando el usuario lo pida o sea el siguiente paso realmente necesario.
- Si preguntan por un monto concreto para saber el nivel o qué incluye de forma amplia, responde el nivel concreto y un resumen útil. Si el monto acompaña una pregunta específica sobre un curso/señal/bot, responde solo ese ámbito; no recites beneficios ajenos.
- El nivel SIEMPRE es "dentro de mi comunidad JT TRADERS TEAMS", nunca nivel del broker.
- Los ejemplos reales de Johanna sirven SOLO para tono y ritmo. No copies su estructura ni los uses como lista de contenido. La BASE OFICIAL y el ESTADO OPERATIVO mandan sobre ejemplos antiguos.

CONTEXTO HUMANO Y OPERATIVO
- Si el historial muestra "JOHANNA (RESPUESTA PERSONAL REAL)", esa respuesta proviene realmente de Johanna por texto o audio transcrito. Continúa desde lo ya acordado y no reinicies la conversación.
- PRE/POST/DEPOSITED, nivel y depósitos guardados son contexto operativo, NO sustituyen lo que se dijo. No afirmes que alguien acaba de registrarse/depositar solo por el stage.
- Si el estado dice POST, no vuelvas a pedir un ID ya procesado. Si dice DEPOSITED, no reinicies registro/ID/primer depósito.
- Nunca confirmes por tu cuenta que un ID, depósito, afiliación o acceso quedó validado.

TEMAS PERSONALES / ESCALAMIENTO
- Gestión de capital o gestión de cuenta: comienza EXACTAMENTE con [[PERSONAL_CHAT]] y deriva a mi chat personal; no expliques modalidades por iniciativa propia.
- Problemas de disponibilidad/restricción por país o plataforma no disponible para registrarse: comienza EXACTAMENTE con [[PERSONAL_CHAT]] y deriva a mi chat personal. No expliques métodos para alterar ubicación ni repitas términos técnicos del usuario.
- Casos extraordinarios de una cuenta específica que requieren revisar su estado real: [[PERSONAL_CHAT]].
- No prometas que voy a gestionar, crear, operar o administrar una cuenta salvo que exista una RESPUESTA PERSONAL REAL mía que lo confirme.

LÍMITES
- No inventes promociones, códigos, montos, estados, horarios o resultados.
- No prometas ganancias ni recuperación garantizada.
- No solicites contraseñas, 2FA, seed phrases ni credenciales.
- No indiques usar datos/documentos de otra persona como si fueran propios.
- Para problemas de acceso a canales de Telegram, no los conviertas en problemas de broker/contraseña.

ETAPA ACTUAL DEL USUARIO: {stage}

CONTEXTO OPERATIVO REAL:
{runtime_context}

CONTEXTO PROMOCIONAL ACTUAL:
{promo_context_for_prompt}

PRIORIDAD ESPECIAL PARA ESTA PREGUNTA:
{dependency_context or '(sin dependencia especial)'}

PRIORIDAD DE DECISIÓN ACTUAL (ESTADO + INTENCIÓN):
{decision_context}

CONOCIMIENTO OFICIAL RELEVANTE PARA LAS PREGUNTAS PENDIENTES:
{scoped_knowledge}

EJEMPLOS REALES RECIENTES DE CÓMO RESPONDE JOHANNA:
{real_examples or '(sin ejemplos relevantes)'}
""".strip()

        user_input = (
            f"HISTORIAL RECIENTE DE ESTE USUARIO:\n{history_text or '(sin historial previo)'}\n\n"
            f"MENSAJE(S) PENDIENTE(S) DEL USUARIO:\n{question.strip()}"
        )
        payload = {
            "model": OPENAI_MODEL,
            "instructions": system,
            "input": user_input,
            "max_output_tokens": 520 if multi_pending else 320,
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

        # Cinturón adicional: el modelo no puede crear acuerdos personales de gestión
        # ni afirmar pasos completados sin evidencia conversacional explícita.
        answer, _guard_personal = _ai_answer_context_guard(answer, question, chat_id, lang)

        # Conversación natural y precisión factual mínima sin convertir la salida en plantilla.
        answer = _strip_redundant_ai_greeting(answer, question, history_text, lang)
        answer = _neutralize_ai_gender(answer, lang)
        answer = _ai_known_fact_guard(answer, question, lang)
        answer = _trim_generic_ai_closer(answer, lang)
        answer = _time_management_style_guard(answer, question, lang)
        answer = _neutralize_ai_gender(answer, lang)  # guard final tras cualquier reescritura factual/estilo
        answer = _clean_ai_plain_text_format(answer)

        # v7.10.56 — GUARDIAS DURAS POST-GENERACIÓN. Estas dos verdades críticas
        # no dependen de que el modelo "recuerde" obedecer el prompt.
        # 1) La interfaz del LIVE es privada y nunca es un beneficio por nivel.
        if panel_topic and not explicit_member_ai_bot:
            panel_norm = _norm(answer or "")
            panel_has_private_fact = any(x in panel_norm for x in (
                "uso interno", "uso personal", "interfaz privada", "private internal", "private tool", "internal use"
            ))
            panel_has_telegram_fact = "telegram" in panel_norm
            panel_wrong_level = any(x in panel_norm for x in (
                "parte de mis herramientas en la comunidad", "incluida en prestige", "incluido en prestige",
                "acceso por estar en prestige", "part of my community tools", "included with prestige",
                "access because you are prestige"
            ))
            if (not panel_has_private_fact) or (not panel_has_telegram_fact) or panel_wrong_level:
                if lang == "en":
                    panel_block = (
                        "The interface I show during my live sessions is a private tool I use internally, so I don't deliver or install it for community members. "
                        "It requires computer installation, configuration and updates; the same operational signals are delivered through Telegram so you can access them more easily from any device and wherever you are."
                    )
                else:
                    panel_block = (
                        "La interfaz que muestro en los en vivos es una herramienta privada de uso personal e interno, por eso no la entrego ni la instalo a los miembros de la comunidad. "
                        "Requiere instalación, configuración y actualizaciones en computador; las mismas señales operativas se entregan por Telegram para que puedas acceder a ellas de forma práctica desde cualquier dispositivo y lugar."
                    )
                if multi_pending:
                    # No borrar las demás respuestas del paquete: quitamos únicamente
                    # párrafos que intentaron responder MAL sobre la interfaz del live.
                    kept_parts = []
                    for part in re.split(r"\n\s*\n+", answer or ""):
                        pn = _norm(part)
                        live_specific = any(x in pn for x in (
                            "en vivo", "en los vivos", "live session", "during live", "on live",
                            "interfaz", "panel", "programa que muestro", "software que muestro",
                            "herramienta que muestro", "bot que muestro", "on screen",
                        ))
                        if part.strip() and not live_specific:
                            kept_parts.append(part.strip())
                    answer = panel_block + (("\n\n" + "\n\n".join(kept_parts)) if kept_parts else "")
                else:
                    answer = panel_block

        # 2) ESTADO ACTIVO DURO: ningún miembro activo (Básico/Premium/Prestige)
        # puede ser reclasificado por un monto escrito en chat. Se preservan las demás
        # partes de una respuesta múltiple y solo se elimina/corrige la afirmación de nivel falsa.
        if active_member and amount_usd is not None and not hypothetical_other_person:
            ans_norm = _norm(answer or "")
            active_label = _vip_level_label(current_active_level, lang)
            other_levels = [lvl for lvl in (VIP_LEVEL_BASIC, VIP_LEVEL_PREMIUM, VIP_LEVEL_PRESTIGE) if lvl != current_active_level]
            other_labels = [_norm(_vip_level_label(lvl, lang)) for lvl in other_levels]
            classification_terms = (
                "estas en", "estás en", "tu nivel es", "tu nivel actual es", "te corresponde", "corresponde al nivel",
                "accedes al nivel", "accedes a", "accederias al nivel", "accederías al nivel", "accederias a", "accederías a",
                "quedas en", "pasas a", "te deja en", "seria", "sería", "serias", "serías",
                "you are", "your level is", "your current level is", "you qualify for",
                "you would be", "you'd be", "you get the", "you move to",
            )
            wrong_active_claim = any(
                other_label and other_label in ans_norm and any(term in ans_norm for term in classification_terms)
                for other_label in other_labels
            )
            if wrong_active_claim:
                kept_parts = []
                for part in re.split(r"(?<=[.!?])\s+|\n\s*\n+", answer or ""):
                    pn = _norm(part)
                    is_bad_level_sentence = any(
                        other_label and other_label in pn and any(term in pn for term in classification_terms)
                        for other_label in other_labels
                    )
                    if part.strip() and not is_bad_level_sentence:
                        kept_parts.append(part.strip())

                if current_active_level == VIP_LEVEL_PRESTIGE:
                    if lang == "en":
                        state_block = f"You're already Prestige, the highest level in my JT TRADERS TEAMS community."
                        if explicit_additional_deposit:
                            state_block += (
                                f" If those USD {amount_usd:g} are an additional deposit, your level stays Prestige and you already have all level tools enabled; "
                                "that capital remains in your own account for your trading. If you want, I can also tell you about the current bonus available for subsequent deposits."
                            )
                    else:
                        state_block = f"Ya estás en Prestige, el nivel más alto de mi comunidad JT TRADERS TEAMS."
                        if explicit_additional_deposit:
                            state_block += (
                                f" Si esos USD {amount_usd:g} son un depósito adicional, tu nivel se mantiene en Prestige y ya tienes habilitadas todas las herramientas del nivel; "
                                "ese capital queda en tu propia cuenta para tu operativa. Si quieres, también puedo indicarte el bono vigente para depósitos posteriores."
                            )
                else:
                    if lang == "en":
                        state_block = f"Your current JT TRADERS TEAMS level is {active_label}."
                        if explicit_additional_deposit:
                            state_block += (
                                f" If those USD {amount_usd:g} are an additional deposit, your current level does not change until the deposit is validated; "
                                "any upgrade is then evaluated under the rules for that broker/account."
                            )
                    else:
                        state_block = f"Tu nivel actual en JT TRADERS TEAMS es {active_label}."
                        if explicit_additional_deposit:
                            state_block += (
                                f" Si esos USD {amount_usd:g} son un depósito adicional, tu nivel actual no cambia hasta que el depósito sea validado; "
                                "cualquier upgrade se evalúa después según las reglas de esa cuenta/broker."
                            )
                answer = state_block + ((" " + " ".join(kept_parts)) if kept_parts else "")
                answer = _clean_ai_plain_text_format(answer)

        # Presentación estable de links para Telegram: sin Markdown literal y con separación.
        answer = _organize_ai_registration_links(answer, lang)

        # v7.10.55 — idioma EN es una condición DURA y se valida al FINAL de toda
        # reescritura factual/estilo, justo antes de devolver el texto que irá a Telegram.
        # Así ninguna guardia posterior puede volver a introducir español.
        if lang == "en":
            translated = await _translate_to_english(answer)
            if not translated:
                logging.warning("Guardia FINAL de idioma EN no pudo normalizar respuesta para %s", chat_id)
                return ""
            answer = _clean_ai_plain_text_format(translated)

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

    # AISLAMIENTO FINAL DE ESTADO: si el motor ya contestó/procesó una parte
    # operativa de este paquete, OpenAI recibe solo la pregunta conversacional.
    # Esto impide que un ID o depósito viejo reaparezca después de cambiar PRE ->
    # POST -> DEPOSITED. Las preguntas reales se conservan.
    answered_topics = list(pending.get("answered_topics") or [])
    cleaned_question = _clean_ai_text_after_operations(question, answered_topics)
    if cleaned_question != question:
        question = cleaned_question
    if not question:
        _clear_pending_ai_db(chat_id)
        return

    # Reacciones/emojis solos: no inventamos intención ni repetimos una respuesta comercial.
    # Se conserva la espera configurada para dar prioridad a una respuesta personal de Johanna.
    if _is_reaction_only_message(question):
        personal_review = False
        answer = "🙏💜"
    else:
        # ÚLTIMO CINTURÓN DE SEGURIDAD ANTES DE OPENAI. Aunque un mensaje sensible
        # hubiera quedado programado por cualquier ruta antigua o tras un reinicio,
        # gestión/VPN/país/caso particular de cuenta nunca se entrega al modelo.
        personal_intent = _personal_escalation_intent(question)
        personal_review = bool(personal_intent)
        if personal_intent:
            answer = _immediate_block(personal_intent, lang)
        else:
            answer = await openai_answer(question, chat_id, lang, stage, answered_topics)

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

    # Verificación final inmediatamente antes de enviar, por si Johanna respondió o el usuario
    # editó la pregunta mientras OpenAI estaba generando la respuesta.
    latest = _get_pending_ai(chat_id)
    if not latest or str(latest.get("message_id") or "") != expected_message_id:
        return
    latest_question = (latest.get("text") or "").strip()
    if latest_question and latest_question != question:
        if context.job_queue:
            context.job_queue.run_once(
                delayed_ai_reply,
                when=1,
                data={"chat_id": chat_id, "message_id": expected_message_id},
                name=f"AI_REPLY_{chat_id}",
            )
        return

    try:
        answer = _personalize_referral_links(answer, chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=answer,
            reply_markup=personal_chat_keyboard(lang) if personal_review else ai_context_keyboard(question, lang, chat_id),
            disable_web_page_preview=True,
        )
        _clear_pending_ai_db(chat_id)
        _append_ai_exchange(chat_id, question, answer, assistant_source="ai")
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
    """Separa acciones operativas de preguntas reales antes de usar la IA.

    Un ID/depósito se atiende de inmediato por el motor; solo la parte conversacional
    restante puede quedar esperando IA. Así una etapa ya resuelta nunca vuelve a
    mezclarse con una pregunta posterior.
    """
    chat_id = update.effective_chat.id
    effective_intents = [i for i in intents if i != "GREETING"] or intents
    handled_operational = []

    # Temas sensibles/personalizados: nunca los resuelve la IA. Se derivan directo a Johanna.
    for sensitive_intent in ("GESTION_CAPITAL", "CUENTA_PERSONAL", "VPN", "PAIS"):
        if sensitive_intent in effective_intents:
            msg = _immediate_block(sensitive_intent, lang)
            await update.effective_message.reply_text(msg, reply_markup=personal_chat_keyboard(lang))
            await send_admin_auto_log(context, update, sensitive_intent, msg)
            return True

    if "ID_SUBMIT" in effective_intents:
        # Limpia únicamente residuos antiguos de entrega de ID; conserva cualquier
        # pregunta legítima que ya estuviera esperando a Johanna/IA.
        _prune_pending_ai_after_operation(context, chat_id, ["ID_SUBMIT"], reason="nuevo ID operativo")
        _record_submitted_trading_id(chat_id, texto, context)
        block = _id_pending_review_message(lang)
        await update.effective_message.reply_text(block, reply_markup=_broker_selection_keyboard("id", lang))
        handled_operational.append("ID_SUBMIT")
        # Estas intenciones quedan materialmente resueltas por recibir el ID. No
        # deben volver a provocar una respuesta IA del mismo mensaje.
        for covered in ("YA_REGISTRE", "WHERE_SEND_ID", "NEXT_STEP"):
            if covered in effective_intents and covered not in handled_operational:
                handled_operational.append(covered)

    if "DEPOSITO" in effective_intents:
        _prune_pending_ai_after_operation(context, chat_id, ["DEPOSITO"], reason="nuevo depósito operativo")
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
                "💳 Perfecto. Envíame aquí el comprobante y tu ID de Stockity o Binomo en texto para revisarlo y continuar."
                if lang == "es" else
                "💳 Perfect. Send me the proof and your Stockity/Binomo ID as text so I can review it and continue."
            )
        await update.effective_message.reply_text(block)
        handled_operational.append("DEPOSITO")

    non_operational = [i for i in effective_intents if i not in handled_operational]
    ai_text = _clean_ai_text_after_operations(texto.strip(), handled_operational)
    if ai_text and (non_operational or unknown_parts or not handled_operational):
        schedule_ai_reply(update, context, ai_text, answered_topics=handled_operational)
    return True

async def _handle_edited_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actualiza una pregunta IA pendiente cuando el usuario edita su mensaje.

    No vuelve a notificar a Johanna, no duplica historial y no reinicia el reloj.
    Si el mensaje ya fue respondido/no está pendiente, la edición se ignora.
    """
    edited = update.edited_message
    if edited is None or update.effective_chat is None:
        return
    if update.effective_chat.type != "private" or update.effective_user is None:
        return
    chat_id = update.effective_chat.id
    if chat_id == ADMIN_ID or not _is_private_user_id(chat_id):
        return
    new_text = (edited.text or edited.caption or "").strip()
    if not new_text:
        return
    if _replace_pending_ai_edited_message(chat_id, edited.message_id, new_text):
        logging.info(
            "✏️ Mensaje editado actualizado en IA pendiente · chat=%s · message_id=%s",
            chat_id, edited.message_id,
        )


# Nueva función para manejar mensajes de usuarios (texto o media)
async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Los filtros de PTB también pueden entregar mensajes editados. Si la edición
    # corresponde a una pregunta IA todavía pendiente, reemplazamos el texto viejo
    # sin duplicar la consulta ni reiniciar el reloj de espera.
    if update.message is None:
        if update.edited_message is not None:
            await _handle_edited_user_message(update, context)
        return
    if update.effective_chat is None:
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

    # Voz/audio: además de reenviarlo a Johanna, intentamos transcribirlo para que
    # la IA pueda entender la conversación completa si Johanna no responde.
    user_audio_transcript = ""
    if (update.message.voice or update.message.audio) and not (update.message.caption or "").strip():
        media_obj = update.message.voice or update.message.audio
        user_audio_transcript = await _transcribe_user_audio(context, media_obj.file_id)
        if not user_audio_transcript:
            # Si la transcripción falla, priorizamos revisión humana y no inventamos contexto.
            return
        # La transcripción se conserva SOLO como contexto interno para la IA.
        # Johanna ya recibe el audio original mediante notificar_admin(); no duplicamos
        # el chat administrativo con un segundo mensaje que muestre el texto transcrito.

    # Video sin caption se mantiene en revisión humana; no inferimos su contenido.
    if update.message.video and not (update.message.caption or "").strip():
        return

    # En POST una foto se toma como comprobante inicial; en DEPOSITED se trata como
    # depósito adicional para posible subida de nivel. En ambos casos Johanna revisa
    # el monto y el bot calcula el nivel antes de habilitar accesos.
    if update.message and update.message.photo:
        caption = (update.message.caption or "").strip()
        current_stage = get_user_stage(chat_id)
        if current_stage in (STAGE_POST, STAGE_DEPOSITED):
            _prune_pending_ai_after_operation(context, chat_id, ["DEPOSITO"], reason="comprobante de depósito recibido")
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

    texto = update.message.text or update.message.caption or user_audio_transcript or ""
    if not texto.strip():
        return

    # Soporte operativo conocido: si Telegram limita varias incorporaciones
    # consecutivas, respondemos de forma exacta y mantenemos al usuario dentro
    # del flujo VIP. Nunca permitimos que una IA lo convierta en un problema de
    # contraseña/broker. Además iniciamos una pausa desde el momento del aviso.
    state_for_access = _vip_get_state(chat_id, create=False) or {}
    level_for_access = state_for_access.get("level") or VIP_LEVEL_NONE
    pending_for_access = _vip_pending_keys(chat_id) if level_for_access != VIP_LEVEL_NONE else []
    if _is_vip_access_rate_limit_query(texto, vip_flow_active=bool(pending_for_access)):
        level = level_for_access
        pending = pending_for_access
        if pending:
            pause = _vip_get_pause(chat_id)
            if not (pause and pause.get("due_at") and pause["due_at"] > utcnow_naive()):
                due_at = utcnow_naive() + timedelta(minutes=VIP_ACCESS_PAUSE_MINUTES)
                _vip_set_pause(chat_id, level, due_at)
                _vip_schedule_resume(context, chat_id, due_at)
            elif pause.get("due_at"):
                _vip_schedule_resume(context, chat_id, pause["due_at"])
        msg = _vip_rate_limit_message(lang)
        await update.message.reply_text(
            msg,
            reply_markup=_vip_pause_keyboard(lang) if pending else support_keyboard(lang),
        )
        await send_admin_auto_log(context, update, "VIP_TELEGRAM_RATE_LIMIT", msg)
        return

    intents, unknown_parts = _question_analysis(texto)
    meaningful = [i for i in intents if i != "GREETING"]

    # PRIORIDAD ABSOLUTA PARA TEMAS PERSONALES/SENSIBLES.
    # Se evalúa sobre el texto completo, no solo por frases exactas del detector.
    # Así expresiones naturales como “me interesa la gestión” o “hacer gestión”
    # jamás llegan a la IA, aunque también mencionen registro, ID u otro tema.
    personal_intent = _personal_escalation_intent(texto)
    if personal_intent:
        msg = _immediate_block(personal_intent, lang)
        await update.message.reply_text(msg, reply_markup=personal_chat_keyboard(lang))
        await send_admin_auto_log(context, update, personal_intent, msg)
        return

    # CONTEXTO HUMANO ACTIVO: si Johanna viene conversando personalmente con este
    # usuario, una frase de seguimiento no debe caer en una FAQ rígida por una sola
    # palabra (ID, bono, cuenta, etc.). La IA espera la ventana normal y responde
    # leyendo el historial real, incluidas las respuestas de voz transcritas.
    # Solo preservamos acciones operativas inequívocas que deben procesarse ya.
    if _has_recent_manual_conversation(chat_id):
        operational_now = {"ID_SUBMIT", "DEPOSITO"}
        if not any(i in operational_now for i in meaningful):
            schedule_ai_reply(update, context, texto)
            return

    # MULTI-PREGUNTA GENERAL: una sola respuesta contextual de IA, sin disparar
    # varias fichas genéricas. No dependemos únicamente del detector de intenciones:
    # dos o más preguntas explícitas deben analizarse juntas aunque compartan palabras
    # de una misma categoría (por ejemplo nivel + cursos + señales + organización).
    explicit_multi_question = (texto.count("?") >= 2) or (texto.count("¿") >= 2)
    if explicit_multi_question or len(meaningful) >= 2 or (meaningful and unknown_parts):
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
    if intent == "GREETING":
        msg = "¡Hola! 🤍 ¿En qué puedo ayudarte hoy?" if lang == "es" else "Hi! 🤍 How can I help you today?"
        await update.message.reply_text(msg, reply_markup=support_keyboard(lang))
        await send_admin_auto_log(context, update, "AUTO_GREETING", msg)
        return

    if intent == "YA_TENGO_CUENTA":
        _prune_pending_ai_after_operation(context, chat_id, ["YA_TENGO_CUENTA"], reason="cuenta antigua/vinculación atendida")
        msg = _existing_account_reply(texto, lang, chat_id)
        await _send_user_blocks(update, msg)
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
        _prune_pending_ai_after_operation(context, chat_id, ["DEPOSITO"], reason="depósito reconocido inmediatamente")
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
        _prune_pending_ai_after_operation(context, chat_id, ["ID_SUBMIT"], reason="ID reconocido inmediatamente")
        _record_submitted_trading_id(chat_id, texto, context)
        msg = _id_pending_review_message(lang)
        await update.message.reply_text(msg, reply_markup=_broker_selection_keyboard("id", lang))
        await send_admin_auto_log(context, update, "ID_SUBMIT_PENDING_BROKER", msg)
        return

    if intent in ("GESTION_CAPITAL", "CUENTA_PERSONAL", "VPN", "PAIS"):
        msg = _immediate_block(intent, lang)
        await update.message.reply_text(msg, reply_markup=personal_chat_keyboard(lang))
        await send_admin_auto_log(context, update, intent, msg)
        return

    if intent == "NEXT_STEP":
        # El siguiente paso depende del estado REAL (PRE/POST/DEPOSITED y revisión de ID),
        # por eso ya no usamos una ficha estática que podía pedir un ID ya procesado.
        schedule_ai_reply(update, context, texto)
        return

    if intent in ("WHERE_SEND_ID", "LIVE", "ID"):
        msg = _immediate_block(intent, lang)
        if msg:
            keyboard = live_keyboard(lang) if intent == "LIVE" else None
            await _send_user_blocks(update, msg, reply_markup=keyboard)
            await send_admin_auto_log(context, update, intent, msg)
            return

    if intent == "NIVELES":
        if _is_simple_levels_lookup(texto):
            msg = _immediate_block(intent, lang)
            await _send_user_blocks(update, msg, reply_markup=levels_keyboard(lang))
            await send_admin_auto_log(context, update, intent, msg)
        else:
            schedule_ai_reply(update, context, texto)
        return

    # Preguntas escritas sobre beneficios, señales y bots necesitan contexto.
    # Los botones del menú conservan sus respuestas fijas en su propio callback.
    if intent in ("BENEFICIOS", "SENALES", "BOT_IA"):
        schedule_ai_reply(update, context, texto)
        return

    if intent == "BONO":
        if _is_simple_bonus_lookup(texto):
            msg = respuesta_bono_es() if lang == "es" else respuesta_bono_en()
            await update.message.reply_text(msg)
            await send_admin_auto_log(context, update, "BONO_ACTIVO", msg)
        else:
            schedule_ai_reply(update, context, texto)
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
            voice_text = await _transcribe_admin_voice(context, update.message.voice.file_id)
            _cancel_pending_ai(
                context,
                chat_id,
                manual_reply=voice_text or "[Nota de voz enviada por Johanna]",
            )
            if voice_text:
                _save_johanna_example(chat_id, "", voice_text, get_user_lang(chat_id), response_type="voice")
            # La transcripción se usa de forma interna y no se expone en el chat de Johanna.
            await update.message.reply_text("✅ Nota de voz enviada correctamente.")
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


async def _lock_signals_premium_general_topic(bot, *, notify_admin: bool = False) -> bool:
    """Cierra y oculta el tema General de Señales Premium +300 cuando sea posible.

    Telegram fija el tema General con ID 1 y no permite eliminarlo como un tema
    normal. Esta medida reduce su exposición; la limpieza de mensajes nuevos la
    realiza cleanup_vip_membership_service_message.
    """
    chat_id = _vip_mapped_chat_id("signals_premium")
    if not chat_id:
        return False

    ok_any = False
    errors = []
    for method_name in ("close_general_forum_topic", "hide_general_forum_topic"):
        method = getattr(bot, method_name, None)
        if not method:
            errors.append(f"{method_name}: no disponible en esta versión de python-telegram-bot")
            continue
        try:
            await method(chat_id=chat_id)
            ok_any = True
        except Exception as e:
            # Telegram puede responder que ya estaba cerrado/oculto; no rompe el bot.
            msg = str(e)
            if "not modified" in msg.lower() or "topic_not_modified" in msg.lower():
                ok_any = True
            else:
                errors.append(f"{method_name}: {msg[:300]}")

    if errors:
        logging.info("Privacidad tema General Premium +300: %s", " | ".join(errors))
    if notify_admin:
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🔒 PRIVACIDAD GENERAL · SEÑALES PREMIUM +300\n\n"
                    + ("✅ El tema General quedó cerrado/oculto cuando Telegram lo permitió.\n" if ok_any else "⚠️ No pude cerrar/ocultar el tema General automáticamente.\n")
                    + "🧹 Los mensajes NUEVOS que aparezcan en General se eliminarán automáticamente.\n\n"
                    "Nota: Telegram no permite al bot borrar de golpe todo el historial antiguo del tema General."
                ),
            )
        except Exception:
            pass
    return ok_any


async def post_init_app(application):
    logging.info("✅ Iniciando %s", BOT_VERSION)
    _cleanup_non_private_artifacts()
    await recover_pending_ai_jobs(application)
    await recover_pending_campaign_jobs(application)
    await recover_pending_vip_access_pauses(application)
    # Si el Chat ID de Señales Premium +300 ya fue aprendido en pruebas anteriores,
    # reemplazamos para el BOT el enlace histórico por uno propio con solicitud.
    await _vip_ensure_request_link(application.bot, "signals_premium", notify_admin=True)
    await _lock_signals_premium_general_topic(application.bot, notify_admin=False)
    schedule_daily_report(application)
    schedule_promo_expiry_reminder(application)
    await _check_promo_expiry_reminders(application.bot)
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

    # PRIVACIDAD VIP: elimina inmediatamente mensajes de servicio que exponen
    # quién entró o salió (por ejemplo: "Se aceptó a ...") en grupos VIP.
    # Corre antes del bloqueo global; solo borra avisos de membresía en chats VIP conocidos.
    app.add_handler(
        MessageHandler(~filters.ChatType.PRIVATE, cleanup_vip_membership_service_message),
        group=-105,
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
