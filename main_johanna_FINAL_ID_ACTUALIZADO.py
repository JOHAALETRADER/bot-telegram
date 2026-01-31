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
import httpx
import html
import unicodedata
import urllib.parse

ADMIN_ID = 5924691120  # Tu ID personal de Telegram

# === FLUJO POST-VALIDACIÓN (ES) ===
TRIGGER_ID_CORRECTO_ES = """Tu ID es correcto puedes depositar en tu cuenta de trading Binomo a partir de 50 USD.

Cuando tú deposito este listo escríbeme para darte acceso"""

TRIGGER_ID_ERRADO_ES = """Tu ID está errado.

Para tener acceso a mi comunidad vip y todas las herramientas debes realizar tu registro con mi enlace..

Copia y pega el enlace de registro en barra de búsqueda de una ventana de incógnito de tu navegador y usa otro correo.. luego me envías ID de binomo para validar.

Enlace de registro:

https://binomo.com?a=95604cd745da&t=0&sa=JTTRADERS"""

STAGE_PRE = "pre_verificacion"
STAGE_POST = "post_verificacion"
STAGE_DEP = "depositado"

# URL de tu chat de validación/soporte (ya existe en tu menú)
SOPORTE_URL = "https://t.me/Johaaletradervalidacion"


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
# --- fin migración ---

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


# === MENSAJES POST-VALIDACIÓN (solo ES) ===
MENSAJE_POST_1H_ES = """✅ ID verificado.

¿Ya activaste o depositaste en tu cuenta de Binomo?"""

MENSAJE_POST_3H_ES = """🚀 Tip rápido: si hoy vas a depositar, aprovecha el bono del 100% (si te aparece disponible en tu cuenta).

Si ya depositaste, toca ✅ Ya deposité y te habilito el acceso."""

MENSAJE_POST_24H_ES = """⏳ Cuando completes tu depósito/activación, yo te habilito el acceso.

¿Ya depositaste?"""

MENSAJE_POST_48H_ES = """📌 Último recordatorio:
Cuando ya tengas tu depósito/activación listo(a), te habilito el acceso a la comunidad VIP gratuita.

¿Ya depositaste?"""

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
        # Solo enviar si el usuario sigue en pre-verificación
        if get_user_stage(chat_id) != STAGE_PRE:
            return
        await context.bot.send_message(chat_id=chat_id, text=text_es if lang == "es" else text_en)
    except Exception as e:
        logging.warning("Job send failed to %s: %s", chat_id, e)

async def mensaje_1h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message(context, MENSAJE_1H_ES, MENSAJE_1H_EN)

async def mensaje_3h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message(context, MENSAJE_3H_ES, MENSAJE_3H_EN)

async def mensaje_24h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message(context, MENSAJE_24H_ES, MENSAJE_24H_EN)

async def mensaje_48h(context: ContextTypes.DEFAULT_TYPE):
    await _send_job_message(context, MENSAJE_48H_ES, MENSAJE_48H_EN)

def _job_name(prefix: str, chat_id: int, tag: str) -> str:
    return "{}_{}_{}".format(prefix, tag, chat_id)

def cancel_jobs(context: ContextTypes.DEFAULT_TYPE, prefix: str, chat_id: int):
    jq = context.job_queue
    if not jq:
        return
    for tag in ("1h", "3h", "24h", "48h"):
        name = _job_name(prefix, chat_id, tag)
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()

async def schedule_pre_series(chat_id: int, lang: str, context: ContextTypes.DEFAULT_TYPE):
    # Serie A (pre-verificación) - mantiene tus mensajes actuales
    if not context.job_queue:
        logging.warning("⚠️ Job queue no está disponible.")
        return
    context.job_queue.run_once(mensaje_1h,  when=3600,   data=(chat_id, lang), name=_job_name("A", chat_id, "1h"))
    context.job_queue.run_once(mensaje_3h,  when=10800,  data=(chat_id, lang), name=_job_name("A", chat_id, "3h"))
    context.job_queue.run_once(mensaje_24h, when=86400,  data=(chat_id, lang), name=_job_name("A", chat_id, "24h"))
    context.job_queue.run_once(mensaje_48h, when=172800, data=(chat_id, lang), name=_job_name("A", chat_id, "48h"))
    logging.info("✅ Serie A programada 1h, 3h, 24h, 48h para chat_id %s (lang=%s)", chat_id, lang)

async def schedule_post_series_es(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    # Serie B (post-validación) - SOLO ES, como pediste
    if not context.job_queue:
        logging.warning("⚠️ Job queue no está disponible.")
        return
    context.job_queue.run_once(post_mensaje_1h_es,  when=3600,   data=chat_id, name=_job_name("B", chat_id, "1h"))
    context.job_queue.run_once(post_mensaje_3h_es,  when=10800,  data=chat_id, name=_job_name("B", chat_id, "3h"))
    context.job_queue.run_once(post_mensaje_24h_es, when=86400,  data=chat_id, name=_job_name("B", chat_id, "24h"))
    context.job_queue.run_once(post_mensaje_48h_es, when=172800, data=chat_id, name=_job_name("B", chat_id, "48h"))
    logging.info("✅ Serie B (post-validación ES) programada 1h, 3h, 24h, 48h para chat_id %s", chat_id)

# === TECLADOS POST-VALIDACIÓN ===
def deposit_keyboard_es() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Ya deposité", callback_data="DEP_YES")],
        [InlineKeyboardButton("⏳ Aún no", callback_data="DEP_NO")],
        [InlineKeyboardButton("❓ Tengo dudas", callback_data="DEP_HELP")],
    ])

def confirm_proof_keyboard_es() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sí, ya deposité", callback_data="DEP_YES")],
        [InlineKeyboardButton("❌ No, era otra cosa", callback_data="DEP_NOPROOF")],
    ])

async def post_mensaje_1h_es(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    if get_user_stage(chat_id) != STAGE_POST:
        return
    await context.bot.send_message(chat_id=chat_id, text=MENSAJE_POST_1H_ES, reply_markup=deposit_keyboard_es())

async def post_mensaje_3h_es(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    if get_user_stage(chat_id) != STAGE_POST:
        return
    await context.bot.send_message(chat_id=chat_id, text=MENSAJE_POST_3H_ES, reply_markup=deposit_keyboard_es())

async def post_mensaje_24h_es(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    if get_user_stage(chat_id) != STAGE_POST:
        return
    await context.bot.send_message(chat_id=chat_id, text=MENSAJE_POST_24H_ES, reply_markup=deposit_keyboard_es())

async def post_mensaje_48h_es(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    if get_user_stage(chat_id) != STAGE_POST:
        return
    await context.bot.send_message(chat_id=chat_id, text=MENSAJE_POST_48H_ES, reply_markup=deposit_keyboard_es())

def _text_is_deposit_confirm(text: str) -> bool:
    if not text:
        return False
    t = text.lower().strip()
    patrones = [
        r"\bya\s+deposite\b",
        r"\bya\s+deposit[eé]\b",
        r"\bya\s+hice\s+el\s+dep[oó]sito\b",
        r"\bhe\s+depositado\b",
        r"\bdeposit[eé]\b",
        r"\bya\s+active\b",
        r"\bya\s+activ[eé]\b",
        r"\bactiv[eé]\b",
    ]
    for p in patrones:
        if re.search(p, t):
            return True
    return False



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

# === STAGE (persistencia del flujo por usuario) ===
def get_user_stage(chat_id: int) -> str:
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        # Usamos el campo 'registrado' como stage (no se usa en otra parte del script)
        if u and u.registrado in (STAGE_PRE, STAGE_POST, STAGE_DEP):
            return u.registrado
        return STAGE_PRE

def set_user_stage(chat_id: int, stage: str):
    if stage not in (STAGE_PRE, STAGE_POST, STAGE_DEP):
        return
    with Session() as session:
        u = session.query(Usuario).filter_by(telegram_id=str(chat_id)).first()
        if not u:
            u = Usuario(telegram_id=str(chat_id), nombre="", lang="es", fecha_registro=datetime.utcnow())
            session.add(u)
        u.registrado = stage
        session.commit()

def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Escríbeme aquí", url=SOPORTE_URL)]
    ])

# === IA (Soporte inteligente) ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def _norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    # quitar tildes para comparar mejor
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s

def _detect_intent_es(texto: str) -> str:
    t = _norm_text(texto)

    # prioridad: vpn / pais -> chat personal
    if any(k in t for k in ["vpn", "proxy"]):
        return "VPN"
    if any(k in t for k in ["error de pais", "pais no", "no disponible en mi pais", "bloqueado por pais", "bloqueado en mi pais", "region", "pais", "country error", "country"]):
        return "PAIS"

    # horarios live
    if any(k in t for k in ["horario", "horarios", "live", "en vivo", "directo", "transmision"]):
        return "LIVE"

    # bono
    if any(k in t for k in ["bono", "bonus", "100%", "promocion", "promociones"]):
        return "BONO"

    # id
    if ("id" in t and any(k in t for k in ["donde", "como", "encuentro", "ver", "buscar"])) or any(k in t for k in ["donde veo mi id", "como encuentro mi id", "como ver mi id"]):
        return "ID"

    # correo
    if any(k in t for k in ["no me llega el correo", "no llega el correo", "no me llega email", "no llega email", "correo", "email", "mail"]):
        return "EMAIL"

    # retiro / withdraw
    if any(k in t for k in ["retiro", "retirar", "withdraw", "rechaz", "rechazo", "deneg", "fallo", "error al retirar", "no me deja retirar"]):
        return "RETIRO"

    # metodos / banco
    if any(k in t for k in ["metodo", "metodos", "banco", "cuenta bancaria", "astropay", "nequi", "transfiya"]):
        return "METODOS"

    # ya deposité
    if any(k in t for k in ["ya deposite", "ya deposité", "ya hice el deposito", "ya hice el depósito", "ya recargue", "ya recargué", "ya active", "ya activé"]):
        return "DEPOSITO"

    return "OTRO"

def _respuesta_horarios_live() -> str:
    return (
        "📊 **Horarios de mis lives (hora Colombia):**\n\n"
        "• **Martes:** 11:00 am y 8:00 pm\n"
        "• **Miércoles:** 8:00 pm\n"
        "• **Jueves:** 11:00 am y 8:00 pm\n"
        "• **Viernes:** 8:00 pm\n"
        "• **Sábados:** 11:00 am y 8:00 pm\n\n"
        "Si hay cambios, los aviso por el canal antes del live 🚀"
    )

def _respuesta_bono_base() -> str:
    return (
        "💰 **¿Cómo funciona el bono en Binomo?**\n\n"
        "El bono es un beneficio **opcional** que a veces aparece al momento de depositar.\n"
        "Si lo activas, Binomo te añade un porcentaje extra sobre tu depósito para operar con más capital.\n\n"
        "📌 Importante: los bonos suelen tener **condiciones**, por ejemplo un volumen mínimo de operaciones antes de poder retirar lo relacionado con ese bono.\n"
        "Las reglas exactas pueden variar según tu cuenta y la promoción activa.\n\n"
        "Si quieres, escríbeme a mi chat personal y te digo si te conviene activarlo según tu caso 👇"
    )

def _respuesta_id_base() -> str:
    return (
        "🆔 **¿Dónde encuentro mi ID de Binomo?**\n\n"
        "1) Entra a tu cuenta en Binomo (app o web).\n"
        "2) Ve a tu **perfil / ajustes** (icono de usuario).\n"
        "3) Busca el campo **ID** o **User ID** y cópialo.\n\n"
        "Si no lo ves, dime si estás en app o en navegador y te guío paso a paso 👇"
    )

def _fallback_johabot() -> str:
    return (
        "Para este caso prefiero revisarlo contigo directamente 🤍\n\n"
        "Soy **Johabot** y para ayudarte correctamente escríbeme a mi chat personal 👇"
    )

async def _notify_admin_auto_reply(context: ContextTypes.DEFAULT_TYPE, update: Update, intent: str, reply_text: str):
    try:
        u = update.effective_user
        cid = update.effective_chat.id
        header = (
            "🤖 **Respuesta automática enviada**\n"
            f"👤 @{u.username or u.full_name} (ID: `{cid}`)\n"
            f"🧩 Intento: **{intent}**\n\n"
        )
        txt = (reply_text or "").strip()
        if len(txt) > 3500:
            txt = txt[:3500] + "\n\n…(recortado)"
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=header + txt,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.info("No pude notificar admin auto-reply: %s", e)

async def _binomo_search_snippets(query: str, max_results: int = 3) -> str:
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
            body = re.sub(r"\s+", " ", body).strip()
            link = item.get("html_url") or ""
            if body:
                body = body[:900]
            chunks.append(f"TITULO: {title}\nCONTENIDO: {body}\nFUENTE: {link}".strip())
        return "\n\n---\n\n".join(chunks)
    except Exception:
        return ""

async def _openai_answer(question: str, context_text: str) -> str:
    if not OPENAI_API_KEY:
        return ""
    try:
        system = (
            "Eres un asistente de soporte para usuarios de Binomo en español. "
            "Responde en 6–10 líneas, claro y directo. "
            "NO inventes información. Si algo depende del país, método de pago o datos de la cuenta, dilo. "
            "NO des instrucciones para evadir restricciones (VPN/proxy). "
            "Si la info del contexto no alcanza, responde con una salida segura: "
            "'Para este caso prefiero revisarlo contigo directamente… Soy Johabot…' "
        )
        payload = {
            "model": OPENAI_MODEL,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"PREGUNTA: {question}\n\nCONTEXTO (Help Center Binomo):\n{context_text}"}
            ],
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=18) as client:
            resp = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json=payload,
            )
        if resp.status_code != 200:
            return ""
        out = resp.json()
        txt_parts = []
        for item in out.get("output", []):
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    txt_parts.append(c.get("text", ""))
        return "\n".join([t for t in txt_parts if t]).strip()
    except Exception:
        return ""



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
    await context.bot.send_message(
        chat_id=chat_id,
        text=("👇 Elige una opción para continuar:" if lang=="es" else "👇 Choose an option to continue:"),
        reply_markup=build_main_menu(lang)
    )

    # Programar mensajes diferidos con lang (Serie A)
    set_user_stage(chat_id, STAGE_PRE)
    await schedule_pre_series(chat_id, lang, context)

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

                # === DETECCIÓN DE GATILLOS DE VALIDACIÓN (ADMIN -> USUARIO) ===
                try:
                    admin_text = (update.message.text or "").strip()
                    if admin_text == TRIGGER_ID_CORRECTO_ES.strip():
                        set_user_stage(destinatario_id, STAGE_POST)
                        cancel_jobs(context, "A", destinatario_id)
                        if get_user_lang(destinatario_id) == "es":
                            await schedule_post_series_es(destinatario_id, context)
                    elif admin_text == TRIGGER_ID_ERRADO_ES.strip():
                        set_user_stage(destinatario_id, STAGE_PRE)
                except Exception as _e:
                    logging.warning("Trigger detection warning: %s", _e)

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
    # --- Lógica post-validación (solo ES) ---
    chat_id = update.effective_chat.id
    stage = get_user_stage(chat_id)

    handled = False

    if stage == STAGE_POST:
        texto = (update.message.text or update.message.caption or "").strip()
        if _text_is_deposit_confirm(texto):
            set_user_stage(chat_id, STAGE_DEP)
            cancel_jobs(context, "B", chat_id)
            await update.message.reply_text(
                "Perfecto ✅\nEscríbeme al chat personal para habilitar tu acceso a mi comunidad VIP gratuita.",
                reply_markup=support_keyboard()
            )
            handled = True

        elif update.message.photo or update.message.video or (update.message.document and (update.message.document.mime_type or "").startswith("image/")):
            await update.message.reply_text(
                "📩 Recibido. ¿Esto es tu comprobante de depósito/activación?",
                reply_markup=confirm_proof_keyboard_es()
            )
            handled = True


        elif stage == STAGE_DEP:
            # Si ya está marcado como depositado y vuelve a enviar capturas, respondemos sin re-activar flujos
            if update.message.photo or update.message.video or (update.message.document and (update.message.document.mime_type or "").startswith("image/")):
                await update.message.reply_text(
                    "✅ Recibido. Ya tengo tu estado como *depositado/activado*.

"
                    "Escríbeme al chat personal para habilitar tu acceso 👇",
                    reply_markup=support_keyboard(),
                    parse_mode=ParseMode.MARKDOWN
                )
                handled = True


# === IA / Respuestas automáticas (solo texto) ===
    if (not handled) and update.message and update.message.text:
        texto = (update.message.text or "").strip()

        intent = _detect_intent_es(texto)
        reply_text = ""
        reply_markup = None
        reply_parse = None

        if intent in ("VPN", "PAIS"):
            reply_text = (
                "⚠️ Para este tipo de casos necesito revisarlo contigo directamente.\n\n"
                "Soy **Johabot** 🤍 Escríbeme a mi chat personal y te ayudo según tu país 👇"
            )
            reply_markup = support_keyboard()
            reply_parse = ParseMode.MARKDOWN

        elif intent == "LIVE":
            reply_text = _respuesta_horarios_live()
            reply_parse = ParseMode.MARKDOWN

        elif intent == "BONO":
            # Para bono NO usamos fallback: siempre explicamos primero
            reply_text = _respuesta_bono_base()
            reply_markup = support_keyboard()
            reply_parse = ParseMode.MARKDOWN

        elif intent == "ID":
            reply_text = _respuesta_id_base()
            reply_markup = support_keyboard()
            reply_parse = ParseMode.MARKDOWN

        elif intent == "DEPOSITO":
            reply_text = (
                "Perfecto ✅ Ya que depositaste/activaste, escríbeme al chat personal y te habilito el acceso.\n\n"
                "Soy **Johabot** 🤍"
            )
            reply_markup = support_keyboard()
            reply_parse = ParseMode.MARKDOWN

        else:
            snippets = await _binomo_search_snippets(texto)
            if snippets:
                ai = await _openai_answer(texto, snippets)
                if ai:
                    reply_text = ai
                    reply_parse = ParseMode.MARKDOWN
                    if "prefiero revisarlo" in ai.lower() or "escribeme" in ai.lower():
                        reply_markup = support_keyboard()
                else:
                    reply_text = _fallback_johabot()
                    reply_markup = support_keyboard()
                    reply_parse = ParseMode.MARKDOWN
            else:
                reply_text = _fallback_johabot()
                reply_markup = support_keyboard()
                reply_parse = ParseMode.MARKDOWN

        if reply_text:
            try:
                await update.message.reply_text(
                    reply_text,
                    reply_markup=reply_markup,
                    parse_mode=reply_parse,
                    disable_web_page_preview=True
                )
                await _notify_admin_auto_reply(context, update, intent, reply_text)
            except Exception as e:
                logging.info("Error enviando respuesta automática: %s", e)

    # Mantener comportamiento actual (guardar + notificar admin)
    await guardar_mensaje(update, context)
    await notificar_admin(update, context)

# Función para enviar texto/imagen/video al usuario, desde caption con /enviar
, desde caption con /enviar
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


# === CALLBACKS POST-VALIDACIÓN ===
async def dep_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id

    set_user_stage(chat_id, STAGE_DEP)
    cancel_jobs(context, "B", chat_id)

    await query.message.reply_text(
        "Perfecto ✅\nEscríbeme al chat personal para habilitar tu acceso a mi comunidad VIP gratuita.",
        reply_markup=support_keyboard()
    )

async def dep_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Listo 👍 Cuando ya esté tu depósito/activación, toca ✅ Ya deposité.")

async def dep_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "Entiendo ✅ Para ayudarte más rápido, escríbeme por aquí:",
        reply_markup=support_keyboard()
    )

async def dep_noproof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id

    # Si el usuario se equivocó al enviar captura o al oprimir "Sí", volvemos a post-validación
    set_user_stage(chat_id, STAGE_POST)

    # Re-programamos Serie B desde cero (sin duplicados)
    cancel_jobs(context, "B", chat_id)
    await schedule_post_series_es(chat_id, context)

    await query.message.reply_text(
        "Perfecto 👍
Cuando tu depósito esté listo, escríbeme **"Ya deposité"** y te habilito el acceso.

"
        "Si necesitas ayuda rápida, toca el botón 👇",
        reply_markup=support_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

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

    # Callbacks post-validación (depósito)
    app.add_handler(CallbackQueryHandler(dep_yes, pattern="^DEP_YES$"))
    app.add_handler(CallbackQueryHandler(dep_no, pattern="^DEP_NO$"))
    app.add_handler(CallbackQueryHandler(dep_help, pattern="^DEP_HELP$"))
    app.add_handler(CallbackQueryHandler(dep_noproof, pattern="^DEP_NOPROOF$"))


    # Botones generales (incluye set_lang_es / set_lang_en / registrarme / etc.)
    app.add_handler(CallbackQueryHandler(botones))

    # Mensajes del admin (responder a usuarios con texto o audio deslizando)
    app.add_handler(MessageHandler((filters.TEXT | filters.VOICE) & filters.User(ADMIN_ID), responder_a_usuario))

    # Mensajes normales de los usuarios (texto o media)
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL) & ~filters.COMMAND & ~filters.User(ADMIN_ID), manejar_mensaje))

    logging.info("Bot corriendo…")
    app.run_polling()
