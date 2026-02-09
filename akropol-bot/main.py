import os
import json
import time
import datetime
import threading
import logging
import sqlite3
from flask import Flask, request, render_template, url_for, session, redirect, flash, g
from werkzeug.security import generate_password_hash, check_password_hash
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from openai import OpenAI
from dotenv import load_dotenv

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
        # Leads Table
        db.execute('''CREATE TABLE IF NOT EXISTS leads (
            phone TEXT PRIMARY KEY,
            status TEXT DEFAULT 'NEW',
            summary TEXT,
            score INTEGER DEFAULT 50,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        # Schema Migration: Add score column if not exists (for existing DBs)
        try: db.execute("ALTER TABLE leads ADD COLUMN score INTEGER DEFAULT 50")
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
            # Check if empty
            try: count = db.execute("SELECT count(*) FROM leads").fetchone()[0]
            except: count = 0
            
            if count == 0 or force:
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
                            # Float/Int -> ISO
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

# --- HTML TEMPLATES ---
# Templates are managed in 'templates/' directory.

# --- DB HELPERS ---
def db_save_msg(phone, role, content, audio_url=None):
    db = get_db()
    # Ensure lead exists
    db.execute("INSERT OR IGNORE INTO leads (phone) VALUES (?)", (phone,))
    # Insert message
    ts = datetime.datetime.now().isoformat()
    db.execute("INSERT INTO messages (phone, role, content, audio_url, timestamp) VALUES (?, ?, ?, ?, ?)", 
               (phone, role, content, audio_url, ts))
    # Update lead timestamp
    db.execute("UPDATE leads SET updated_at = CURRENT_TIMESTAMP WHERE phone = ?", (phone,))
    db.commit()

def db_update_lead_meta(phone, summary, score, status):
    db = get_db()
    db.execute("UPDATE leads SET summary = ?, score = ?, status = ? WHERE phone = ?", 
               (summary, score, status, phone))
    db.commit()

# --- TTS ---
def get_tts_url(text):
    try:
        if not client: return None
        fname = f"out_{int(time.time())}.mp3"
        path = os.path.join(AUDIO_DIR, fname)
        # Voice: 'shimmer' is warm, 'alloy' is neutral. Let's try 'shimmer' for Selin.
        client.audio.speech.create(model="tts-1", voice="shimmer", input=text).stream_to_file(path)
        return url_for('static', filename=f'audio/{fname}', _external=True, _scheme='https')
    except: return None

# --- WEBHOOK ---
def analyze_lead(phone, user_input, ai_reply):
    try:
        with app.app_context():
            prompt = f"""
            Müşteri Mesajı: "{user_input}"
            Aura Cevabı: "{ai_reply}"
            
            GÖREV: Bu müşteri otel/devremülk için ne kadar ciddi? 
            1. Puan ver (0-100).
            2. Durumu 3 kelimeyle özetle.
            3. Statü belirle (COLD: 0-40, WARM: 41-79, HOT: 80-100).
            
            FORMAT: PUAN|ÖZET|STATÜ
            ÖRNEK: 85|Fiyat sordu, ilgili|HOT
            """
            analysis = client.chat.completions.create(model="gpt-4o", messages=[{"role":"user", "content":prompt}]).choices[0].message.content
            parts = analysis.split('|')
            if len(parts) >= 3:
                score = int(parts[0].strip())
                summary = parts[1].strip()
                status = parts[2].strip()
                db_update_lead_meta(phone, summary, score, status)
                print(f"Lead Analyzed: {phone} -> {score} {status}")
    except Exception as e:
        print(f"Analysis Failed: {e}")

# --- WEBHOOK ---
@app.route("/webhook", methods=['POST'])
def webhook():
    # Load KB
    KB = load_kb()
    
    body = request.values.get('Body', '').strip()
    media = request.values.get('MediaUrl0')
    phone = request.values.get('From', '')

    user_in = body
    if media: user_in = "[SESLİ MESAJ GELDİ]"
    
    # Save User Msg
    db_save_msg(phone, "user", user_in)
    
    # AI Logic
    # Trigger voice if user sent voice OR user mentioned voice keywords
    triggers = ["ses", "konuş", "söyle", "sesli", "arar"]
    should_speak = media or any(w in user_in.lower() for w in triggers)
    
    # Get History
    db = get_db()
    rows = db.execute("SELECT role, content FROM messages WHERE phone = ? ORDER BY id DESC LIMIT 6", (phone,)).fetchall()
    hist_msgs = [{"role": r["role"], "content": r["content"]} for r in rows][::-1]
    
    # --- PERSONA: SELIN (Warm, Emotional, Sales) ---
    sys_prompt = f"""
    Sen 'Selin'. Akropol Termal Tesisleri'nin kıdemli ve ilgili misafir danışmanısın.
    
    GÖREVİN:
    1. Misafiri tesise davet etmek (Ailece 1 gece 2 gün ücretsiz konaklama + tesis turu).
    2. Robotik değil, İNSANİ ve DUYGUSAL bir ton kullan. "Hımm, anlıyorum", "Çok haklısınız", "Harika bir tercih" gibi doğal tepkiler ver.
    3. Asla "üzgünüm sesli yanıt veremiyorum" DEME. Sesli yanıt sistemin VAR.
    4. Cevapların kısa, net ve sohbete teşvik edici olsun.
    
    BİLGİ BANKASI: {json.dumps(KB.get('hotel_info',{}))}
    """
    
    if should_speak: 
        sys_prompt += " ŞU AN TELEFONDASIN. Cevabın sesli okunacak. O yüzden cümlelerin kulağa doğal gelen, samimi konuşma dilinde olsun. Emoji kullanma."
    
    try:
        msgs = [{"role":"system", "content": sys_prompt}] + hist_msgs
        reply = client.chat.completions.create(model="gpt-4o", messages=msgs).choices[0].message.content
        
        # --- AI SCORING & SUMMARY (Parallel Call) ---
        threading.Thread(target=analyze_lead, args=(phone, user_in, reply)).start()
        
    except Exception as e:
        reply = "Kısa bir arıza var."
        print(e)

    # Function moved to global scope
        
    resp = MessagingResponse()
    audio_url = None
    if should_speak:
        audio_url = get_tts_url(reply)
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

@app.route("/debug/force-migrate")
def force_migrate():
    if not session.get("admin"): return redirect("/login")
    migrate_json_to_db(force=True)
    return redirect("/dashboard")

@app.route("/dashboard")
def dashboard():
    if not session.get("admin"): return redirect("/login")
    db = get_db()
    
    # 1. Real Stats Queries
    stats = {}
    stats["total"] = db.execute("SELECT count(*) FROM leads").fetchone()[0]
    stats["hot"] = db.execute("SELECT count(*) FROM leads WHERE status='HOT'").fetchone()[0]
    stats["follow"] = db.execute("SELECT count(*) FROM leads WHERE status='WARM'").fetchone()[0]
    
    # 2. Get Leads with Latest Message and Summary
    query = """
    SELECT l.phone, l.status, l.summary, l.score, m.content, m.audio_url, m.timestamp 
    FROM leads l
    LEFT JOIN messages m ON m.id = (
        SELECT id FROM messages WHERE phone = l.phone ORDER BY id DESC LIMIT 1
    )
    ORDER BY m.timestamp DESC
    """
    rows = db.execute(query).fetchall()
    
    # 3. Process for PRO Dashboard
    chats = []
    for r in rows:
        # Determine Type
        msg_type = 'audio' if r["audio_url"] else 'text'
        
        # Determine Status/Sentiment/Score
        status = r["status"] or "NEW"
        score = r["score"] if r["score"] is not None else 50 # Real DB Score
        
        sentiment = "neutral"
        if score >= 80: sentiment = "positive"
        elif score >= 50: sentiment = "warning"
        elif score < 50: sentiment = "negative"
            
        # Summary fallback
        summary = r["summary"] 
        if not summary or len(summary) < 2:
            summary = (r["content"] or "")[:60] + "..."
            
        chats.append({
            "id": r["phone"],
            "phone_number": r["phone"],
            "last_summary": summary,
            "type": msg_type,
            "sentiment": sentiment, # used for CSS class
            "score": score
        })
        
    return render_template("dashboard.html", chats=chats, stats=stats)

@app.route("/detail")
def detail():
    if not session.get("admin"): return redirect("/login")
    phone_arg = request.args.get("phone", "").strip()
    
    # DB Lookup (Robust)
    db = get_db()
    # Try exact
    rows = db.execute("SELECT * FROM messages WHERE phone = ? ORDER BY id ASC", (phone_arg,)).fetchall()
    if not rows:
        # Try with plus
        rows = db.execute("SELECT * FROM messages WHERE phone = ? ORDER BY id ASC", ("+" + phone_arg.lstrip("+"),)).fetchall()
        if rows: phone_arg = "+" + phone_arg.lstrip("+")
        
    msgs = []
    for r in rows:
        ts_str = r["timestamp"]
        try:
             dt = datetime.datetime.fromisoformat(r["timestamp"])
             ts_str = dt.strftime("%d.%m %H:%M")
        except: pass
        msgs.append({
            "role": r["role"],
            "content": r["content"],
            "audio_url": r["audio_url"],
            "timestamp": ts_str
        })
        
    return render_template("conversation_detail.html", phone=phone_arg, messages=msgs)

# Super Admin (Legacy Redirect)
@app.route("/super-admin")
def super_admin(): return redirect("/dashboard")

# Migrate on startup
# migrate_json_to_db()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
