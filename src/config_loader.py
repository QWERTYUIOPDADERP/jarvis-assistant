"""
Loads config.yaml once when it's imported and checks it so the other files
don't have to.

A couple of things it cleans up:
- every allowed_apps entry ends up as {"cmd": str, "closable": bool},
  whether it was written as a plain string or a mapping in the yaml
- paths get ~ expanded (and the whisper paths are made relative to the
  project folder instead of wherever you ran the script from)

If the config is broken it just exits with a message, since that's way easier
than debugging a random KeyError in some other file.
"""

import os
import sys
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
_PROJECT_ROOT = _CONFIG_PATH.parent


def _fail(msg: str) -> None:
    sys.exit(f"jarvis: invalid config.yaml: {msg}")


def _load_raw() -> dict:
    try:
        with open(_CONFIG_PATH) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        _fail(f"no config.yaml found at {_CONFIG_PATH} - copy config.example.yaml to config.yaml first")
    except yaml.YAMLError as e:
        _fail(f"config.yaml isn't valid YAML ({e})")

    if not isinstance(data, dict):
        _fail("config.yaml must be a mapping at the top level")
    return data


def _require(data: dict, *path: str):
    # walks down the nested dict, bails if any key is missing
    node = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            _fail(f"missing required key '{'.'.join(path)}'")
        node = node[key]
    return node


def _normalize_allowed_apps(raw: dict) -> dict:
    # entries can be `name: "cmd"` or `name: {cmd: "...", closable: bool}`.
    # this turns both into the second form so app_tools only has one case to handle
    if not isinstance(raw, dict) or not raw:
        _fail("allowed_apps must be a non-empty mapping")

    normalized = {}
    for name, entry in raw.items():
        if isinstance(entry, str):
            normalized[name] = {"cmd": entry, "closable": True}
        elif isinstance(entry, dict):
            cmd = entry.get("cmd")
            if not cmd:
                _fail(f"allowed_apps.{name} is missing 'cmd'")
            normalized[name] = {"cmd": cmd, "closable": bool(entry.get("closable", True))}
        else:
            _fail(f"allowed_apps.{name} must be a string or a {{cmd, closable}} mapping")
    return normalized


def _resolve_project_relative(path_str: str) -> str:
    # expand ~, and if the path is relative make it relative to the project
    # root (not the current working dir, since the hotkey can launch from anywhere)
    expanded = os.path.expanduser(path_str)
    if not os.path.isabs(expanded):
        expanded = str((_PROJECT_ROOT / expanded).resolve())
    return expanded


def _build_config() -> dict:
    data = _load_raw()

    for path in (
        ("ollama", "host"), ("ollama", "model"), ("ollama", "keep_alive"),
        ("voice", "whisper_binary"), ("voice", "whisper_model"), ("voice", "sample_rate"),
        ("memory", "enabled"), ("memory", "session_path"),
        ("allowed_apps",), ("file_search_root",),
        ("logging", "enabled"), ("logging", "path"),
        ("tts", "engine"),
    ):
        _require(data, *path)

    data.setdefault("debug", False)

    data["allowed_apps"] = _normalize_allowed_apps(data["allowed_apps"])

    data["file_search_root"] = os.path.expanduser(data["file_search_root"])
    data["logging"]["path"] = os.path.expanduser(data["logging"]["path"])
    data["logging"].setdefault("max_lines", 5000)
    data["memory"]["session_path"] = os.path.expanduser(data["memory"]["session_path"])

    for key in ("whisper_binary", "whisper_model"):
        data["voice"][key] = _resolve_project_relative(data["voice"][key])

    if data.get("tts", {}).get("piper_model"):
        data["tts"]["piper_model"] = os.path.expanduser(data["tts"]["piper_model"])

    if data["tts"]["engine"] not in ("piper", "espeak"):
        _fail("tts.engine must be 'piper' or 'espeak'")

    return data


CONFIG = _build_config()