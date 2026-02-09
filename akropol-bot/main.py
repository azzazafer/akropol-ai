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
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
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

# --- MIGRATION FROM JSON (ONE-TIME) ---
def migrate_json_to_db():
    if not os.path.exists(DB_FILE) or os.path.getsize(DB_FILE) < 100:
        json_file = os.path.join(BASE_DIR, "conversations.json")
        if os.path.exists(json_file):
            with app.app_context():
                db = get_db()
                try:
                    data = json.load(open(json_file, encoding='utf-8'))
                    for phone, val in data.items():
                        # Insert Lead
                        meta = val.get("metadata", {})
                        db.execute("INSERT OR IGNORE INTO leads (phone, status, summary) VALUES (?, ?, ?)", 
                                   (phone, meta.get("status", "NEW"), meta.get("summary", "")))
                        # Insert Messages
                        for msg in val.get("messages", []):
                            ts = datetime.datetime.fromtimestamp(msg.get("timestamp", time.time())).isoformat()
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
    # 1. NEW DASHBOARD (DB-DRIVEN)
    dash_path = os.path.join(TEMPLATE_DIR, "dashboard.html")
    with open(dash_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aura Dashboard DB</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root { --primary-color: #ff9f43; --bg-color: #f4f6f9; --text-dark: #333; }
        body { background-color: var(--bg-color); font-family: 'Montserrat', sans-serif; color: var(--text-dark); }
        .navbar { background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.05); padding: 15px 0; margin-bottom: 25px; border-top: 3px solid var(--primary-color); }
        .stat-card { background: white; border: none; border-radius: 12px; padding: 25px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); position: relative; overflow: hidden; }
        .stat-value { font-size: 2.5rem; font-weight: 700; color: #32325d; }
        .card-icon { position: absolute; right: 20px; top: 20px; background: #f6f9fc; width: 50px; height: 50px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: var(--primary-color); font-size: 1.2rem; }
        .table-card { background: white; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); border: none; }
        .table td { vertical-align: middle; padding: 15px 25px; border-bottom: 1px solid #f6f9fc; font-size: 0.9rem; }
        .badge-status { padding: 5px 10px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
        .badge-HOT { background: #fee2e2; color: #ef4444; }
        .badge-WARM { background: #fef3c7; color: #f59e0b; }
        .badge-NEW { background: #d1fae5; color: #10b981; }
        .avatar-circle { width: 40px; height: 40px; background: #e9ecef; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; color: #555; margin-right: 15px; }
        .msg-icon { font-size: 0.8rem; margin-right: 5px; color: #aaa; }
    </style>
</head>
<body>
<div id="loader" style="height:3px;width:0%;background:var(--primary-color);position:fixed;top:0;left:0;z-index:9999;transition:width 0.5s;"></div>
<nav class="navbar"><div class="container"><div class="fw-bold fs-4"><i class="fas fa-layer-group text-warning"></i> AKROPOL AI</div><a href="/logout" class="btn btn-light btn-sm text-danger">Çıkış</a></div></nav>
<div class="container">
    <div class="row g-4 mb-5">
        <div class="col-md-4"><div class="stat-card"><div>TOPLAM</div><div class="stat-value">{{ stats.total }}</div><div class="card-icon"><i class="fas fa-users"></i></div></div></div>
        <div class="col-md-4"><div class="stat-card"><div>SICAK</div><div class="stat-value text-danger">{{ stats.hot }}</div><div class="card-icon"><i class="fas fa-fire"></i></div></div></div>
        <div class="col-md-4"><div class="stat-card"><div>TAKİP</div><div class="stat-value text-warning">{{ stats.follow }}</div><div class="card-icon"><i class="fas fa-clock"></i></div></div></div>
    </div>
    <div class="table-card">
        <div class="p-4 border-bottom d-flex justify-content-between"><span>SON GÖRÜŞMELER</span><small class="text-muted"><i class="fas fa-sync-alt"></i> Canlı</small></div>
        <div class="table-responsive">
            <table class="table mb-0">
                <thead><tr style="background:#fcfcfc;"><th class="ps-4">MÜŞTERİ</th><th>SON MESAJ</th><th>STATÜ</th><th>ZAMAN</th><th></th></tr></thead>
                <tbody>
                    {% for lead in leads %}
                    <tr>
                        <td class="ps-4"><div class="d-flex align-items-center"><div class="avatar-circle">{{ lead.phone[-2:] }}</div><div><strong>{{ lead.phone }}</strong><br><small class="text-muted">WP</small></div></div></td>
                        <td class="text-muted small" style="max-width:300px;">
                            {% if lead.audio_url %}<i class="fas fa-microphone text-danger msg-icon"></i> Ses{% else %}<i class="fas fa-comment text-secondary msg-icon"></i>{% endif %} {{ lead.content[:50] }}...
                        </td>
                        <td><span class="badge-status badge-{{ lead.status }}">{{ lead.status }}</span></td>
                        <td class="text-muted small">{{ lead.time_str }}</td>
                        <td class="text-end"><a href="/detail?phone={{ lead.phone }}" class="btn btn-sm btn-outline-secondary">İncele <i class="fas fa-chevron-right"></i></a></td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>
<script>
    var loader = document.getElementById("loader");
    var width = 0;
    setInterval(function() { width = (width >= 100) ? 0 : width + Math.random() * 20; loader.style.width = width + "%"; }, 500);
    setTimeout(() => window.location.reload(), 5000);
</script>
</body></html>""")

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
    except Exception as e:
        reply = "Kısa bir arıza var."
        print(e)
        
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

@app.route("/dashboard")
def dashboard():
    if not session.get("admin"): return redirect("/login")
    db = get_db()
    # Complex query to get leads and their last message
    query = """
    SELECT l.phone, l.status, m.content, m.audio_url, m.timestamp 
    FROM leads l
    LEFT JOIN messages m ON m.id = (
        SELECT id FROM messages WHERE phone = l.phone ORDER BY id DESC LIMIT 1
    )
    ORDER BY m.timestamp DESC
    """
    rows = db.execute(query).fetchall()
    
    # Process for display
    leads = []
    stats = {"total": 0, "hot": 0, "follow": 0}
    for r in rows:
        stats["total"] += 1
        if r["status"] == "HOT": stats["hot"] += 1
        if r["status"] == "WARM": stats["follow"] += 1
        
        # Format time
        ts_str = "-"
        if r["timestamp"]:
            try:
                dt = datetime.datetime.fromisoformat(r["timestamp"])
                ts_str = dt.strftime("%H:%M")
            except: pass
            
        leads.append({
            "phone": r["phone"],
            "status": r["status"] or "NEW",
            "content": r["content"] or "",
            "audio_url": r["audio_url"],
            "time_str": ts_str
        })
        
    return render_template("dashboard.html", leads=leads, stats=stats)

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
