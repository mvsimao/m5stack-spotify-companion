@echo off
chcp 65001 >nul
title Spotify M5 Companion (Modo Debug / Visual)
cd /d "%~dp0"
echo [*] Encerrando instancias em segundo plano para liberar a porta COM...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*spotify_m5_companion.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo [*] Iniciando em modo interativo com logs no terminal:
echo.
python spotify_m5_companion.py
pause
