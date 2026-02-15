@echo off
cd /d "%~dp0"
echo Iki sistem baslatiliyor...
start "Akropol Bot Sunucusu" cmd /k "call BOTU_BASLAT.bat"
start "Akropol Tüneli (Ngrok)" cmd /k "call TUNELI_AC.bat"
exit
