@echo off
chcp 65001 >nul
title Reiniciar Spotify M5 Companion
cd /d "%~dp0"
echo [*] Encerrando versao anterior...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*spotify_m5_companion.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 1 >nul
echo [*] Iniciando versao atualizada em segundo plano...
wscript.exe "%~dp0iniciar_companion.vbs"
echo [+] Companion reiniciado com sucesso!
timeout /t 2 >nul
