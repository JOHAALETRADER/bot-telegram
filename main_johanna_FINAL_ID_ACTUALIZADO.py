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

import urllib.request
import urllib.parse
import urllib.error
import json as _json
import html as _html

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


# === IA (FAQ Binomo + Respuestas Inteligentes) ===
# Activa IA colocando OPENAI_API_KEY en variables de entorno.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# Respuesta "soy Johabot" + redirección a chat personal
def johabot_fallback_text() -> str:
    return (
        "No estoy segura de darte una respuesta exacta en este caso 🤍\n\n"
        "Soy Johabot y para ayudarte correctamente escríbeme a mi chat personal y lo revisamos paso a paso 👇"
    )

# Horarios fijos (Hora Colombia)
LIVES_TEXT = (
    "📊 **Horarios de mis lives (hora Colombia):**\n\n"
    "▪️ **Martes:** 11:00 am y 8:00 pm\n"
    "▪️ **Miércoles:** 8:00 pm\n"
    "▪️ **Jueves:** 11:00 am y 8:00 pm\n"
    "▪️ **Viernes:** 8:00 pm\n"
    "▪️ **Sábados:** 11:00 am y 8:00 pm\n\n"
    "Si hay cambios, los aviso por el canal antes del live 🚀"
)

# Intenciones (detección por intención, no por frase exacta)
INTENT_VPN_COUNTRY = "vpn_country"
INTENT_LIVES = "lives"
INTENT_WITHDRAW_REJECTED = "withdraw_rejected"
INTENT_WITHDRAW_TIME = "withdraw_time"
INTENT_WITHDRAW_CANT = "withdraw_cant"
INTENT_FIND_ID = "find_id"
INTENT_WITHDRAW_METHODS_CO = "withdraw_methods_co"
INTENT_WITHDRAW_BANK = "withdraw_bank"
INTENT_EMAIL_NOT_RECEIVED = "email_not_received"
INTENT_NEXT_AFTER_DEPOSIT = "next_after_deposit"
INTENT_BONUS = "bonus"
INTENT_UNKNOWN = "unknown"

def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    # quitar tildes simples
    rep = str.maketrans("áéíóúüñ", "aeiouun")
    return s.translate(rep)

def detect_intent(user_text: str) -> str:
    t = _norm(user_text)

    # VPN / país (directo a chat)
    if any(k in t for k in [
        "vpn", "proxy", "pais", "país", "country", "region", "regional",
        "no disponible en mi pais", "no disponible en mi país", "bloqueado", "bloqueada",
        "error de pais", "error de país", "platform unavailable", "unavailable"
    ]):
        # si menciona explícitamente país/bloqueo, tratamos como caso sensible
        if ("vpn" in t) or ("proxy" in t) or ("pais" in t) or ("país" in t) or ("country" in t) or ("error de" in t) or ("no disponible" in t) or ("bloque" in t):
            return INTENT_VPN_COUNTRY

    # Horarios live
    if any(k in t for k in ["horario", "horarios", "live", "en vivo", "directo"]):
        return INTENT_LIVES

    # Retiros
    if "retiro" in t or "retirar" in t or "withdraw" in t:
        if any(k in t for k in ["rechaz", "recha", "failed", "fall", "deneg", "cancel"]):
            return INTENT_WITHDRAW_REJECTED
        if any(k in t for k in ["cuanto tarda", "cuanto demora", "tiempo", "horas", "dias", "días", "processing"]):
            return INTENT_WITHDRAW_TIME
        if any(k in t for k in ["no me deja", "no puedo", "no permite", "no aparece", "bloque", "error"]):
            return INTENT_WITHDRAW_CANT
        if any(k in t for k in ["banco", "cuenta bancaria", "bank"]):
            return INTENT_WITHDRAW_BANK
        if any(k in t for k in ["colombia", "col"]):
            return INTENT_WITHDRAW_METHODS_CO
        return INTENT_WITHDRAW_CANT

    # ID
    if "id" in t and any(k in t for k in ["encuentro", "donde", "dónde", "ver", "busco", "ubico"]):
        return INTENT_FIND_ID

    # Email
    if any(k in t for k in ["no me llega el correo", "no llega el correo", "no recibo correo", "email", "correo", "confirmacion", "confirmación", "codigo", "código"]):
        return INTENT_EMAIL_NOT_RECEIVED

    # Bono
    if "bono" in t or "bonus" in t:
        return INTENT_BONUS

    # Después de depositar
    if any(k in t for k in ["ya deposite", "ya deposité", "deposite", "deposité", "ya active", "ya activé", "que sigue", "qué sigue", "siguiente paso"]):
        return INTENT_NEXT_AFTER_DEPOSIT

    return INTENT_UNKNOWN

# --- Zendesk Binomo (fuente pública) ---
ZENDESK_BASES = [
    "https://binomo2.zendesk.com",
]

def _strip_html(html_text: str) -> str:
    # limpiar tags muy básico
    text = re.sub(r"<script[\s\S]*?</script>", " ", html_text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def zendesk_search(query: str, max_articles: int = 3) -> list:
    q = urllib.parse.quote(query)
    results = []
    for base in ZENDESK_BASES:
        url = f"{base}/api/v2/help_center/articles/search.json?query={q}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = _json.loads(resp.read().decode("utf-8", errors="ignore"))
            for art in data.get("results", [])[:max_articles]:
                results.append({
                    "title": art.get("title", ""),
                    "url": art.get("html_url", ""),
                    "snippet": _strip_html(art.get("body", "")[:2000]),
                })
            if results:
                return results
        except Exception as e:
            logging.info("Zendesk search fail (%s): %s", url, e)
            continue
    return results

def build_binomo_query(intent: str, user_text: str) -> str:
    # queries orientadas a Binomo Help Center
    if intent == INTENT_WITHDRAW_REJECTED:
        return "withdraw rejected failed binomo"
    if intent == INTENT_WITHDRAW_TIME:
        return "withdrawal processing time binomo"
    if intent == INTENT_WITHDRAW_CANT:
        return "cannot withdraw binomo"
    if intent == INTENT_WITHDRAW_METHODS_CO:
        return "withdrawal methods Colombia binomo"
    if intent == INTENT_WITHDRAW_BANK:
        return "withdraw to bank account binomo"
    if intent == INTENT_EMAIL_NOT_RECEIVED:
        return "email not received binomo confirmation code"
    if intent == INTENT_BONUS:
        return "how bonus works binomo"
    # fallback a texto del usuario
    return user_text

def openai_answer(question: str, sources: list) -> str:
    # Si no hay key, devolvemos fallback para no fallar
    if not OPENAI_API_KEY:
        return ""
    # construir contexto
    context_parts = []
    for i, s in enumerate(sources[:3], start=1):
        context_parts.append(f"Fuente {i}: {s.get('title','')}\n{(s.get('snippet','')[:1200])}\nURL: {s.get('url','')}")
    context_text = "\n\n".join(context_parts) if context_parts else "Sin fuentes."
    system = (
        "Eres Johabot, asistente de soporte de Johanna. Respondes en español claro y profesional. "
        "Máximo 6 a 10 líneas. No inventes políticas ni datos. "
        "Si la información no está clara en las fuentes, di que no estás segura y sugiere el botón al chat personal. "
        "No des consejos para evadir restricciones (VPN/país)."
    )
    user = (
        f"Pregunta del usuario: {question}\n\n"
        f"Fuentes públicas de Binomo (Help Center):\n{context_text}\n\n"
        "Escribe una respuesta directa (6-10 líneas). Si corresponde, sugiere revisar soporte de Binomo."
    )

    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 260,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=_json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            out = _json.loads(resp.read().decode("utf-8", errors="ignore"))
        return (out.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    except Exception as e:
        logging.warning("OpenAI call failed: %s", e)
        return ""

async def maybe_answer_with_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    # Retorna True si respondió (y por tanto no se requiere respuesta adicional)
    if not update.message:
        return False

    # Solo usuarios (no admin)
    if update.effective_user and update.effective_user.id == ADMIN_ID:
        return False

    chat_id = update.effective_chat.id
    stage = get_user_stage(chat_id)

    # Evitar interferencia con estados críticos
    # (En post-validación ya existe lógica de depósito arriba; aquí solo contestamos dudas.)
    texto = (update.message.text or update.message.caption or "").strip()
    if not texto:
        return False

    intent = detect_intent(texto)

    # VPN / país -> directo a chat personal
    if intent == INTENT_VPN_COUNTRY:
        await update.message.reply_text(
            "⚠️ Para este tipo de casos necesito revisarlo directamente contigo.\n\n"
            "Soy Johabot y para ayudarte correctamente escríbeme a mi chat personal 👇",
            reply_markup=support_keyboard()
        )
        return True

    # Horarios live -> respuesta fija
    if intent == INTENT_LIVES:
        await update.message.reply_text(LIVES_TEXT, parse_mode=ParseMode.MARKDOWN)
        return True

    # Si el usuario está en post-validación y pregunta "qué sigue" pero aún no depositó:
    if intent == INTENT_NEXT_AFTER_DEPOSIT and stage != STAGE_DEP:
        await update.message.reply_text(
            "Si ya activaste o hiciste tu depósito, toca **✅ Ya deposité** (o envíame tu comprobante) "
            "y te habilito el acceso.\n\n"
            "Si aún no, dime qué te aparece en Binomo y te oriento.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=deposit_keyboard_es() if stage == STAGE_POST else None
        )
        return True

    # Para el resto, usamos Binomo Help Center + OpenAI (si hay key)
    if intent == INTENT_UNKNOWN:
        # Solo contestar con IA si parece una pregunta (signo o palabras interrogativas)
        tnorm = _norm(texto)
        if not ("?" in texto or any(w in tnorm for w in ["como", "cómo", "por que", "por qué", "porque", "cuanto", "cuánto", "donde", "dónde", "que", "qué"])):
            return False

    query = build_binomo_query(intent, texto)
    sources = zendesk_search(query, max_articles=3)
    answer = openai_answer(texto, sources) if sources else ""

    if answer:
        await update.message.reply_text(answer)
        return True

    # Si no hay respuesta fiable (sin key o sin fuentes)
    await update.message.reply_text(johabot_fallback_text(), reply_markup=support_keyboard())
    return True


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
        [InlineKeyboardButton("💬 Escríbeme (Soporte)", url=SOPORTE_URL)]
    ])


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

    if stage == STAGE_POST:
        texto = (update.message.text or update.message.caption or "").strip()
        if _text_is_deposit_confirm(texto):
            set_user_stage(chat_id, STAGE_DEP)
            cancel_jobs(context, "B", chat_id)
            await update.message.reply_text(
                "Perfecto ✅\nEscríbeme al chat personal para habilitar tu acceso a mi comunidad VIP gratuita.",
                reply_markup=support_keyboard()
            )
        elif update.message.photo or update.message.video or (update.message.document and (update.message.document.mime_type or "").startswith("image/")):
            await update.message.reply_text(
                "📩 Recibido. ¿Esto es tu comprobante de depósito/activación?",
                reply_markup=confirm_proof_keyboard_es()
            )

    # --- IA (FAQ) ---
    try:
        await maybe_answer_with_ai(update, context)
    except Exception as _ai_e:
        logging.warning("IA handler warning: %s", _ai_e)

# Mantener comportamiento actual (guardar + notificar admin)
    await guardar_mensaje(update, context)
    await notificar_admin(update, context)

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
    await query.message.reply_text("Perfecto 👍")
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
