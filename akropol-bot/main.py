from gevent import monkey
monkey.patch_all()

import os
import json
import time
import datetime
import threading
import logging
import sqlite3
import re
import math
from flask import Flask, request, jsonify, render_template, session, redirect
from datetime import timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from twilio.rest import Client
from openai import OpenAI
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream, Say
from dotenv import load_dotenv
from flask_sock import Sock
import urllib.parse
import base64
import io

# --- G.711 MU-LAW ENCODER/DECODER (Embedded for Python 3.13 Compat) ---
BIAS = 0x84
CLIP = 32635

def lin2ulaw(pcm_val):
    pcm_val = pcm_val >> 2
    if pcm_val < 0:
        pcm_val = -pcm_val
        sign = 0x80
    else:
        sign = 0x00
    if pcm_val > CLIP: pcm_val = CLIP
    pcm_val += BIAS
    exponent = int(math.log(pcm_val, 2)) - 7
    mantissa = (pcm_val >> (exponent + 3)) & 0x0F
    ulaw_byte = ~(sign | (exponent << 4) | mantissa)
    return ulaw_byte & 0xFF

def ulaw2lin(ulaw_byte):
    ulaw_byte = ~ulaw_byte
    sign = ulaw_byte & 0x80
    exponent = (ulaw_byte >> 4) & 0x07
    mantissa = ulaw_byte & 0x0F
    linear = (mantissa << 3) + 0x84
    linear <<= exponent
    linear -= 0x84
    if sign: linear = -linear
    return linear << 2

def audioop_lin2ulaw(fragment, width):
    # Assumes width=2 (16-bit PCM)
    out = bytearray()
    for i in range(0, len(fragment), 2):
        sample = int.from_bytes(fragment[i:i+2], byteorder='little', signed=True)
        out.append(lin2ulaw(sample))
    return bytes(out)

def audioop_ulaw2lin(fragment, width):
    # Assumes width=2 (16-bit PCM out)
    out = bytearray()
    for b in fragment:
        sample = ulaw2lin(b)
        out.extend(sample.to_bytes(2, byteorder='little', signed=True))
    return bytes(out)

# Mock ratecv (Super simple resampling: Drop samples for 24k->8k)
def audioop_ratecv(fragment, width, nchannels, inrate, outrate, state):
    # Only supports 24k -> 8k (factor 3)
    if inrate == 24000 and outrate == 8000:
        out = bytearray()
        for i in range(0, len(fragment), 6): # Skip every 3 samples (2 bytes each)
            out.extend(fragment[i:i+2])
        return bytes(out), None
    return fragment, None # Fallback


# --- KONFİGÜRASYON ---
load_dotenv()

# Admin Şifresi (Hashlenmiş) - Örnek: '123'
ADMIN_HASH = generate_password_hash(os.getenv("ADMIN_PASSWORD", "123"))

# Twilio & OpenAI
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "whatsapp:+14155238886") 
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

twilio_client = Client(TWILIO_SID, TWILIO_TOKEN) if TWILIO_SID else None
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Flask & DB Setup
app = Flask(__name__)
sock = Sock(app)
app.secret_key = os.urandom(24)
app.permanent_session_lifetime = timedelta(minutes=30)
logging.basicConfig(level=logging.INFO)

DATABASE = "akropol.db"

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('''CREATE TABLE IF NOT EXISTS leads (
            phone TEXT PRIMARY KEY,
            name TEXT,
            summary TEXT,
            status TEXT, -- NEW, WARM, HOT, COLD
            score INTEGER, -- 0-100
            churn_reason TEXT,
            last_interaction DATETIME
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT,
            role TEXT, -- user, assistant, system
            content TEXT,
            audio_url TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        db.commit()

init_db()

# --- HELPER FUNCTIONS ---
def load_kb():
    try:
        with open("knowledge_base.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except: return {}

def db_save_msg(phone, role, content, audio_url=None):
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO messages (phone, role, content, audio_url) VALUES (?, ?, ?, ?)", 
                   (phone, role, content, audio_url))
        db.commit()

def db_update_lead_meta(phone, summary, score, status, churn_reason=None):
    with app.app_context():
        db = get_db()
        # Ensure lead exists
        exists = db.execute("SELECT 1 FROM leads WHERE phone = ?", (phone,)).fetchone()
        if not exists:
            db.execute("INSERT INTO leads (phone, last_interaction) VALUES (?, datetime('now'))", (phone,))
        
        db.execute("""
            UPDATE leads 
            SET summary = ?, score = ?, status = ?, churn_reason = ?, last_interaction = datetime('now')
            WHERE phone = ?
        """, (summary, score, status, churn_reason, phone))
        db.commit()

def migrate_json_to_db(force=False):
    """One-time migration from json files to sqlite"""
    if os.path.exists("conversations.json"):
        with open("conversations.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            for phone, msgs in data.items():
                # Check if migrated
                db = get_db()
                exists = db.execute("SELECT 1 FROM leads WHERE phone = ?", (phone,)).fetchone()
                if exists and not force: continue
                
                # Create Lead
                db.execute("INSERT OR IGNORE INTO leads (phone, last_interaction) VALUES (?, datetime('now'))", (phone,))
                
                # Import msgs
                for m in msgs:
                    # Parse timestamp if complex
                    ts = m.get("timestamp")
                    db.execute("INSERT INTO messages (phone, role, content, audio_url, timestamp) VALUES (?, ?, ?, ?, ?)",
                               (phone, m.get("role"), m.get("content"), m.get("audio_url"), ts))
                db.commit()
                print(f"Migrated {phone}")

# --- AI LOGIC (TEXT) ---
def check_safety_guard(phone, user_input):
    """If user mentions death/illness, abort sales."""
    keywords = ["vefat", "öldü", "cenaze", "hastane", "yoğun bakım", "kanser"]
    if any(w in user_input.lower() for w in keywords):
        db_update_lead_meta(phone, "Vefat/Hastalık Durumu", 0, "COLD", "Safety Guard")
        return True
    return False

def get_best_rebuttal(user_input, kb):
    """Simple keyword matching for objection handling."""
    scenarios = kb.get("scenarios", [])
    user_input = user_input.lower()
    
    # Priority Match
    for scene in scenarios:
        trigger = scene.get("trigger", "").lower()
        if trigger and trigger in user_input:
            return scene.get("response")
            
    return None

def get_hybrid_tts_url(text):
    """
    Generate audio via OpenAI TTS API (HD quality) and save to static folder.
    Returns public URL.
    """
    try:
        filename = f"tts_{int(time.time()*1000)}.mp3"
        filepath = os.path.join("static", filename)
        
        response = client.audio.speech.create(
            model="tts-1",
            voice="shimmer",
            input=text
        )
        response.stream_to_file(filepath)
        
        # Public URL
        public_url = os.getenv("PUBLIC_URL", "https://akropol-ai.onrender.com")
        return f"{public_url}/static/{filename}"
    except Exception as e:
        print(f"TTS Error: {e}")
        return None

# --- OUTBOUND CALLING TRIGGER ---
def async_outbound_call(phone, name, delay=20):
    with app.app_context():
        try:
            print(f"Speed-to-Lead Timer Started: {phone} (Delay: {delay}s)")
            if delay > 0: time.sleep(delay)
            
            # TRIGGER REAL CALL
            if twilio_client:
                public_url = os.getenv("PUBLIC_URL", "https://akropol-ai.onrender.com") 
                
                # Safer URL construction
                safe_name = urllib.parse.quote(name)
                stream_url = f"{public_url}/voice-stream?name={safe_name}&phone={phone}"
                
                # Clean Sender Number
                # Use User's ACTIVE Twilio Number from screenshot
                # +1 618 776 2828
                sender = "+16187762828"
                
                call = twilio_client.calls.create(
                    to=phone,
                    from_=sender,
                    url=stream_url,
                    method="POST"
                )
                
                msg = f"Sistem: {name} aranıyor... Call SID: {call.sid}"
                db_save_msg(phone, "system", msg)
                print(f"Speed-to-Lead Call Initiated: {phone} | {call.sid}")
        except Exception as e:
            print(f"Async Call Failed: {e}")

@app.route("/test-call")
def test_call():
    """
    Manual Trigger for Testing (Synchronous & Debug Mode).
    """
    try:
        phone = request.args.get("phone", "").strip().replace(" ", "")
        name = request.args.get("name", "Misafir")
        
        # Auto-Format Turkish Numbers
        if phone.startswith("0"): phone = phone[1:]
        if not phone.startswith("+"): phone = "+90" + phone
        
        if not twilio_client:
            return "Twilio Client Init Failed! Check Account SID/Token.", 500

        public_url = os.getenv("PUBLIC_URL", "https://akropol-ai.onrender.com") 
        safe_name = urllib.parse.quote(name)
        stream_url = f"{public_url}/voice-stream?name={safe_name}&phone={phone}"
        
        # Determine Sender (User's Twilio Number)
        # Use VALIDATED Twilio Number hardcoded from previous valid config
        # +1 618 776 2828 is the one owned by the account based on logs
        sender = "+16187762828"
        
        # Fallback: If user set it in env correctly, use it (but be careful) 
        env_sender = os.getenv("TWILIO_PHONE_NUMBER", "")
        if env_sender and "whatsapp:" not in env_sender and len(env_sender) > 8:
             # Only use env var if it looks valid
             sender = env_sender.replace("whatsapp:", "")
        
        call = twilio_client.calls.create(
            to=phone,
            from_=sender,
            url=stream_url,
            method="POST"
        )
        
        return f"""
        <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h1 style="color: green;">SUCCESS! Call Initiated.</h1>
                <p><strong>Call SID:</strong> {call.sid}</p>
                <p><strong>To:</strong> {phone}</p>
                <p><strong>From:</strong> {sender}</p>
                <p><strong>Stream URL:</strong> {stream_url}</p>
                <p><strong>Initial Status:</strong> {call.status}</p>
                <hr>
                <p>Check your phone now. If it doesn't ring within 10s, check Twilio Dashboard logs.</p>
            </body>
        </html>
        """, 200
        
    except Exception as e:
        import traceback
        return f"""
        <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h1 style="color: red;">ERROR: Call Failed</h1>
                <p><strong>Error Message:</strong> {str(e)}</p>
                <pre style="background: #f0f0f0; padding: 10px;">{traceback.format_exc()}</pre>
            </body>
        </html>
        """, 500

# --- VOICE STREAMING (TWILIO WEBSOCKETS) ---
@app.route("/voice-stream", methods=['GET', 'POST'])
def voice_stream():
    """
    TwiML endpoint using Official Twilio Library to ensure valid XML.
    Connects call to WebSocket.
    """
    try:
        logging.info(f"Voice Stream Hit: {request.method}")
        name = request.args.get('name', 'Misafirimiz')
        phone = request.args.get('phone', 'Unknown')
        
        # Safe quote
        safe_name = urllib.parse.quote(name)
        
        # FORCE WSS Protocol and Render Domain
        render_url = os.getenv("PUBLIC_URL", "https://akropol-ai.onrender.com")
        host = render_url.replace("https://", "")
        
        # Generate Valid TwiML using VoiceResponse
        resp = VoiceResponse()
        
        # 1. Say Intro
        resp.say("Merhaba, ses kontrolü tamam. Simdi WebSocket baglantisini deniyorum...", language="tr-TR")
        
        # 2. Connect to Stream
        connect = Connect()
        stream = Stream(url=f"wss://{host}/stream?name={safe_name}&phone={phone}")
        # Add Parameters if needed (Twilio allows specific params in Stream)
        # stream.parameter(name="callerName", value=name)
        connect.append(stream)
        resp.append(connect)
        
        return str(resp), 200, {'Content-Type': 'application/xml'}
    except Exception as e:
        logging.error(f"Voice Stream Error: {e}")
        return str(e), 500

@sock.route('/stream')
def stream(ws):
    """
    Minimal Debug Stream Handler.
    Checks if connection can be established and maintained.
    """
    logging.info("DEBUG: WebSocket Connection Attempted")
    try:
        while True:
            message = ws.receive()
            if message is None:
                logging.info("DEBUG: WebSocket Closed by Client (None)")
                break
            
            data = json.loads(message)
            
            if data['event'] == 'start':
                logging.info(f"DEBUG: Stream Started - ID: {data['start']['streamSid']}")
            elif data['event'] == 'media':
                # Just acknowledge receipt, do nothing
                pass
            elif data['event'] == 'stop':
                logging.info("DEBUG: Stream Stopped")
                break
                
    except Exception as e:
        logging.error(f"DEBUG: WebSocket Crash: {e}")
    finally:
        logging.info("DEBUG: WebSocket Connection Closed")

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
@app.route("/ws-test")
def ws_test(): return render_template("ws_test.html")

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
