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

# --- DOSYA OLUŞTURMA (KURTARICI) ---
# Bu fonksiyon her başlatmada HTML dosyalarını garanti eder.
def setup_files():
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
            <div><span class="text-white me-3">Yönetici Paneli</span></div>
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
        setTimeout(function(){ window.location.reload(1); }, 5000);
    </script>
</body>
</html>""")

    detail_path = os.path.join(TEMPLATE_DIR, "conversation_detail.html")
    with open(detail_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html><head><title>Detay</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head><body><div class="container mt-5"><h1>Detay: {{ phone }}</h1>{% for msg in messages %}<div class="alert alert-secondary"><b>{{ msg.role }}:</b> {{ msg.content }}</div>{% endfor %}<a href="/dashboard" class="btn btn-primary">Geri</a></div></body></html>""")

setup_files()

# --- FLASK BAŞLATMA ---
load_dotenv()
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

# --- API ANAHTARLARI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

# İstemciler
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
try: twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
except: twilio_client = None

# --- VERİ VE HAFIZA ---
MEMORY_FILE = os.path.join(BASE_DIR, "conversations.json")
KNOWLEDGE_BASE_FILE = os.path.join(BASE_DIR, "knowledge_base.json")

def load_json(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
    except: return {}

def save_json(filepath, data):
    try:
        with open(filepath, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)
    except: pass

AKROPOL_FACTS = json.dumps(load_json(KNOWLEDGE_BASE_FILE), ensure_ascii=False, indent=2)

def get_memory():
    return load_json(MEMORY_FILE)

def update_memory_immediate(phone, role, content):
    data = get_memory()
    if phone not in data:
        data[phone] = {"messages": [], "metadata": {"status": "YENİ", "summary": "Görüşme başladı..."}}
    data[phone]["messages"].append({"role": role, "content": content, "timestamp": time.time()})
    save_json(MEMORY_FILE, data)

def update_analysis_result(phone, analysis):
    data = get_memory()
    if phone in data:
        data[phone]["metadata"] = analysis
        save_json(MEMORY_FILE, data)

# --- SES İŞLEMLERİ ---
def speech_to_text(media_url):
    try:
        if not client: return "(API Yok)"
        r = requests.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        if r.status_code != 200: r = requests.get(media_url)
        filename = f"input_{int(time.time())}.ogg"
        filepath = os.path.join(AUDIO_DIR, filename)
        with open(filepath, 'wb') as f: f.write(r.content)
        with open(filepath, "rb") as audio:
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio, language="tr")
        os.remove(filepath)
        return transcript.text
    except Exception as e: return f"(Ses Hatası: {e})"

def text_to_speech(text):
    try:
        if not client: return None
        filename = f"reply_{int(time.time())}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)
        response = client.audio.speech.create(model="tts-1", voice="shimmer", input=text)
        response.stream_to_file(filepath)
        return filename
    except Exception as e: return None

# --- ANALİZ ---
def background_analysis(phone, history):
    if not client: return
    recent = "\n".join([f"{m['role']}: {m['content']}" for m in history[-10:]])
    prompt = f"""
    Sen Akropol CRM Analistisin. Zaman: {datetime.datetime.now().strftime("%Y-%m-%d")}
    Çıktı JSON: {{"summary": "Özet", "status": "HOT/WARM/COLD", "follow_up_date": "YYYY-MM-DD"}}
    Konuşma: {recent}
    """
    try:
        res = client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "system", "content": prompt}],
            response_format={"type": "json_object"}
        )
        update_analysis_result(phone, json.loads(res.choices[0].message.content))
    except: pass

# --- WEBHOOK ---
@app.route("/webhook", methods=['POST'])
def webhook():
    incoming_msg = request.values.get('Body', '').strip()
    media_url = request.values.get('MediaUrl0')
    media_type = request.values.get('MediaContentType0', '')
    phone = request.values.get('From', '')

    is_voice = False
    if media_url and ('audio' in media_type or 'ogg' in media_type):
        is_voice = True
        user_text = f"[SESLİ]: {speech_to_text(media_url)}"
    else:
        user_text = incoming_msg

    update_memory_immediate(phone, "user", user_text)

    history = get_memory().get(phone, {}).get("messages", [])
    prompt = f"Sen Aura. Akropol Termal Asistanı. Bilgi: {AKROPOL_FACTS}. Kısa ve net ol."
    msgs = [{"role": "system", "content": prompt}] + [{"role": m["role"], "content": m["content"]} for m in history[-6:]]
    
    try: ai_res = client.chat.completions.create(model="gpt-4o", messages=msgs).choices[0].message.content
    except: ai_res = "Sistem yoğun."

    update_memory_immediate(phone, "assistant", ai_res)
    threading.Thread(target=background_analysis, args=(phone, history + [{"role": "assistant", "content": ai_res}])).start()

    resp = MessagingResponse()
    if is_voice:
        audio_file = text_to_speech(ai_res)
        if audio_file:
            msg = resp.message(ai_res)
            msg.media(url_for('static', filename=f'audio/{audio_file}', _external=True))
        else: resp.message(ai_res)
    else: resp.message(ai_res)

    return str(resp)

# --- DASHBOARD & HATA YAKALAMA ---
@app.route("/dashboard")
def dashboard():
    try:
        data = get_memory()
        stats = {"total": len(data), "hot": 0, "follow": 0}
        for p, v in data.items():
            s = v.get("metadata", {}).get("status", "")
            if s == "HOT": stats["hot"] += 1
            if s == "WARM": stats["follow"] += 1
        return render_template("dashboard.html", memory=data, stats=stats)
    except Exception as e:
        # HATA OLURSA EKRANA BAS
        return f"<h1>Dashboard Hatası</h1><p>{str(e)}</p><pre>{traceback.format_exc()}</pre>", 500

@app.route("/dashboard/<path:phone>")
def detail(phone):
    try:
        data = get_memory().get(phone, {})
        return render_template("conversation_detail.html", phone=phone, messages=data.get("messages", []))
    except Exception as e:
        return f"<h1>Detay Hatası</h1><p>{str(e)}</p>", 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
