import os
import json
import time
import requests
import datetime
import dateparser
import threading
from functools import wraps
from flask import Flask, request, send_from_directory, url_for, render_template, Response, redirect
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from openai import OpenAI
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import traceback

# --- CONFIG & PATHS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
AUDIO_DIR = os.path.join(STATIC_DIR, "audio")

# --- AUTO SETUP ---
def setup_files():
    if not os.path.exists(TEMPLATE_DIR): os.makedirs(TEMPLATE_DIR)
    if not os.path.exists(AUDIO_DIR): os.makedirs(AUDIO_DIR)

    # Dashboard.html
    dash_path = os.path.join(TEMPLATE_DIR, "dashboard.html")
    with open(dash_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Akropol AI (True Intelligence)</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        body { background-color: #f4f6f9; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .card { border: none; box-shadow: 0 4px 6px rgba(0,0,0,0.1); border-radius: 12px; margin-bottom: 20px; }
        .status-HOT { background-color: #ffebee; color: #c62828; font-weight: bold; padding: 5px 12px; border-radius: 20px; font-size: 0.9em; }
        .status-COLD { background-color: #e3f2fd; color: #1565c0; font-weight: bold; padding: 5px 12px; border-radius: 20px; font-size: 0.9em; }
        .status-WARM { background-color: #fff3e0; color: #ef6c00; font-weight: bold; padding: 5px 12px; border-radius: 20px; font-size: 0.9em; }
        .navbar { background: linear-gradient(90deg, #1a237e, #0d47a1); }
    </style>
</head>
<body>
    <nav class="navbar navbar-dark mb-4">
        <div class="container-fluid">
            <span class="navbar-brand mb-0 h1"><i class="fas fa-brain me-2"></i>Akropol AI</span>
            <div>
                <span class="text-white me-3">Yönetici Paneli</span>
                <a href="/super-admin" class="btn btn-sm btn-danger"><i class="fas fa-lock me-1"></i> Admin</a>
            </div>
        </div>
    </nav>
    <div class="container">
        <div class="row mb-4">
            <div class="col-md-4"><div class="card text-white bg-primary h-100"><div class="card-body"><h6>Toplam Kişi</h6><h2 class="display-4">{{ stats.total }}</h2></div></div></div>
            <div class="col-md-4"><div class="card text-white bg-danger h-100"><div class="card-body"><h6>🔥 Sıcak Fırsatlar</h6><h2 class="display-4">{{ stats.hot }}</h2></div></div></div>
            <div class="col-md-4"><div class="card text-white bg-warning h-100"><div class="card-body"><h6>📅 Takip Edilecekler</h6><h2 class="display-4">{{ stats.follow }}</h2></div></div></div>
        </div>
        <div class="card">
            <div class="card-header bg-white py-3"><h5 class="mb-0">AI Analiz Raporu</h5></div>
            <div class="card-body">
                <div class="table-responsive">
                    <table class="table table-hover align-middle">
                        <thead class="table-light"><tr><th>Telefon</th><th>AI Özeti</th><th>Durum</th><th>Takip Tarihi</th><th>Aksiyon</th></tr></thead>
                        <tbody>
                            {% for phone, data in memory.items() %}
                            {% set meta = data.get('metadata', {}) %} 
                            <tr>
                                <td>{{ phone }}</td>
                                <td style="max-width: 450px;"><small class="text-muted">{{ meta.get('summary', 'Analiz bekleniyor...') }}</small></td>
                                <td><span class="status-{{ meta.get('status', 'COLD') }}">{{ meta.get('status', 'Yeni') }}</span></td>
                                <td>{{ meta.get('follow_up_date', '-') }}</td>
                                <td><a href="/dashboard/{{ phone }}" class="btn btn-sm btn-outline-primary">Detay</a></td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
    <script>
        setTimeout(function(){ window.location.reload(1); }, 10000);
    </script>
</body>
</html>""")

    admin_path = os.path.join(TEMPLATE_DIR, "super_admin.html")
    if not os.path.exists(admin_path):
        with open(admin_path, "w", encoding="utf-8") as f: f.write("<html><body>Admin</body></html>")

    detail_path = os.path.join(TEMPLATE_DIR, "conversation_detail.html")
    if not os.path.exists(detail_path):
        with open(detail_path, "w", encoding="utf-8") as f: f.write("<html><body>Detay</body></html>")

setup_files()

# --- FLASK SETUP ---
load_dotenv()
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

if OPENAI_API_KEY: client = OpenAI(api_key=OPENAI_API_KEY)
else: client = None

try: twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
except: twilio_client = None

# --- FILES ---
MEMORY_FILE = os.path.join(BASE_DIR, "conversations.json")
KNOWLEDGE_BASE_FILE = os.path.join(BASE_DIR, "knowledge_base.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

def load_json_file(filepath, default):
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default

AKROPOL_FACTS = json.dumps(load_json_file(KNOWLEDGE_BASE_FILE, {}), ensure_ascii=False, indent=2)

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except: return {}
    return {}

def save_memory(data):
    try:
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)
    except: pass

def get_conversation_history(phone_number):
    memory = load_memory()
    return memory.get(phone_number, {}).get("messages", [])

# --- AI LOGIC ---
class LeadAnalyzer:
    @staticmethod
    def analyze(phone, history):
        if not client: return {"summary": "API Key Eksik", "status": "COLD"}
        recent_convo = "\n".join([f"{m['role']}: {m['content']}" for m in history[-10:]])
        system_prompt = f"""
        Sen Akropol CRM Analistisin. Görev: Konuşmayı analiz et, JSON dön.
        Zaman: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
        ÇIKTI: {{"summary": "Özet", "status": "HOT/WARM/COLD", "follow_up_date": "YYYY-MM-DD HH:MM"}}
        """
        try:
            completion = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": recent_convo}],
                response_format={ "type": "json_object" }
            )
            return json.loads(completion.choices[0].message.content)
        except: return {"summary": "Analiz Hata", "status": "COLD"}

analyzer = LeadAnalyzer()

def handle_audio_message(media_url):
    """Ses dosyasını indirip metne çevirir (Whisper)."""
    try:
        if not client: return "Ses analizi için API Key eksik."
        
        # 1. Dosyayı indir (Twilio Basic Auth ile)
        # Twilio bazen medya indirmek için auth ister, bazen istemez. Biz requests ile deneyelim.
        # Not: Render ortamında güvenli indirme için user/pass gerekebilir ama public url ise direkt iner.
        response = requests.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        if response.status_code != 200:
            # Belki redirect vardır
            response = requests.get(media_url) 
        
        if response.status_code == 200:
            filename = f"temp_audio_{int(time.time())}.ogg"
            filepath = os.path.join(AUDIO_DIR, filename)
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            # 2. Whisper ile Transcribe et
            with open(filepath, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model="whisper-1", 
                    file=audio_file,
                    language="tr"
                )
            
            # Temizlik
            os.remove(filepath)
            return transcription.text
        else:
            return "(Ses dosyası indirilemedi)"
    except Exception as e:
        print(f"Ses Hatası: {e}")
        return "(Ses anlaşılamadı)"

def background_process(phone, history, user_msg, ai_reply):
    try:
        full_history = history + [{"role": "user", "content": user_msg}, {"role": "assistant", "content": ai_reply}]
        analysis_result = analyzer.analyze(phone, full_history)
        memory = load_memory()
        if phone not in memory: memory[phone] = {"messages": [], "metadata": {}}
        memory[phone]["messages"].append({"role": "user", "content": user_msg, "timestamp": time.time()})
        memory[phone]["messages"].append({"role": "assistant", "content": ai_reply, "timestamp": time.time()})
        memory[phone]["metadata"] = analysis_result
        save_memory(memory)
    except: pass

# --- ROUTES ---
@app.route("/")
def home():
    return "Akropol AI v4.0 (Voice Enabled) Running..."

@app.route("/webhook", methods=['POST'])
def webhook():
    if not client: return str(MessagingResponse().message("Bakımdayız."))
    
    # Gelen Mesajı Al (Text veya Ses)
    incoming_msg = request.values.get('Body', '').strip()
    media_url = request.values.get('MediaUrl0', None)
    media_type = request.values.get('MediaContentType0', '')
    from_number = request.values.get('From', '')
    
    # Ses Kontrolü
    start_msg = ""
    if media_url and ('audio' in media_type or 'ogg' in media_type):
        transcript = handle_audio_message(media_url)
        incoming_msg = f"[SESLİ MESAJ]: {transcript}"
        start_msg = f"🎤 (Sesini dinledim: \"{transcript}\")\n\n"
    elif not incoming_msg:
        incoming_msg = "(Boş Mesaj)"

    # AI Cevabı Hazırla
    history = get_conversation_history(from_number)
    system_prompt = f"""
    Sen **Akropol Termal**'in profesyonel, sıcakkanlı ve ikna edici Satış Danışmanısın.
    İSMİN: Aura (Akropol Asistanı).
    GÖREVİN: Müşteriyi tesise davet etmek ve satış yapmak.
    BİLGİLER: {AKROPOL_FACTS}
    TONU: Samimi, enerjik, güven verici. Emojileri bol kullan ama cıvık olma.
    KURALLAR:
    - Asla "Ben bir yapay zekayım" deme.
    - Kısa ve öz cevaplar ver (WhatsApp için uygun olsun).
    - Fiyat sorulursa hemen söyleme, önce tarih ve kişi sayısı isteyip ilgiyi artır.
    """
    
    msgs = [{"role": "system", "content": system_prompt}] + \
           [{"role": m["role"], "content": m["content"]} for m in history[-6:]] + \
           [{"role": "user", "content": incoming_msg}]
           
    try:
        completion = client.chat.completions.create(model="gpt-4o", messages=msgs)
        ai_response_text = completion.choices[0].message.content
    except:
        ai_response_text = "Şu an cevap veremiyorum, lütfen arayınız."

    final_response = start_msg + ai_response_text

    # Arka Plan İşlemi
    thread = threading.Thread(target=background_process, args=(from_number, history, incoming_msg, final_response))
    thread.start()

    # Twilio Yanıtı
    resp = MessagingResponse()
    resp.message(final_response)
    return str(resp)

@app.route("/dashboard")
def dashboard():
    try:
        memory = load_memory()
        stats = {"total": len(memory), "hot": 0, "follow": 0}
        for p, data in memory.items():
            meta = data.get("metadata", {})
            if meta.get("status") == "HOT": stats["hot"] += 1
            if meta.get("status") == "WARM": stats["follow"] += 1
        return render_template("dashboard.html", memory=memory, stats=stats)
    except: return "Dashboard Hatası", 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
