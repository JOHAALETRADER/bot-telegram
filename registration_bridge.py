"""Authenticated bridge to the panel. Failures never change existing VIP deposits."""
import asyncio,json,os,io,urllib.request,urllib.error
from datetime import datetime,timezone,timedelta
from sqlalchemy import Column,String,Text,Integer,DateTime,or_
from sqlalchemy.orm import declarative_base
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,InputFile
import panel_launch

Base=declarative_base()
ACCOUNT='https://panel-api-production-1509.up.railway.app/account#telegram-link'
PORTAL='https://panel-api-production-1509.up.railway.app/portal'
def now():return datetime.now(timezone.utc).replace(tzinfo=None)
class Grant(Base):
    __tablename__='panel_registration_bridge_grant'
    event_id=Column(String,primary_key=True)
    payload=Column(Text,nullable=False)
    applied_at=Column(DateTime)
    last_error=Column(String)
    next_attempt_at=Column(DateTime)
    activation_result=Column(Text)
    notice_done=Column(Integer,default=0)
class Notice(Base):
    __tablename__='panel_registration_bridge_notice'
    key=Column(String,primary_key=True)
    message_id=Column(Integer)
    proof_done=Column(Integer,default=0)
    sent_at=Column(DateTime)
class BridgeError(Exception):
    def __init__(self,code):super().__init__(code);self.code=code
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
class Client:
    def __init__(self,engine,Session,secret,owner):
        self.Session=Session;self.secret=secret;self.owner=int(owner);self.enabled=bool(secret and len(secret)>=48);self.lock=asyncio.Lock();self.language=lambda uid:'es'
        Base.metadata.create_all(engine)
    def _request(self,path,data=None):
        if not self.enabled:raise BridgeError('bridge_not_configured')
        if not path.startswith('/registration-bridge/'):raise BridgeError('invalid_bridge_path')
        req=urllib.request.Request('https://panel-api-production-1509.up.railway.app'+path,data=json.dumps(data).encode() if data is not None else None,headers={'authorization':'Bearer '+self.secret,'content-type':'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect).open(req,timeout=12) as r:raw=r.read(8*1024*1024)
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            try:code=json.loads(e.read(4096)).get('error','bridge_http_error')
            except Exception:code='bridge_http_error'
            raise BridgeError(code) from None
        except (urllib.error.URLError,TimeoutError,OSError,ValueError):raise BridgeError('bridge_unavailable') from None
    async def api(self,path,data=None):return await asyncio.to_thread(self._request,path,data)
    def capture(self,launch,uid,accounts):
        if not self.enabled or not launch or not launch.eligible(uid,accounts):return None
        with launch.Session() as s:
            m=s.get(panel_launch.Member,str(uid))
            if not m or not m.granted_at:return None
            if m.grant_ticket:
                e=s.get(panel_launch.Deposit,m.grant_ticket)
                if not e or e.status!='APPROVED' or not e.deposited_since_launch_confirmed or not e.id_before_deposit_confirmed:return None
                payload=dict(grant_id=e.ticket,telegram_id=str(uid),broker=e.broker,broker_id=e.trading_id,amount_cents=e.amount_cents,route=e.route,date_confirmed=True,id_before_deposit_confirmed=True,minimum_qualifying_cents=23000 if e.route=='ENTRY230' else 10000)
            else:
                a=next((a for a in accounts if panel_launch.valid_account(a)),None)
                if not a:return None
                payload=dict(grant_id='legacy:'+str(uid)+':'+a['broker'],telegram_id=str(uid),broker=a['broker'],broker_id=a['trading_id'],route='LEGACY_DELIVERED')
        payload.update(id_validated=True,deposit_validated=True)
        with self.Session() as s:
            if not s.get(Grant,payload['grant_id']):s.add(Grant(event_id=payload['grant_id'],payload=json.dumps(payload)));s.commit()
        return payload['grant_id']
    async def apply(self,event_id,force=False):
        with self.Session() as s:
            g=s.get(Grant,event_id)
            if not g:return {'state':'no_grant'}
            if g.applied_at and not force:return {'state':'already_applied'}
            payload=json.loads(g.payload)
        try:r=await self.api('/registration-bridge/activate',payload)
        except BridgeError as e:
            with self.Session() as s:g=s.get(Grant,event_id);g.last_error=e.code;g.next_attempt_at=now()+timedelta(seconds=60);s.commit()
            raise
        if r.get('state')=='awaiting_link':
            with self.Session() as s:g=s.get(Grant,event_id);g.next_attempt_at=now()+timedelta(seconds=90);s.commit()
        if r.get('state')=='activated':
            with self.Session() as s:g=s.get(Grant,event_id);g.applied_at=now();g.last_error=None;g.activation_result=json.dumps(r);s.commit()
        return r
    async def sync(self,launch,uid,accounts,force=False):
        event=self.capture(launch,uid,accounts)
        return await self.apply(event,force) if event else {'state':'awaiting_validation'}
    async def job(self,context,launch,rows,log,language=None):
        if not self.enabled:return
        if language:self.language=language
        async with self.lock:
            with self.Session() as s:ids=[g.event_id for g in s.query(Grant).filter(Grant.applied_at.is_(None),or_(Grant.next_attempt_at.is_(None),Grant.next_attempt_at<=now())).order_by(Grant.next_attempt_at.asc().nullsfirst(),Grant.event_id).limit(20)]
            for event in ids:
                try:await self.apply(event)
                except BridgeError as e:log.warning('PANEL_BRIDGE_GRANT_PENDING code=%s',e.code)
            with self.Session() as s:completed=[(g.event_id,g.payload,g.activation_result) for g in s.query(Grant).filter(Grant.applied_at.isnot(None),Grant.notice_done==0).limit(20)]
            for event,payload,result in completed:
                try:await self.activation_notice(context,event,json.loads(payload),json.loads(result))
                except Exception as e:log.warning('PANEL_BRIDGE_ACTIVATION_NOTICE_PENDING type=%s',type(e).__name__)
            try:
                for r in (await self.api('/registration-bridge/reports')).get('reports',[]):
                    try:await self.owner_notice(context,r)
                    except Exception as e:
                        log.warning('PANEL_BRIDGE_OWNER_NOTICE_PENDING type=%s',type(e).__name__)
                        try:await self.api('/registration-bridge/reports/defer',{'id':r['id']})
                        except BridgeError:pass
                for r in (await self.api('/registration-bridge/user-notices')).get('notices',[]):
                    try:await self.user_notice(context,r)
                    except Exception as e:
                        log.warning('PANEL_BRIDGE_USER_NOTICE_PENDING type=%s',type(e).__name__)
                        try:await self.api('/registration-bridge/user-notices/defer',{'id':r['id']})
                        except BridgeError:pass
            except BridgeError as e:log.warning('PANEL_BRIDGE_NOTICES_PENDING code=%s',e.code)
    def _notice(self,key):
        with self.Session() as s:
            n=s.get(Notice,key)
            return dict(message_id=n.message_id,proof_done=n.proof_done,sent_at=n.sent_at) if n else {}
    def _save_notice(self,key,**values):
        with self.Session() as s:
            n=s.get(Notice,key)
            if not n:n=Notice(key=key);s.add(n)
            for k,v in values.items():setattr(n,k,v)
            s.commit()
    async def activation_notice(self,context,event,payload,result):
        user=result.get('user',{});active=user.get('state')=='active';tg=int(payload['telegram_id']);en=self.language(tg)=='en'
        expiry=self.expiry(user.get('expires_at'))
        usertext=(('✅ Your panel validation is synchronized. ' if en else '✅ La validación de tu panel quedó sincronizada. ')+(('Your access is active.' if en else 'Tu acceso está activo.') if active else ('Check your access status in the portal.' if en else 'Consulta el estado de tu acceso en el portal.'))+(' Vence: ' if not en else ' Expiry: ')+expiry)
        ownertext=f"✅ CUENTA DEL PANEL SINCRONIZADA\nUsuario: {user.get('name','')}\nTelegram ID: {tg}\nEstado: {user.get('state','')}\nVencimiento: {expiry}"
        for suffix,chat,text in [('user',tg,usertext),('owner',self.owner,ownertext)]:
            key='activation:'+event+':'+suffix
            if not self._notice(key).get('sent_at'):
                await context.bot.send_message(chat_id=chat,text=text)
                self._save_notice(key,sent_at=now())
        with self.Session() as s:g=s.get(Grant,event);g.notice_done=1;s.commit()
    @staticmethod
    def expiry(value):
        if not value:return 'Consulta el portal'
        try:return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ZoneInfo('America/Bogota')).strftime('%d/%m/%Y %I:%M %p · Colombia')
        except (ValueError,TypeError):return 'Consulta el portal'
    async def owner_notice(self,context,r):
        key='owner:'+r['id'];n=self._notice(key)
        if not n.get('message_id'):
            text=(f"💳 PAGO DEL PANEL POR VERIFICAR\n\nUsuario: {r['user_name']}\nCorreo: {r['email']}\nPlan: {'Solo Exnova' if r['plan']=='exnova' else 'Los tres brokers'}\nMedio: {r['method']}\nMonto declarado: {r['amount_cents']/100:,.2f} {r['currency']}\nImporte esperado: {r.get('current_expected_amount_cents',r['amount_cents'])/100:,.2f} {r['currency']} · {'reconexión' if r.get('current_kind')=='reactivation' else 'mensualidad'}\nReferencia: {r['reference']}\n\nComprueba la recepción del dinero antes de confirmar. El comprobante no activa el acceso por sí solo.")
            msg=await context.bot.send_message(chat_id=self.owner,text=text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Confirmar pago recibido',callback_data='registration_pay_yes:'+r['id'])],[InlineKeyboardButton('❌ Rechazar reporte',callback_data='registration_pay_no:'+r['id'])]]))
            self._save_notice(key,message_id=msg.message_id)
        if not n.get('proof_done'):
            proof=(await self.api('/registration-bridge/reports/proof',dict(id=r['id'],reviewer_id=str(self.owner)))).get('proof')
            if proof:
                import base64
                data=base64.b64decode(proof['base64'],validate=True)
                ext={'image/png':'png','image/jpeg':'jpg','application/pdf':'pdf'}[proof['mime']]
                await context.bot.send_document(chat_id=self.owner,document=InputFile(io.BytesIO(data),filename='comprobante-'+r['id']+'.'+ext),caption='Comprobante del reporte '+r['id']+' · referencia '+r['reference'])
            self._save_notice(key,proof_done=1)
        await self.api('/registration-bridge/reports/ack',{'id':r['id']})
    async def user_notice(self,context,r):
        key='user:'+r['id']+':'+r['status'];n=self._notice(key)
        if not n.get('sent_at'):
            # The server report is tied to the verified Telegram/account binding.
            if not r.get('telegram_id'):return
            en=self.language(int(r['telegram_id']))=='en'
            if r['status']=='verified':text=('✅ Your panel payment was confirmed. Your access was renewed until ' if en else '✅ Tu pago del panel fue confirmado. Tu acceso se renovó hasta ')+self.expiry(r.get('renewed_until'))+('. Check your plan in the portal.' if en else '. Consulta tu plan en el portal.')
            else:text=('Your payment report was not confirmed. Check the proof and contact me. This report did not change your access.' if en else 'Tu reporte de pago no fue confirmado. Revisa el comprobante y consulta conmigo. Este reporte no modificó tu acceso.')
            await context.bot.send_message(chat_id=int(r['telegram_id']),text=text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🖥 My portal' if en else '🖥 Mi portal',url=PORTAL)]]))
            self._save_notice(key,sent_at=now())
        await self.api('/registration-bridge/user-notices/ack',{'id':r['id']})
