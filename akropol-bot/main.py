import os
import json
import time
import requests
import datetime
import threading
import logging
from flask import Flask, request, render_template, url_for, session, redirect, flash
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
CONVERSATIONS_FILE = os.path.join(BASE_DIR, "conversations.json")
KNOWLEDGE_BASE_FILE = os.path.join(BASE_DIR, "knowledge_base.json")

# Klasörleri oluştur
for d in [TEMPLATE_DIR, STATIC_DIR, AUDIO_DIR]:
    if not os.path.exists(d): os.makedirs(d)

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super_secret_key_change_me")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "whatsapp:+14155238886") 

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
try: twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
except: twilio_client = None

# --- PERSISTENCE ---
def load_conversations():
    try:
        if os.path.exists(CONVERSATIONS_FILE):
            with open(CONVERSATIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except: return {}

def save_conversations():
    try:
        with open(CONVERSATIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(CONVERSATIONS, f, ensure_ascii=False, indent=2)
    except Exception as e: print(f"Save error: {e}")

CONVERSATIONS = load_conversations()

def load_kb():
    try:
        if os.path.exists(KNOWLEDGE_BASE_FILE):
            with open(KNOWLEDGE_BASE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except: pass
    return {}

KB = load_kb()

# --- HTML ŞABLONLARI (REVERT TO STABLE SERVER-SIDE RENDERING) ---
def setup_files():
    # 1. SIMPLE DASHBOARD (Statik, Güvenilir)
    dash_path = os.path.join(TEMPLATE_DIR, "dashboard.html")
    with open(dash_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Akropol AI CRM</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"><style>body{background:#f8f9fa;font-family:'Segoe UI',sans-serif}.card{border:none;box-shadow:0 2px 10px rgba(0,0,0,0.05);border-radius:10px}.status-badge{padding:5px 12px;border-radius:20px;font-size:0.8rem;font-weight:600}.bg-HOT{background:#ffe0e0;color:#d63031}.bg-WARM{background:#fff3cd;color:#ff9f43}.bg-NEW{background:#e8f5e9;color:#00b894}</style></head><body><nav class="navbar navbar-light bg-white shadow-sm mb-4"><div class="container"><a class="navbar-brand fw-bold" href="#"><i class="fas fa-robot text-warning me-2"></i>AKROPOL AI</a><div class="d-flex gap-2"><a href="/super-admin" class="btn btn-dark btn-sm">Admin Paneli</a><a href="/logout" class="btn btn-outline-danger btn-sm">Çıkış</a></div></div></nav><div class="container"><div class="row mb-4"><div class="col-md-4"><div class="card p-3"><h3 class="fw-bold">{{ stats.total }}</h3><span class="text-muted">Toplam Görüşme</span></div></div><div class="col-md-4"><div class="card p-3"><h3 class="fw-bold text-danger">{{ stats.hot }}</h3><span class="text-muted">Sıcak Müşteri</span></div></div><div class="col-md-4"><div class="card p-3"><h3 class="fw-bold text-warning">{{ stats.follow }}</h3><span class="text-muted">Takip Gerektiren</span></div></div></div><div class="card"><div class="card-body"><div class="table-responsive"><table class="table table-hover align-middle"><thead><tr><th>Telefon</th><th>Son Durum</th><th>Statü</th><th>Zaman</th><th>İşlem</th></tr></thead><tbody>{% for phone, data in memory.items() %}{% set meta = data.get('metadata', {}) %}{% set last_msg = data.messages[-1] if data.messages else None %}<tr><td><i class="fab fa-whatsapp text-success me-2"></i>{{ phone }}</td><td class="small text-muted" style="max-width:300px">{{ meta.get('summary', 'Henüz özet yok...') }}</td><td><span class="status-badge bg-{{ meta.get('status', 'NEW') }}">{{ meta.get('status', 'YENİ') }}</span></td><td class="small">{{ last_msg.time_str if last_msg else '-' }}</td><td><a href="/super-admin?phone={{ phone }}" class="btn btn-light btn-sm border">Detay</a></td></tr>{% endfor %}</tbody></table></div></div></div></div></body></html>""")

    # 2. SUPER ADMIN (Klasik, Sorunsuz Versiyon)
    admin_path = os.path.join(TEMPLATE_DIR, "super_admin.html")
    with open(admin_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html lang="tr"><head><title>Admin Panel</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"><style>
        body{background:#e5ddd5;height:100vh;overflow:hidden;}
        .sidebar{background:white;border-right:1px solid #ddd;height:100vh;overflow-y:auto;}
        .chat-area{height:100vh;display:flex;flex-direction:column;background:#efe7dd url('https://user-images.githubusercontent.com/15075759/28719144-86dc0f70-73b1-11e7-911d-60d70fcded21.png') repeat;}
        .msg-box{flex:1;overflow-y:auto;padding:20px;}
        .msg{max-width:70%;padding:10px 15px;margin-bottom:10px;border-radius:7px;position:relative;font-size:0.9rem;box-shadow:0 1px 1px rgba(0,0,0,0.1);}
        .msg.user{background:white;align-self:flex-start;float:left;clear:both;border-top-left-radius:0;}
        .msg.assistant{background:#dcf8c6;align-self:flex-end;float:right;clear:both;border-top-right-radius:0;}
        .msg.system{background:#fff3cd;text-align:center;font-size:0.8rem;margin:10px auto;clear:both;display:table;float:none;}
        .time{font-size:0.7rem;color:#999;text-align:right;margin-top:3px;display:block;}
        .conv-item:hover{background:#f5f5f5;cursor:pointer;}
        .conv-item.active{background:#ebebeb;}
        </style></head>
        <body><div class="container-fluid"><div class="row">
        <div class="col-md-3 sidebar p-0">
            <div class="p-3 bg-light border-bottom d-flex justify-content-between align-items-center">
                <h5 class="mb-0">Görüşmeler</h5>
                <a href="/dashboard" class="btn btn-sm btn-outline-secondary"><i class="fas fa-arrow-left"></i> CRM</a>
            </div>
            {% for phone, data in conversations.items() %}
            <a href="/super-admin?phone={{ phone }}" class="d-block text-decoration-none text-dark border-bottom p-3 conv-item {% if phone == selected_phone %}active{% endif %}">
                <div class="d-flex justify-content-between">
                    <span class="fw-bold">{{ phone }}</span>
                    <small class="text-muted">{{ data.messages[-1].time_str if data.messages else '' }}</small>
                </div>
                <div class="text-truncate small text-muted">{{ data.messages[-1].content if data.messages else '...' }}</div>
            </a>
            {% endfor %}
        </div>
        <div class="col-md-9 chat-area p-0">
            {% if selected_phone %}
            <div class="p-3 bg-light border-bottom shadow-sm">
                <strong><i class="fab fa-whatsapp text-success"></i> {{ selected_phone }}</strong>
                <span class="badge bg-success ms-2">Online</span>
            </div>
            <div class="msg-box" id="msgBox">
                {# Try direct access first, then safe get #}
                {% set msgs = conversations.get(selected_phone, {}).get('messages', []) %}
                {% if not msgs and '+' in selected_phone %}
                     {# Handle URL decoded/encoded mismatch #}
                     {% set msgs = conversations.get(selected_phone.replace(' ', '+'), {}).get('messages', []) %}
                {% endif %}
                
                {% for msg in msgs %}
                <div class="msg {{ msg.role }}">
                    {% if msg.role == 'assistant' and msg.get('audio_url') %}
                        <div>{{ msg.content }}</div>
                        <audio controls src="{{ msg.audio_url }}" style="height:30px; margin-top:5px; max-width:200px;"></audio>
                    {% else %}
                        {{ msg.content }}
                    {% endif %}
                    <span class="time">{{ msg.time_str }}</span>
                </div>
                {% endfor %}
            </div>
            <div class="p-3 bg-light">
                <form action="/admin/send" method="POST" class="d-flex gap-2">
                    <input type="hidden" name="phone" value="{{ selected_phone }}">
                    <input type="text" name="message" class="form-control" placeholder="Mesaj yaz..." required autocomplete="off">
                    <button type="submit" class="btn btn-success"><i class="fas fa-paper-plane"></i></button>
                </form>
            </div>
            {% else %}
            <div class="d-flex align-items-center justify-content-center h-100 text-muted">Solicdan bir görüşme seçin...</div>
            {% endif %}
        </div>
        </div></div>
        <script>
        var d = document.getElementById("msgBox");
        if(d) d.scrollTop = d.scrollHeight;
        // Basit Oto Yenileme (5sn)
        // setTimeout(function(){ location.reload(); }, 5000); // Kullanıcı yazarken yenilemesin diye kapalı, "Manuel Yenile" butonu koymak daha iyi olurdu ama basitlik için F5 yeterli.
        </script></body></html>""")

    # 3. Login
    with open(os.path.join(TEMPLATE_DIR, "login.html"), "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html><head><title>Giriş</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{background:#2c3e50;display:flex;align-items:center;justify-content:center;height:100vh;}.card{width:350px;}</style></head><body><div class="card p-4"><h4 class="mb-3 text-center">Akropol Login</h4><form method="POST"><input type="password" name="password" class="form-control mb-3" placeholder="Şifre" required><button class="btn btn-dark w-100">Giriş</button></form></div></body></html>""")

setup_files()

# --- HELPER ---
def get_time_str(): return datetime.datetime.now().strftime("%H:%M")

def update_memory(phone, role, content, meta_update=None, audio_url=None):
    if phone not in CONVERSATIONS:
        CONVERSATIONS[phone] = {"messages": [], "metadata": {"status": "YENİ", "summary": "Yeni görüşme"}}
    
    msg = {"role": role, "content": content, "timestamp": time.time(), "time_str": get_time_str()}
    if audio_url: msg["audio_url"] = audio_url
    
    CONVERSATIONS[phone]["messages"].append(msg)
    if meta_update: CONVERSATIONS[phone]["metadata"].update(meta_update)
    save_conversations()

# --- TTS & STT ---
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
    body = request.values.get('Body', '').strip()
    media = request.values.get('MediaUrl0')
    phone = request.values.get('From', '')
    
    # KNOWLEDGE BASE
    try: kb = json.load(open(KNOWLEDGE_BASE_FILE, "r", encoding="utf-8"))
    except: kb = {}

    user_in = body
    if media: user_in = "[SESLİ MESAJ GELDİ]"

    update_memory(phone, "user", user_in) # Kaydet
    
    # AI CEVAP
    triggers = ["ses", "konuş", "söyle", "sesli"]
    should_speak = media or any(w in user_in.lower() for w in triggers)
    
    sys = f"""Sen {kb.get('identity',{}).get('name','Aura')}. 
    Bilgi: {json.dumps(kb.get('hotel_info',{}))}
    Kısa, net ve satış odaklı ol.
    {'Cevabı SESLİ okuyacaksın, ona göre yaz.' if should_speak else ''}"""
    
    hist = CONVERSATIONS.get(phone, {}).get("messages", [])
    try:
        msgs = [{"role":"system","content":sys}] + [{"role":m["role"],"content":m["content"]} for m in hist[-6:]]
        reply = client.chat.completions.create(model="gpt-4o", messages=msgs).choices[0].message.content
    except: reply = "Size hemen dönüyorum."

    resp = MessagingResponse()
    audio_url = None
    if should_speak:
        audio_url = get_tts_url(reply)
        if audio_url: resp.message(reply).media(audio_url)
        else: resp.message(reply)
    else:
        resp.message(reply)

    update_memory(phone, "assistant", reply, audio_url=audio_url)
    return str(resp)

# --- ROUTES ---
@app.route("/")
def index(): return redirect("/dashboard")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method=="POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/dashboard")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("admin", None)
    return redirect("/login")

@app.route("/dashboard")
def dash():
    if not session.get("admin"): return redirect("/login")
    return render_template("dashboard.html", memory=CONVERSATIONS, stats={"total":len(CONVERSATIONS), "hot":0, "follow":0})

@app.route("/super-admin")
def s_admin():
    if not session.get("admin"): return redirect("/login")
    phone = request.args.get("phone")
    # Sort conversations by last message timestamp (descending)
    try:
        sorted_conv = {k: v for k, v in sorted(CONVERSATIONS.items(), key=lambda item: item[1]['messages'][-1]['timestamp'] if item[1]['messages'] else 0, reverse=True)}
    except: sorted_conv = CONVERSATIONS
    return render_template("super_admin.html", conversations=sorted_conv, selected_phone=phone)

@app.route("/admin/send", methods=["POST"])
def send_msg():
    if not session.get("admin"): return redirect("/login")
    phone = request.form.get("phone")
    text = request.form.get("message")
    if phone and text:
        try:
            if twilio_client: twilio_client.messages.create(from_=TWILIO_PHONE_NUMBER, body=text, to=phone)
            update_memory(phone, "assistant", text)
        except Exception as e: print(e)
    return redirect(f"/super-admin?phone={phone}")

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
