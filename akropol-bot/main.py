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

# --- PERSISTENCE (VERİ KALICILIĞI) ---
def load_conversations():
    try:
        if os.path.exists(CONVERSATIONS_FILE):
            with open(CONVERSATIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Load Error: {e}")
    return {}

def save_conversations():
    try:
        with open(CONVERSATIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(CONVERSATIONS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Save error: {e}")

CONVERSATIONS = load_conversations()

def load_kb():
    try:
        if os.path.exists(KNOWLEDGE_BASE_FILE):
            with open(KNOWLEDGE_BASE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"KB Error: {e}") 
    return {}

KB = load_kb()

# --- HTML ŞABLONLARI (GOD MODE UPDATE) ---
def setup_files():
    # 1. SUPER ADMIN V8.0 (GOD MODE)
    # Tek sayfa uygulaması (SPA) mantığıyla çalışan, AJAX ile yenilenen, ses oynatabilen modern arayüz
    admin_path = os.path.join(TEMPLATE_DIR, "super_admin.html")
    with open(admin_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aura OS - God Mode</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        :root { --bg-dark: #0f172a; --sidebar-bg: #1e293b; --active-item: #334155; --text-main: #f8fafc; --text-sec: #94a3b8; --accent: #38bdf8; --msg-user: #0ea5e9; --msg-bot: #334155; }
        body { background-color: var(--bg-dark); color: var(--text-main); font-family: 'Inter', sans-serif; height: 100vh; overflow: hidden; }
        
        /* Layout */
        .app-container { display: flex; height: 100vh; }
        .sidebar { width: 350px; background: var(--sidebar-bg); border-right: 1px solid #334155; display: flex; flex-direction: column; }
        .chat-area { flex: 1; display: flex; flex-direction: column; background: var(--bg-dark); position: relative; }
        
        /* Sidebar Components */
        .sidebar-header { padding: 20px; border-bottom: 1px solid #334155; display: flex; align-items: center; justify-content: space-between; }
        .search-box { padding: 10px 20px; }
        .search-input { background: var(--bg-dark); border: 1px solid #334155; color: white; border-radius: 8px; padding: 8px 15px; width: 100%; }
        .conversation-list { flex: 1; overflow-y: auto; }
        .conv-item { padding: 15px 20px; border-bottom: 1px solid #334155; cursor: pointer; transition: 0.2s; display: flex; align-items: center; gap: 15px; }
        .conv-item:hover { background: var(--active-item); }
        .conv-item.active { background: var(--active-item); border-left: 3px solid var(--accent); }
        .avatar { width: 45px; height: 45px; background: #475569; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 1.2rem; }
        .conv-info { flex: 1; min-width: 0; }
        .conv-name { font-weight: 600; margin-bottom: 2px; }
        .conv-preview { color: var(--text-sec); font-size: 0.85rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .conv-meta { display: flex; flex-direction: column; align-items: flex-end; font-size: 0.75rem; color: var(--text-sec); }
        
        /* Chat Area Components */
        .chat-header { padding: 15px 30px; background: rgba(30, 41, 59, 0.8); backdrop-filter: blur(10px); border-bottom: 1px solid #334155; display: flex; align-items: center; justify-content: space-between; z-index: 10; }
        .chat-messages { flex: 1; overflow-y: auto; padding: 20px 30px; display: flex; flex-direction: column; gap: 15px; }
        .message { max-width: 70%; padding: 12px 16px; border-radius: 12px; position: relative; line-height: 1.5; font-size: 0.95rem; }
        .message.user { align-self: flex-start; background: var(--msg-bot); border-top-left-radius: 2px; }
        .message.assistant { align-self: flex-end; background: var(--msg-user); color: white; border-top-right-radius: 2px; }
        .message.system { align-self: center; background: rgba(255,255,255,0.05); font-size: 0.8rem; color: var(--text-sec); padding: 5px 15px; border-radius: 20px; }
        .msg-time { font-size: 0.7rem; opacity: 0.7; margin-top: 5px; text-align: right; display: block; }
        
        /* Input Area */
        .chat-input-area { padding: 20px; background: var(--sidebar-bg); border-top: 1px solid #334155; }
        .input-group { background: var(--bg-dark); border-radius: 12px; padding: 5px; border: 1px solid #334155; }
        .form-control { background: transparent; border: none; color: white; padding: 10px 15px; }
        .form-control:focus { background: transparent; color: white; box-shadow: none; }
        .btn-send { color: var(--accent); border: none; background: transparent; padding: 0 15px; }
        
        /* Audio Player */
        .audio-player { background: rgba(0,0,0,0.2); border-radius: 8px; padding: 5px; margin-top: 5px; width: 100%; }
        
        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #475569; border-radius: 3px; }
    </style>
</head>
<body>

<div class="app-container">
    <!-- SIDEBAR -->
    <div class="sidebar">
        <div class="sidebar-header">
            <h5 class="mb-0"><i class="fas fa-bolt text-warning me-2"></i>Aura OS</h5>
            <a href="/logout" class="btn btn-sm btn-outline-danger"><i class="fas fa-power-off"></i></a>
        </div>
        <div class="search-box">
            <input type="text" class="search-input" placeholder="Görüşme Ara..." id="searchInput">
        </div>
        <div class="conversation-list" id="convList">
            <!-- JS ile doldurulacak -->
            <div class="text-center mt-5 text-muted"><i class="fas fa-spinner fa-spin"></i> Yükleniyor...</div>
        </div>
    </div>

    <!-- CHAT AREA -->
    <div class="chat-area">
        <div class="chat-header" id="chatHeader" style="display:none;">
            <div class="d-flex align-items-center gap-3">
                <div class="avatar" id="headerAvatar">#</div>
                <div>
                    <h6 class="mb-0" id="headerName">Seçili Görüşme Yok</h6>
                    <small class="text-success"><i class="fas fa-circle" style="font-size: 8px;"></i> Online</small>
                </div>
            </div>
            <button class="btn btn-sm btn-outline-light" onclick="refreshChat()"><i class="fas fa-sync-alt"></i></button>
        </div>

        <div class="chat-messages" id="chatMessages">
            <div class="h-100 d-flex align-items-center justify-content-center flex-column text-muted">
                <i class="fab fa-whatsapp fa-4x mb-3 opacity-25"></i>
                <p>Bir görüşme seçin</p>
            </div>
        </div>

        <div class="chat-input-area" id="inputArea" style="display:none;">
            <form id="sendForm" onsubmit="event.preventDefault(); sendMessage();">
                <div class="input-group d-flex align-items-center">
                    <input type="text" class="form-control" id="messageInput" placeholder="Mesaj yaz..." autocomplete="off">
                    <button type="submit" class="btn-send"><i class="fas fa-paper-plane"></i></button>
                </div>
            </form>
        </div>
    </div>
</div>

<script>
    let currentPhone = null;
    let lastMsgCount = 0;

    // --- CORE FUNCTIONS ---
    async function fetchConversations() {
        try {
            const res = await fetch('/api/conversations');
            const data = await res.json();
            renderList(data);
        } catch (e) {
            console.error("Connection Error", e);
        }
    }

    function renderList(data) {
        const list = document.getElementById('convList');
        // Sadece liste boşsa veya yeni veri varsa güncellemek daha iyi olabilir ama
        // şimdilik basitlik için her seferinde temizleyip yazıyoruz (v8.1'de diff eklenebilir)
        // Ancak seçili olanı korumak için innerHTML'i dikkatli değiştirmeliyiz.
        // Hızlı çözüm: String building.
        
        let html = '';
        const sortedKeys = Object.keys(data).sort((a,b) => 
            data[b].messages[data[b].messages.length-1]?.timestamp - data[a].messages[data[a].messages.length-1]?.timestamp
        );

        if (sortedKeys.length === 0) {
            list.innerHTML = '<div class="text-center mt-5 text-muted">Henüz görüşme yok.</div>';
            return;
        }

        sortedKeys.forEach(phone => {
            const conv = data[phone];
            const lastMsg = conv.messages[conv.messages.length - 1];
            const content = lastMsg ? (lastMsg.content.includes('[SESLİ') ? '🎤 Sesli Mesaj' : lastMsg.content) : '...';
            const time = lastMsg ? lastMsg.time_str : '';
            const activeClass = phone === currentPhone ? 'active' : '';

            html += `
                <div class="conv-item ${activeClass}" onclick="selectChat('${phone}')">
                    <div class="avatar">${phone.slice(-2)}</div>
                    <div class="conv-info">
                        <div class="d-flex justify-content-between">
                            <div class="conv-name">${phone}</div>
                            <span class="conv-meta">${time}</span>
                        </div>
                        <div class="conv-preview">${content}</div>
                    </div>
                </div>
            `;
        });
        
        // Sadece içerik değiştiyse güncelle (basit bir check)
        if(list.getAttribute('data-hash') !== JSON.stringify(sortedKeys)) {
           list.innerHTML = html;
           list.setAttribute('data-hash', JSON.stringify(sortedKeys));
        }
    }

    async function selectChat(phone) {
        currentPhone = phone;
        document.getElementById('chatHeader').style.display = 'flex';
        document.getElementById('inputArea').style.display = 'block';
        document.getElementById('headerName').innerText = phone;
        document.getElementById('headerAvatar').innerText = phone.slice(-2);
        
        // Highlight active item manually to avoid re-render wait
        document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
        // (Gerçek highlight fetchConversations render edince gelecek)
        
        await loadMessages();
        fetchConversations(); // Update list ui
    }

    async function loadMessages() {
        if (!currentPhone) return;
        const res = await fetch(`/api/messages?phone=${encodeURIComponent(currentPhone)}`);
        const msgs = await res.json();
        
        const container = document.getElementById('chatMessages');
        let html = '';
        
        msgs.forEach(msg => {
            let content = msg.content;
            
            // Audio Player Check
            // Format: [SESLİ MESAJ]: transcript (URL: /static/...) - Eğer biz böyle formatlarsak
            // Ancak backend sadece metin tutuyor. URL'yi content içinde mi tutuyoruz?
            // Hayır, get_tts_url sadece anlık dönüyor.
            // MP3 dosyalarını kaydedersek player koyabiliriz.
            // Şimdilik "url" property'si varsa oynatalım.
            
            let extra = '';
            if(msg.role === 'assistant' && msg.audio_url) {
                extra = `<audio controls class="audio-player" src="${msg.audio_url}"></audio>`;
            }
            // User voices are transcript only for now unless we save url in memory
            
            html += `
                <div class="message ${msg.role}">
                    ${content}
                    ${extra}
                    <span class="msg-time">${msg.time_str}</span>
                </div>
            `;
        });

        // Scroll only if new messages arrived
        if (msgs.length !== lastMsgCount) {
             container.innerHTML = html;
             container.scrollTop = container.scrollHeight;
             lastMsgCount = msgs.length;
        }
    }

    async function sendMessage() {
        const input = document.getElementById('messageInput');
        const text = input.value.trim();
        if (!text || !currentPhone) return;

        input.value = ''; // Clear immediately
        
        // Optimistic UI
        const container = document.getElementById('chatMessages');
        container.innerHTML += `
            <div class="message assistant" style="opacity:0.7">
                ${text} <i class="fas fa-clock ms-1"></i>
                <span class="msg-time">Gönderiliyor...</span>
            </div>
        `;
        container.scrollTop = container.scrollHeight;

        try {
            await fetch('/api/send', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ phone: currentPhone, message: text })
            });
            loadMessages(); // Refresh real state
        } catch (e) {
            alert("Gönderim hatası!");
        }
    }

    // --- AUTO REFRESH LOOP ---
    setInterval(() => {
        fetchConversations();
        if(currentPhone) loadMessages();
    }, 3000); // 3 saniyede bir güncelle

    // Initial Load
    fetchConversations();

</script>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
""")

    # 4. Login ve Diğerleri için basit dosyalar
    with open(os.path.join(TEMPLATE_DIR, "login.html"), "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html lang="tr"><head><title>Admin Giriş</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{background:#0f172a;height:100vh;display:flex;align-items:center;justify-content:center}.login-card{width:100%;max-width:400px;background:#1e293b;border:1px solid #334155;border-radius:15px;color:white;padding:30px;box-shadow:0 10px 30px rgba(0,0,0,0.5)} input{background:#0f172a!important;border:1px solid #334155!important;color:white!important;}</style></head><body><div class="login-card"><h4 class="text-center mb-4">Aura OS<br><small class="text-muted" style="font-size:0.6em">Security Layer</small></h4><form method="POST"><div class="mb-3"><input type="password" name="password" class="form-control" placeholder="Erişim Anahtarı" required></div><button type="submit" class="btn btn-primary w-100">Giriş</button></form></div></body></html>""")
        
    dashboard_path = os.path.join(TEMPLATE_DIR, "dashboard.html")
    with open(dashboard_path, "w", encoding="utf-8") as f:
        f.write("<!-- Public dashboard deprecated in God Mode. Use /super-admin -->Redirecting...")

setup_files()

# --- HELPER ---
def get_time_str(): return datetime.datetime.now().strftime("%H:%M")

def update_memory(phone, role, content, meta_update=None, audio_url=None):
    if phone not in CONVERSATIONS:
        CONVERSATIONS[phone] = {"messages": [], "metadata": {"status": "YENİ", "summary": "Görüşme başladı..."}}
    
    msg_obj = {
        "role": role, 
        "content": content, 
        "timestamp": time.time(), 
        "time_str": get_time_str()
    }
    if audio_url: msg_obj["audio_url"] = audio_url
    
    CONVERSATIONS[phone]["messages"].append(msg_obj)
    
    if meta_update:
        CONVERSATIONS[phone]["metadata"].update(meta_update)
        CONVERSATIONS[phone]["metadata"]["last_update"] = get_time_str()
    
    save_conversations()

# --- TTS & STT ---
def get_transcript(url):
    try:
        if not client: return "Hata"
        r = requests.get(url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        if r.status_code != 200: r = requests.get(url)
        path = os.path.join(AUDIO_DIR, f"in_{int(time.time())}.ogg")
        with open(path, 'wb') as f: f.write(r.content)
        with open(path, "rb") as audio: txt = client.audio.transcriptions.create(model="whisper-1", file=audio, language="tr").text
        os.remove(path)
        return txt
    except: return "Ses anlaşılamadı"

def get_tts_url(text):
    try:
        if not client: return None
        fname = f"out_{int(time.time())}.mp3"
        path = os.path.join(AUDIO_DIR, fname)
        res = client.audio.speech.create(model="tts-1", voice="shimmer", input=text)
        res.stream_to_file(path)
        return url_for('static', filename=f'audio/{fname}', _external=True, _scheme='https')
    except Exception as e:
        print(f"TTS Error: {e}")
        return None

def analyze_bg(phone, history):
    if not client: return
    try:
        txt = "\n".join([f"{m['role']}: {m['content']}" for m in history[-8:]])
        prompt = f"Analiz et: {txt}. JSON: {{'summary': 'Özet', 'status': 'HOT/WARM/COLD'}}"
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role":"system","content":prompt}], response_format={"type":"json_object"})
        update_memory(phone, "system", "analiz", meta_update=json.loads(res.choices[0].message.content))
    except: pass

# --- WEBHOOK ---
@app.route("/webhook", methods=['POST'])
def webhook():
    # Always reload KB to get latest sales psychology
    KB = load_kb()
    
    body = request.values.get('Body', '').strip()
    media = request.values.get('MediaUrl0')
    phone = request.values.get('From', '')

    is_voice_in = False
    if media:
        is_voice_in = True
        user_in = f"[SESLİ MESAJ]: {get_transcript(media)}"
    else:
        user_in = body

    update_memory(phone, "user", user_in)
    
    triggers = ["ses", "konuş", "duymak", "söyle", "anlat", "dinle", "özetle", "sesli"]
    should_speak = is_voice_in or any(w in user_in.lower() for w in triggers)

    persona_prompt = f"""
    Sen {KB.get('identity', {}).get('name', 'Aura')}; {KB.get('identity', {}).get('tone', 'Doğal, samimi ve güven veren')} bir {KB.get('identity', {}).get('role', 'Turizm Asistanı')}.
    AMACIN: Misafiri {KB.get('identity', {}).get('mission', 'büyülemek ve satışa ikna etmek')}.
    
    OTEL BİLGİSİ:
    {json.dumps(KB.get('hotel_info', {}), ensure_ascii=False)}
    
    PSİKOLOJİK SATIŞ TAKTİKLERİ:
    1. {KB.get('sales_psychology', {}).get('approach', 'Özellik değil fayda sat')}
    2. {KB.get('sales_psychology', {}).get('handling_price', 'Fiyatı hemen söyleme, önce değeri parlat')}
    3. {KB.get('sales_psychology', {}).get('closing', 'Her mesajı bir soruyla bitir')}
    
    {'CEVABIN SESLİ OKUNACAK. Kısa, nefes payları olan, doğal konuşma dilinde yaz.' if should_speak else 'WhatsApp formatında kısa, net ve emojili yaz.'}
    """
    
    hist = CONVERSATIONS.get(phone, {}).get("messages", [])
    try: 
        messages = [{"role":"system","content":persona_prompt}] + [{"role":m["role"],"content":m["content"]} for m in hist[-8:]]
        ai_reply = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.7).choices[0].message.content
    except Exception as e: 
        print(f"OpenAI Error: {e}")
        ai_reply = "Şu an kısa bir yoğunluk var, hemen döneceğim size 🌸"

    
    resp = MessagingResponse()
    audio_url_public = None
    
    if should_speak:
        try:
            audio_url_public = get_tts_url(ai_reply)
            msg = resp.message(ai_reply)
            if audio_url_public: msg.media(audio_url_public)
        except: resp.message(ai_reply)
    else:
        resp.message(ai_reply)
        
    update_memory(phone, "assistant", ai_reply, audio_url=audio_url_public)
    threading.Thread(target=analyze_bg, args=(phone, hist+[{"role":"assistant","content":ai_reply}])).start()
    return str(resp)

# --- ROUTES & API ---
@app.route("/")
def idx(): return redirect("/super-admin")

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

@app.route("/super-admin")
def admin_panel():
    if not session.get("admin"): return redirect("/login")
    return render_template("super_admin.html") # Tüm veri API ile gelecek

# --- JSON API ROUTES ---
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
            if twilio_client:
                twilio_client.messages.create(from_=TWILIO_PHONE_NUMBER, body=text, to=phone)
            update_memory(phone, "assistant", text)
            return jsonify({"status": "sent"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Invalid data"}), 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
