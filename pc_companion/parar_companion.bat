@echo off
chcp 65001 >nul
title Parar Spotify M5 Companion
echo [*] Encerrando Spotify M5 Companion...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*spotify_m5_companion.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo [+] Companion encerrado com sucesso!
timeout /t 2 >nul
