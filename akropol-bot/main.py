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
# Bu numara Twilio Console'dan alınmalı. Sandbox ise 'whatsapp:+14155238886' olabilir.
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
        # Global CONVERSATIONS değişkenini kaydet
        with open(CONVERSATIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(CONVERSATIONS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Save error: {e}")

CONVERSATIONS = load_conversations()

# --- HTML ŞABLONLARI ---
def setup_files():
    # 1. PREMIUM DASHBOARD (CRM View)
    dash_path = os.path.join(TEMPLATE_DIR, "dashboard.html")
    with open(dash_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Akropol AI CRM</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"><link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600&display=swap" rel="stylesheet"><style>:root { --primary: #2c3e50; --accent: #e67e22; --bg: #f8f9fa; } body { background-color: var(--bg); font-family: 'Outfit', sans-serif; color: #333; } .navbar { background: white; padding: 1rem 0; box-shadow: 0 2px 10px rgba(0,0,0,0.03); } .brand-logo { font-weight: 600; font-size: 1.2rem; color: var(--primary); display: flex; align-items: center; gap: 10px; } .card { border: none; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.04); transition: .3s; background: white; } .stat-val { font-size: 2.5rem; font-weight: 600; color: var(--primary); } .stat-label { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 1px; color: #888; font-weight: 500; } .status-badge { padding: 5px 12px; border-radius: 30px; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.5px; } .bg-HOT { background: #ffe0e0; color: #d63031; } .bg-WARM { background: #fff3cd; color: #ff9f43; } .bg-COLD { background: #e2eafc; color: #0984e3; } .bg-NEW { background: #e8f5e9; color: #00b894; } .table-custom th { font-weight: 500; color: #888; text-transform: uppercase; font-size: 0.75rem; border-bottom: 2px solid #f1f1f1; } .avatar { width: 40px; height: 40px; background: #eee; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; color: #555; } .refresh-bar { height: 3px; background: var(--accent); width: 0%; animation: load 3s infinite linear; position: fixed; top: 0; left: 0; z-index: 9999; } @keyframes load { 0% { width: 0; } 100% { width: 100%; } }</style></head><body><div class="refresh-bar"></div><nav class="navbar mb-4"><div class="container"><div class="brand-logo"><i class="fas fa-layer-group text-warning"></i> AKROPOL AI</div><a href="/super-admin" class="btn btn-sm btn-outline-dark">Yönetici Girişi</a></div></nav><div class="container"><div class="row g-4 mb-4"><div class="col-md-4"><div class="card p-4"><div class="stat-label">Toplam Görüşme</div><div class="stat-val">{{ stats.total }}</div></div></div><div class="col-md-4"><div class="card p-4"><div class="stat-label">Sıcak Potansiyel</div><div class="stat-val text-danger">{{ stats.hot }}</div></div></div><div class="col-md-4"><div class="card p-4"><div class="stat-label">Takip Listesi</div><div class="stat-val text-warning">{{ stats.follow }}</div></div></div></div><div class="card"><div class="card-body p-0"><div class="table-responsive"><table class="table table-custom table-hover align-middle mb-0"><thead class="bg-light"><tr><th class="ps-4 py-3">Müşteri</th><th>Son Durum (AI Özeti)</th><th>Statü</th><th>Zaman</th><th></th></tr></thead><tbody>{% for phone, data in memory.items() %}{% set meta = data.get('metadata', {}) %} <tr><td class="ps-4"><div class="d-flex align-items-center gap-3"><div class="avatar">{{ phone[-2:] }}</div><span class="fw-bold">{{ phone }}</span></div></td><td class="text-muted small" style="max-width:350px;">{{ meta.get('summary', 'Analiz bekleniyor...') }}</td><td><span class="status-badge bg-{{ meta.get('status', 'NEW') }}">{{ meta.get('status', 'YENİ') }}</span></td><td class="small text-muted">{{ meta.get('last_update', '-') }}</td><td class="pe-4 text-end"><a href="/dashboard/{{ phone }}" class="btn btn-sm btn-light border px-3">İncele</a></td></tr>{% endfor %}</tbody></table></div></div></div></div><script>setTimeout(() => window.location.reload(), 3000);</script></body></html>""")
    
    # 2. Detail View
    detail_path = os.path.join(TEMPLATE_DIR, "conversation_detail.html")
    with open(detail_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html lang="tr"><head><title>Detay</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{background:#f8f9fa;font-family:'Segoe UI',sans-serif}.timeline{position:relative;padding:20px 0}.timeline::before{content:'';position:absolute;left:50px;top:0;bottom:0;width:2px;background:#e9ecef}.msg-card{margin-bottom:20px;border:none;border-radius:8px;box-shadow:0 2px 5px rgba(0,0,0,0.05);margin-left:80px;position:relative}.msg-card::before{content:'';position:absolute;left:-41px;top:20px;width:12px;height:12px;border-radius:50%;background:#ccc;border:2px solid white;z-index:2}.role-user .msg-card::before{background:#3498db}.role-assistant .msg-card::before{background:#e67e22}.role-user .msg-card{background:white;border-left:4px solid #3498db}.role-assistant .msg-card{background:#fff8e1;border-left:4px solid #e67e22}.timestamp{position:absolute;left:-80px;top:15px;font-size:0.75rem;color:#999;width:60px;text-align:right}.voice-tag{font-size:0.8rem;background:#eee;padding:2px 8px;border-radius:4px;display:inline-block;margin-bottom:5px}</style></head><body><div class="container py-5" style="max-width:800px;"><div class="d-flex justify-content-between align-items-center mb-5"><h4 class="mb-0 fw-bold">{{ phone }}</h4><a href="/dashboard" class="btn btn-outline-secondary btn-sm">Panele Dön</a></div><div class="timeline">{% for msg in messages %}<div class="position-relative role-{{ msg.role }}"><div class="timestamp">{{ msg.time_str }}</div><div class="card msg-card p-3">{% if "[SESLİ" in msg.content %}<div class="voice-tag">🎤 Ses Kaydı</div>{% endif %}<div class="text-dark">{{ msg.content }}</div></div></div>{% endfor %}</div></div></body></html>""")

    # 3. SUPER ADMIN (Full Management)
    admin_path = os.path.join(TEMPLATE_DIR, "super_admin.html")
    with open(admin_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Super Admin - Aura OS</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css"><style>
        body{background:#f8f9fa;font-family:'Segoe UI',sans-serif; overflow-x: hidden;}
        .sidebar{height:100vh;background:#2c3e50;color:white;position:fixed;top:0;left:0;width:280px;padding:20px;z-index:1000;}
        .main-content{margin-left:280px;padding:20px;min-height:100vh;}
        .card{border:none;box-shadow:0 4px 15px rgba(0,0,0,0.05);border-radius:10px; margin-bottom: 20px;}
        .nav-link{color:rgba(255,255,255,0.8);margin-bottom:5px; padding: 10px 15px; border-radius: 8px;}
        .nav-link:hover, .nav-link.active{color:white;background:rgba(255,255,255,0.1);}
        .chat-box{height:500px;overflow-y:auto;background:#e5ddd5;border-radius:10px;padding:20px; display: flex; flex-direction: column;}
        .msg{margin-bottom:10px;padding:12px;border-radius:10px;max-width:70%; position: relative; font-size: 0.95rem; line-height: 1.4;}
        .msg.user{background:white; align-self: flex-start; border-top-left-radius: 0;}
        .msg.assistant{background:#dcf8c6; align-self: flex-end; border-top-right-radius: 0;}
        .msg.system{align-self: center; background: rgba(0,0,0,0.1); font-size: 0.8rem; padding: 5px 15px; border-radius: 20px; color: #555;}
        .time-label { font-size: 0.7rem; color: #999; margin-top: 5px; text-align: right; display: block;}
        </style></head><body>
        
        <div class="sidebar">
            <h4 class="mb-4 d-flex align-items-center"><i class="fas fa-robot text-warning me-2"></i>Aura Admin</h4>
            <div class="mb-4">
                <small class="text-uppercase text-muted fw-bold" style="font-size:0.7rem;">Menü</small>
                <ul class="nav flex-column mt-2">
                    <li class="nav-item"><a class="nav-link {% if not selected_phone %}active{% endif %}" href="/super-admin"><i class="fas fa-tachometer-alt me-2"></i>Genel Bakış</a></li>
                    <li class="nav-item"><a class="nav-link text-danger" href="/logout"><i class="fas fa-sign-out-alt me-2"></i>Çıkış Yap</a></li>
                </ul>
            </div>
            
            <div>
                <small class="text-uppercase text-muted fw-bold" style="font-size:0.7rem;">Görüşmeler</small>
                <div class="mt-2" style="max-height: 60vh; overflow-y: auto;">
                    {% for phone in conversations.keys() | sort(reverse=True) %}
                    <a href="/super-admin?phone={{phone}}" class="nav-link small d-flex align-items-center {% if selected_phone == phone %}active{% endif %}">
                        <i class="fab fa-whatsapp me-2 text-success"></i> {{ phone }}
                    </a>
                    {% endfor %}
                </div>
            </div>
        </div>

        <div class="main-content">
            {% if selected_phone %}
            <div class="container-fluid p-0">
                <div class="card">
                    <div class="card-header bg-white py-3 d-flex justify-content-between align-items-center">
                        <h5 class="mb-0"><i class="fab fa-whatsapp text-success me-2"></i>{{ selected_phone }}</h5>
                        <div class="d-flex gap-2">
                             <span class="badge bg-success">Online</span>
                             <a href="/super-admin" class="btn btn-sm btn-outline-secondary"><i class="fas fa-times"></i></a>
                        </div>
                    </div>
                    <div class="card-body p-0">
                        <div class="chat-box" id="chatBox">
                            {% for msg in conversations[selected_phone]['messages'] %}
                            <div class="msg {{ msg.role }}">
                                {% if msg.role == 'system' %}
                                    <i class="fas fa-info-circle me-1"></i> {{ msg.content }}
                                {% else %}
                                    {{ msg.content }}
                                {% endif %}
                                <span class="time-label">{{ msg.time_str }}</span>
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                    <div class="card-footer bg-white p-3">
                        <form action="/admin/send-message" method="POST">
                            <input type="hidden" name="phone" value="{{ selected_phone }}">
                            <div class="input-group">
                                <input type="text" name="message" class="form-control" placeholder="Mesajınızı yazın..." required autocomplete="off">
                                <button class="btn btn-primary" type="submit"><i class="fas fa-paper-plane me-2"></i>Gönder</button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
            {% else %}
            <div class="row">
                <div class="col-md-12 mb-4">
                    <h2 class="fw-bold">Hoş Geldin, Admin 👋</h2>
                    <p class="text-muted">Sistem durumu ve istatistiklerine buradan ulaşabilirsin.</p>
                </div>
                <div class="col-md-4">
                    <div class="card p-4 bg-primary text-white">
                        <h3>{{ conversations|length }}</h3>
                        <span>Toplam Görüşme</span>
                    </div>
                </div>
                 <div class="col-md-4">
                    <div class="card p-4 bg-success text-white">
                        <h3>Aktif</h3>
                        <span>Sistem Durumu</span>
                    </div>
                </div>
                 <div class="col-md-4">
                    <div class="card p-4">
                        <h5 class="mb-3">Kısayollar</h5>
                        <button class="btn btn-outline-dark w-100" onclick="alert('Henüz aktif değil')">Ayarlar</button>
                    </div>
                </div>
            </div>
            {% endif %}
        </div>
        
        <script>
            var chatBox = document.getElementById('chatBox');
            if(chatBox) chatBox.scrollTop = chatBox.scrollHeight; 
            // 5 saniyede bir sayfayı yenile (basit canlı izleme)
            // setTimeout(() => window.location.reload(), 5000); 
            // Otomatik yenileme form doldururken sorun yaratabilir, şimdilik kapalı veya uzun tutalım
        </script>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
        </body></html>""")

    # 4. Login Page
    login_path = os.path.join(TEMPLATE_DIR, "login.html")
    with open(login_path, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html><html lang="tr"><head><title>Admin Giriş</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{background:#2c3e50;height:100vh;display:flex;align-items:center;justify-content:center}.login-card{width:100%;max-width:400px;border-radius:15px;overflow:hidden;box-shadow:0 10px 30px rgba(0,0,0,0.2)}.card-header{background:white;padding:2rem;text-align:center;border-bottom:1px solid #eee}.card-body{background:white;padding:2rem}</style></head><body><div class="login-card"><div class="card-header"><h4 class="mb-0 fw-bold">Aura OS Admin</h4><p class="text-muted mb-0 small">Sisteme erişmek için giriş yapın</p></div><div class="card-body"><form method="POST"><div class="mb-3"><label>Şifre</label><input type="password" name="password" class="form-control" required></div><button type="submit" class="btn btn-dark w-100">Giriş Yap</button></form></div></div></body></html>""")

setup_files()

# --- HELPER ---
def get_time_str(): return datetime.datetime.now().strftime("%H:%M")

def update_memory(phone, role, content, meta_update=None):
    if phone not in CONVERSATIONS:
        CONVERSATIONS[phone] = {"messages": [], "metadata": {"status": "YENİ", "summary": "Görüşme başladı..."}}
    CONVERSATIONS[phone]["messages"].append({"role": role, "content": content, "timestamp": time.time(), "time_str": get_time_str()})
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
    
    # TRIGGER LOGIC
    triggers = ["ses", "konuş", "duymak", "söyle", "anlat", "dinle", "özetle", "sesli"]
    should_speak = is_voice_in or any(w in user_in.lower() for w in triggers)

    KB = load_kb()
    
    # 3. SALES PSYCHOLOGY & PERSONA GENERATION
    # Bu adımlar botun "robotik" olmasını engeller, empatik ve satış odaklı yapar.
    persona_prompt = f"""
    Sen {KB.get('identity', {}).get('name', 'Aura')}; {KB.get('identity', {}).get('tone', 'Sıcak ve profesyonel')} bir {KB.get('identity', {}).get('role', 'Asistan')}.
    GÖREVİN: {KB.get('identity', {}).get('mission', 'Mükemmel tatil deneyimi sunmak')}.
    
    BİLGİ BANKASI:
    {json.dumps(KB.get('hotel_info', {}), ensure_ascii=False)}
    
    SATIŞ PSİKOLOJİSİ KURALLARI (BUNLARI UYGULA):
    1. EMPATİ KUR: Kullanıcının duygusunu veya ihtiyacını anla. (Örn: "Yorgunluğunuzu atmanız için harika bir fırsat...", "Ailenizle keyifli vakit geçirmeniz bizim için önemli...")
    2. DEĞER KAT: Sadece "Evet var" deme. O özelliğin kullanıcıya faydasını anlat. (Örn: "Evet havuzumuz var" YERİNE "Termal havuzlarımızda günün yorgunluğunu atarken şifalı sularımızın keyfini sürebilirsiniz.")
    3. YÖNLENDİR: Konuşmayı asla cevapsız bırakma. Her zaman bir sonraki adıma (tarih sorma, kişi sayısı öğrenme, arama teklifi) yönlendiren nazik bir soru sor.
    4. FİYAT TAKTİĞİ: {KB.get('sales_psychology', {}).get('handling_price', 'Fiyat sormadan önce değeri hissettir.')}
    
    FORMAT:
    - Kısa paragraflar kullan.
    - Samimi ol ama labali olma. "Siz" dilini koru ama sıcak olsun.
    - Emoji kullanımı: Ölçülü ve yerinde (Örn: 🌿, ✨, 💧)
    {'CEVABIN SESLİ OKUNACAK. Lütfen noktalama işaretlerini dikkatli kullan ve akıcı cümleler kur.' if should_speak else ''}
    """
    
    # 4. CONTEXT MANAGEMENT (Smart History)
    # Son 8 mesajı alarak konuşmanın akışını daha iyi anla
    hist = CONVERSATIONS.get(phone, {}).get("messages", [])
    
    try: 
        messages = [{"role":"system", "content": persona_prompt}] + [{"role":m["role"], "content":m["content"]} for m in hist[-8:]]
        
        # Call OpenAI with higher temperature for creativity but controlled top_p
        completion = client.chat.completions.create(
            model="gpt-4o", 
            messages=messages,
            temperature=0.7, # Biraz daha yaratıcı olsun
            presence_penalty=0.3, # Tekrara düşmesin
            frequency_penalty=0.3
        )
        ai_reply = completion.choices[0].message.content
    except Exception as e: 
        print(f"OpenAI Error: {e}")
        ai_reply = "Şu an sistemlerimizde kısa bir bakım var, ancak size yardımcı olmayı çok isterim. Lütfen biraz sonra tekrar yazar mısınız? 🌸"

    update_memory(phone, "assistant", ai_reply)

    resp = MessagingResponse()
    
    if should_speak:
         # Generate TTS
        try:
            audio_url = get_tts_url(ai_reply)
            msg = resp.message(ai_reply)
            if audio_url: msg.media(audio_url)
            else: msg.body(ai_reply + " (Ses hatası)")
        except Exception as e:
            print(f"TTS Error: {e}")
            resp.message(ai_reply + " (Ses gönderilemedi)")
    else:
        resp.message(ai_reply)
        
    threading.Thread(target=analyze_bg, args=(phone, hist+[{"role":"assistant","content":ai_reply}])).start()
    return str(resp)

# --- ROUTES ---
@app.route("/")
def idx(): return redirect("/dashboard")

# AUTH
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

# ADMIN PANEL
@app.route("/super-admin")
def admin_panel():
    if not session.get("admin"): return redirect("/login")
    
    selected_phone = request.args.get('phone')
    return render_template("super_admin.html", conversations=CONVERSATIONS, selected_phone=selected_phone)

@app.route("/admin/send-message", methods=["POST"])
def admin_send():
    if not session.get("admin"): return redirect("/login")
    
    phone = request.form.get("phone")
    text = request.form.get("message")
    
    if phone and text:
        try:
            # Twilio Send
            if twilio_client:
                # Twilio'dan mesaj gönder
                twilio_client.messages.create(
                    from_=TWILIO_PHONE_NUMBER,
                    body=text,
                    to=phone
                )
            
            # Hafızaya ekle
            update_memory(phone, "assistant", text)
            
        except Exception as e:
            print(f"Send Error: {e}")
            flash(f"Mesaj gönderilemedi: {e}")
            
    return redirect(f"/super-admin?phone={phone}")

# PUBLIC CRM DASHBOARD
@app.route("/dashboard")
def dsh():
    stats = {"total": len(CONVERSATIONS), "hot": 0, "follow": 0}
    for k,v in CONVERSATIONS.items():
        if v["metadata"].get("status")=="HOT": stats["hot"]+=1
        elif v["metadata"].get("status")=="WARM": stats["follow"]+=1
    return render_template("dashboard.html", memory=CONVERSATIONS, stats=stats)

@app.route("/dashboard/<path:phone>")
def det(phone):
    return render_template("conversation_detail.html", phone=phone, messages=CONVERSATIONS.get(phone,{}).get("messages",[]))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
