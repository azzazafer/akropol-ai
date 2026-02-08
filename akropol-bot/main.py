import os
import json
import time
import requests
import datetime
import threading
import logging
from flask import Flask, request, render_template, url_for
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

# Klasörleri oluştur
for d in [TEMPLATE_DIR, STATIC_DIR, AUDIO_DIR]:
    if not os.path.exists(d): os.makedirs(d)

# Logging (Hata Ayıklama İçin)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- IN-MEMORY VERİTABANI (RAM) ---
# Render disk sorununu aşmak için veriyi canlı hafızada tutuyoruz
CONVERSATIONS = {} 

# --- FLASK ---
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

# --- API CLIENTS ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
try: twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
except: twilio_client = None

# --- HTML ŞABLONLARI (PREMIUM CRM DESIGN) ---
def setup_templates():
    # 1. Dashboard
    with open(os.path.join(TEMPLATE_DIR, "dashboard.html"), "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Akropol AI CRM</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
        :root { --primary: #2c3e50; --accent: #e67e22; --bg: #f8f9fa; }
        body { background-color: var(--bg); font-family: 'Outfit', sans-serif; color: #333; }
        .navbar { background: white; padding: 1rem 0; box-shadow: 0 2px 10px rgba(0,0,0,0.03); }
        .brand-logo { font-weight: 600; font-size: 1.2rem; color: var(--primary); display: flex; align-items: center; gap: 10px; }
        .card { border: none; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.04); transition: .3s; background: white; }
        .stat-val { font-size: 2.5rem; font-weight: 600; color: var(--primary); }
        .stat-label { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 1px; color: #888; font-weight: 500; }
        .status-badge { padding: 5px 12px; border-radius: 30px; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.5px; }
        .bg-HOT { background: #ffe0e0; color: #d63031; }
        .bg-WARM { background: #fff3cd; color: #ff9f43; }
        .bg-COLD { background: #e2eafc; color: #0984e3; }
        .bg-NEW { background: #e8f5e9; color: #00b894; }
        .table-custom th { font-weight: 500; color: #888; text-transform: uppercase; font-size: 0.75rem; border-bottom: 2px solid #f1f1f1; }
        .avatar { width: 40px; height: 40px; background: #eee; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; color: #555; }
        .refresh-bar { height: 3px; background: var(--accent); width: 0%; animation: load 3s infinite linear; position: fixed; top: 0; left: 0; z-index: 9999; }
        @keyframes load { 0% { width: 0; } 100% { width: 100%; } }
    </style>
</head>
<body>
    <div class="refresh-bar"></div>
    <nav class="navbar mb-4">
        <div class="container">
            <div class="brand-logo"><i class="fas fa-layer-group text-warning"></i> AKROPOL AI</div>
            <a href="/super-admin" class="btn btn-sm btn-outline-dark">Yönetici Girişi</a>
        </div>
    </nav>
    <div class="container">
        <div class="row g-4 mb-4">
            <div class="col-md-4"><div class="card p-4"><div class="stat-label">Toplam Görüşme</div><div class="stat-val">{{ stats.total }}</div></div></div>
            <div class="col-md-4"><div class="card p-4"><div class="stat-label">Sıcak Potansiyel</div><div class="stat-val text-danger">{{ stats.hot }}</div></div></div>
            <div class="col-md-4"><div class="card p-4"><div class="stat-label">Takip Listesi</div><div class="stat-val text-warning">{{ stats.follow }}</div></div></div>
        </div>
        <div class="card">
            <div class="card-body p-0">
                <div class="table-responsive">
                    <table class="table table-custom table-hover align-middle mb-0">
                        <thead class="bg-light"><tr><th class="ps-4 py-3">Müşteri</th><th>Son Durum (AI Özeti)</th><th>Statü</th><th>Zaman</th><th></th></tr></thead>
                        <tbody>
                            {% for phone, data in memory.items() %}
                            {% set meta = data.get('metadata', {}) %} 
                            <tr>
                                <td class="ps-4"><div class="d-flex align-items-center gap-3"><div class="avatar">{{ phone[-2:] }}</div><span class="fw-bold">{{ phone }}</span></div></td>
                                <td class="text-muted small" style="max-width:350px;">{{ meta.get('summary', 'Analiz bekleniyor...') }}</td>
                                <td><span class="status-badge bg-{{ meta.get('status', 'NEW') }}">{{ meta.get('status', 'YENİ') }}</span></td>
                                <td class="small text-muted">{{ meta.get('last_update', '-') }}</td>
                                <td class="pe-4 text-end"><a href="/dashboard/{{ phone }}" class="btn btn-sm btn-light border px-3">İncele</a></td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
    <script>setTimeout(() => window.location.reload(), 3000);</script>
</body>
</html>""")

    # 2. Detail (Professional Timeline)
    with open(os.path.join(TEMPLATE_DIR, "conversation_detail.html"), "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html>
<html lang="tr">
<head>
    <title>Görüşme Detayı</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background: #f8f9fa; font-family: 'Segoe UI', sans-serif; }
        .timeline { position: relative; padding: 20px 0; }
        .timeline::before { content: ''; position: absolute; left: 50px; top: 0; bottom: 0; width: 2px; background: #e9ecef; }
        .msg-card { margin-bottom: 20px; border: none; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); margin-left: 80px; position: relative; }
        .msg-card::before { content: ''; position: absolute; left: -41px; top: 20px; width: 12px; height: 12px; border-radius: 50%; background: #ccc; border: 2px solid white; z-index: 2; }
        .role-user .msg-card::before { background: #3498db; }
        .role-assistant .msg-card::before { background: #e67e22; }
        .role-user .msg-card { background: white; border-left: 4px solid #3498db; }
        .role-assistant .msg-card { background: #fff8e1; border-left: 4px solid #e67e22; }
        .timestamp { position: absolute; left: -80px; top: 15px; font-size: 0.75rem; color: #999; width: 60px; text-align: right; }
        .voice-tag { font-size: 0.8rem; background: #eee; padding: 2px 8px; border-radius: 4px; display: inline-block; margin-bottom: 5px; }
    </style>
</head>
<body>
    <div class="container py-5" style="max-width: 800px;">
        <div class="d-flex justify-content-between align-items-center mb-5">
            <h4 class="mb-0 fw-bold"><i class="fas fa-history text-muted me-2"></i>{{ phone }}</h4>
            <a href="/dashboard" class="btn btn-outline-secondary btn-sm">Panele Dön</a>
        </div>
        <div class="timeline">
            {% for msg in messages %}
            <div class="position-relative role-{{ msg.role }}">
                <div class="timestamp">{{ msg.time_str }}</div>
                <div class="card msg-card p-3">
                    {% if "[SESLİ" in msg.content %}
                        <div class="voice-tag"><i class="fas fa-microphone"></i> Ses Kaydı</div>
                    {% endif %}
                    <div class="text-dark">{{ msg.content }}</div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
</body>
</html>""")
    
    # 3. Super Admin
    with open(os.path.join(TEMPLATE_DIR, "super_admin.html"), "w", encoding="utf-8") as f:
        f.write("<html><body><h1>Admin</h1></body></html>")

setup_templates()

# --- HELPER FUNCTIONS ---
def get_time_str():
    return datetime.datetime.now().strftime("%H:%M")

def update_memory(phone, role, content, meta_update=None):
    # Hafıza veritabanını güncelle
    if phone not in CONVERSATIONS:
        CONVERSATIONS[phone] = {"messages": [], "metadata": {"status": "YENİ", "summary": "Görüşme başlatıldı."}}
    
    # Mesajı ekle
    CONVERSATIONS[phone]["messages"].append({
        "role": role,
        "content": content,
        "timestamp": time.time(),
        "time_str": get_time_str()
    })
    
    # Metadata güncelle
    if meta_update:
        CONVERSATIONS[phone]["metadata"].update(meta_update)
        CONVERSATIONS[phone]["metadata"]["last_update"] = get_time_str()

# --- TTS & STT ---
def get_transcript(url):
    try:
        url_auth = requests.get(url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        if url_auth.status_code != 200: url_auth = requests.get(url)  # Fallback
        
        fname = f"in_{int(time.time())}.ogg"
        fpath = os.path.join(AUDIO_DIR, fname)
        with open(fpath, 'wb') as f: f.write(url_auth.content)
        
        if client:
            with open(fpath, "rb") as audio_file:
                res = client.audio.transcriptions.create(model="whisper-1", file=audio_file, language="tr")
            os.remove(fpath)
            return res.text
        return "(Ses dosyası alındı ama API yok)"
    except Exception as e:
        return f"(Ses Hatası: {str(e)})"

def get_tts_url(text):
    try:
        if not client: return None
        fname = f"out_{int(time.time())}.mp3"
        fpath = os.path.join(AUDIO_DIR, fname)
        
        res = client.audio.speech.create(model="tts-1", voice="shimmer", input=text)
        res.stream_to_file(fpath)
        
        # Render public URL
        return url_for('static', filename=f'audio/{fname}', _external=True)
    except: return None

# --- AI ANALİZ (Arka Plan) ---
def run_analysis(phone, history):
    if not client: return
    try:
        # Son 10 mesajı al
        txt = "\n".join([f"{m['role']}: {m['content']}" for m in history[-10:]])
        prompt = f"""
        Rol: CRM Analisti.
        Konuşma: {txt}
        Görev: Müşterinin durumunu analiz et.
        Çıktı JSON: {{"summary": "Tek cümlelik özet", "status": "HOT/WARM/COLD"}}
        """
        res = client.chat.completions.create(
            model="gpt-4o", messages=[{"role":"system", "content":prompt}], 
            response_format={"type": "json_object"}
        )
        result = json.loads(res.choices[0].message.content)
        
        if phone in CONVERSATIONS:
            CONVERSATIONS[phone]["metadata"].update(result)
            CONVERSATIONS[phone]["metadata"]["last_update"] = get_time_str()
    except Exception as e:
        print(f"Analiz Hatası: {e}")

# --- WEBHOOK (ANA BEYİN) ---
@app.route("/webhook", methods=['POST'])
def webhook():
    # 1. GİRDİLERİ AL
    body = request.values.get('Body', '').strip()
    media_url = request.values.get('MediaUrl0')
    phone = request.values.get('From', '')
    
    # 2. İŞLE (SES Mİ, YAZI MI?)
    is_voice_in = False
    if media_url: 
        is_voice_in = True
        user_input = f"[SESLİ MESAJ]: {get_transcript(media_url)}"
    else:
        user_input = body
    
    # 3. KAYDET (Kullanıcı)
    update_memory(phone, "user", user_input)
    
    # 4. YANIT HAZIRLA
    # Ses tetikleyiciler: Eğer kullanıcı ses attıysa VEYA içinde "ses", "konuş" geçiyorsa
    trigger_words = ["ses", "konuş", "duymak", "söyle", "anlat"]
    should_speak_back = is_voice_in or any(w in user_input.lower() for w in trigger_words)
    
    system_prompt = f"""
    Sen **Aura**, Akropol Termal'in profesyonel, elit ve çözüm odaklı asistanısın.
    Asla saçmalama, kısa ve net ol. Emojileri az ve yerinde kullan.
    Eğer kullanıcı 'kendini tanıt' derse, otelin ayrıcalıklarından bahset.
    {'NOT: Cevabın sesli okunacak, ona göre akıcı bir Türkçe kullan.' if should_speak_back else ''}
    """
    
    history = CONVERSATIONS.get(phone, {}).get("messages", [])
    msgs = [{"role": "system", "content": system_prompt}] + [{"role": m["role"], "content": m["content"]} for m in history[-6:]]
    
    try: ai_resp = client.chat.completions.create(model="gpt-4o", messages=msgs).choices[0].message.content
    except: ai_resp = "Sistem şu an meşgul, lütfen arayınız."
    
    # 5. KAYDET (Asistan)
    update_memory(phone, "assistant", ai_resp)
    
    # 6. YANIT GÖNDER
    tw_resp = MessagingResponse()
    
    if should_speak_back:
        audio_url = get_tts_url(ai_resp)
        if audio_url:
            msg = tw_resp.message(ai_resp) # Metni de göster
            msg.media(audio_url)
        else:
            tw_resp.message(ai_resp)
    else:
        tw_resp.message(ai_resp)
    
    # 7. ANALİZİ BAŞLAT
    threading.Thread(target=run_analysis, args=(phone, history + [{"role": "assistant", "content": ai_resp}])).start()
    
    return str(tw_resp)

# --- ROUTES ---
@app.route("/")
def index(): return "Akropol AI v7.0 (RamDB Active)"

@app.route("/dashboard")
def dash():
    # RAM'den oku (Hızlı)
    stats = {"total": len(CONVERSATIONS), "hot": 0, "follow": 0}
    for k, v in CONVERSATIONS.items():
        s = v["metadata"].get("status")
        if s == "HOT": stats["hot"] += 1
        if s == "WARM": stats["follow"] += 1
    return render_template("dashboard.html", memory=CONVERSATIONS, stats=stats)

@app.route("/dashboard/<path:phone>")
def detail(phone):
    if phone in CONVERSATIONS:
        return render_template("conversation_detail.html", phone=phone, messages=CONVERSATIONS[phone]["messages"])
    return "Görüşme bulunamadı"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
