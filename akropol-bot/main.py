import os
import json
import time
import requests
import datetime
import threading
import traceback
from flask import Flask, request, render_template, url_for
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from openai import OpenAI
from dotenv import load_dotenv

# --- AYARLAR VE YOLLAR ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
AUDIO_DIR = os.path.join(STATIC_DIR, "audio")

if not os.path.exists(TEMPLATE_DIR): os.makedirs(TEMPLATE_DIR)
if not os.path.exists(STATIC_DIR): os.makedirs(STATIC_DIR)
if not os.path.exists(AUDIO_DIR): os.makedirs(AUDIO_DIR)

# --- TASARIM VE DOSYA OLUŞTURMA (PREMIUM UI) ---
def setup_files():
    # 1. PREMIUM DASHBOARD
    dash_path = os.path.join(TEMPLATE_DIR, "dashboard.html")
    with open(dash_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Akropol AI Panel</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { background-color: #f8f9fa; font-family: 'Inter', sans-serif; color: #343a40; }
        .navbar { background: white; box-shadow: 0 2px 15px rgba(0,0,0,0.05); padding: 1rem 0; }
        .navbar-brand { font-weight: 700; color: #1a237e; letter-spacing: -0.5px; }
        .card { border: none; border-radius: 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.02); transition: transform 0.2s; }
        .card:hover { transform: translateY(-5px); }
        .stat-card { padding: 1.5rem; }
        .stat-icon { width: 48px; height: 48px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 24px; margin-bottom: 1rem; }
        .icon-blue { background: #e3f2fd; color: #1565c0; }
        .icon-red { background: #ffebee; color: #c62828; }
        .icon-orange { background: #fff3e0; color: #ef6c00; }
        .table-card { overflow: hidden; }
        .status-badge { padding: 6px 12px; border-radius: 20px; font-weight: 600; font-size: 0.85rem; }
        .badge-HOT { background: #ffebee; color: #c62828; }
        .badge-WARM { background: #fff3e0; color: #ef6c00; }
        .badge-COLD { background: #e3f2fd; color: #1565c0; }
        .badge-NEW { background: #e8f5e9; color: #2e7d32; }
        .btn-primary { background-color: #1a237e; border: none; padding: 8px 20px; border-radius: 8px; }
        .btn-primary:hover { background-color: #0d47a1; }
        .summary-text { font-size: 0.95rem; color: #6c757d; line-height: 1.5; }
        .refresh-loader { width: 100%; height: 3px; background: linear-gradient(90deg, #1a237e, #42a5f5); animation: load 3s infinite linear; }
        @keyframes load { 0% { width: 0%; } 100% { width: 100%; } }
    </style>
</head>
<body>
    <div class="refresh-loader"></div>
    <nav class="navbar mb-5">
        <div class="container">
            <span class="navbar-brand"><i class="fas fa-robot me-2" style="color:#1a237e;"></i>AKROPOL AI <span style="font-weight:400; color:#6c757d; font-size:0.9em;">| Akıllı Satış Asistanı</span></span>
            <a href="/super-admin" class="btn btn-outline-dark btn-sm"><i class="fas fa-shield-alt me-2"></i>Admin Paneli</a>
        </div>
    </nav>

    <div class="container">
        <!-- Stats Row -->
        <div class="row mb-5 g-4">
            <div class="col-md-4">
                <div class="card stat-card h-100">
                    <div class="stat-icon icon-blue"><i class="fas fa-users"></i></div>
                    <h6 class="text-muted text-uppercase fw-bold" style="font-size:0.8rem;">Toplam Görüşme</h6>
                    <h2 class="fw-bold mb-0">{{ stats.total }}</h2>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card stat-card h-100">
                    <div class="stat-icon icon-red"><i class="fas fa-fire"></i></div>
                    <h6 class="text-muted text-uppercase fw-bold" style="font-size:0.8rem;">Sıcak Fırsatlar</h6>
                    <h2 class="fw-bold mb-0">{{ stats.hot }}</h2>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card stat-card h-100">
                    <div class="stat-icon icon-orange"><i class="far fa-calendar-check"></i></div>
                    <h6 class="text-muted text-uppercase fw-bold" style="font-size:0.8rem;">Takip Gerektiren</h6>
                    <h2 class="fw-bold mb-0">{{ stats.follow }}</h2>
                </div>
            </div>
        </div>

        <!-- Main Content -->
        <div class="card table-card">
            <div class="card-header bg-white py-4 px-4 border-bottom">
                <h5 class="mb-0 fw-bold">📢 Canlı Görüşme Akışı</h5>
            </div>
            <div class="table-responsive">
                <table class="table table-hover align-middle mb-0">
                    <thead class="bg-light">
                        <tr>
                            <th class="ps-4 py-3">Müşteri</th>
                            <th class="py-3">Yapay Zeka Analizi</th>
                            <th class="py-3">Durum</th>
                            <th class="py-3">Son İşlem</th>
                            <th class="pe-4 py-3 text-end">Aksiyon</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for phone, data in memory.items() %}
                        {% set meta = data.get('metadata', {}) %} 
                        <tr>
                            <td class="ps-4 fw-bold">{{ phone }}</td>
                            <td style="max-width: 400px;">
                                <div class="summary-text">{{ meta.get('summary', 'Analiz yapılıyor... Lütfen bekleyin.') }}</div>
                            </td>
                            <td><span class="status-badge badge-{{ meta.get('status', 'NEW') }}">{{ meta.get('status', 'YENİ') }}</span></td>
                            <td class="text-muted small">{{ meta.get('last_update', 'Az önce') }}</td>
                            <td class="pe-4 text-end">
                                <a href="/dashboard/{{ phone }}" class="btn btn-primary btn-sm rounded-pill px-3">
                                    Detay <i class="fas fa-arrow-right ms-1"></i>
                                </a>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        // Sayfayı her 3 saniyede bir sessizce yenile
        setTimeout(function(){ window.location.reload(); }, 3000);
    </script>
</body>
</html>""")

    # 2. PREMIUM DETAIL PAGE
    detail_path = os.path.join(TEMPLATE_DIR, "conversation_detail.html")
    with open(detail_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html>
<html lang="tr">
<head>
    <title>Görüşme Detayı</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #f0f2f5; font-family: 'Inter', sans-serif; }
        .chat-container { max-width: 800px; margin: 30px auto; background: white; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.05); overflow: hidden; }
        .chat-header { background: #1a237e; color: white; padding: 20px; display: flex; align-items: center; justify-content: space-between; }
        .chat-body { padding: 30px; background: #e5ddd5; min-height: 500px; max-height: 700px; overflow-y: auto; }
        .message { margin-bottom: 15px; max-width: 75%; clear: both; position: relative; padding: 10px 15px; border-radius: 12px; font-size: 0.95rem; line-height: 1.4; }
        .msg-user { float: left; background: white; color: #333; border-top-left-radius: 0; box-shadow: 0 1px 2px rgba(0,0,0,0.1); }
        .msg-assistant { float: right; background: #dcf8c6; color: #111; border-top-right-radius: 0; box-shadow: 0 1px 2px rgba(0,0,0,0.1); }
        .msg-time { font-size: 0.7rem; color: #999; display: block; text-align: right; margin-top: 5px; }
        .voice-note { font-style: italic; color: #555; display: flex; align-items: center; gap: 8px; }
    </style>
</head>
<body>
    <div class="chat-container">
        <div class="chat-header">
            <div>
                <h5 class="mb-0"><i class="fab fa-whatsapp me-2"></i>{{ phone }}</h5>
                <small style="opacity:0.8;">Canlı Görüşme Kaydı</small>
            </div>
            <a href="/dashboard" class="btn btn-outline-light btn-sm rounded-pill">Kapat ✕</a>
        </div>
        <div class="chat-body" id="chatbox">
            {% for msg in messages %}
            <div class="message msg-{{ msg.role }}">
                {% if "[SESLİ MESAJ]" in msg.content %}
                    <div class="voice-note"><i class="fas fa-microphone"></i> {{ msg.content|replace("[SESLİ MESAJ]:", "") }}</div>
                {% else %}
                    {{ msg.content }}
                {% endif %}
            </div>
            {% endfor %}
            <!-- Clear float -->
            <div style="clear:both;"></div>
        </div>
    </div>
    <script>
        // Otomatik en alta kaydır
        var chatbox = document.getElementById("chatbox");
        chatbox.scrollTop = chatbox.scrollHeight;
    </script>
</body>
</html>""")

    # 3. SUPER ADMIN (Basic but functional)
    admin_path = os.path.join(TEMPLATE_DIR, "super_admin.html")
    with open(admin_path, "w", encoding="utf-8") as f:
        f.write("<html><body><h1>Süper Admin Paneli</h1><p>Yapım Aşamasında...</p><a href='/dashboard'>Geri</a></body></html>")

setup_files()

# --- FLASK ---
load_dotenv()
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

# KEYS
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
try: twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
except: twilio_client = None

MEMORY_FILE = os.path.join(BASE_DIR, "conversations.json")
KNOWLEDGE_BASE_FILE = os.path.join(BASE_DIR, "knowledge_base.json")

def load_json(fp): 
    try: return json.load(open(fp, 'r', encoding='utf-8'))
    except: return {}
def save_json(fp, d):
    try: json.dump(d, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    except: pass

facts = json.dumps(load_json(KNOWLEDGE_BASE_FILE), ensure_ascii=False)

def update_memory(phone, role, content, meta_update=None):
    data = load_json(MEMORY_FILE)
    if phone not in data: data[phone] = {"messages": [], "metadata": {"status": "YENİ", "summary": "Yeni görüşme..."}}
    
    data[phone]["messages"].append({"role": role, "content": content, "timestamp": time.time()})
    
    if meta_update:
        data[phone]["metadata"].update(meta_update)
        data[phone]["metadata"]["last_update"] = datetime.datetime.now().strftime("%H:%M")
        
    save_json(MEMORY_FILE, data)

# --- SERVICES ---
def get_transcript(url):
    try:
        if not client: return "Hata"
        r = requests.get(url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        if r.status_code != 200: r = requests.get(url)
        
        path = os.path.join(AUDIO_DIR, f"in_{int(time.time())}.ogg")
        with open(path, 'wb') as f: f.write(r.content)
        
        with open(path, "rb") as audio:
            txt = client.audio.transcriptions.create(model="whisper-1", file=audio, language="tr").text
        os.remove(path)
        return txt
    except: return "Ses anlaşılamadı"

def get_tts(text):
    try:
        if not client: return None
        path = os.path.join(AUDIO_DIR, f"out_{int(time.time())}.mp3")
        res = client.audio.speech.create(model="tts-1", voice="shimmer", input=text)
        res.stream_to_file(path)
        return os.path.basename(path)
    except: return None

def analyze_bg(phone, history):
    time.sleep(1) # Cevabın gitmesini bekle
    try:
        convo = "\n".join([f"{m['role']}: {m['content']}" for m in history[-8:]])
        prompt = f"""Konuşmayı analiz et:
        Konuşma: {convo}
        JSON: {{"summary": "Kısa durum özeti", "status": "HOT/WARM/COLD"}}"""
        
        res = client.chat.completions.create(model="gpt-4o", messages=[{"role":"system","content":prompt}], response_format={"type":"json_object"})
        meta = json.loads(res.choices[0].message.content)
        update_memory(phone, "system", "analiz", meta_update=meta)
    except: pass

@app.route("/webhook", methods=['POST'])
def webhook():
    body = request.values.get('Body', '').strip()
    media = request.values.get('MediaUrl0')
    phone = request.values.get('From', '')
    
    # 1. GİRDİ
    is_voice = False
    if media:
        is_voice = True
        user_in = f"[SESLİ MESAJ]: {get_transcript(media)}"
    else:
        user_in = body
        
    update_memory(phone, "user", user_in)
    
    # 2. ÜRETİM (PROMPT FIX: Konuşabileceğini Söyle!)
    history = load_json(MEMORY_FILE).get(phone, {}).get("messages", [])
    system_prompt = f"""
    Sen Aura, Akropol Termal'in sesli asistanısın.
    ÖNEMLİ: Kullanıcı seninle SESLİ konuşursa, cevabın SES dosyası olarak gidecek.
    O yüzden ASLA "ben yazılı bir modelim" veya "ses özelliğim yok" deme.
    Sanki telefonda konuşuyormuş gibi canlı, samimi ve kısa cevaplar ver.
    Bilgiler: {facts}
    """
    
    msgs = [{"role": "system", "content": system_prompt}] + [{"role": m["role"], "content": m["content"]} for m in history[-6:]]
    
    try: ai_reply = client.chat.completions.create(model="gpt-4o", messages=msgs).choices[0].message.content
    except: ai_reply = "Hata oluştu."
    
    update_memory(phone, "assistant", ai_reply)
    
    # 3. YANIT
    resp = MessagingResponse()
    
    if is_voice:
        audio = get_tts(ai_reply)
        if audio:
            msg = resp.message(ai_reply) # Altyazı
            msg.media(url_for('static', filename=f'audio/{audio}', _external=True))
        else:
            resp.message(ai_reply)
    else:
        resp.message(ai_reply)
        
    threading.Thread(target=analyze_bg, args=(phone, history)).start()
    return str(resp)

@app.route("/dashboard")
def dash():
    try:
        data = load_json(MEMORY_FILE)
        stats = {"total": len(data), "hot": 0, "follow": 0}
        for k,v in data.items():
            s = v["metadata"].get("status")
            if s == "HOT": stats["hot"] += 1
            if s == "WARM": stats["follow"] += 1
        return render_template("dashboard.html", memory=data, stats=stats)
    except Exception as e: return f"Hata: {e}"

@app.route("/dashboard/<path:phone>")
def det(phone):
    d = load_json(MEMORY_FILE).get(phone, {})
    return render_template("conversation_detail.html", phone=phone, messages=d.get("messages", []))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
