import asyncio
import numpy as np
import sounddevice as sd
import serial
import serial.tools.list_ports
import pyautogui
import time
import sys
from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus
)

NUM_BANDS = 16
SAMPLE_RATE = 44100
BLOCK_SIZE = 2048
BAUD_RATE = 115200

# Faixas de frequência logarítmicas de 35 Hz a 15.000 Hz
FREQ_EDGES = np.logspace(np.log10(35), np.log10(15000), NUM_BANDS + 1)
FFT_FREQS = np.fft.rfftfreq(BLOCK_SIZE, 1.0 / SAMPLE_RATE)

# Pré-cálculo da inclinação padrão de masterização (+3.5 dB por oitava relativa a 1 kHz)
# Isso equilibra a distribuição física natural do ruído rosa sem inventar frequências falsas
TILTS = []
for i in range(NUM_BANDS):
    center_freq = np.sqrt(FREQ_EDGES[i] * FREQ_EDGES[i + 1])
    octaves_from_1k = np.log2(center_freq / 1000.0)
    tilt_db = octaves_from_1k * 3.5
    TILTS.append(10.0 ** (tilt_db / 20.0))
TILTS = np.array(TILTS)

# Janela Hanning e fator de normalização de energia
WINDOW = np.hanning(BLOCK_SIZE)
WINDOW_SUM = np.sum(WINDOW)

# Escala de Decibéis de Estúdio (dBFS)
DB_FLOOR = -50.0  # Nível de silêncio / base (0% na barra)
DB_CEIL  = -2.0   # Nível de pico máximo (100% na barra)
DB_RANGE = DB_CEIL - DB_FLOOR

# Balística de medidor de estúdio (Ataque rápido, queda suave)
smooth_bars = np.zeros(NUM_BANDS)
DECAY_FACTOR = 0.84  # Coeficiente de queda natural (~200ms)

spectrum_data = [0] * NUM_BANDS
is_spotify_paused = False

def find_m5stack_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        if "cp210" in desc or "10c4:ea60" in hwid or "m5stack" in desc or p.device == "COM5":
            return p.device
    if len(ports) > 0:
        return ports[0].device
    return "COM5"

def audio_callback(indata, frames, time_info, status):
    global spectrum_data, smooth_bars, is_spotify_paused
    if status:
        return

    mono = indata[:, 0]
    
    # 1. Energia RMS geral para corte imediato de silêncio
    rms = np.sqrt(np.mean(mono ** 2))
    if rms < 0.0005 or is_spotify_paused:
        smooth_bars *= 0.7  # Cai rapidamente para zero
        spectrum_data = [int(v) for v in smooth_bars]
        return

    # 2. FFT com calibração de amplitude absoluta (0 dBFS = 1.0)
    windowed = mono * WINDOW
    fft = np.abs(np.fft.rfft(windowed)) * (2.0 / WINDOW_SUM)

    # 3. Cálculo de energia RMS por banda de frequência
    band_energies = []
    for i in range(NUM_BANDS):
        low_f = FREQ_EDGES[i]
        high_f = FREQ_EDGES[i + 1]
        mask = (FFT_FREQS >= low_f) & (FFT_FREQS < high_f)
        
        if np.any(mask):
            # RMS da energia naquela faixa
            mag = np.sqrt(np.mean(fft[mask] ** 2))
        else:
            mag = 0.0
            
        # Aplica a inclinação de referência de estúdio (+3.5 dB/oitava)
        calibrated_mag = mag * TILTS[i]
        band_energies.append(calibrated_mag)

    band_energies = np.array(band_energies)

    # 4. Conversão para escala logarítmica real de estúdio (Decibéis dBFS)
    # Evita log(0) com piso de 1e-6 (-120 dB)
    db_values = 20.0 * np.log10(np.maximum(band_energies, 1e-6))

    # Mapeamento linear dos decibéis para 0% a 100%
    # [-50 dBFS = 0%] até [-2 dBFS = 100%]
    target_pct = np.clip((db_values - DB_FLOOR) / DB_RANGE * 100.0, 0.0, 100.0)

    # 5. Balística de Medidor Profissional (Peak Program Meter):
    # - Ataque instantâneo: se o som subiu, a barra sobe no mesmo frame
    # - Decaimento suave: se o som baixou, a barra desce com inércia física
    for i in range(NUM_BANDS):
        if target_pct[i] >= smooth_bars[i]:
            smooth_bars[i] = target_pct[i]
        else:
            smooth_bars[i] = smooth_bars[i] * DECAY_FACTOR + target_pct[i] * (1.0 - DECAY_FACTOR)

    spectrum_data = [int(v) for v in smooth_bars]

async def get_spotify_media():
    global is_spotify_paused
    try:
        sessions = await MediaManager.request_async()
        current = sessions.get_current_session()
        
        if not current or "spotify" not in (current.source_app_user_model_id or "").lower():
            for s in sessions.get_sessions():
                if "spotify" in (s.source_app_user_model_id or "").lower():
                    current = s
                    break

        if current:
            playback_info = current.get_playback_info()
            if playback_info:
                is_spotify_paused = (playback_info.playback_status != PlaybackStatus.PLAYING)

            props = await current.try_get_media_properties_async()
            timeline = current.get_timeline_properties()
            
            title = props.title if props.title else "Unknown Track"
            artist = props.artist if props.artist else "Unknown Artist"
            
            if is_spotify_paused:
                artist = "[PAUSED] " + artist

            progress = 0
            if timeline and timeline.end_time.total_seconds() > 0:
                progress = int((timeline.position.total_seconds() / timeline.end_time.total_seconds()) * 100)
                
            return title, artist, progress
    except Exception:
        pass
    is_spotify_paused = True
    return "Spotify Paused", "Press Play on PC", 0

def handle_m5_command(cmd):
    if cmd == "CMD:PLAYPAUSE":
        pyautogui.press('playpause')
    elif cmd == "CMD:NEXT":
        pyautogui.press('nexttrack')
    elif cmd == "CMD:PREV":
        pyautogui.press('prevtrack')

async def main():
    target_port = find_m5stack_port()
    print(f"[*] Connecting to M5Stack on {target_port} at {BAUD_RATE} baud...")
    try:
        ser = serial.Serial(target_port, BAUD_RATE, timeout=0.05)
    except Exception as e:
        print(f"[!] Error opening {target_port}: {e}")
        sys.exit(1)

    device_id = None
    devices = sd.query_devices()
    for idx, d in enumerate(devices):
        name = d['name'].lower()
        if d['max_input_channels'] > 0 and ('loopback' in name or '[loopback]' in name):
            device_id = idx
            break

    print(f"[*] Audio loopback device: {device_id if device_id is not None else 'Default'}")
    print("[*] Studio Reference RTA Calibration active (dBFS Scale: -50 dB to -2 dB)!")
    print("[*] Press Ctrl+C to disconnect and stop.")

    stream = sd.InputStream(
        device=device_id,
        channels=2,
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        callback=audio_callback
    )

    last_meta_time = 0
    try:
        with stream:
            while True:
                # 1. Envia espectro (~30 FPS)
                fft_msg = "FFT|" + ",".join(map(str, spectrum_data)) + "\n"
                ser.write(fft_msg.encode('utf-8'))

                # 2. Atualiza metadados do Spotify
                now = time.time()
                if now - last_meta_time > 0.8:
                    title, artist, prog = await get_spotify_media()
                    meta_msg = f"META|{title}|{artist}|{prog}\n"
                    ser.write(meta_msg.encode('utf-8'))
                    last_meta_time = now

                # 3. Lê comandos dos botões
                if ser.in_waiting:
                    incoming = ser.readline().decode('utf-8', errors='ignore').strip()
                    if incoming.startswith("CMD:"):
                        handle_m5_command(incoming)

                await asyncio.sleep(0.03)

    except KeyboardInterrupt:
        print("\n[*] Stopping companion...")
    finally:
        try:
            print("[*] Sending disconnect signal to M5Stack...")
            ser.write(b"DISCONNECT\n")
            ser.flush()
            time.sleep(0.1)
            ser.close()
        except Exception:
            pass
        print("[*] Disconnected cleanly.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
EOF
