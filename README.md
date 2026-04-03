# Voice Transcriptor

A lightweight Windows app that transcribes your speech and pastes it into any text field. Press a hotkey, speak, press the hotkey again — the transcribed text appears right where your cursor was.

![Windows](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)

## Features

- **Global hotkey** — trigger recording from any app (default: `Ctrl+Alt+Space`)
- **Auto language detection** — works with any language supported by AssemblyAI
- **Smart field memory** — remembers which field was focused when you started recording, so you can switch windows while speaking
- **Transcription history** — last 50 transcriptions saved and accessible from the tray
- **Network retry** — automatic retries on connection errors
- **System tray** — runs quietly in the background
- **Autostart** — optional launch on Windows startup

## Quick Start (Download)

1. Go to [Releases](../../releases) and download `VoiceTranscriptor.exe`
2. Run the exe — a settings window will open on first launch
3. Paste your [AssemblyAI API key](https://www.assemblyai.com/dashboard/signup) and click **Save**
4. Press `Ctrl+Alt+Space` to start recording, press again to stop and transcribe

## Build from Source

**Prerequisites:** Python 3.10+ and pip.

```bash
# Clone the repository
git clone https://github.com/igornersisian/voice-transcriptor.git
cd voice-transcriptor

# Install dependencies
pip install -r requirements.txt

# Run directly
python main.py

# Or build a standalone exe
build.bat
# Output: dist/VoiceTranscriptor.exe
```

> **Note:** `pyaudio` may require Visual C++ Build Tools on some systems. If `pip install` fails, try: `pip install pipwin && pipwin install pyaudio`

## Usage

| Action | How |
|---|---|
| Start/stop recording | Press your hotkey (default `Ctrl+Alt+Space`) |
| Open settings | Right-click tray icon → **Settings** |
| View history | Right-click tray icon → **History** |
| Copy from history | Click **Copy** on any entry |
| Quit | Right-click tray icon → **Quit** |

## Configuration

Settings are stored in `%APPDATA%\VoiceTranscriptor\config.json`:

- **API Key** — your AssemblyAI key ([get one free](https://www.assemblyai.com/dashboard/signup))
- **Hotkey** — any combination like `ctrl+m`, `ctrl+alt+space`, `f9`, etc.
- **Microphone** — select a specific input device or use system default
- **Autostart** — launch on Windows boot

## Tech Stack

Python, Tkinter + CustomTkinter, PyAudio, AssemblyAI API, pystray, keyboard, PyInstaller

## License

MIT
