import os
import json
import time
import requests
import datetime
import threading
from flask import Flask, request, render_template, url_for
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from openai import OpenAI
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

# --- AYARLAR VE YOLLAR ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
AUDIO_DIR = os.path.join(STATIC_DIR, "audio")

# Klasör Kontrolleri
if not os.path.exists(TEMPLATE_DIR): os.makedirs(TEMPLATE_DIR)
if not os.path.exists(STATIC_DIR): os.makedirs(STATIC_DIR)
if not os.path.exists(AUDIO_DIR): os.makedirs(AUDIO_DIR)

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
try:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
except:
    twilio_client = None

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
    """Mesajı anında kaydeder (Analizi beklemez)"""
    data = get_memory()
    if phone not in data:
        data[phone] = {"messages": [], "metadata": {"status": "YENİ", "summary": "Görüşme başladı..."}}
    
    data[phone]["messages"].append({
        "role": role,
        "content": content,
        "timestamp": time.time()
    })
    save_json(MEMORY_FILE, data)

def update_analysis_result(phone, analysis):
    """Sadece analiz sonucunu günceller"""
    data = get_memory()
    if phone in data:
        data[phone]["metadata"] = analysis
        save_json(MEMORY_FILE, data)

# --- SES İŞLEMLERİ (STT & TTS) ---
def speech_to_text(media_url):
    """Sesi yazıya çevirir (Whisper)"""
    try:
        if not client: return "(API Yok)"
        # Dosyayı indir
        r = requests.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        if r.status_code != 200: r = requests.get(media_url) # Authsuz dene
        
        filename = f"input_{int(time.time())}.ogg"
        filepath = os.path.join(AUDIO_DIR, filename)
        with open(filepath, 'wb') as f: f.write(r.content)
        
        # Whisper
        with open(filepath, "rb") as audio:
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio, language="tr")
        
        os.remove(filepath)
        return transcript.text
    except Exception as e:
        print(f"STT Hata: {e}")
        return "(Ses anlaşılamadı)"

def text_to_speech(text):
    """Yazıyı sese çevirir (TTS) ve dosya yolunu döner"""
    try:
        if not client: return None
        filename = f"reply_{int(time.time())}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)
        
        response = client.audio.speech.create(
            model="tts-1",
            voice="shimmer", # Kadın sesi (Aura için uygun)
            input=text
        )
        response.stream_to_file(filepath)
        return filename # Sadece dosya adı
    except Exception as e:
        print(f"TTS Hata: {e}")
        return None

# --- ANALİZ (ARKA PLAN) ---
def background_analysis(phone, history):
    """Konuşmayı analiz edip dashboard'u günceller"""
    if not client: return
    
    recent = "\n".join([f"{m['role']}: {m['content']}" for m in history[-10:]])
    prompt = f"""
    Sen Akropol CRM Analistisin. Şu an: {datetime.datetime.now().strftime("%Y-%m-%d")}
    Çıktı JSON: {{"summary": "Tek cümlelik durum özeti", "status": "HOT/WARM/COLD", "follow_up_date": "YYYY-MM-DD"}}
    Konuşma: {recent}
    """
    try:
        res = client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "system", "content": prompt}],
            response_format={"type": "json_object"}
        )
        analysis = json.loads(res.choices[0].message.content)
        update_analysis_result(phone, analysis)
        print(f"✅ Analiz güncellendi: {phone}")
    except: pass

# --- WEBHOOK ---
@app.route("/webhook", methods=['POST'])
def webhook():
    # Gelen Veriler
    incoming_msg = request.values.get('Body', '').strip()
    media_url = request.values.get('MediaUrl0')
    media_type = request.values.get('MediaContentType0', '')
    phone = request.values.get('From', '')

    is_voice_input = False
    
    # 1. GİRDİYİ İŞLE (Ses mi Yazı mı?)
    if media_url and ('audio' in media_type or 'ogg' in media_type):
        is_voice_input = True
        transcription = speech_to_text(media_url)
        user_text = f"[SESLİ MESAJ]: {transcription}"
        # Dashboard için hemen kaydet
        update_memory_immediate(phone, "user", user_text)
    else:
        user_text = incoming_msg
        update_memory_immediate(phone, "user", user_text)

    # 2. AI CEVABI ÜRET
    history = get_memory().get(phone, {}).get("messages", [])
    prompt = f"""
    Sen 'Aura', Akropol Termal Şehir'in asistanısın.
    Kısa, net ve samimi konuş. Eğer kullanıcı sesli konuştuysa, sen de MÜKEMMEL bir Türkçe ile cevap ver (çünkü metnin okunacak).
    Bilgiler: {AKROPOL_FACTS}
    """
    
    msgs = [{"role": "system", "content": prompt}] + \
           [{"role": m["role"], "content": m["content"]} for m in history[-6:]]
    
    try:
        ai_res = client.chat.completions.create(model="gpt-4o", messages=msgs)
        reply_text = ai_res.choices[0].message.content
    except:
        reply_text = "Şu an sistemimde bir yoğunluk var, lütfen birazdan tekrar deneyin."

    # 3. DASHBOARD'A CEVABI KAYDET (ANINDA)
    update_memory_immediate(phone, "assistant", reply_text)

    # 4. YANIT HAZIRLA (Sesli mi Yazılı mı?)
    resp = MessagingResponse()
    
    if is_voice_input:
        # Sesli cevap üret
        audio_filename = text_to_speech(reply_text)
        if audio_filename:
            # Twilio'ya ses dosyasının linkini ver
            audio_url = url_for('static', filename=f'audio/{audio_filename}', _external=True)
            msg = resp.message(reply_text) # Yazıyı da ekle (altyazı gibi)
            msg.media(audio_url)
        else:
            resp.message(reply_text)
    else:
        # Sadece yazı
        resp.message(reply_text)

    # 5. ARKA PLAN ANALİZİNİ BAŞLAT
    threading.Thread(target=background_analysis, args=(phone, history + [{"role": "assistant", "content": reply_text}])).start()

    return str(resp)

# --- DİĞER ROTALAR ---
@app.route("/")
def home(): return "Akropol AI v5.0 (Voice & Instant Dash) Active"

@app.route("/dashboard")
def dashboard():
    data = get_memory()
    stats = {"total": len(data), "hot": 0, "follow": 0}
    for p, v in data.items():
        s = v.get("metadata", {}).get("status", "")
        if s == "HOT": stats["hot"] += 1
        if s == "WARM": stats["follow"] += 1
    return render_template("dashboard.html", memory=data, stats=stats)

@app.route("/dashboard/<path:phone>")
def detail(phone):
    data = get_memory().get(phone, {})
    return render_template("conversation_detail.html", phone=phone, messages=data.get("messages", []))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
