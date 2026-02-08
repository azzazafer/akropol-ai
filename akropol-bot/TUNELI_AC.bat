@echo off
cls
echo ==========================================
echo   AKROPOL AI ASISTAN - TUNEL ACICI
echo ==========================================
echo.
echo 1. Ngrok Token Ayarlaniyor...
tools\ngrok.exe config add-authtoken 39HR2oSs5OswLgZ9SC4PvFJnxcL_7kpfMyUPuNxcuUpYHSiZC
echo.
echo 2. Tunel Aciliyor...
echo.
echo ******************************************************
echo  ACILAN EKRANDA 'Forwarding' YAZAN SATIRI BUL.
echo  ORNEK: Forwarding https://abcd-1234.ngrok-free.app
echo  O LINKI KOPYALA VE TWILIO'YA YAPISTIR.
echo ******************************************************
echo.
tools\ngrok.exe http 5000
pause
