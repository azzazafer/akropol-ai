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

# Run setup
init_db()
# migrate_json_to_db() # Uncomment if migration is needed, calling safely inside main block is better

def load_kb():
    try:
        if os.path.exists(KNOWLEDGE_BASE_FILE):
            with open(KNOWLEDGE_BASE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except: pass
    return {}

# --- HTML TEMPLATES ---
def setup_files():
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Akropol AI PRO</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary: #4f46e5;
            --bg: #f3f4f6;
            --card-bg: white;
            --text-main: #111827;
            --text-sub: #6b7280;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
        }
        body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text-main); margin: 0; padding: 0; }
        
        .dashboard-container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        
        /* HEADER */
        .admin-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; background: white; padding: 15px 25px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .logo { font-weight: 800; font-size: 1.25rem; display: flex; align-items: center; gap: 10px; color: #1f2937; }
        .badge { background: #fee2e2; color: #991b1b; padding: 2px 8px; border-radius: 6px; font-size: 0.7rem; font-weight: 700; border: 1px solid #fecaca; }
        .user-info { font-size: 0.9rem; color: var(--text-sub); }
        .btn-exit { margin-left: 15px; color: var(--danger); text-decoration: none; font-weight: 600; border: 1px solid #fee2e2; padding: 5px 12px; border-radius: 6px; transition: all 0.2s; }
        .btn-exit:hover { background: #fee2e2; }

        /* CARDS */
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card { background: white; padding: 25px; border-radius: 16px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); text-align: center; border: 1px solid #e5e7eb; transition: transform 0.2s; }
        .card:hover { transform: translateY(-3px); }
        
        div.card h3 { margin: 0 0 10px 0; font-size: 0.8rem; text-transform: uppercase; color: var(--text-sub); font-weight: 600; letter-spacing: 0.5px; }
        .card .value { font-size: 2.5rem; font-weight: 800; color: #111827; line-height: 1.2; }
        .card .label { font-size: 0.85rem; color: var(--text-sub); margin-top: 5px; }
        
        .hot-card .value { color: var(--danger); }
        .voice-card .value { color: var(--primary); font-size: 2rem; }

        /* TABLE */
        .content-table { background: white; border-radius: 16px; padding: 25px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }
        .content-table h3 { margin-top: 0; font-size: 1.1rem; color: #374151; margin-bottom: 20px; border-bottom: 1px solid #F3F4F6; padding-bottom: 15px; }
        
        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; padding: 12px 15px; color: var(--text-sub); font-size: 0.8rem; font-weight: 600; text-transform: uppercase; border-bottom: 1px solid #e5e7eb; }
        td { padding: 15px; font-size: 0.9rem; border-bottom: 1px solid #f3f4f6; color: #4b5563; vertical-align: middle; }
        
        tr:last-child td { border-bottom: none; }
        tr:hover td { background: #f9fafb; }
        
        .score-pill { background: #dbeafe; color: #1e40af; padding: 4px 10px; border-radius: 20px; font-weight: 600; font-size: 0.75rem; }
        
        /* Status sentiments used for row styling or badge logic if needed */
        .btn-view { background: white; border: 1px solid #d1d5db; color: #374151; padding: 6px 16px; border-radius: 8px; cursor: pointer; font-weight: 500; font-size: 0.85rem; transition: all 0.2s; }
        .btn-view:hover { border-color: var(--primary); color: var(--primary); background: #eef2ff; }
        
        /* Loading Bar */
        #loader { height:3px; width:0%; background:linear-gradient(90deg, var(--primary), var(--danger)); position:fixed; top:0; left:0; z-index:9999; transition: width 0.3s; }
    </style>
</head>
<body>

<div id="loader"></div>

<div class="dashboard-container">
    <header class="admin-header">
        <div class="logo">🏛️ AKROPOL AI <span class="badge">PRO</span></div>
        <div class="user-info">Hoş Geldiniz, <strong>Süper Admin</strong> <a href="/logout" class="btn-exit">Çıkış</a></div>
    </header>

    <div class="stats-grid">
        <div class="card lead-card">
            <h3>TOPLAM LEAD</h3>
            <div class="value">{{ stats.total }}</div>
            <div class="label">Müşteri Adayı</div>
        </div>
        <div class="card hot-card">
            <h3>SICAK FIRSAT 🔥</h3>
            <div class="value">{{ stats.hot }}</div>
            <div class="label">Hemen Aranmalı</div>
        </div>
        <div class="card voice-card">
            <h3>SESLİ ANALİZ 🎙️</h3>
            <div class="value">Aktif</div>
            <div class="label">Whisper AI Devrede</div>
        </div>
    </div>

    <div class="content-table">
        <h3>Son Görüşmeler & Canlı Akış</h3>
        <table>
            <thead>
                <tr>
                    <th>Müşteri</th>
                    <th>Son Mesaj / Özet</th>
                    <th>Tür</th>
                    <th>Skor</th>
                    <th>Aksiyon</th>
                </tr>
            </thead>
            <tbody>
                {% for chat in chats %}
                <tr class="status-{{ chat.sentiment }}">
                    <td style="font-weight:600; color:#111827;">{{ chat.phone_number }}</td>
                    <td style="max-width:350px;">{{ chat.last_summary }}</td>
                    <td>
                        {% if chat.type == 'audio' %}
                            <span style="color:var(--primary); font-weight:600;">🎙️ Ses</span>
                        {% else %}
                            <span style="color:#6b7280;">💬 Metin</span>
                        {% endif %}
                    </td>
                    <td><span class="score-pill" style="{% if chat.score > 80 %}background:#dcfce7;color:#166534;{% elif chat.score < 50 %}background:#fee2e2;color:#991b1b;{% endif %}">{{ chat.score }}/100</span></td>
                    <td><button onclick="window.location.href='/detail?phone={{ chat.id }}'" class="btn-view">İncele</button></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    
    <div style="text-align:center; margin-top:30px; font-size:0.8rem; color:#9ca3af;">
        Auto-refreshing every 5s • Connected to akropol.db (SQLite)
    </div>
</div>

<script>
    var loader = document.getElementById("loader");
    var width = 0;
    // Smooth infinite loader
    setInterval(function() { 
        width += Math.random() * 15;
        if (width > 100) width = 0; 
        loader.style.width = width + "%"; 
    }, 300);
    
    // Auto Refresh
    setTimeout(() => window.location.reload(), 5000);
    
    function openChat(id) {
        window.location.href = "/detail?phone=" + id;
    }
</script>

</body>
</html>

    # 2. DETAIL & LOGIN (Reuse existing logic but updated for DB if needed, keeping simple HTML)
    detail_path = os.path.join(TEMPLATE_DIR, "conversation_detail.html")
    # Using previous HTML for detail but will feed it with DB data
    with open(detail_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html lang="tr"><head><title>Detay</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet"><style>body{background:#e5ddd5}.chat-container{max-width:800px;margin:30px auto;background:#efe7dd;border-radius:10px;overflow:hidden}.chat-header{background:#075e54;color:white;padding:15px;display:flex;justify-content:space-between}.chat-body{height:600px;overflow-y:auto;padding:20px}.msg{max-width:75%;padding:10px;margin-bottom:10px;border-radius:7px;position:relative}.msg.user{background:white;float:left;clear:both}.msg.assistant{background:#dcf8c6;float:right;clear:both}.msg.system{background:#fff3cd;text-align:center;float:none;clear:both;margin:10px auto}.timestamp{display:block;font-size:0.7rem;color:#999;text-align:right}</style></head><body><div class="chat-container"><div class="chat-header"><a href="/dashboard" class="text-white text-decoration-none"><i class="fas fa-arrow-left"></i> Geri</a><h5>{{ phone }}</h5><div></div></div><div class="chat-body" id="scrollArea">{% for msg in messages %}<div class="msg {{ msg.role }}">{% if msg.audio_url %}<div>{{ msg.content }}</div><audio controls src="{{ msg.audio_url }}" style="height:30px;max-width:100%"></audio>{% else %}{{ msg.content }}{% endif %}<span class="timestamp">{{ msg.timestamp }}</span></div>{% endfor %}</div></div><script>document.getElementById("scrollArea").scrollTop = document.getElementById("scrollArea").scrollHeight;</script></body></html>""")

    # Login
    with open(os.path.join(TEMPLATE_DIR, "login.html"), "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html><head><title>Giriş</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{background:#2c3e50;display:flex;align-items:center;justify-content:center;height:100vh}.card{width:350px}</style></head><body><div class="card p-4"><h3 class="text-center mb-3">Admin Login</h3><form method="POST"><div class="mb-3"><input type="password" name="password" class="form-control" placeholder="Şifre" required></div><button class="btn btn-warning w-100 fw-bold">Giriş Yap</button></form></div></body></html>""")

setup_files()

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
        client.audio.speech.create(model="tts-1", voice="shimmer", input=text).stream_to_file(path)
        return url_for('static', filename=f'audio/{fname}', _external=True, _scheme='https')
    except: return None

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
    triggers = ["ses", "konuş", "söyle", "sesli"]
    should_speak = media or any(w in user_in.lower() for w in triggers)
    
    # Get History
    db = get_db()
    rows = db.execute("SELECT role, content FROM messages WHERE phone = ? ORDER BY id DESC LIMIT 6", (phone,)).fetchall()
    hist_msgs = [{"role": r["role"], "content": r["content"]} for r in rows][::-1]
    
    sys_prompt = f"Sen {KB.get('identity',{}).get('name','Aura')}. Bilgi: {json.dumps(KB.get('hotel_info',{}))}. Kısa ve net ol."
    if should_speak: sys_prompt += " Cevabı sesli okuyacaksın."
    
    try:
        msgs = [{"role":"system", "content": sys_prompt}] + hist_msgs
        reply = client.chat.completions.create(model="gpt-4o", messages=msgs).choices[0].message.content
        
        # --- AI SCORING & SUMMARY (Parallel Call) ---
        threading.Thread(target=analyze_lead, args=(phone, user_in, reply)).start()
        
    except Exception as e:
        reply = "Kısa bir arıza var."
        print(e)

def analyze_lead(phone, user_input, ai_reply):
    try:
        # Create a new context for thread
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
migrate_json_to_db()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
