"""
Tool registry.

This is the ONLY place tools get exposed to the model. To add a tool you add
one entry here: the schema, the tier, and the function. The model can't run
anything that isn't listed below (no shell, no generic "run command" tool).

Tiers:
  read    - runs right away, can't change anything on the system
  action  - runs right away, low risk, gets logged
  confirm - shows a yes/no popup first, gets logged
"""

from tools import system_tools, app_tools, file_tools

# name -> (tier, function)
_DISPATCH = {
    "get_system_stats": ("read", system_tools.get_system_stats),
    "list_processes": ("read", system_tools.list_processes),
    "search_files": ("read", file_tools.search_files),
    "read_file": ("read", file_tools.read_file),
    "edit_file": ("confirm", file_tools.edit_file),
    "zip_files": ("action", file_tools.zip_files),
    "open_app": ("action", app_tools.open_app),
    "close_app": ("confirm", app_tools.close_app),
}

# name -> function(args) -> str, only for confirm-tier tools. These build the
# text in the yes/no popup using the same logic the tool itself uses (how many
# processes get closed, how big the edit is), so the popup can't say something
# different from what actually happens. Tools without one get a generic
# description from ollama_client._describe_action.
_DESCRIBERS = {
    "close_app": lambda args: app_tools.describe_close(args.get("app_name", "")),
    "edit_file": lambda args: file_tools.describe_edit(args.get("path", ""), args.get("new_content", "")),
}

# tool definitions in the Ollama / OpenAI JSON schema format
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_system_stats",
            "description": "Get current CPU, RAM, disk, and network usage stats.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_processes",
            "description": "List the top processes by CPU or memory usage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sort_by": {"type": "string", "enum": ["cpu", "memory"]},
                    "limit": {"type": "integer", "description": "Max number of processes to return"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for files by name under the user's configured file_search_root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Filename or partial filename to search for"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read and return the text content of a file under the user's configured file_search_root. "
                "Truncated at 20,000 characters for large files - if this result is truncated, do not "
                "attempt to edit_file that same path, since you'd only have a partial view of it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file, absolute or relative to file_search_root"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Overwrite a file with new content. A backup of the original is always made first. Requires "
                "the file to already exist - read it first to see its current content. Refuses files "
                "longer than 20,000 characters, since editing those would mean writing back only a "
                "partial view of the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the existing file to edit"},
                    "new_content": {"type": "string", "description": "The full new content the file should contain after editing"},
                },
                "required": ["path", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "zip_files",
            "description": "Create a zip archive containing one or more files. Does not modify or delete the "
                "original files, and refuses if an archive with that name already exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}, "description": "Paths of files to include in the archive"},
                    "archive_name": {"type": "string", "description": "Name for the resulting .zip file"},
                },
                "required": ["paths", "archive_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Open, run, launch, start, or execute an application by name - "
                "all the same action. This is a scoped, whitelisted launch, not "
                "shell access. Only apps on the user's allowlist can be opened.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Name of the app, e.g. 'firefox'"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_app",
            "description": "Close, exit, quit, end, stop, or kill a running application - "
                "all the same action. Applies whether or not Jarvis is the one "
                "that opened it in the first place. Some apps are configured as not "
                "closable this way (e.g. games launched through Steam) and will be refused.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string"},
                },
                "required": ["app_name"],
            },
        },
    },
]


def get_tier(name: str) -> str | None:
    entry = _DISPATCH.get(name)
    return entry[0] if entry else None


def describe(name: str, args: dict) -> str | None:
    # custom confirm text for a tool, or None if it doesn't have one
    describer = _DESCRIBERS.get(name)
    if describer is None:
        return None
    try:
        return describer(args)
    except Exception:
        return None


def dispatch(name: str, args: dict):
    # runs a registered tool. raises KeyError if it isn't registered, and
    # that's on purpose, don't catch it
    if name not in _DISPATCH:
        raise KeyError(f"Refusing to run unregistered tool: {name!r}")
    _, fn = _DISPATCH[name]
    return fn(**args)