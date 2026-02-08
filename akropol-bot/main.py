import os
import json
import time
import requests
import datetime
import dateparser
from functools import wraps
from flask import Flask, request, send_from_directory, url_for, render_template, Response, redirect
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from openai import OpenAI
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

# --- OTOMATİK KURULUM (SIRF SENİN İÇİN) ---
def setup_files():
    """Eksik klasör ve dosyaları otomatik yaratır."""
    if not os.path.exists("templates"):
        os.makedirs("templates")
        print("📂 'templates' klasörü oluşturuldu.")

    # 1. Dashboard.html
    if not os.path.exists("templates/dashboard.html"):
        with open("templates/dashboard.html", "w", encoding="utf-8") as f:
            f.write("""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Akropol AI Yönetim Paneli</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        body { background-color: #f4f6f9; }
        .card { border: none; box-shadow: 0 4px 6px rgba(0,0,0,0.1); border-radius: 12px; }
        .status-HOT { background-color: #ffebee; color: #c62828; font-weight: bold; padding: 5px 10px; border-radius: 20px; }
        .status-COLD { background-color: #e3f2fd; color: #1565c0; font-weight: bold; padding: 5px 10px; border-radius: 20px; }
        .status-WARM { background-color: #fff3e0; color: #ef6c00; font-weight: bold; padding: 5px 10px; border-radius: 20px; }
        .status-CLOSED { background-color: #eee; color: #666; font-weight: bold; padding: 5px 10px; border-radius: 20px; }
    </style>
</head>
<body>
    <nav class="navbar navbar-dark bg-dark mb-4">
        <div class="container-fluid">
            <span class="navbar-brand mb-0 h1"><i class="fas fa-brain me-2"></i>Akropol AI (True Intelligence)</span>
            <div>
                <span class="text-white me-3">Yönetici Paneli</span>
                <a href="/super-admin" class="btn btn-sm btn-danger"><i class="fas fa-lock me-1"></i>Süper Admin</a>
            </div>
        </div>
    </nav>
    <div class="container">
        <div class="row mb-4">
            <div class="col-md-4"><div class="card text-white bg-primary h-100"><div class="card-body"><h6>Toplam Kişi</h6><h2>{{ stats.total }}</h2></div></div></div>
            <div class="col-md-4"><div class="card text-white bg-danger h-100"><div class="card-body"><h6>🔥 Sıcak Fırsatlar</h6><h2>{{ stats.hot }}</h2></div></div></div>
            <div class="col-md-4"><div class="card text-white bg-warning h-100"><div class="card-body"><h6>📅 Takip Edilecekler</h6><h2>{{ stats.follow }}</h2></div></div></div>
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
                                <td style="max-width: 350px;"><small>{{ meta.get('summary', 'Analiz bekleniyor...') }}</small></td>
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
        setTimeout(function(){ location.reload(); }, 10000);
    </script>
</body>
</html>""")
        print("📄 'dashboard.html' oluşturuldu.")

    # 2. Super_admin.html
    if not os.path.exists("templates/super_admin.html"):
        with open("templates/super_admin.html", "w", encoding="utf-8") as f:
            f.write("""<!DOCTYPE html>
<html lang="tr">
<head><title>Süper Admin</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-dark text-white">
<div class="container mt-5"><h1>Süper Admin Paneli</h1><p>Burası sadece yetkililer içindir.</p><a href="/dashboard" class="btn btn-light">Geri Dön</a></div>
</body></html>""")
        print("📄 'super_admin.html' oluşturuldu.")

    # 3. Conversation Detail
    if not os.path.exists("templates/conversation_detail.html"):
        with open("templates/conversation_detail.html", "w", encoding="utf-8") as f:
            f.write("""<!DOCTYPE html>
<html lang="tr">
<head><title>Detay</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body><div class="container mt-5"><h1>Konuşma Detayı: {{ phone }}</h1>
{% for msg in messages %}<div class="alert alert-secondary"><b>{{ msg.role }}:</b> {{ msg.content }}</div>{% endfor %}
<a href="/dashboard" class="btn btn-primary">Geri</a></div></body></html>""")
        print("📄 'conversation_detail.html' oluşturuldu.")

# OTOMATİK KURULUMU ÇALIŞTIR
setup_files()

# --- STANDART KODLAR ---
load_dotenv()
app = Flask(__name__)

# API Keyler
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

# İstemciler
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)
else:
    print("⚠️ UYARI: OPENAI_API_KEY eksik!")
    client = None

try:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
except:
    twilio_client = None

scheduler = BackgroundScheduler()
scheduler.start()

# Sabitler
MEMORY_FILE = "conversations.json"
KNOWLEDGE_BASE_FILE = "knowledge_base.json"
REVIEWS_FILE = "reviews.json"
SETTINGS_FILE = "settings.json"
AUDIO_DIR = "static/audio"
os.makedirs(AUDIO_DIR, exist_ok=True)

# 2. YARDIMCI VE VERİ FONKSİYONLARI
def load_json_file(filepath, default):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save_json_file(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

AKROPOL_FACTS = json.dumps(load_json_file(KNOWLEDGE_BASE_FILE, {}), ensure_ascii=False, indent=2)
REVIEWS_DATA = json.dumps(load_json_file(REVIEWS_FILE, []), ensure_ascii=False, indent=2)

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def save_memory(data):
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_conversation_history(phone_number):
    memory = load_memory()
    return memory.get(phone_number, {}).get("messages", [])

def update_memory(phone_number, role, content, analysis=None):
    memory = load_memory()
    if phone_number not in memory:
        memory[phone_number] = {"messages": [], "metadata": {}}
    
    memory[phone_number]["messages"].append({
        "role": role,
        "content": content,
        "timestamp": time.time()
    })

    if analysis:
        memory[phone_number]["metadata"] = analysis

    save_memory(memory)

# 3. GÜVENLİK (BASIC AUTH)
def check_auth(username, password):
    settings = load_json_file(SETTINGS_FILE, {"admin_user": "admin", "admin_pass": "akropol123"})
    return username == settings.get("admin_user") and password == settings.get("admin_pass")

def authenticate():
    return Response(
    'Bu alan SÜPER ADMIN yetkisi gerektirir.\nLütfen şifrenizi girin.', 401,
    {'WWW-Authenticate': 'Basic realm="Akropol Secure Zone V3"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# 4. GERÇEK AI BEYNİ (LEAD ANALYZER)
class LeadAnalyzer:
    @staticmethod
    def analyze(phone, history):
        if not client: return {"summary": "API Key Eksik", "status": "COLD"}
        recent_convo = "\n".join([f"{m['role']}: {m['content']}" for m in history[-10:]])
        
        system_prompt = f"""
        Sen Akropol CRM Analistisin. Görevin bu konuşmayı analiz edip JSON formatında rapor vermek.
        Şu anki zaman: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}

        ÇIKTI FORMATI (JSON):
        {{
            "summary": "Müşterinin durumu hakkında 1 cümlelik teknik özet",
            "status": "HOT | WARM | COLD | CLOSED",
            "follow_up_date": "YYYY-MM-DD HH:MM" veya null,
            "sentiment": "Positive / Negative / Neutral"
        }}

        KURALLAR:
        - "Haftaya arayın" -> +7 gün. "Yarın" -> +1 gün.
        - "Cenaze/Hasta" -> status=WARM, follow_up_date=+7 gün.
        - "Fiyat" sordu ve olumlu -> status=HOT.
        """

        try:
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"KONUŞMA:\n{recent_convo}"}
                ],
                response_format={ "type": "json_object" }
            )
            return json.loads(completion.choices[0].message.content)
        except Exception as e:
            print(f"Analiz Hatası: {e}")
            return {"summary": "Analiz yapılamadı", "status": "COLD"}

    @staticmethod
    def format_time(timestamp):
        return datetime.datetime.fromtimestamp(timestamp).strftime('%d.%m %H:%M')

analyzer = LeadAnalyzer()

# 5. SCHEDULER (OTOMATİK TAKİP)
def check_followups():
    settings = load_json_file(SETTINGS_FILE, {"system_active": True})
    if not settings.get("system_active", True): return

    now = datetime.datetime.now()
    if not (9 <= now.hour < 19): return 

    print(f"⏰ [Scheduler] Takip kontrolü... {now.strftime('%H:%M')}")
    memory = load_memory()
    
    for phone, data in memory.items():
        meta = data.get("metadata", {})
        f_date_str = meta.get("follow_up_date")
        
        if f_date_str:
            try:
                f_date = datetime.datetime.strptime(f_date_str, "%Y-%m-%d %H:%M")
                if now >= f_date and meta.get("status") != "CONTACTED":
                    print(f"🔔 SİNYAL: {phone} için OTOMATİK MESAJ gönderiliyor...")
                    
                    msg_body = f"Merhaba! Akropol Termal Asistanı ben. Müsait olduğunuzda görüşelim demiştik. Yardımcı olabilir miyim?"
                    if twilio_client:
                        twilio_client.messages.create(
                            from_=TWILIO_WHATSAPP_NUMBER,
                            to=phone,
                            body=msg_body
                        )
                    
                    meta["status"] = "CONTACTED"
                    meta["summary"] += " (Otomatik mesaj atıldı)"
                    update_memory(phone, "assistant", msg_body, meta)
                    
            except Exception as e:
                print(f"Otomatik Mesaj Hatası: {e}")

scheduler.add_job(check_followups, 'interval', seconds=60)

# 7. ROUTE'LAR
@app.route("/")
def home():
    return "Akropol AI Bot Çalışıyor! /dashboard adresine gidin."

@app.route("/webhook", methods=['POST'])
def webhook():
    if not client: return str(MessagingResponse().message("Sistem bakımda (API Key Eksik)."))
    
    incoming_msg = request.values.get('Body', '').strip()
    from_number = request.values.get('From', '')
    
    # Basit bir cevap (replit test için)
    history = get_conversation_history(from_number)
    
    system_prompt = f"""
    Sen Akropol Termal'in **Kıdemli Satış Danışmanısın**.
    BİLGİ BANKASI: {AKROPOL_FACTS}
    GÖREVİN: Sadece bilgi vermek değil, **SATIŞI KAPATMAK**.
    PSİKOLOJİ: Kıtlık ilkesi kullan ("Son 3 oda kaldı").
    """
    
    msgs = [{"role": "system", "content": system_prompt}] + \
           [{"role": m["role"], "content": m["content"]} for m in history[-6:]] + \
           [{"role": "user", "content": incoming_msg}]

    try:
        completion = client.chat.completions.create(model="gpt-4o", messages=msgs)
        ai_response = completion.choices[0].message.content
    except: ai_response = "Sistem yoğun, lütfen bekleyiniz."

    resp = MessagingResponse()
    resp.message(ai_response)

    current_history = history + [{"role": "user", "content": incoming_msg}, {"role": "assistant", "content": ai_response}]
    analysis_result = analyzer.analyze(from_number, current_history)
    update_memory(from_number, "user", incoming_msg)
    update_memory(from_number, "assistant", ai_response, analysis_result)
    return str(resp)

@app.route("/dashboard")
def dashboard():
    memory = load_memory()
    stats = {"total": len(memory), "hot": 0, "follow": 0}
    for p, data in memory.items():
        meta = data.get("metadata", {})
        if meta.get("status") == "HOT": stats["hot"] += 1
        if meta.get("status") == "WARM": stats["follow"] += 1
    return render_template("dashboard.html", memory=memory, stats=stats, analyzer=analyzer)

@app.route("/dashboard/<path:phone>")
def detail(phone):
    memory = load_memory()
    data = memory.get(phone, {})
    return render_template("conversation_detail.html", phone=phone, messages=data.get("messages", []), logic=analyzer)

@app.route("/super-admin")
@requires_auth
def super_admin():
    memory = load_memory()
    return render_template("super_admin.html", memory=memory)

@app.route("/api/stats")
def api_stats():
    memory = load_memory()
    stats = {"total": len(memory), "hot": 0, "follow": 0}
    for p, data in memory.items():
        meta = data.get("metadata", {})
        if meta.get("status") == "HOT": stats["hot"] += 1
        if meta.get("status") == "WARM": stats["follow"] += 1
    return json.dumps(stats)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
