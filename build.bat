@echo off
setlocal
cd /d "%~dp0"

set APP_NAME=VoiceTranscriptor
set ENTRY=main.py
set ICON=assets\icon.ico

echo Installing PyInstaller...
pip install pyinstaller --quiet

echo Building %APP_NAME%...
pyinstaller ^
  --name "%APP_NAME%" ^
  --onefile ^
  --windowed ^
  --icon "%ICON%" ^
  --collect-all customtkinter ^
  --collect-all pystray ^
  --hidden-import assemblyai ^
  --hidden-import pyaudio ^
  --hidden-import keyboard ^
  --hidden-import pyperclip ^
  --hidden-import pyautogui ^
  --hidden-import PIL._tkinter_finder ^
  --hidden-import pystray._win32 ^
  --add-data "assets;assets" ^
  --clean ^
  "%ENTRY%"

if errorlevel 1 (
  echo Build FAILED.
  pause
  exit /b 1
)

echo.
echo Build complete: dist\%APP_NAME%.exe
echo.
pause
