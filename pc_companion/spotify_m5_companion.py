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
last_audio_time = 0
is_media_playing = False

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
    global spectrum_data, smooth_bars, last_audio_time, FFT_FREQS, CHANNELS
    
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

    # 1. Corte de silêncio e medição de energia real (RMS)
    rms = np.sqrt(np.mean(mono ** 2))
    if rms > 0.0004:
        last_audio_time = time.time()
    else:
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

async def get_media_info():
    global is_media_playing
    is_spotify_open = False
    is_spotify_paused = False
    try:
        sessions = await MediaManager.request_async()
        current = sessions.get_current_session()
        
        # Procura qualquer sessão de mídia ativa (priorizando Spotify ou navegadores/YouTube)
        all_sessions = sessions.get_sessions()
        playing_session = None
        spotify_session = None

        for s in all_sessions:
            app_id = (s.source_app_user_model_id or "").lower()
            if "spotify" in app_id:
                spotify_session = s
                is_spotify_open = True
                s_info = s.get_playback_info()
                if s_info and s_info.playback_status != PlaybackStatus.PLAYING:
                    is_spotify_paused = True

            info = s.get_playback_info()
            if info and info.playback_status == PlaybackStatus.PLAYING:
                if not playing_session or "spotify" in app_id:
                    playing_session = s

        active = playing_session or current or spotify_session
        if not active and all_sessions:
            active = all_sessions[0]

        if active:
            playback_info = active.get_playback_info()
            is_media_playing = (playback_info.playback_status == PlaybackStatus.PLAYING) if playback_info else False
            
            props = await active.try_get_media_properties_async()
            timeline = active.get_timeline_properties()
            
            raw_title = props.title if (props and props.title) else ""
            raw_artist = props.artist if (props and props.artist) else ""
            app_id = (active.source_app_user_model_id or "").lower()

            if not raw_artist:
                if "spotify" in app_id:
                    raw_artist = "Spotify"
                elif any(b in app_id for b in ["chrome", "msedge", "edge", "firefox", "brave"]):
                    raw_artist = "YouTube / Web"
                else:
                    raw_artist = "PC Audio"

            # Sanitiza o delimitador '|' para não corromper o enquadramento do protocolo serial
            title = raw_title.replace('|', '-')
            artist = raw_artist.replace('|', '-')

            if not is_media_playing and artist:
                artist = "[PAUSED] " + artist

            progress = 0
            if timeline and timeline.end_time.total_seconds() > 0:
                progress = int((timeline.position.total_seconds() / timeline.end_time.total_seconds()) * 100)

            if title:
                return title, artist, progress, is_media_playing, is_spotify_open, is_spotify_paused
    except Exception:
        pass

    is_media_playing = False
    return None, None, 0, False, is_spotify_open, is_spotify_paused

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
            is_standby = False
            last_sent_title = ""
            last_sent_artist = ""
            last_sent_prog = -1
            is_spotify_open = False
            is_spotify_paused = False

            try:
                while True:
                    now = time.time()
                    # Se Spotify estiver aberto e pausado, espera 60 segundos antes de entrar em standby
                    standby_delay = 60.0 if (is_spotify_open and is_spotify_paused) else 3.0
                    has_audio = (now - last_audio_time < standby_delay)

                    # 1. Atualiza metadados da mídia periodicamente (~0.8s)
                    if now - last_meta_time > 0.8:
                        meta_title, meta_artist, meta_prog, is_playing, is_spotify_open, is_spotify_paused = await get_media_info()
                        last_meta_time = now

                    # Determina se há som tocando no PC ou mídia em execução
                    should_be_active = has_audio or is_media_playing

                    if should_be_active:
                        if is_standby:
                            is_standby = False

                        if is_spotify_open and is_spotify_paused and not meta_title:
                            title = "Spotify Paused"
                            artist = "Press Play on PC"
                        else:
                            title = meta_title or "PC Audio Visualizer"
                            artist = meta_artist or "Pioneer DJ Mode"
                        prog = meta_prog

                        # Envia metadados se mudaram ou em intervalo regular
                        if title != last_sent_title or artist != last_sent_artist or abs(prog - last_sent_prog) >= 1 or (now - last_meta_time > 0.75):
                            meta_msg = f"META|{title}|{artist}|{prog}\n"
                            ser.write(meta_msg.encode('utf-8'))
                            last_sent_title = title
                            last_sent_artist = artist
                            last_sent_prog = prog

                        # Envia espectro (~30 FPS)
                        fft_msg = "FFT|" + ",".join(map(str, spectrum_data)) + "\n"
                        ser.write(fft_msg.encode('utf-8'))

                    else:
                        # Entra em tela de espera caso não haja som nem mídia ativa
                        if not is_standby:
                            is_standby = True
                            ser.write(b"DISCONNECT\n")
                            ser.flush()
                            last_sent_title = ""
                            last_sent_artist = ""
                            last_sent_prog = -1

                    # 2. Lê comandos dos botões frontais do M5Stack
                    if ser.in_waiting:
                        incoming = ser.readline().decode('utf-8', errors='ignore').strip()
                        if incoming.startswith("CMD:"):
                            handle_m5_command(incoming)

                    await asyncio.sleep(0.03 if should_be_active else 0.1)

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
