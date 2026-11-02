# 🎵 M5Stack Core Spotify Desk Companion & Pioneer DJ Audio Visualizer

[![Platform](https://img.shields.io/badge/Platform-ESP32%20%7C%20M5Stack%20Core-E7352C.svg?logo=espressif&logoColor=white)](#)
[![Framework](https://img.shields.io/badge/Framework-PlatformIO%20%7C%20Arduino-FF7A00.svg?logo=platformio&logoColor=white)](#)
[![Library](https://img.shields.io/badge/Library-M5Unified-00ADD8.svg)](#)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](#)
[![Audio DSP](https://img.shields.io/badge/Audio%20DSP-WASAPI%20Loopback%20%2B%202048%20FFT-blue.svg)](#-como-funciona--how-it-works)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](#)

A zero-latency, hardware desktop companion for **Spotify** built for the **M5Stack Core (ESP32)**. It captures digital audio directly from your headphones using **Windows WASAPI loopback**, analyzes the audio with a high-resolution 2048-sample FFT across 16 logarithmic bands, and renders a **Pioneer DJ-style segmented LED VU meter** with live track metadata and tactile hardware controls.

---

## 📌 O que é / What It Is

O **M5Stack Spotify Companion** é um dispositivo de mesa físico que fica ao lado do seu monitor exibindo em tempo real:
1. **Nome da música, artista e barra de progresso** do Spotify em reprodução.
2. **Equalizador de espectro reativo de 16 bandas** com visual clássico de mesas de estúdio da Pioneer DJ (LEDs verdes, amarelos e vermelhos segmentados com agulhas de pico brancas).
3. **Identificação exata das frequências na base:** de 40 Hz (sub-graves) até 15 kHz (agudos).
4. **Controle físico de mídia:** botões frontais no M5Stack para pausar, voltar ou avançar faixas sem sair da tela do jogo ou do código.

---

## ⚙️ Como funciona / How It Works

A solução combina um script em Python de alta fidelidade rodando em segundo plano no Windows com o firmware do M5Stack Core:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        WINDOWS PC (HOST AUDIO)                         │
│                                                                        │
│   [ Spotify Player ] ──> [ Windows Media Session (GSMTC API) ]         │
│                                  │ (Título, Artista, Progresso)        │
│   [ Fone de Ouvido ] ──> [ WASAPI Loopback Digital ]                   │
│                                  │ (Captura direta digital sem mic)    │
│                                  ▼                                     │
│                     [ Python DSP Engine (2048 FFT) ]                   │
│                     • Resolução de 21.5 Hz (captura 63 Hz perfeitamente)│
│                     • 16 Bandas logarítmicas (35 Hz a 15 kHz)          │
│                     • Auto Gain Control (AGC) + Compensação Espectral  │
│                     • Noise Gate instantâneo & detecção de pausa       │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ USB Serial (115200 Baud)
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                          M5STACK CORE (ESP32)                          │
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │  Starboy (feat. Daft Punk)                     [Título]        │   │
│   │  The Weeknd                                    [Artista]       │   │
│   │  ━━━━━━━━━━━━━━━━●───────────────────────      [Progresso]     │   │
│   ├────────────────────────────────────────────────────────────────┤   │
│   │  [■] [■] [■] [■] [■] [■] [■] [■] [■] [■] [■]   [Pioneer DJ LED] │   │
│   │  [■] [■] [■] [■] [■] [■] [■] [■] [■] [■] [■]   [Blocos 3px]   │   │
│   │  [■] [■] [■] [■] [■] [■] [■] [■] [■] [■] [■]   (Verde/Amarelo/ │   │
│   │                                                 Vermelho Peak) │   │
│   ├────────────────────────────────────────────────────────────────┤   │
│   │  40  63 100 160 250 400 630  1k 1.6 2.5  4k 6.3 8k 10k 12k 15k   │   │
│   └────────────────────────────────────────────────────────────────┘   │
│             [Btn A]                [Btn B]               [Btn C]       │
│           (Voltar)              (Play / Pause)         (Avançar)       │
└────────────────────────────────────────────────────────────────────────┘
```

### Destaques do Pipeline de Áudio (DSP)
* **Zero Microfone:** Como lê o áudio digital enviado para o fone via WASAPI, não capta barulho de teclado mecânico nem conversas na sala.
* **Resolução 2048 FFT ($\Delta f = 21.5\text{ Hz}$):** Elimina o "buraco de frequência" clássico em blocos menores de FFT, permitindo que os graves em **63 Hz** dancem com intensidade precisa.
* **Double Buffering via M5Canvas:** Todo o redesenho das barras ocorre em memória RAM e é transferido via DMA para o display, eliminando qualquer rasgo ou *flicker* na tela.
* **Noise Gate & Watchdog:** Se pausar a música, as barras caem para zero instantaneamente; se desconectar o cabo, o M5Stack volta sozinho para a tela de espera após 2.5s.

---

## 💻 Comandos para Rodar / Commands to Run

### Pré-requisitos
* **Driver Serial:** Instalar o driver Silicon Labs CP210x para o Windows reconhecer a porta COM (`COM5`).
* **Python 3.10+** instalado no Windows com `pip`.

---

### Passo 1: Clonar o Repositório
```powershell
git clone https://github.com/mvsimao/m5stack-spotify-companion.git
cd m5stack-spotify-companion
```

---

### Passo 2: Gravar o Firmware no M5Stack Core
Com o M5Stack conectado via cabo USB-C ao computador:

```powershell
py -m platformio run --target upload
```
> *A tela do M5Stack vai acender mostrando: **"Spotify Desk Companion - Waiting for PC connection..."***

---

### Passo 3: Instalar as Dependências no Windows
```powershell
cd pc_companion
py -m pip install -r requirements.txt
```

---

### Passo 4: Iniciar o Companion no PC
```powershell
py spotify_m5_companion.py
```
> *O script detecta a porta COM automaticamente, conecta ao áudio do fone e inicia a sincronização.*  
> *Coloque qualquer música para tocar no Spotify e o equalizador começará a reagir instantaneamente!*

---

### Passo 5: Encerrar ou Desconectar
Para parar o companion, aperte **`Ctrl + C`** no terminal.  
O script enviará o comando de desconexão e o M5Stack limpará a tela voltando para o modo de espera automaticamente.

---

## 🎛️ Controles Físicos do M5Stack

| Botão | Ação |
| :---: | :--- |
| **Botão A** *(Esquerda)* | Volta para a faixa anterior (`Previous Track`) |
| **Botão B** *(Centro)* | Alterna entre Reproduzir e Pausar (`Play / Pause`) |
| **Botão C** *(Direita)* | Avança para a próxima faixa (`Next Track`) |

---

## 📄 Licença

Distribuído sob a licença MIT. Sinta-se livre para customizar temas de cores, adicionar novos modos visuais ou integrar com outros players de áudio!
