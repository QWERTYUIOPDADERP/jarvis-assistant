"""
Conversation memory between runs.

Jarvis still doesn't run in the background, so "memory" is just a small json
file with the last few messages and a timestamp. If you talk to it again within
ttl_seconds of the last reply it loads that file and keeps going, so follow-ups
like this work:

    "find the budget spreadsheet"    -> finds it
    "now read it to me"              -> "it" works because of the history
    "now zip it up"                  -> still has context

If you wait longer than ttl_seconds the history is ignored, which is how a
conversation "ends" (there's no way to hang up). It's also capped at
memory.max_turns so it can't grow forever and overflow the model's context.
"""

import json
import os
import time
from config_loader import CONFIG

_MEMORY_CFG = CONFIG.get("memory", {})
_ENABLED = _MEMORY_CFG.get("enabled", True)
_TTL_SECONDS = _MEMORY_CFG.get("ttl_seconds", 300)
_MAX_TURNS = _MEMORY_CFG.get("max_turns", 12)
_SESSION_PATH = os.path.expanduser(
    _MEMORY_CFG.get("session_path", "~/.local/share/jarvis-assistant/session.json")
)


def load_history() -> list[dict]:
    # returns the saved messages, or [] if memory is off, there's no file,
    # or it's been longer than the ttl
    if not _ENABLED:
        return []

    try:
        with open(_SESSION_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    if time.time() - data.get("timestamp", 0) > _TTL_SECONDS:
        return []

    return data.get("messages", [])


def append_turn(user_prompt: str, assistant_reply: str) -> None:
    # add this exchange and save. cuts off the oldest messages if it's over max_turns
    if not _ENABLED:
        return

    history = load_history()
    history.append({"role": "user", "content": user_prompt})
    history.append({"role": "assistant", "content": assistant_reply})

    max_messages = _MAX_TURNS * 2
    if len(history) > max_messages:
        history = history[-max_messages:]

    os.makedirs(os.path.dirname(_SESSION_PATH), exist_ok=True)
    data = {"timestamp": time.time(), "messages": history}
    with open(_SESSION_PATH, "w") as f:
        json.dump(data, f)


def clear() -> None:
    # used by `jarvis --reset`. just deletes the file
    try:
        os.remove(_SESSION_PATH)
    except FileNotFoundError:
        pass