"""
Tools for opening and closing apps.

open_app  (action)  - only works for apps in config.yaml's allowed_apps.
                      anything else gets refused, no exceptions.
close_app (confirm) - closes every running process that matches an allowed
                      app's executable name, even if Jarvis didn't open it.
                      Always asks first, and the popup says how many processes
                      it'll close (e.g. 3 terminal windows = 3 processes).

Some apps get launched through another process (the Steam games in config.yaml
use `steam steam://...`), so the process name we'd have to match is `steam`,
not the game. For those I set `closable: false` in config.yaml so close_app
says no instead of killing all of Steam.
"""

import os
import re
import shlex
import subprocess

import psutil

from config_loader import CONFIG

# {app_name: Popen} for apps opened during THIS run. only used so a "closed"
# message can be friendlier, closing doesn't depend on it anymore
_session_launched: dict[str, subprocess.Popen] = {}


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _resolve_app_key(app_name: str, allowed: dict) -> str | None:
    # figure out which allowed app the model meant. exact match first, then
    # ignoring punctuation/case, then a partial match (but only if exactly
    # one app matches, otherwise it's too ambiguous)
    key = app_name.strip().lower()
    if key in allowed:
        return key

    norm_key = _normalize(app_name)
    norm_allowed = {_normalize(k): k for k in allowed}

    if norm_key in norm_allowed:
        return norm_allowed[norm_key]

    candidates = {orig for norm, orig in norm_allowed.items() if norm in norm_key or norm_key in norm}
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def open_app(app_name: str) -> str:
    allowed = CONFIG["allowed_apps"]
    resolved = _resolve_app_key(app_name, allowed)

    if resolved is None:
        return (
            f"Refused: '{app_name}' is not on the allowlist. "
            f"Allowed apps: {', '.join(sorted(allowed))}"
        )

    command = allowed[resolved]["cmd"]
    if not command:
        return f"Refused: '{resolved}' has no launch command configured in config.yaml."

    try:
        proc = subprocess.Popen(
            shlex.split(command),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        # app isn't installed on this machine (it'd crash the whole script otherwise)
        return f"Couldn't open {resolved}: '{shlex.split(command)[0]}' doesn't seem to be installed."

    _session_launched[resolved] = proc
    return f"Opened {resolved}."


def _find_matching_processes(exe_name: str) -> list[psutil.Process]:
    # matches on the process name or the basename of argv[0]. I used to do a
    # substring search on the whole command line but that also grabbed random
    # processes that just had the app name somewhere in their arguments
    exe_lower = exe_name.lower()
    matched = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmdline = proc.info.get("cmdline") or []
            argv0 = os.path.basename(cmdline[0]).lower() if cmdline else ""

            if name == exe_lower or argv0 == exe_lower:
                matched.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return matched


def _resolve_for_close(app_name: str) -> tuple[str | None, str | None, list[psutil.Process]]:
    # shared by describe_close() and close_app() so the popup and the real
    # action always agree. returns (resolved_name, refusal_reason, matches)
    allowed = CONFIG["allowed_apps"]
    resolved = _resolve_app_key(app_name, allowed)

    if resolved is None:
        return None, (
            f"'{app_name}' is not on the allowlist. Allowed apps: {', '.join(sorted(allowed))}"
        ), []

    entry = allowed[resolved]
    if not entry.get("closable", True):
        return resolved, (
            f"{resolved} is configured as not closable by name (see config.yaml) - "
            f"it's launched through another process, so Jarvis can't reliably close "
            f"just this one without affecting something else. Close it directly instead."
        ), []

    command = entry["cmd"]
    if not command:
        return resolved, f"'{resolved}' has no launch command configured, so its process can't be identified.", []

    exe_name = shlex.split(command)[0]
    matched = _find_matching_processes(exe_name)
    return resolved, None, matched


def describe_close(app_name: str) -> str:
    # the text shown in the confirm popup, like "close terminal (2 running
    # processes - ALL of them will be closed)"
    resolved, refusal, matches = _resolve_for_close(app_name)
    if refusal:
        return refusal
    if not matches:
        return f"close {resolved} (though it doesn't look like it's currently running)"
    if len(matches) == 1:
        return f"close {resolved}"
    return f"close {resolved} ({len(matches)} running processes - ALL of them will be closed)"


def close_app(app_name: str) -> str:
    resolved, refusal, matched = _resolve_for_close(app_name)
    if refusal:
        return f"Refused: {refusal}"

    if not matched:
        return f"No running window found for {resolved} - it doesn't look like it's open."

    closed = 0
    for proc in matched:
        try:
            proc.terminate()
            closed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    _session_launched.pop(resolved, None)

    if closed == 0:
        return f"Found {resolved} running but couldn't close it (permission denied)."

    return f"Closed {resolved} ({closed} process{'es' if closed != 1 else ''})."