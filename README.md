# Jarvis Assistant

A voice assistant for my Linux desktop that can use tools (open apps, search files, check system stats, etc). It runs fully on my own machine through Ollama, so no cloud, no accounts, no telemetry.

It's Linux only. I built it on Linux Mint (Cinnamon) and the install script uses `apt`, so Debian/Ubuntu based distros should work. You need Python 3.10 or newer.

## How it's designed

If you want to add stuff to this, read this part first. A lot of the code looks the way it does because of these.

1. **No background daemon.** `bin/jarvis` is a single script that runs when I hit a keyboard shortcut. It does one thing and exits. When I'm not using it nothing from this project is running (0% CPU, 0 MB RAM). This was a hard requirement for me, not just an optimization.
2. **The model can only use the tools I give it.** There's no shell tool, no `sudo`, no `rm`, and no writing files outside `file_search_root`. The model can only call functions registered in `src/tools/registry.py`, with the arguments in their schema. If it isn't registered it doesn't exist as far as the model knows.
3. **Tiered permissions.** Read-only tools (checking stats, searching filenames) run right away. Things that change stuff (opening an app, zipping files) get logged. Riskier stuff (closing an app, editing a file) pops up a real confirmation dialog that says exactly what's about to happen, and if the dialog can't be shown it defaults to *no*.
4. **Ollama unloads itself.** Every request sets a short `keep_alive` (in `config.yaml`), so the model is gone from RAM a few seconds after Jarvis exits and I never have to remember to stop anything. The downside: if a confirm-tier action (close_app, edit_file) is waiting on me and that wait plus my response time is longer than `keep_alive`, Ollama can unload the model in the middle of the conversation. Then my answer has to wait for a cold reload before Jarvis continues. If that bugs you just raise `keep_alive`.

## Setup

```bash
git clone <this repo's url>
cd jarvis-assistant
./scripts/install.sh
ollama pull qwen3:8b
```

The install script copies `config.example.yaml` to `config.yaml` if you don't have one yet. `config.yaml` is your own personal config and it's git-ignored, so edit that one (add the apps you actually have installed, etc). If you set things up by hand, just run `cp config.example.yaml config.yaml`.

That gets you **text mode with espeak-ng TTS**, and you don't need to do anything else for that. Voice input and the nicer TTS voice are both optional and covered below.

### Try it in text mode first

```bash
source .venv/bin/activate
./bin/jarvis --text "what's my current CPU and RAM usage?"
./bin/jarvis --text "open firefox" --speak
```

### Voice input (the fun part)

Voice needs whisper.cpp built locally:

```bash
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
cmake -B build && cmake --build build -j --config Release
sh ./models/download-ggml-model.sh base.en
```

Then in your `config.yaml` update `voice.whisper_binary` and `voice.whisper_model` to point at what whisper.cpp just built. The defaults in the config assume whisper.cpp is cloned right next to this project (like `~/whisper.cpp` and `~/jarvis-assistant`), so change them if yours is somewhere else.

`voice.correction_enabled` is on by default. It sends the raw transcript back through the model one time, checks it against `voice.custom_vocabulary`, and fixes obvious mishears before Jarvis acts on them. It costs one extra small, fast call per voice command, so turn it off if you don't want that.

### Better voice (piper)

`tts.engine` in `config.yaml` is `"espeak"` by default because that's what `install.sh` sets up, so it works with no extra steps. `"piper"` sounds a lot more natural, but it needs two things that `install.sh` doesn't set up (`piper-tts` itself is already in `requirements.txt`):

```bash
mkdir -p ~/piper-voices
# download a voice model + its .json config from
# https://github.com/rhasspy/piper/blob/master/VOICES.md into ~/piper-voices/
# (the default in config.yaml expects en_US-hfc_male-medium.onnx there)

sudo apt install -y ffmpeg   # used to apply tts.volume
```

Then set `tts.engine: "piper"` in `config.yaml`. If something is missing, Jarvis quietly falls back to espeak-ng instead of crashing. So if switching to piper doesn't change how it sounds, check that first.

### Hotkey (Cinnamon)

1. Menu > Preferences > Keyboard > Shortcuts > Custom Shortcuts
2. Add a new one:
   - Name: `Jarvis (voice)`
   - Command: `/full/path/to/jarvis-assistant/.venv/bin/python /full/path/to/jarvis-assistant/bin/jarvis --voice --speak`
   - Key combo: anything that's free, `Super+J` recommended
3. Optionally add a second one for typing instead of talking:
   - Command: `... bin/jarvis --text-prompt --speak`

Then you press the hotkey, talk, and it answers. Nothing is left running afterwards.

You can leave off `--speak` if you only want silent text/notification replies. One thing to know: confirm-tier prompts (close_app, edit_file) only read their yes/no question out loud if `--speak` is there too. Without it they use the same zenity/terminal dialog that `--text` mode uses, since a run you didn't ask to be spoken shouldn't suddenly start talking halfway through.

### Memory between calls (optional)

By default every call to Jarvis starts fresh and nothing carries over. If you set `memory.enabled: true` in `config.yaml`, it remembers the last few turns for `memory.ttl_seconds` after each reply, so short follow-ups like "now edit it" or "yes" make sense. It's off by default because of a small privacy tradeoff: your recent prompts and replies sit in a plain JSON file (`memory.session_path`) between calls instead of disappearing as soon as Jarvis exits. You can run `./bin/jarvis --reset` any time to delete that file and end the conversation early without waiting for the TTL.

### Debugging

`debug: false` at the top of `config.yaml` keeps everything quiet. Set it to `true` when you're troubleshooting tool calling or TTS and it writes full prompts, tool call arguments, and which TTS engine got picked to `/tmp/jarvis-debug.log`. Turn it back off after, because that file is plain text and most systems let anyone read `/tmp`.

## Adding new tools

Three steps, all in `src/tools/registry.py`:

1. Write the function in the right file in `src/tools/` (or make a new file for a new category).
2. Add its JSON schema to `TOOL_SCHEMAS`.
3. Add it to `_DISPATCH` with a tier: `"read"`, `"action"`, or `"confirm"`.

For a `"confirm"` tool you should also think about adding an entry to `_DESCRIBERS` if the generic `"<tool name> (<args>)"` text wouldn't tell the user what they're really approving. `app_tools.describe_close` is a good example, since it reports how many processes will actually be closed.

Don't add a tool that takes a raw shell command, an arbitrary file path outside `file_search_root`, or an arbitrary PID. That opens up exactly the problem this project is built to avoid. If you want it to do something more powerful, write a narrow function for that one specific thing instead of a general escape hatch.

## What it can't do (on purpose)

No shell execution, no `sudo`, no killing random processes by PID, no installing packages, no file access outside `file_search_root` (my whole home folder by default, so narrow it in `config.yaml` if you want it to see less), and no touching this project's own folder no matter what that setting is. These aren't missing features I'll add later. They're left out so that a bad prompt (or the model just having a bad day) can't do real damage.

The one exception is `edit_file`, which **can** overwrite existing files (it never deletes and never creates new ones). It's `"confirm"` tier so it always needs an explicit yes, it makes a timestamped backup before writing, it refuses a list of sensitive paths, and it refuses any file over 20,000 characters instead of silently cutting it off (see `_MAX_READ_CHARS` in `file_tools.py` for why). If "confirm dialog + automatic backup" isn't a strong enough guarantee for you, remove `edit_file` from `_DISPATCH` in `src/tools/registry.py`. Don't loosen the other limits to make up for it.

### About `close_app`

It matches on the *running process name* of an app's launch command, not on whatever Jarvis opened itself, so it closes **every** matching process at once. If you say "close terminal" with three terminal windows open, all three close. The confirmation dialog tells you how many it found before you approve.

A few `allowed_apps` entries (like the commented-out Steam game example in `config.example.yaml`) are marked `closable: false`. Games launched through `steam://` get handed to the Steam client, so there's no separate process for just that game, only `steam`. "Closing" one would really close your whole Steam client and everything running in it. Jarvis refuses these by name instead of doing that, so close them from Steam or in the game.