"""Independent, auditable panel benefit. Never changes VIP levels or licence dates."""
from datetime import datetime, timezone
import uuid
from sqlalchemy import Column,String,Integer,DateTime,Text,UniqueConstraint,select,text
from sqlalchemy.orm import declarative_base
from telegram import InlineKeyboardButton,InlineKeyboardMarkup

Base=declarative_base()
POLICY='launch-2026-11-11'
LAUNCH_DATE='11/11/2026'
LEVELS={'PREMIUM','PRESTIGE'}
def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
class Policy(Base):
    __tablename__='panel_launch_policy'
    key=Column(String,primary_key=True)
    activated_at=Column(DateTime,nullable=False)
class Member(Base):
    __tablename__='panel_launch_member'
    telegram_id=Column(String,primary_key=True)
    existing=Column(Integer,default=0,nullable=False)
    granted_at=Column(DateTime)
    grant_ticket=Column(String)
class Deposit(Base):
    __tablename__='panel_launch_deposit'
    ticket=Column(String(32),primary_key=True)
    telegram_id=Column(String,index=True,nullable=False)
    broker=Column(String,nullable=False)
    trading_id=Column(String,nullable=False)
    sequence=Column(Integer,nullable=False)
    amount_cents=Column(Integer,nullable=False)
    route=Column(String,nullable=False)
    validated_at=Column(DateTime,nullable=False)
    status=Column(String,default='PENDING',nullable=False)
    deposited_since_launch_confirmed=Column(Integer,default=0,nullable=False)
    id_before_deposit_confirmed=Column(Integer,default=0,nullable=False)
    reviewed_at=Column(DateTime)
    reviewer_id=Column(String)
    __table_args__=(UniqueConstraint('telegram_id','broker','sequence',name='uq_panel_launch_deposit'),)

class Store:
    def __init__(self,engine,Session,VIP,Broker,Receipt):
        self.Session=Session; self.ready=False
        Base.metadata.create_all(engine)
        with Session() as s:
            if engine.dialect.name=='postgresql':s.execute(text('SELECT pg_advisory_xact_lock(724619113)'))
            if not s.get(Policy,POLICY):
                cohort={str(x.telegram_id) for x in s.query(VIP).filter(VIP.level.in_(LEVELS))}
                cohort.update(str(x.telegram_id) for x in s.query(Broker).filter(Broker.level.in_(LEVELS)))
                delivered={str(x.telegram_id):x.delivered_at for x in s.query(Receipt).filter(Receipt.delivered_at.isnot(None))}
                for uid in cohort|set(delivered):s.add(Member(telegram_id=uid,existing=int(uid in cohort),granted_at=delivered.get(uid)))
                s.add(Policy(key=POLICY,activated_at=now()));s.commit()
        self.ready=True
    def existing(self,uid):
        with self.Session() as s:
            m=s.get(Member,str(uid));return bool(m and m.existing)
    def eligible(self,uid,accounts):
        if not self.ready:return False
        with self.Session() as s:
            m=s.get(Member,str(uid))
            if not m or not m.granted_at:return False
            event=s.get(Deposit,m.grant_ticket) if m.grant_ticket else None
            return any(valid_account(a) and (not event or (a['broker']==event.broker and str(a['trading_id'])==event.trading_id)) for a in accounts)
    def record(self,s,uid,broker,row,preview):
        """Called inside the deposit transaction/savepoint, after ID/duplicate checks."""
        if not self.ready or not row.id_validated or row.pending_trading_id or not row.trading_id:return None
        uid=str(uid);m=s.get(Member,uid)
        if m and m.granted_at:return None
        amount=int(preview['amount_cents'])
        existing=bool(m and m.existing)
        qualifies=(amount>=10000) if existing else (
            row.level in LEVELS and (amount>=23000 or (preview.get('accumulates') and int(preview.get('new_accum_cents') or 0)>=23000)))
        if not qualifies:return None
        event=s.query(Deposit).filter_by(telegram_id=uid,broker=broker,sequence=int(preview['new_count'])).first()
        if event:return event.ticket
        ticket=uuid.uuid4().hex[:20]
        s.add(Deposit(ticket=ticket,telegram_id=uid,broker=broker,trading_id=str(row.trading_id),sequence=int(preview['new_count']),amount_cents=amount,route='EXISTING100' if existing else 'ENTRY230',validated_at=now(),status='PENDING'))
        return ticket
    def pending(self,uid=None):
        with self.Session() as s:
            q=s.query(Deposit).filter_by(status='PENDING')
            if uid is not None:q=q.filter_by(telegram_id=str(uid))
            return [event_dict(e) for e in q.order_by(Deposit.validated_at).limit(40)]
    def review(self,ticket,approve,reviewer,accounts):
        with self.Session() as s:
            e=s.execute(select(Deposit).where(Deposit.ticket==ticket).with_for_update()).scalar_one_or_none()
            if not e:return None,'No encuentro esa revisión.'
            if e.status!='PENDING':return event_dict(e),'Esta revisión ya fue procesada. No se repite el depósito ni la entrega.'
            if approve:
                if s.bind.dialect.name=='postgresql':s.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(e.telegram_id)})
                if not any(valid_account(a) and a['broker']==e.broker and str(a['trading_id'])==e.trading_id for a in accounts):
                    return None,'El ID cambió o ya no está validado. No se habilitó el panel.'
                m=s.get(Member,e.telegram_id)
                if not m:m=Member(telegram_id=e.telegram_id,existing=0);s.add(m)
                m.granted_at=m.granted_at or now();m.grant_ticket=m.grant_ticket or ticket
            e.deposited_since_launch_confirmed=int(approve)
            e.id_before_deposit_confirmed=int(approve)
            e.status='APPROVED' if approve else 'REJECTED'
            e.reviewed_at=now();e.reviewer_id=str(reviewer)
            s.commit();return event_dict(e),('Panel habilitado para entregar.' if approve else 'No se habilitó el panel. El depósito y el nivel de Telegram se conservan.')
    def lookup(self,ticket):
        with self.Session() as s:
            e=s.get(Deposit,ticket);return event_dict(e) if e else None

def valid_account(a):
    return a.get('broker') in {'BINOMO','STOCKITY','EXNOVA'} and bool(a.get('id_validated')) and bool(str(a.get('trading_id') or '').strip()) and not str(a.get('pending_trading_id') or '').strip() and int(a.get('deposit_count') or 0)>0

def event_dict(e):
    return {k:getattr(e,k) for k in ('ticket','telegram_id','broker','trading_id','sequence','amount_cents','route','status')}

def review_text(e):
    return (f"🖥 VALIDACIÓN ADICIONAL PARA EL PANEL\nUsuario: {e['telegram_id']}\nBroker: {e['broker']} · ID: {e['trading_id']}\nDepósito ya validado: USD {e['amount_cents']/100:.2f}\nCondición: {'Premium/Prestige anterior al lanzamiento · depósito nuevo USD100' if e['route']=='EXISTING100' else 'Ingreso / upgrade Premium + Panel USD230'}\n\nConfirma en el comprobante que este depósito fue REALIZADO desde el 11/11/2026 (hora Colombia), después de validar el ID, y que el usuario lo notificó. No uses la fecha de validación como fecha del depósito.\nEsta confirmación solo habilita la entrega del panel; no cambia el nivel ni activa la licencia.")
def review_keyboard(e):
    return InlineKeyboardMarkup([[InlineKeyboardButton('✅ Sí, cumple fecha e ID: entregar panel',callback_data='panel_launch_yes:'+e['ticket'])],[InlineKeyboardButton('❌ No cumple: mantener sin panel',callback_data='panel_launch_no:'+e['ticket'])]])

def conditions(lang):
    if lang=='en':return ('🖥 ADD THE WINDOWS SIGNALS PANEL\nIf you were Premium or Prestige before launch, make a NEW deposit of at least USD100 into your own trading account, dated on or after 11 November 2026. Notify me here with the proof; I validate the deposit and its date. Previous deposits do not qualify.\nNew members: I first validate your ID; then deposit at least USD230 in your own account. Basic members must upgrade to Premium + Panel: complete USD230 within the existing accumulation rules/window, or make one new USD230 deposit if accumulation no longer applies. Accounts/brokers are not combined. The panel benefit does not itself upgrade your community tier.')
    return ('🖥 AÑADE EL PANEL DE SEÑALES WINDOWS\nSi ya eras Premium o Prestige antes del lanzamiento, realiza un depósito NUEVO de al menos USD100 en tu propia cuenta de trading, efectuado desde el 11 de noviembre de 2026. Notifícamelo aquí con el comprobante; valido el depósito y su fecha. Los depósitos anteriores no cuentan.\nNuevos ingresos: primero valido tu ID y después realizas el depósito mínimo de USD230 en tu cuenta. Si eres Básico, debes hacer upgrade a Premium + Panel: completar USD230 dentro de las reglas y ventana de acumulación vigentes, o un depósito nuevo de USD230 si ya no aplica acumular. No se suman cuentas ni brokers. Obtener el panel no cambia por sí solo tu nivel de comunidad.')
