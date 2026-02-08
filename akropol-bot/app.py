"""
AKROPOL TERMAL AI WHATSAPP ASİSTANI
Basit başlangıç versiyonu - Test için
"""

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Basit yanıt fonksiyonu (GPT-4 sonra eklenecek)
def get_bot_response(user_message):
    """Şimdilik basit yanıtlar"""
    
    message_lower = user_message.lower()
    
    if "merhaba" in message_lower or "selam" in message_lower:
        return "Merhaba! Akropol Termal'e hoş geldiniz! 🏨\n\nSize nasıl yardımcı olabilirim?\n\n1️⃣ Fiyat bilgisi\n2️⃣ Rezervasyon\n3️⃣ Tesis özellikleri"
    
    elif "fiyat" in message_lower:
        return "Akropol Termal fiyatlarımız:\n\n✅ 2 kişi 1 gece: ₺2,000\n   (Termal havuz + açık büfe dahil)\n\n✅ Tek kişi: ₺1,500\n\nRezervasyon yapmak ister misiniz?"
    
    elif "rezervasyon" in message_lower or "evet" in message_lower:
        return "Harika! 🎉\n\nRezervasyon için yetkili arkadaşımız sizi arayacak.\n\nHangi tarihleri düşünüyorsunuz?"
    
    elif "özellik" in message_lower or "neler var" in message_lower:
        return "Akropol Termal özellikleri:\n\n🏊 5 termal havuz\n🧖 Spa merkezi\n🍽️ Açık büfe restoran\n💪 Fitness salonu\n♨️ Hamam & Sauna\n\nDaha fazla bilgi ister misiniz?"
    
    else:
        return "Anlıyorum! 😊\n\nDaha detaylı bilgi için yetkili arkadaşımız sizi arayabilir.\n\nSize nasıl yardımcı olabilirim?"


@app.route("/webhook", methods=['POST'])
def whatsapp_webhook():
    """Twilio'dan gelen WhatsApp mesajlarını al"""
    
    # Gelen mesaj
    incoming_msg = request.values.get('Body', '').strip()
    from_number = request.values.get('From', '')
    
    print(f"📱 Mesaj geldi: {from_number}")
    print(f"💬 İçerik: {incoming_msg}")
    
    # Yanıt oluştur
    bot_response = get_bot_response(incoming_msg)
    
    # Twilio'ya gönder
    resp = MessagingResponse()
    resp.message(bot_response)
    
    print(f"🤖 Yanıt: {bot_response}")
    
    return str(resp)


@app.route("/")
def home():
    """Test endpoint"""
    return """
    <h1>Akropol AI Asistan 🤖</h1>
    <p>WhatsApp bot çalışıyor!</p>
    <p>Webhook URL: /webhook</p>
    """


if __name__ == "__main__":
    print("🚀 Akropol bot başlatılıyor...")
    print("📍 http://localhost:5000")
    app.run(debug=True, port=5000)
