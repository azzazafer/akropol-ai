from gevent import monkey
monkey.patch_all()

import os
import json
import time
import datetime
import threading
import logging
import sqlite3
import re
import math
import urllib.parse
import base64
import io
import xml.sax.saxutils as saxutils

from flask import Flask, request, jsonify, render_template, session, redirect, Response
from datetime import timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from twilio.rest import Client
from openai import OpenAI
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream, Say
from dotenv import load_dotenv

# Use gevent-websocket directly (No flask-sock)
from gevent.pywsgi import WSGIServer
from geventwebsocket.handler import WebSocketHandler
from flask_sockets import Sockets

# --- G.711 MU-LAW ENCODER/DECODER (Embedded) ---
BIAS = 0x84
CLIP = 32635

def lin2ulaw(pcm_val):
    pcm_val = pcm_val >> 2
    if pcm_val < 0: pcm_val = -pcm_val; sign = 0x80
    else: sign = 0x00
    if pcm_val > CLIP: pcm_val = CLIP
    pcm_val += BIAS
    exponent = int(math.log(pcm_val, 2)) - 7
    mantissa = (pcm_val >> (exponent + 3)) & 0x0F
    ulaw_byte = ~(sign | (exponent << 4) | mantissa)
    return ulaw_byte & 0xFF

def ulaw2lin(ulaw_byte):
    ulaw_byte = ~ulaw_byte
    sign = ulaw_byte & 0x80
    exponent = (ulaw_byte >> 4) & 0x07
    mantissa = ulaw_byte & 0x0F
    linear = (mantissa << 3) + 0x84
    linear <<= exponent
    linear -= 0x84
    if sign: linear = -linear
    return linear << 2

def audioop_lin2ulaw(fragment, width):
    out = bytearray()
    for i in range(0, len(fragment), 2):
        sample = int.from_bytes(fragment[i:i+2], byteorder='little', signed=True)
        out.append(lin2ulaw(sample))
    return bytes(out)

def audioop_ulaw2lin(fragment, width):
    out = bytearray()
    for b in fragment:
        sample = ulaw2lin(b)
        out.extend(sample.to_bytes(2, byteorder='little', signed=True))
    return bytes(out)

def audioop_ratecv(fragment, width, nchannels, inrate, outrate, state):
    if inrate == 24000 and outrate == 8000:
        out = bytearray()
        for i in range(0, len(fragment), 6):
            out.extend(fragment[i:i+2])
        return bytes(out), None
    return fragment, None

# --- KONFİGÜRASYON ---
load_dotenv()
ADMIN_HASH = generate_password_hash(os.getenv("ADMIN_PASSWORD", "123"))
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

twilio_client = Client(TWILIO_SID, TWILIO_TOKEN) if TWILIO_SID else None
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = Flask(__name__)
sockets = Sockets(app) # Use flask-sockets
app.secret_key = os.urandom(24)
app.permanent_session_lifetime = timedelta(minutes=30)
logging.basicConfig(level=logging.INFO)

DATABASE = "akropol.db"

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('''CREATE TABLE IF NOT EXISTS leads (
            phone TEXT PRIMARY KEY, name TEXT, summary TEXT, status TEXT, score INTEGER, churn_reason TEXT, last_interaction DATETIME
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, role TEXT, content TEXT, audio_url TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        db.commit()

init_db()

# --- HELPER FUNCTIONS ---
def load_kb():
    try:
        with open("knowledge_base.json", "r", encoding="utf-8") as f: return json.load(f)
    except: return {}

def db_save_msg(phone, role, content, audio_url=None):
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO messages (phone, role, content, audio_url) VALUES (?, ?, ?, ?)", (phone, role, content, audio_url))
        db.commit()

def db_update_lead_meta(phone, summary, score, status, churn_reason=None):
    with app.app_context():
        db = get_db()
        db.execute("INSERT OR IGNORE INTO leads (phone, last_interaction) VALUES (?, datetime('now'))", (phone,))
        db.execute("UPDATE leads SET summary=?, score=?, status=?, churn_reason=?, last_interaction=datetime('now') WHERE phone=?", 
                   (summary, score, status, churn_reason, phone))
        db.commit()

# --- AI LOGIC ---
def check_safety_guard(phone, user_input):
    keywords = ["vefat", "öldü", "cenaze", "hastane", "yoğun bakım", "kanser"]
    if any(w in user_input.lower() for w in keywords):
        db_update_lead_meta(phone, "Vefat/Hastalık", 0, "COLD", "Safety Guard")
        return True
    return False

def get_best_rebuttal(user_input, kb):
    for scene in kb.get("scenarios", []):
        if scene.get("trigger", "").lower() in user_input.lower(): return scene.get("response")
    return None

def get_hybrid_tts_url(text):
    try:
        filename = f"tts_{int(time.time()*1000)}.mp3"
        filepath = os.path.join("static", filename)
        response = client.audio.speech.create(model="tts-1", voice="shimmer", input=text)
        response.stream_to_file(filepath)
        return f"https://akropol-ai-production.up.railway.app/static/{filename}"
    except Exception as e:
        print(f"TTS Error: {e}")
        return None

# --- OUTBOUND CALLING ---
def async_outbound_call(phone, name, delay=20):
    with app.app_context():
        try:
            time.sleep(delay)
            if twilio_client:
                public_url = "https://akropol-ai-production.up.railway.app"
                safe_name = urllib.parse.quote(name)
                stream_url = f"{public_url}/voice-stream?name={safe_name}&phone={phone}"
                twilio_client.calls.create(to=phone, from_="+16187762828", url=stream_url, method="POST")
        except Exception as e: print(e)

@app.route("/test-call")
def test_call():
    try:
        phone = request.args.get("phone", "").strip().replace(" ", "")
        name = request.args.get("name", "Misafir")
        if phone.startswith("0"): phone = phone[1:]
        if not phone.startswith("+"): phone = "+90" + phone
        
        if not twilio_client: return "Twilio Client Init Failed!", 500

        public_url = "https://akropol-ai-production.up.railway.app"
        safe_name = urllib.parse.quote(name)
        stream_url = f"{public_url}/voice-stream?name={safe_name}&phone={phone}"
        
        sender = "+16187762828"
        call = twilio_client.calls.create(to=phone, from_=sender, url=stream_url, method="POST")
        
        return f"BAŞARILI! Çağrı ID: {call.sid} <br> URL: {stream_url}", 200
    except Exception as e: return str(e), 500

# --- VOICE STREAMING ---
@app.route("/voice-stream", methods=['GET', 'POST'])
def voice_stream():
    name = request.args.get('name', 'Misafirimiz')
    phone = request.args.get('phone', 'Unknown')
    safe_name = urllib.parse.quote(name)
    host = "akropol-ai-production.up.railway.app"
    
    # RAW XML - English Only - Hardcoded Host
    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}/stream?name={safe_name}&phone={phone}" />
    </Connect>
</Response>"""
    return Response(twiml_response, mimetype='text/xml')

# --- WEBSOCKET HANDLER (FLASK-SOCKETS) ---
@sockets.route('/stream')
def stream(ws):
    import wave
    logging.info("WS Connected")
    phone = request.args.get('phone', 'Unknown')
    name = request.args.get('name', 'Misafir')
    stream_sid = None
    buffer = bytearray()
    
    # TTS Helper (Inner Function)
    def send_ai_response(text):
        try:
            if not client: return
            logging.info(f"AI Speaking: {text}")
            resp = client.audio.speech.create(model="tts-1", voice="shimmer", input=text, response_format="pcm")
            pcm_8k, _ = audioop_ratecv(resp.content, 2, 1, 24000, 8000, None)
            ulaw = audioop_lin2ulaw(pcm_8k, 2)
            payload = base64.b64encode(ulaw).decode("utf-8")
            ws.send(json.dumps({"event": "media", "streamSid": stream_sid, "media": {"payload": payload}}))
        except Exception as e: logging.error(f"TTS Error: {e}")

    try:
        while not ws.closed:
            message = ws.receive()
            if message is None: break
            
            data = json.loads(message)
            if data['event'] == 'start':
                stream_sid = data['start']['streamSid']
                logging.info(f"Stream Started: {stream_sid}")
                # AI INTRO
                time.sleep(0.5)
                send_ai_response(f"Merhaba {name} Bey, ben Aura. Nasılsınız?")
                
            elif data['event'] == 'media':
                # STT Logic (Simplified for stability)
                chunk = base64.b64decode(data['media']['payload'])
                buffer.extend(chunk)
                if len(buffer) > 20000: # ~2.5 sec
                    logging.info(f"Buffer filled: {len(buffer)} bytes. Transcribing...")
                    try:
                        pcm_data = audioop_ulaw2lin(buffer, 2)
                        wav_io = io.BytesIO()
                        with wave.open(wav_io, 'wb') as wav_file:
                            wav_file.setnchannels(1)
                            wav_file.setsampwidth(2)
                            wav_file.setframerate(8000)
                            wav_file.writeframes(pcm_data)
                        
                        wav_io.name = "audio.wav"
                        wav_io.seek(0)
                        
                        transcript_response = client.audio.transcriptions.create(
                            model="whisper-1",
                            file=wav_io,
                            language="tr"
                        )
                        transcript = transcript_response.text.strip()
                        logging.info(f"USER TRANSCRIPT: {transcript}")
                        
                        if transcript:
                            completion = client.chat.completions.create(
                                model="gpt-4o-mini",
                                messages=[
                                    {"role": "system", "content": f"Sen Akropol Termal'ın kıdemli satış uzmanısın. Müşteri şunu dedi: [{transcript}]. Kısa, ikna edici, Türkçe cevap ver."}
                                ]
                            )
                            ai_text = completion.choices[0].message.content.strip()
                            send_ai_response(ai_text)
                    except Exception as loop_e:
                        logging.error(f"Audio processing error: {loop_e}")
                    finally:
                        buffer.clear()
                    
            elif data['event'] == 'stop': break
    except Exception as e:
        logging.error(f"WS Error: {e}")

# --- WEBHOOKS & DASHBOARD (Simplified) ---
@app.route("/")
def index(): return "Aura Bot Active"

@app.route('/ws-test')
def ws_test(): return render_template('ws_test.html')

if __name__ == "__main__":
    # Custom Server Start for Gevent + WebSockets
    port = int(os.environ.get("PORT", 8080))
    http_server = WSGIServer(('0.0.0.0', port), app, handler_class=WebSocketHandler)
    logging.info(f"Starting WSGIServer on port {port}")
    http_server.serve_forever()
