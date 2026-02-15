# 🚀 AKROPOL BOT - BAŞLATMA REHBERİ

## ADIM 1: Kurulum (5 dakika)

```bash
# Terminal aç (PowerShell)
cd "C:\Users\PCkopat\OneDrive\Desktop\Yeni klasör\akropol-bot"

# Python sanal ortam
python -m venv venv

# Aktifleştir
venv\Scripts\activate

# Paketleri yükle
pip install -r requirements.txt
```

## ADIM 2: Ayarları Yap (2 dakika)

1. `.env.example` dosyasını kopyala
2. `.env` olarak kaydet
3. İçine Aura OS'tan aldığın bilgileri yapıştır:
   - Twilio Account SID
   - Twilio Auth Token
   - OpenAI API Key

## ADIM 3: Bot'u Başlat

```bash
python app.py
```

Çıktı:
```
🚀 Akropol bot başlatılıyor...
📍 http://localhost:5000
```

## ADIM 4: Test Et

1. Tarayıcıda aç: http://localhost:5000
2. "Akropol AI Asistan 🤖" yazısını görmelisin
3. ✅ Çalışıyor!

## ADIM 5: WhatsApp Bağla (sonra)

Ngrok ile public URL alıp Twilio'ya bağlayacağız.

---

**SORU?** Bana sor! 💬
