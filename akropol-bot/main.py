import os
import json
import time
import datetime
import threading
import logging
import sqlite3
import re
from flask import Flask, request, render_template, url_for, session, redirect, g, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from openai import OpenAI
from dotenv import load_dotenv
from fuzzywuzzy import process
import phonenumbers
from flask_sock import Sock

# --- KONFİGÜRASYON ---
load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
AUDIO_DIR = os.path.join(STATIC_DIR, "audio")
DB_FILE = os.path.join(BASE_DIR, "akropol.db")
KNOWLEDGE_BASE_FILE = os.path.join(BASE_DIR, "knowledge_base.json")

# Klasörleri oluştur
for d in [TEMPLATE_DIR, STATIC_DIR, AUDIO_DIR]:
    if not os.path.exists(d): os.makedirs(d)

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
sock = Sock(app) # WebSocket Initialization
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super_secret_key_change_me")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_HASH = generate_password_hash(ADMIN_PASSWORD) # Hash on startup

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "whatsapp:+14155238886") 

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
try: twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
except: twilio_client = None

# --- DATABASE SETUP ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_FILE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        # Leads Table with Enhanced Metrics
        db.execute('''CREATE TABLE IF NOT EXISTS leads (
            phone TEXT PRIMARY KEY,
            status TEXT DEFAULT 'NEW',
            summary TEXT,
            score INTEGER DEFAULT 50,
            churn_reason TEXT,
            safety_mode INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        # Schema Migration
        try: db.execute("ALTER TABLE leads ADD COLUMN score INTEGER DEFAULT 50")
        except: pass
        try: db.execute("ALTER TABLE leads ADD COLUMN churn_reason TEXT")
        except: pass
        try: db.execute("ALTER TABLE leads ADD COLUMN safety_mode INTEGER DEFAULT 0")
        except: pass
        
        # Messages Table
        db.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT,
            role TEXT,
            content TEXT,
            audio_url TEXT,
            timestamp TEXT,
            FOREIGN KEY(phone) REFERENCES leads(phone)
        )''')
        db.commit()

# --- MIGRATION FROM JSON (ROBUST) ---
def migrate_json_to_db(force=False):
    with app.app_context():
        db = get_db()
        try:
            # Always migrate to ensure history is present
            json_file = os.path.join(BASE_DIR, "conversations.json")
            if os.path.exists(json_file):
                print(f"Starting migration... Force={force}")
                if force:
                    db.execute("DELETE FROM leads")
                    db.execute("DELETE FROM messages")
                    
                data = json.load(open(json_file, encoding='utf-8'))
                for phone, val in data.items():
                        # Insert Lead
                        meta = val.get("metadata", {})
                        db.execute("INSERT OR IGNORE INTO leads (phone, status, summary, score) VALUES (?, ?, ?, ?)", 
                                   (phone, meta.get("status", "NEW"), meta.get("summary", ""), 50))
                        # Insert Messages
                        for msg in val.get("messages", []):
                            raw_ts = msg.get("timestamp", time.time())
                            if isinstance(raw_ts, (int, float)):
                                ts = datetime.datetime.fromtimestamp(raw_ts).isoformat()
                            else:
                                ts = str(raw_ts)
                            db.execute("INSERT INTO messages (phone, role, content, audio_url, timestamp) VALUES (?, ?, ?, ?, ?)",
                                       (phone, msg.get("role"), msg.get("content"), msg.get("audio_url"), ts))
                db.commit()
                print("Migration complete.")
        except Exception as e:
            print(f"Migration failed: {e}")

# Run setup safely
try:
    init_db()
    migrate_json_to_db() 
except Exception as e:
    print(f"DB Init Error: {e}")

def load_kb():
    try:
        if os.path.exists(KNOWLEDGE_BASE_FILE):
            with open(KNOWLEDGE_BASE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except: pass
    return {}

# --- DB HELPERS ---
def db_save_msg(phone, role, content, audio_url=None):
    db = get_db()
    db.execute("INSERT OR IGNORE INTO leads (phone) VALUES (?)", (phone,))
    ts = datetime.datetime.now().isoformat()
    db.execute("INSERT INTO messages (phone, role, content, audio_url, timestamp) VALUES (?, ?, ?, ?, ?)", 
               (phone, role, content, audio_url, ts))
    db.execute("UPDATE leads SET updated_at = CURRENT_TIMESTAMP WHERE phone = ?", (phone,))
    db.commit()

def db_update_lead_meta(phone, summary, score, status, churn_reason=None, safety_mode=None):
    db = get_db()
    query = "UPDATE leads SET summary = ?, score = ?, status = ?"
    params = [summary, score, status]
    
    if churn_reason:
        query += ", churn_reason = ?"
        params.append(churn_reason)
    if safety_mode is not None:
        query += ", safety_mode = ?"
        params.append(safety_mode)
        
    query += " WHERE phone = ?"
    params.append(phone)
    
    db.execute(query, tuple(params))
    db.commit()

def db_set_safety_mode(phone):
    db = get_db()
    db.execute("UPDATE leads SET safety_mode = 1, status = 'SAFETY_PAUSED' WHERE phone = ?", (phone,))
    db.commit()

# --- TTS & SAFETY ---
def get_hybrid_tts_url(text, duration_so_far=0):
    """
    Cost Optimization: Uses High Quality for hook (first 30s) or critical messages,
    Economic for follow-ups. Since we don't have ElevenLabs key in context, 
    we default to OpenAI 'shimmer' but structure is here.
    """
    try:
        if not client: return None
        fname = f"out_{int(time.time())}.mp3"
        path = os.path.join(AUDIO_DIR, fname)
        
        # Hybrid Logic (Conceptual)
        # if duration_so_far < 30 and ELEVENLABS_KEY:
        #    use_eleven_labs(text)
        # else:
        client.audio.speech.create(model="tts-1", voice="shimmer", input=text).stream_to_file(path)
        
        return url_for('static', filename=f'audio/{fname}', _external=True, _scheme='https')
    except: return None

def check_safety_guard(phone, text):
    """
    Safety Guard: Detects keywords related to death, sickness, accidents.
    """
    try:
        KB = load_kb()
        triggers = KB.get("safety_guard", {}).get("triggers", [])
        text_lower = text.lower()
        for t in triggers:
            if t in text_lower:
                db_set_safety_mode(phone)
                return True
    except: pass
    return False

# --- REAL-TIME SALES LOGIC (Dynamic Rebuttal) ---
def get_best_rebuttal(user_input, kb):
    """
    Latency < 10ms. Instantly detects objections and aims for the kill.
    Uses simple keyword matching for zero-latency vs embedding search.
    """
    if not user_input or len(user_input) < 3: return None
    user_input = user_input.lower()
    objections = kb.get("objection_handling", {})
    
    # 1. Price Objection
    if any(w in user_input for w in ["pahalı", "bütçe", "fiyat", "indir", "kaç para", "çok para"]):
        return objections.get("price_too_high")
        
    # 2. Distance Objection
    if any(w in user_input for w in ["uzak", "yol", "araba", "benzin", "ulaşım", "beypazarı"]):
        return objections.get("distance")
        
    # 3. Spouse/Partner Objection
    if any(w in user_input for w in ["eşim", "hanım", "beyim", "kocam", "karım", "sorayım"]):
        return objections.get("spouse")
        
    # 4. Trust Objection
    if any(w in user_input for w in ["güven", "dolandır", "gerçek mi", "yalan", "kandır"]):
        return objections.get("trust")
        
    return None

# --- ASYNC SPEED-TO-LEAD ---
def async_outbound_call(phone, name):
    """
    Simulates asyncio background task for Speed-to-Lead.
    Waits 15 seconds then sends a template message.
    """
    with app.app_context():
        try:
            print(f"Speed-to-Lead Timer Started: {phone}")
            time.sleep(15)
            
            KB = load_kb()
            # Send Template Message
            msg = f"Merhaba {name}, ben Akropol Termal'den Aura. Başvurunuzu şimdi aldım. Hayalinizdeki tatili konuşmak için müsait misiniz?"
            
            # Use Twilio to send
            if twilio_client:
                twilio_client.messages.create(
                    from_=TWILIO_PHONE_NUMBER,
                    to=phone,
                    body=msg
                )
                db_save_msg(phone, "assistant", msg)
                print(f"Speed-to-Lead Executed: {phone}")
        except Exception as e:
            print(f"Async Task Failed: {e}")

# --- VOICE STREAMING (TWILIO WEBSOCKETS) ---
@app.route("/voice-stream", methods=['POST'])
def voice_stream():
    """
    TwiML endpoint that connects the call to our WebSocket stream.
    """
    response = MessagingResponse() # Actually VoiceResponse, but using string manipulation for TwiML
    # Manual TwiML for Voice Stream
    xml = f"""
    <Response>
        <Connect>
            <Stream url="wss://{request.host}/stream" />
        </Connect>
    </Response>
    """
    return xml, 200, {'Content-Type': 'application/xml'}

@sock.route('/stream')
def stream(ws):
    """
    WebSocket handler for real-time audio processing.
    """
    logging.info("WebSocket Connection Accepted")
    while True:
        message = ws.receive()
        if message is None: break
        
        data = json.loads(message)
        if data['event'] == 'start':
            logging.info(f"Stream started: {data['start']['streamSid']}")
        elif data['event'] == 'media':
            # This is where raw audio comes in (ulaw/8000hz)
            # Todo: Feed to STT engine (Deepgram/OpenAI Whisper Realtime)
            pass
        elif data['event'] == 'stop':
            logging.info("Stream stopped")
            break

# --- WEBHOOKS ---
@app.route("/webhook-meta", methods=['POST'])
def webhook_meta():
    """
    Endpoint for Meta/Instagram Lead Forms.
    """
    data = request.json
    phone = data.get("phone_number")
    name = data.get("full_name", "Misafirimiz")
    
    if phone:
        # Normalize phone
        if "whatsapp:" not in phone: phone = f"whatsapp:{phone}"
        
        # Save initial lead
        db_save_msg(phone, "system", f"META LEAD FORM: {name}")
        
        # Trigger Async Task
        threading.Thread(target=async_outbound_call, args=(phone, name)).start()
        
        return jsonify({"status": "queued", "eta": "15s"}), 200
    return jsonify({"error": "missing phone"}), 400

def analyze_lead(phone, user_input, ai_reply):
    try:
        with app.app_context():
            prompt = f"""
            GÖREV: Satış Analizi ve Skorlama
            Müşteri: "{user_input}"
            Aura: "{ai_reply}"
            
            ANALİZ ET:
            1. Skor (0-100)
            2. Özet (3 kelime)
            3. Statü (COLD, WARM, HOT)
            4. Engel (Fiyat, Mesafe, Güven, Yok)
            
            FORMAT: PUAN|ÖZET|STATÜ|ENGEL
            ÖRNEK: 45|Fiyatı yüksek buldu|COLD|Fiyat
            """
            analysis = client.chat.completions.create(model="gpt-4o", messages=[{"role":"user", "content":prompt}]).choices[0].message.content
            parts = analysis.split('|')
            if len(parts) >= 3:
                score = int(parts[0].strip())
                summary = parts[1].strip()
                status = parts[2].strip()
                churn = parts[3].strip() if len(parts) > 3 else "Yok"
                
                db_update_lead_meta(phone, summary, score, status, churn_reason=churn)
                print(f"Lead Analyzed: {phone} -> {score} {status} ({churn})")
    except Exception as e:
        print(f"Analysis Failed: {e}")

@app.route("/webhook", methods=['POST'])
def webhook():
    KB = load_kb()
    
    body = request.values.get('Body', '').strip()
    media = request.values.get('MediaUrl0')
    phone = request.values.get('From', '')

    user_in = body
    if media: user_in = "[SESLİ MESAJ GELDİ]"
    
    # Save User Msg
    db_save_msg(phone, "user", user_in, audio_url=media)
    
    # 1. SAFETY GUARD CHECK
    if check_safety_guard(phone, user_in):
        guard_resp = KB.get("safety_guard", {}).get("script", "Başımız sağolsun.")
        resp = MessagingResponse()
        resp.message(guard_resp)
        db_save_msg(phone, "assistant", guard_resp)
        return str(resp)

    # 2. AI Logic
    triggers = ["ses", "konuş", "söyle", "sesli", "arar"]
    should_speak = media or any(w in user_in.lower() for w in triggers)
    
    # Get History
    db = get_db()
    rows = db.execute("SELECT role, content FROM messages WHERE phone = ? ORDER BY id DESC LIMIT 6", (phone,)).fetchall()
    hist_msgs = [{"role": r["role"], "content": r["content"]} for r in rows][::-1]
    
    # --- PERSONA: SELIN (Digital Sales Architect) ---
    sys_prompt = f"""
    Sen 'Aura'. {KB.get('identity',{}).get('role')}.
    
    HEDEF: Müşteriyi 'Ücretsiz Tanıtım Tatili'ne getirmek.
    
    TON: Kıdemli, samimi, güvenilir. (Bir abla/arkadaş gibi konuş).
    
    KURALLAR:
    1. Cümlelerin KISA ve VURUCU olsun (Max 15 kelime).
    2. Her cevabın sonu mutlaka bir KAPANIŞ SORUSU veya SONRAKİ ADIM ile bitsin. Asla havada bırakma.
    3. Asla "üzgünüm" veya "robotum" deme.
    4. Müşterinin itirazını (Fiyat, Mesafe, Eş) anla ve Bilgi Bankasındaki hazır senaryolarla çürüt.
    
    BİLGİ BANKASI VE SENARYOLAR: 
    {json.dumps(KB)}
    
    ÖNEMLİ: Eğer sesli yanıt veriyorsan nefes alıyormuş gibi doğal duraklamalar yap.
    """
    
    if should_speak: 
        sys_prompt += " ŞU AN TELEFONDASIN. Cevabın sesli okunacak. Noktalama işaretlerine dikkat et. Kısa konuş."
    
    try:
        msgs = [{"role":"system", "content": sys_prompt}] + hist_msgs
        reply = client.chat.completions.create(model="gpt-4o", messages=msgs).choices[0].message.content
        
        # Parallel Analysis (Scoring + Churn Detection)
        threading.Thread(target=analyze_lead, args=(phone, user_in, reply)).start()
        
    except Exception as e:
        reply = "Hatlarimizda yogunluk var, hemen döneceğim."
        print(e)
        
    resp = MessagingResponse()
    audio_url = None
    if should_speak:
        audio_url = get_hybrid_tts_url(reply)
        if audio_url: resp.message(reply).media(audio_url)
        else: resp.message(reply)
    else:
        resp.message(reply)
        
    db_save_msg(phone, "assistant", reply, audio_url)
    return str(resp)

# --- ROUTES ---
@app.route("/")
def index(): return redirect("/dashboard")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pwd = request.form.get("password")
        if check_password_hash(ADMIN_HASH, pwd):
            session["admin"] = True
            return redirect("/dashboard")
        else:
            return render_template("login.html", error="Hatalı Şifre")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("admin", None)
    return redirect("/login")

@app.route("/dashboard")
def dashboard():
    if not session.get("admin"): return redirect("/login")
    db = get_db()
    
    stats = {}
    stats["total"] = db.execute("SELECT count(*) FROM leads").fetchone()[0]
    stats["hot"] = db.execute("SELECT count(*) FROM leads WHERE status='HOT'").fetchone()[0]
    stats["follow"] = db.execute("SELECT count(*) FROM leads WHERE status='WARM'").fetchone()[0]
    
    # Updated Query with Metrics
    query = """
    SELECT l.phone, l.status, l.summary, l.score, l.churn_reason, m.content, m.audio_url, m.timestamp 
    FROM leads l
    LEFT JOIN messages m ON m.id = (
        SELECT id FROM messages WHERE phone = l.phone ORDER BY id DESC LIMIT 1
    )
    ORDER BY m.timestamp DESC
    """
    rows = db.execute(query).fetchall()
    
    chats = []
    for r in rows:
        msg_type = 'audio' if r["audio_url"] else 'text'
        status = r["status"] or "NEW"
        score = r["score"] if r["score"] is not None else 50
        
        sentiment = "neutral"
        if score >= 80: sentiment = "positive"
        elif score >= 50: sentiment = "warning"
        elif score < 50: sentiment = "negative"
            
        summary = r["summary"] 
        if not summary or len(summary) < 2:
            summary = (r["content"] or "")[:60] + "..."
            
        chats.append({
            "id": r["phone"],
            "phone_number": r["phone"],
            "last_summary": summary,
            "type": msg_type,
            "sentiment": sentiment,
            "score": score
        })
        
    return render_template("dashboard.html", chats=chats, stats=stats)

@app.route("/detail")
def detail():
    if not session.get("admin"): return redirect("/login")
    phone_arg = request.args.get("phone", "").strip()
    
    db = get_db()
    
    # URL Decode Fix
    if "whatsapp: " in phone_arg:
        phone_arg = phone_arg.replace("whatsapp: ", "whatsapp:+")
    elif " " in phone_arg and "+" not in phone_arg:
        phone_arg = phone_arg.replace(" ", "+")
        
    rows = db.execute("SELECT * FROM messages WHERE phone = ? ORDER BY id ASC", (phone_arg,)).fetchall()
    
    debug_info = f"Phone: {phone_arg} | Rows: {len(rows)}"
    
    if not rows:
        import re
        digits = "".join(filter(str.isdigit, phone_arg))
        debug_info += f" | Digits: {digits}"
        if len(digits) > 5:
            rows = db.execute("SELECT * FROM messages WHERE phone LIKE ?", (f"%{digits}",)).fetchall()
            debug_info += f" | Fallback Rows: {len(rows)}"
        
    msgs = []
    for r in rows:
        ts_str = r["timestamp"]
        try:
            try:
                ts_val = float(r["timestamp"])
                dt = datetime.datetime.fromtimestamp(ts_val)
            except ValueError:
                dt = datetime.datetime.fromisoformat(r["timestamp"])
            ts_str = dt.strftime("%d.%m %H:%M")
        except: pass
        msgs.append({
            "role": r["role"],
            "content": r["content"],
            "audio_url": r["audio_url"],
            "timestamp": ts_str
        })
        
    return render_template("conversation_detail.html", phone=phone_arg, messages=msgs, debug=debug_info)

@app.route("/debug/force-migrate")
def force_migrate():
    if not session.get("admin"): return redirect("/login")
    migrate_json_to_db(force=True)
    return redirect("/dashboard")

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
