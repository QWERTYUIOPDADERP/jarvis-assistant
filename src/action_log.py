import datetime
import os

from config_loader import CONFIG


def log(event: str) -> None:
    if not CONFIG["logging"]["enabled"]:
        return

    path = CONFIG["logging"]["path"]
    max_lines = CONFIG["logging"].get("max_lines", 5000)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(path, "a") as f:
        f.write(f"[{timestamp}] {event}\n")

    _trim_if_needed(path, max_lines)


def _trim_if_needed(path: str, max_lines: int) -> None:
    # keeps the log from growing forever by only keeping the newest max_lines.
    # it reads the whole file every time but it's only a few thousand lines so whatever
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return

    if len(lines) <= max_lines:
        return

    with open(path, "w") as f:
        f.writelines(lines[-max_lines:])