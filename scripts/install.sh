#!/usr/bin/env bash
set -e

echo "== Jarvis Assistant install =="

# python3 -m venv gives a confusing "ensurepip is not available" error if
# python3-venv isn't installed, so install it first if it's missing
if ! python3 -c "import venv" 2>/dev/null; then
    echo "python3-venv not found - installing it first."
    sudo apt update
    sudo apt install -y python3-venv
fi

# make a personal config from the template if there isn't one yet
if [ ! -f config.yaml ]; then
    cp config.example.yaml config.yaml
    echo "Created config.yaml from config.example.yaml - edit it to add your own apps."
fi

# python deps. this is enough for text mode + espeak, voice input needs
# whisper.cpp too (see README)
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# system packages for voice input, confirmation popups, and notifications
sudo apt update
sudo apt install -y zenity libnotify-bin alsa-utils espeak-ng sox

echo ""
echo "Python deps installed into .venv/ (see requirements.txt)."
echo "System deps installed: zenity, arecord/aplay, notify-send, espeak-ng, sox."
echo ""
echo "Text mode works right now:"
echo "  source .venv/bin/activate"
echo "  ./bin/jarvis --text \"what's my current CPU and RAM usage?\" --speak"
echo ""
echo "Still left to do:"
echo "  1. ollama pull qwen3:8b"
echo "  2. build whisper.cpp for voice input (see README.md)"
echo "  3. set up the Cinnamon keyboard shortcut (see README.md)"
echo ""
echo "Optional: tts.engine in config.yaml is \"espeak\" by default (already"
echo "installed, works now). For the nicer \"piper\" voice see the piper section"
echo "in the README. It needs a voice model download and ffmpeg, and this"
echo "script doesn't install either of those."