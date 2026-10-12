"""
File tools: search, read, edit, and zip. All of them are locked to one root
folder (file_search_root in config.yaml, my home folder by default). The root
is resolved once when this file is imported and symlinks can't get you out of it.

edit_file is the only one that changes file contents, so it has a few extra
rules: it makes a timestamped backup before writing, it's the "confirm" tier
(never runs without a yes), it refuses some sensitive paths (ssh keys, .git,
this project's own folder), and it refuses files bigger than what read_file
can show the model (see _MAX_READ_CHARS below for why).
"""

import os
import shutil
import time
import zipfile
from pathlib import Path

from config_loader import CONFIG

_SEARCH_ROOT = Path(CONFIG["file_search_root"]).resolve()

# This project's own folder is always off limits, even if file_search_root
# covers it, so a prompt can't get Jarvis to rewrite its own code, config,
# logs, or session file.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_MAX_RESULTS = 25
_MAX_READ_BYTES = 200_000     # ~200KB, enough for normal text files and keeps huge/binary ones out
_MAX_READ_CHARS = 20_000      # even smaller cap on what actually goes to the model
_MAX_WRITE_BYTES = 200_000

# if any of these show up in a path it's blocked, wherever it is under the
# root. not a complete list, more of a basic guardrail than real security.
# add more if there's something else you want protected
_DENY_SUBSTRINGS = (
    ".ssh", ".gnupg", ".password-store", "id_rsa", "id_ed25519",
    "known_hosts", "/.git/",
)


def _safe_path(path_str: str) -> Path:
    # resolves a path and checks it's inside the root, doesn't match the deny
    # list, and isn't in this project's folder. raises PermissionError with a
    # readable message if not (callers turn that into a returned string)
    expanded = os.path.expanduser(path_str)
    if not os.path.isabs(expanded):
        # relative paths are relative to the search root, not wherever
        # the script was launched from
        expanded = str(_SEARCH_ROOT / expanded)
    p = Path(expanded).resolve()

    if _SEARCH_ROOT != p and _SEARCH_ROOT not in p.parents:
        raise PermissionError(f"'{path_str}' is outside the allowed directory ({_SEARCH_ROOT}).")

    if _PROJECT_ROOT == p or _PROJECT_ROOT in p.parents:
        raise PermissionError(f"'{path_str}' is inside jarvis-assistant's own project directory and can't be touched.")

    lower = str(p).lower()
    if any(bad in lower for bad in _DENY_SUBSTRINGS):
        raise PermissionError(f"'{path_str}' matches a protected path pattern and can't be touched.")

    return p


def search_files(query: str) -> list[str]:
    query_lower = query.lower()
    results = []

    for root, dirs, files in os.walk(_SEARCH_ROOT, followlinks=False):
        # skip hidden folders (.cache, .config...), they're noisy and usually not what you want
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        root_path = Path(root).resolve()
        # extra check: skip anything that somehow resolved outside the root
        if _SEARCH_ROOT not in root_path.parents and root_path != _SEARCH_ROOT:
            continue
        # don't show this project's own files in results
        if root_path == _PROJECT_ROOT or _PROJECT_ROOT in root_path.parents:
            continue

        for f in files:
            if query_lower in f.lower():
                results.append(str(root_path / f))
                if len(results) >= _MAX_RESULTS:
                    return results

    return results


def read_file(path: str) -> str:
    try:
        p = _safe_path(path)
    except PermissionError as e:
        return f"Refused: {e}"

    if not p.exists() or not p.is_file():
        return f"'{path}' doesn't exist or isn't a file."

    size = p.stat().st_size
    if size > _MAX_READ_BYTES:
        return f"'{path}' is too large to read ({size:,} bytes, limit {_MAX_READ_BYTES:,})."

    try:
        content = p.read_text(errors="replace")
    except Exception as e:
        return f"Couldn't read '{path}': {e}"

    if len(content) > _MAX_READ_CHARS:
        content = content[:_MAX_READ_CHARS] + "\n...[truncated]"

    return content


def _edit_precheck(path: str) -> tuple[Path | None, str | None, str | None]:
    # shared by describe_edit() and edit_file() so the popup and the real
    # edit can't disagree. returns (resolved_path, refusal_reason, current_content)
    try:
        p = _safe_path(path)
    except PermissionError as e:
        return None, str(e), None

    if not p.exists() or not p.is_file():
        return None, f"'{path}' doesn't exist or isn't a file - edit_file only edits existing files.", None

    try:
        current = p.read_text(errors="replace")
    except Exception as e:
        return None, f"Couldn't read '{path}' to edit it: {e}", None

    if len(current) > _MAX_READ_CHARS:
        # the model only ever sees the first _MAX_READ_CHARS chars, so if it
        # "edited" a longer file it'd write back just that part and wipe out
        # the rest of the file
        return None, (
            f"'{path}' is {len(current):,} characters, longer than the {_MAX_READ_CHARS:,}-character "
            f"limit read_file shows. Editing it here would mean overwriting the whole file based on "
            f"only a partial view of it, silently losing everything past that point - refusing rather "
            f"than risking that. Use a regular text editor for a file this size."
        ), None

    return p, None, current


def describe_edit(path: str, new_content: str) -> str:
    # text for the confirm popup
    p, refusal, current = _edit_precheck(path)
    if refusal:
        return refusal
    old_len, new_len = len(current), len(new_content)
    delta = new_len - old_len
    sign = "+" if delta >= 0 else ""
    return f"edit {path} ({old_len:,} -> {new_len:,} chars, {sign}{delta:,}; a backup will be kept)"


def edit_file(path: str, new_content: str) -> str:
    # overwrites a file. backs up the original first as path.bak-<timestamp>
    # so you can always undo it. refuses files over _MAX_READ_CHARS (see _edit_precheck)
    p, refusal, _ = _edit_precheck(path)
    if refusal:
        return f"Refused: {refusal}"

    if len(new_content.encode("utf-8", errors="replace")) > _MAX_WRITE_BYTES:
        return f"Refused: new content exceeds the {_MAX_WRITE_BYTES:,}-byte edit limit."

    backup = p.with_name(p.name + f".bak-{int(time.time())}")
    shutil.copy2(p, backup)
    p.write_text(new_content)

    return f"Edited {path} (backup saved as {backup.name})."


def zip_files(paths: list[str], archive_name: str) -> str:
    # makes a zip from one or more files. it only ever creates a new file, never
    # touches the originals and won't overwrite an existing zip, so it's low
    # risk and doesn't need a confirmation
    if not archive_name.endswith(".zip"):
        archive_name += ".zip"

    try:
        safe_sources = [_safe_path(p) for p in paths]
        archive_path = _safe_path(archive_name)
    except PermissionError as e:
        return f"Refused: {e}"

    if archive_path.exists():
        return f"Refused: '{archive_path.name}' already exists - pick a different name or delete it first."

    missing = [str(p) for p in safe_sources if not p.exists()]
    if missing:
        return f"Refused: these paths don't exist: {', '.join(missing)}"

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in safe_sources:
            zf.write(p, arcname=p.name)

    return f"Created {archive_path.name} with {len(safe_sources)} file(s)."