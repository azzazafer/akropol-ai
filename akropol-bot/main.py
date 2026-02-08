import os
import json
import time
import requests
import datetime
import threading
import logging
from flask import Flask, request, render_template, url_for, session, redirect, flash, jsonify
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

def save_kb(data):
    try:
        with open(KNOWLEDGE_BASE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except: return False

KB = load_kb()

# --- HTML TEMPLATES ---
def setup_files():
    # 1. PREMIUM CRM DASHBOARD (Restore Original)
    dash_path = os.path.join(TEMPLATE_DIR, "dashboard.html")
    with open(dash_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Akropol AI CRM</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"><link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600&display=swap" rel="stylesheet"><style>:root { --primary: #2c3e50; --accent: #e67e22; --bg: #f8f9fa; } body { background-color: var(--bg); font-family: 'Outfit', sans-serif; color: #333; } .navbar { background: white; padding: 1rem 0; box-shadow: 0 2px 10px rgba(0,0,0,0.03); } .brand-logo { font-weight: 600; font-size: 1.2rem; color: var(--primary); display: flex; align-items: center; gap: 10px; } .card { border: none; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.04); transition: .3s; background: white; } .stat-val { font-size: 2.5rem; font-weight: 600; color: var(--primary); } .stat-label { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 1px; color: #888; font-weight: 500; } .status-badge { padding: 5px 12px; border-radius: 30px; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.5px; } .bg-HOT { background: #ffe0e0; color: #d63031; } .bg-WARM { background: #fff3cd; color: #ff9f43; } .bg-COLD { background: #e2eafc; color: #0984e3; } .bg-NEW { background: #e8f5e9; color: #00b894; } .table-custom th { font-weight: 500; color: #888; text-transform: uppercase; font-size: 0.75rem; border-bottom: 2px solid #f1f1f1; } .avatar { width: 40px; height: 40px; background: #eee; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; color: #555; } .refresh-bar { height: 3px; background: var(--accent); width: 0%; animation: load 3s infinite linear; position: fixed; top: 0; left: 0; z-index: 9999; } @keyframes load { 0% { width: 0; } 100% { width: 100%; } }</style></head><body><div class="refresh-bar"></div><nav class="navbar mb-4"><div class="container"><div class="brand-logo"><i class="fas fa-layer-group text-warning"></i> AKROPOL AI</div><a href="/super-admin" class="btn btn-sm btn-outline-dark">Yönetici Paneli & Ayarlar</a></div></nav><div class="container"><div class="row g-4 mb-4"><div class="col-md-4"><div class="card p-4"><div class="stat-label">Toplam Görüşme</div><div class="stat-val">{{ stats.total }}</div></div></div><div class="col-md-4"><div class="card p-4"><div class="stat-label">Sıcak Potansiyel</div><div class="stat-val text-danger">{{ stats.hot }}</div></div></div><div class="col-md-4"><div class="card p-4"><div class="stat-label">Takip Listesi</div><div class="stat-val text-warning">{{ stats.follow }}</div></div></div></div><div class="card"><div class="card-body p-0"><div class="table-responsive"><table class="table table-custom table-hover align-middle mb-0"><thead class="bg-light"><tr><th class="ps-4 py-3">Müşteri</th><th>Son Durum (AI Özeti)</th><th>Statü</th><th>Zaman (Son İşlem)</th><th></th></tr></thead><tbody>{% for phone, data in memory.items() %}{% set meta = data.get('metadata', {}) %} <tr><td class="ps-4"><div class="d-flex align-items-center gap-3"><div class="avatar">{{ phone[-2:] }}</div><span class="fw-bold">{{ phone }}</span></div></td><td class="text-muted small" style="max-width:350px;">{{ meta.get('summary', 'Analiz bekleniyor...') }}</td><td><span class="status-badge bg-{{ meta.get('status', 'NEW') }}">{{ meta.get('status', 'YENİ') }}</span></td><td class="small text-muted">{{ meta.get('last_update', '-') }}</td><td class="pe-4 text-end"><a href="/super-admin" class="btn btn-sm btn-light border px-3">Yönet</a></td></tr>{% endfor %}</tbody></table></div></div></div></div><script>setTimeout(() => window.location.reload(), 5000);</script></body></html>""")

    # 2. SUPER ADMIN V8.1 (Added Settings Tab & Fixed Bugs)
    admin_path = os.path.join(TEMPLATE_DIR, "super_admin.html")
    with open(admin_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aura OS - Yönetim Merkezi</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        :root { --bg-dark: #0f172a; --sidebar-bg: #1e293b; --active-item: #334155; --text-main: #f8fafc; --text-sec: #94a3b8; --accent: #38bdf8; --msg-user: #0ea5e9; --msg-bot: #334155; }
        body { background-color: var(--bg-dark); color: var(--text-main); font-family: 'Inter', sans-serif; height: 100vh; overflow: hidden; }
        .app-container { display: flex; height: 100vh; }
        .sidebar { width: 300px; background: var(--sidebar-bg); border-right: 1px solid #334155; display: flex; flex-direction: column; }
        .main-area { flex: 1; display: flex; flex-direction: column; background: var(--bg-dark); overflow: hidden; }
        
        .nav-tabs { border-bottom: 1px solid #334155; padding: 0 20px; }
        .nav-link { color: var(--text-sec); border: none; padding: 15px 20px; }
        .nav-link.active { color: var(--accent); background: transparent; border-bottom: 2px solid var(--accent); }
        .nav-link:hover { color: white; }
        
        /* Chat Styles */
        .chat-container { display: flex; height: 100%; }
        .chat-list { width: 300px; border-right: 1px solid #334155; overflow-y: auto; }
        .chat-view { flex: 1; display: flex; flex-direction: column; }
        .conv-item { padding: 15px; border-bottom: 1px solid #334155; cursor: pointer; }
        .conv-item:hover, .conv-item.active { background: var(--active-item); }
        
        .messages { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
        .msg { max-width: 70%; padding: 10px 15px; border-radius: 10px; font-size: 0.9rem; }
        .msg.user { align-self: flex-start; background: var(--msg-bot); }
        .msg.assistant { align-self: flex-end; background: var(--msg-user); color: white; }
        .msg.system { align-self: center; background: rgba(255,255,255,0.1); font-size: 0.75rem; }
        
        /* Settings Styles */
        .settings-container { padding: 30px; overflow-y: auto; height: 100%; }
        .json-editor { width: 100%; height: 500px; background: #0f172a; color: #a5b4fc; border: 1px solid #334155; padding: 15px; font-family: monospace; border-radius: 8px; }
        
        /* Audio */
        audio { height: 30px; width: 100%; margin-top: 5px; }
    </style>
</head>
<body>

<div class="app-container">
    <div class="sidebar">
        <div class="p-4">
            <h5 class="mb-0"><i class="fas fa-layer-group text-warning me-2"></i>Aura OS</h5>
            <small class="text-muted">v8.1 Management</small>
        </div>
        <div class="list-group list-group-flush mt-3">
            <a href="#" class="list-group-item list-group-item-action bg-transparent text-white" onclick="showTab('chat')"><i class="fab fa-whatsapp me-3"></i>Canlı Sohbet</a>
            <a href="#" class="list-group-item list-group-item-action bg-transparent text-white" onclick="showTab('settings')"><i class="fas fa-cog me-3"></i>Sistem Ayarları</a>
            <a href="/dashboard" class="list-group-item list-group-item-action bg-transparent text-white"><i class="fas fa-chart-pie me-3"></i>CRM Paneli</a>
            <a href="/logout" class="list-group-item list-group-item-action bg-transparent text-danger mt-5"><i class="fas fa-sign-out-alt me-3"></i>Çıkış</a>
        </div>
    </div>

    <div class="main-area">
        <!-- CHAT TAB -->
        <div id="tab-chat" class="h-100" style="display:flex;">
            <div class="chat-list" id="convList">
                <!-- JS fills here -->
            </div>
            <div class="chat-view" id="chatView">
                <div class="p-3 border-bottom border-secondary d-flex justify-content-between align-items-center">
                    <h6 class="mb-0" id="chatTitle">Görüşme Seçin</h6>
                </div>
                <div class="messages" id="messagesArea"></div>
                <div class="p-3 border-top border-secondary">
                    <form onsubmit="event.preventDefault(); sendMessage();" class="d-flex gap-2">
                        <input type="text" id="msgInput" class="form-control bg-dark text-white border-secondary" placeholder="Aura adına yanıt yaz..." autocomplete="off">
                        <button class="btn btn-primary"><i class="fas fa-paper-plane"></i></button>
                    </form>
                </div>
            </div>
        </div>

        <!-- SETTINGS TAB -->
        <div id="tab-settings" class="h-100" style="display:none;">
            <div class="settings-container">
                <h4 class="mb-4">Sistem Beyni (Bilgi Bankası)</h4>
                <div class="alert alert-info border-0 bg-opacity-10 bg-info small">
                    <i class="fas fa-info-circle me-2"></i>Burada yapacağınız değişiklikler anında botun davranışlarını, otel bilgilerini ve satış stratejisini günceller.
                </div>
                <textarea id="jsonEditor" class="json-editor" spellcheck="false"></textarea>
                <button class="btn btn-success mt-3" onclick="saveSettings()"><i class="fas fa-save me-2"></i>Kaydet ve Uygula</button>
            </div>
        </div>
    </div>
</div>

<script>
    let currentPhone = null;

    function showTab(tab) {
        document.getElementById('tab-chat').style.display = tab === 'chat' ? 'flex' : 'none';
        document.getElementById('tab-settings').style.display = tab === 'settings' ? 'block' : 'none';
        if(tab === 'settings') loadSettings();
    }

    // --- CHAT LOGIC ---
    async function loadConversations() {
        const res = await fetch('/api/conversations');
        const data = await res.json();
        const list = document.getElementById('convList');
        
        let html = '';
        Object.keys(data).sort((a,b) => data[b].messages.length - data[a].messages.length).forEach(phone => {
            let last = data[phone].messages[data[phone].messages.length-1]?.content || '';
            if(last.length > 30) last = last.substring(0,30) + '...';
            html += `
                <div class="conv-item p-3 ${currentPhone === phone ? 'active' : ''}" onclick="selectChat('${phone}')">
                    <div class="fw-bold d-flex justify-content-between">
                        ${phone}
                        <small class="text-muted" style="font-size:0.7em">Online</small>
                    </div>
                    <div class="small text-muted mt-1">${last}</div>
                </div>
            `;
        });
        list.innerHTML = html;
    }

    async function selectChat(phone) {
        currentPhone = phone;
        document.getElementById('chatTitle').innerText = phone;
        loadConversations(); // highlight active
        loadMessages();
    }

    async function loadMessages() {
        if(!currentPhone) return;
        const res = await fetch(`/api/messages?phone=${encodeURIComponent(currentPhone)}`);
        const msgs = await res.json();
        const area = document.getElementById('messagesArea');
        
        let html = '';
        msgs.forEach(m => {
            // FIX: undefined check
            const time = m.time_str || new Date(m.timestamp * 1000).toLocaleTimeString().slice(0,5);
            let extra = '';
            if (m.audio_url) extra = `<br><audio controls src="${m.audio_url}"></audio>`;
            
            html += `
                <div class="msg ${m.role}">
                    ${m.content}
                    ${extra}
                    <div class="text-end opacity-50" style="font-size:0.6em">${time}</div>
                </div>
            `;
        });
        area.innerHTML = html;
        area.scrollTop = area.scrollHeight;
    }

    async function sendMessage() {
        const inp = document.getElementById('msgInput');
        if(!inp.value.trim() || !currentPhone) return;
        
        await fetch('/api/send', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ phone: currentPhone, message: inp.value })
        });
        inp.value = '';
        loadMessages();
    }

    // --- SETTINGS LOGIC ---
    async function loadSettings() {
        const res = await fetch('/api/settings');
        const data = await res.json();
        document.getElementById('jsonEditor').value = JSON.stringify(data, null, 4);
    }

    async function saveSettings() {
        try {
            const json = JSON.parse(document.getElementById('jsonEditor').value);
            await fetch('/api/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(json)
            });
            alert('Ayarlar kaydedildi ve bota yüklendi!');
        } catch(e) {
            alert('JSON Hatası! Lütfen formatı kontrol edin.');
        }
    }

    // Auto Refresh
    setInterval(() => {
        if(document.getElementById('tab-chat').style.display !== 'none') {
            loadConversations();
            if(currentPhone) loadMessages();
        }
    }, 3000);

    loadConversations();
</script>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
""")

setup_files()

# --- HELPER ---
def get_time_str(): return datetime.datetime.now().strftime("%H:%M")

def update_memory(phone, role, content, meta_update=None, audio_url=None):
    if phone not in CONVERSATIONS:
        CONVERSATIONS[phone] = {"messages": [], "metadata": {"status": "YENİ", "summary": "Görüşme başladı..."}}
    msg_obj = {"role": role, "content": content, "timestamp": time.time(), "time_str": get_time_str()}
    if audio_url: msg_obj["audio_url"] = audio_url
    CONVERSATIONS[phone]["messages"].append(msg_obj)
    if meta_update:
        CONVERSATIONS[phone]["metadata"].update(meta_update)
        CONVERSATIONS[phone]["metadata"]["last_update"] = get_time_str()
    save_conversations()

# --- ROUTES & API ---
# 1. /dashboard -> CRM PANEL (Restored)
@app.route("/dashboard")
def dsh():
    stats = {"total": len(CONVERSATIONS), "hot": 0, "follow": 0}
    for k,v in CONVERSATIONS.items():
        if v["metadata"].get("status")=="HOT": stats["hot"]+=1
        elif v["metadata"].get("status")=="WARM": stats["follow"]+=1
    return render_template("dashboard.html", memory=CONVERSATIONS, stats=stats)

# 2. Redirect root to dashboard (Sales user landing)
@app.route("/")
def idx(): 
    return redirect("/dashboard")

# 3. Super Admin
@app.route("/super-admin")
def admin_panel():
    if not session.get("admin"): return redirect("/login")
    return render_template("super_admin.html")

# 4. Auth
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/super-admin")
        flash("Hatalı şifre!")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("admin", None)
    return redirect("/login")

# --- API ---
@app.route("/api/conversations")
def api_conversations():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    return jsonify(CONVERSATIONS)

@app.route("/api/messages")
def api_messages():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    phone = request.args.get("phone")
    if not phone or phone not in CONVERSATIONS: return jsonify([])
    return jsonify(CONVERSATIONS[phone]["messages"])

@app.route("/api/send", methods=["POST"])
def api_send():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    phone = data.get("phone")
    text = data.get("message")
    if phone and text:
        try:
            if twilio_client: twilio_client.messages.create(from_=TWILIO_PHONE_NUMBER, body=text, to=phone)
            update_memory(phone, "assistant", text)
            return jsonify({"status": "sent"})
        except Exception as e: return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Invalid data"}), 400

@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    if request.method == "POST":
        data = request.json
        if save_kb(data): return jsonify({"status": "saved"})
        else: return jsonify({"error": "Save failed"}), 500
    return jsonify(load_kb())

# --- WEBHOOK ---
@app.route("/webhook", methods=['POST'])
def webhook():
    # Always get fresh KB
    KB = load_kb()
    
    body = request.values.get('Body', '').strip()
    media = request.values.get('MediaUrl0')
    phone = request.values.get('From', '')

    is_voice_in = False
    # TRANSCRIPTION PLACEHOLDER (Twilio doesn't give transcript immediately in sync req. 
    # Proper way is async status callback, but here we do best effort or just logging voice msg)
    if media:
        is_voice_in = True
        user_in = "[SESLİ MESAJ - Dinlemek için Panele Bakınız]"
    else:
        user_in = body

    update_memory(phone, "user", user_in)
    
    triggers = ["ses", "konuş", "duymak", "söyle", "anlat", "dinle", "özetle", "sesli"]
    should_speak = is_voice_in or any(w in user_in.lower() for w in triggers)
    
    # Simple Sales Persona
    persona_prompt = f"""
    Sen {KB.get('identity', {}).get('name', 'Aura')}. Görevin: {KB.get('identity', {}).get('mission', 'Yardımcı olmak')}.
    Bilgi: {json.dumps(KB.get('hotel_info', {}), ensure_ascii=False)}
    Kurallar: {json.dumps(KB.get('sales_psychology', {}), ensure_ascii=False)}
    'CEVABIN SESLİ OKUNACAK.' if should_speak else 'WhatsApp formatında yaz.'
    """
    
    hist = CONVERSATIONS.get(phone, {}).get("messages", [])
    try: 
        messages = [{"role":"system","content":persona_prompt}] + [{"role":m["role"],"content":m["content"]} for m in hist[-6:]]
        ai_reply = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.7).choices[0].message.content
    except: ai_reply = "Sistem yoğun, hemen döneceğim."

    resp = MessagingResponse()
    audio_url = None
    
    # TTS
    if should_speak and client:
        try:
            fname = f"out_{int(time.time())}.mp3"
            path = os.path.join(AUDIO_DIR, fname)
            client.audio.speech.create(model="tts-1", voice="shimmer", input=ai_reply).stream_to_file(path)
            audio_url = url_for('static', filename=f'audio/{fname}', _external=True, _scheme='https')
            resp.message(ai_reply).media(audio_url)
        except: resp.message(ai_reply)
    else:
        resp.message(ai_reply)
        
    update_memory(phone, "assistant", ai_reply, audio_url=audio_url)
    return str(resp)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
