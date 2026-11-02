import asyncio
import numpy as np
import pyaudiowpatch as pyaudio
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
BLOCK_SIZE = 2048
BAUD_RATE = 115200

# Faixas de frequência logarítmicas de 35 Hz a 15.000 Hz
FREQ_EDGES = np.logspace(np.log10(35), np.log10(15000), NUM_BANDS + 1)

# Calibração fina de estúdio por frequência
# [ 40Hz,  63Hz, 100Hz, 160Hz,  250,  400,  630,   1k, 1.6k, 2.5k,   4k, 6.3k,   8k,  10k,  12k,  15k ]
WEIGHTS = np.array([
    1.80, 1.75, 1.65, 1.45,  # Graves: Punch no bumbo e contrabaixo
    1.20, 1.25, 1.25, 1.10,  # Médios-Graves
    1.20, 1.35, 1.55, 1.75,  # Médios-Agudos
    1.90, 2.05, 2.15, 2.20   # Agudos
])

# Janela Hanning e normalização
WINDOW = np.hanning(BLOCK_SIZE)
WINDOW_SUM = np.sum(WINDOW)

# Escala em Decibéis (dBFS):
DB_FLOOR = -40.0
DB_CEIL  = -2.0
DB_RANGE = DB_CEIL - DB_FLOOR

# Balística de medidor de estúdio (PPM)
smooth_bars = np.zeros(NUM_BANDS)
DECAY_FACTOR = 0.82

spectrum_data = [0] * NUM_BANDS
is_spotify_paused = False

FFT_FREQS = None
CHANNELS = 2

def find_m5stack_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        # Identifica chips USB-Serial comuns no M5Stack/ESP32 (CP210x, CH9102, CH34x, ESP USB JTAG)
        if any(chip in desc or chip in hwid for chip in ["cp210", "10c4:ea60", "m5stack", "ch9102", "1a86:55d4", "ch340", "1a86:7523", "303a:"]):
            return p.device
    # Se houver alguma porta serial conectada que nao seja a COM1 fisica padrao
    available = [p.device for p in ports if p.device.upper() != "COM1"]
    if available:
        return available[0]
    return None

def audio_callback(in_data, frame_count, time_info, status):
    global spectrum_data, smooth_bars, is_spotify_paused, FFT_FREQS, CHANNELS
    
    audio = np.frombuffer(in_data, dtype=np.float32)
    if CHANNELS > 1:
        mono = audio[0::CHANNELS]
    else:
        mono = audio

    # Garante tamanho exato do bloco para a FFT
    if len(mono) < BLOCK_SIZE:
        mono = np.pad(mono, (0, BLOCK_SIZE - len(mono)))
    else:
        mono = mono[:BLOCK_SIZE]

    # 1. Corte rápido de silêncio
    rms = np.sqrt(np.mean(mono ** 2))
    if rms < 0.0004 or is_spotify_paused:
        smooth_bars *= 0.65
        spectrum_data = [int(v) for v in smooth_bars]
        return (None, pyaudio.paContinue)

    # 2. FFT com amplitude absoluta
    windowed = mono * WINDOW
    fft = np.abs(np.fft.rfft(windowed)) * (2.0 / WINDOW_SUM)

    # 3. Extrai pico por banda
    band_values = []
    for i in range(NUM_BANDS):
        low_f = FREQ_EDGES[i]
        high_f = FREQ_EDGES[i + 1]
        mask = (FFT_FREQS >= low_f) & (FFT_FREQS < high_f)
        
        if np.any(mask):
            raw_peak = np.max(fft[mask])
        else:
            raw_peak = 0.0
            
        calibrated_val = raw_peak * WEIGHTS[i]
        band_values.append(calibrated_val)

    band_values = np.array(band_values)

    # 4. Mapeamento em Decibéis (dBFS)
    db_values = 20.0 * np.log10(np.maximum(band_values, 1e-5))
    target_pct = np.clip((db_values - DB_FLOOR) / DB_RANGE * 100.0, 0.0, 100.0)

    # 5. Balística profissional (PPM)
    for i in range(NUM_BANDS):
        if target_pct[i] >= smooth_bars[i]:
            smooth_bars[i] = target_pct[i]
        else:
            smooth_bars[i] = smooth_bars[i] * DECAY_FACTOR + target_pct[i] * (1.0 - DECAY_FACTOR)

    spectrum_data = [int(v) for v in smooth_bars]
    return (None, pyaudio.paContinue)

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
    global FFT_FREQS, CHANNELS
    
    # Localiza o dispositivo WASAPI Loopback real do Windows
    p = pyaudio.PyAudio()
    try:
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError:
        print("[!] WASAPI is not available on this Windows system.")
        sys.exit(1)

    default_speakers = p.get_device_info_by_index(wasapi_info['defaultOutputDevice'])
    loopback_device = None
    for d in p.get_loopback_device_info_generator():
        if default_speakers['name'] in d['name']:
            loopback_device = d
            break
            
    if not loopback_device:
        loopback_device = next(p.get_loopback_device_info_generator())

    sample_rate = int(loopback_device['defaultSampleRate'])
    CHANNELS = loopback_device['maxInputChannels']
    FFT_FREQS = np.fft.rfftfreq(BLOCK_SIZE, 1.0 / sample_rate)

    print(f"[*] Loopback Device: {loopback_device['name']} (Sample Rate: {sample_rate} Hz, Channels: {CHANNELS})")
    print("[*] PURE DIGITAL WASAPI LOOPBACK ACTIVE (Microphone is 100% disabled)!")

    stream = p.open(
        format=pyaudio.paFloat32,
        channels=CHANNELS,
        rate=sample_rate,
        input=True,
        input_device_index=loopback_device['index'],
        frames_per_buffer=BLOCK_SIZE,
        stream_callback=audio_callback
    )

    try:
        stream.start_stream()
        print("[*] Companion em execucao. Aguardando M5Stack...")
        
        while True:
            target_port = find_m5stack_port()
            if not target_port:
                # M5Stack nao conectado ainda, aguarda e tenta novamente
                await asyncio.sleep(2)
                continue

            print(f"[*] M5Stack detectado na porta {target_port}. Conectando...")
            try:
                ser = serial.Serial(target_port, BAUD_RATE, timeout=0.05)
                print(f"[+] Conectado com sucesso ao M5Stack na porta {target_port}!")
            except Exception as e:
                print(f"[!] Falha ao abrir {target_port}: {e}. Tentando novamente em 2s...")
                await asyncio.sleep(2)
                continue

            last_meta_time = 0
            try:
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

            except (serial.SerialException, OSError) as e:
                print(f"[!] Conexao com M5Stack perdida ({e}). Aguardando reconexao...")
            finally:
                try:
                    ser.write(b"DISCONNECT\n")
                    ser.flush()
                except Exception:
                    pass
                try:
                    ser.close()
                except Exception:
                    pass
                await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\n[*] Stopping companion...")
    finally:
        try:
            stream.stop_stream()
            stream.close()
            p.terminate()
        except Exception:
            pass
        print("[*] Disconnected cleanly.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
EOF
