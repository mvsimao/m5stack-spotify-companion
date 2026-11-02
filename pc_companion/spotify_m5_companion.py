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
# Aumentamos BLOCK_SIZE para 2048 para dobrar a resolução espectral nos graves (resolvendo 63 Hz)
BLOCK_SIZE = 2048
BAUD_RATE = 115200

# Noise Gate: limiar de silêncio
NOISE_GATE = 0.001

FREQ_EDGES = np.logspace(np.log10(35), np.log10(15000), NUM_BANDS + 1)
FFT_FREQS = np.fft.rfftfreq(BLOCK_SIZE, 1.0 / SAMPLE_RATE)

band_peaks = np.ones(NUM_BANDS) * 0.02
decay_rate = 0.985

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
    global spectrum_data, band_peaks, is_spotify_paused
    if status:
        return

    mono = indata[:, 0]
    
    # 1. Energia real do áudio (RMS)
    rms = np.sqrt(np.mean(mono ** 2))

    # 2. Se a música estiver pausada ou volume for silêncio absoluto
    if rms < NOISE_GATE or is_spotify_paused:
        spectrum_data = [0] * NUM_BANDS
        return

    windowed = mono * np.hanning(len(mono))
    fft = np.abs(np.fft.rfft(windowed)) / len(mono)

    bands = []
    for i in range(NUM_BANDS):
        low_f = FREQ_EDGES[i]
        high_f = FREQ_EDGES[i + 1]
        mask = (FFT_FREQS >= low_f) & (FFT_FREQS < high_f)
        
        if np.any(mask):
            raw_val = np.mean(fft[mask])
        else:
            raw_val = 0.0

        # Compensação espectral progressiva
        spectral_tilt = (i + 1) ** 0.65
        val = raw_val * spectral_tilt

        # AGC dinâmico
        band_peaks[i] = max(band_peaks[i] * decay_rate, val, 0.005)
        ratio = val / band_peaks[i]

        dynamic_val = (ratio ** 1.45) * 100.0
        norm = int(np.clip(dynamic_val, 0, 100))
        bands.append(norm)

    spectrum_data = bands

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
    print("[*] High-Resolution 2048 FFT loaded (63 Hz resolved)!")
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
