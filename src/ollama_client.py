"""
Ollama chat wrapper with tool calling.

keep_alive is set short on every request (config.yaml, default "10s"), so
once Jarvis exits Ollama unloads the model from RAM on its own. Nothing to
remember to turn off. The default keep_alive is 5 minutes which is way too long
for what I want.
"""

import json
import os
import re
import time

import requests

from config_loader import CONFIG
from tools import registry
import confirm
import session
from action_log import log

# NOTE: this prompt took a lot of trial and error with qwen3:8b, so be careful
# changing it. Most of the weird-looking rules are there because the model did
# that exact thing wrong at some point (narrating instead of calling a tool, etc.)
SYSTEM_PROMPT = (
    "You are Jarvis, a local desktop assistant running entirely on the user's "
    "own machine. You have a small, fixed set of tools - use them when they "
    "help answer the request. Requests to 'run', 'launch', 'start', 'execute', "
    "or 'open' an application all mean the same thing: call the open_app tool "
    "with that app's name. Likewise, requests to 'close', 'exit', 'quit', "
    "'end', 'stop', or 'kill' an application all mean the same thing - call "
    "the close_app tool with that app's name. Both are scoped, whitelisted "
    "tool calls, not shell access - don't refuse these as if they were a "
    "request for raw command execution. You genuinely cannot do anything "
    "outside the tools you've been "
    "given (no shell access, no arbitrary file deletion) - if a request needs "
    "something you don't have a tool for, say so plainly instead of pretending "
    "to do it. Some tools (close_app, edit_file) already pop up their own "
    "yes/no confirmation dialog in the app after you call them - that check "
    "happens automatically outside this conversation. So when a request maps "
    "to one of those tools, call it immediately, the same as any other tool - "
    "never ask the user 'are you sure?' or similar in your own reply instead "
    "of calling it. Asking in words instead of calling the tool means nothing "
    "happens at all, since the app never sees a request to act. Never describe "
    "an action as done, or as in progress, unless you "
    "actually made the corresponding tool call in this same turn - if you're "
    "about to say you opened, closed, edited, or ran something, that only "
    "belongs in your reply after the tool call, never instead of it. "
    "Keep spoken responses short and natural, since they may be read aloud, "
    "and never use markdown formatting (no **, #, bullets, backticks) - "
    "these responses are spoken or shown as plain notification text, not "
    "rendered. If the conversation includes a recent prior exchange, use it "
    "to interpret short follow-ups like 'yes' or 'do that' - they refer back "
    "to what you just said."
)


# used by correct_transcript() below. {vocab} and {context} get filled in with .format()
CORRECTION_SYSTEM_PROMPT = (
    "You are a transcript-repair step for a voice-controlled desktop assistant. "
    "You are given a raw speech-to-text transcript that may contain errors: the "
    "recognizer sometimes hallucinates extra words out of silence or background "
    "noise, and sometimes mishears a word as a different, similar-sounding one.\n\n"
    "Read the transcript and judge whether it makes sense as a spoken command or "
    "question. If it already makes sense, leave it alone. If some word or phrase "
    "doesn't fit, check whether a similar-sounding word or phrase would make it "
    "coherent - especially against the known vocabulary below - and substitute "
    "only that. Never invent new content, never guess at a totally different "
    "meaning, and don't touch words that are already sensible even if you'd have "
    "phrased them differently. Only remove words if they are clearly stray "
    "hallucinated filler (e.g. a repeated word, a trailing fragment) with no "
    "bearing on any plausible command.\n\n"
    "Known vocabulary (app names, game names, etc.) - prefer these exact "
    "spellings when a word sounds close to one of them. But watch for this "
    "specific failure mode: the recognizer sometimes tacks a second, unrelated "
    "vocabulary word onto the end of an already-complete command (e.g. 'open "
    "firefox' coming through as 'open firefox, spotify'), because those "
    "words were primed together and it latched onto the wrong one at the end. "
    "If the transcript already forms one complete, sensible command by itself "
    "and an extra vocabulary word is simply appended afterward with no "
    "grammatical connection to it, drop that stray trailing word rather than "
    "keeping it just because it's on the list.\n{vocab}\n\n"
    "Recent conversation, for context on what would make sense right now "
    "(may be empty):\n{context}\n\n"
    "Respond with strict JSON only, no other text, matching exactly this shape: "
    '{{"corrected": "<the transcript, fixed if needed>", "changed": true|false}}'
)

_DEBUG = bool(CONFIG.get("debug"))
_DEBUG_LOG_PATH = "/tmp/jarvis-debug.log"


def _debug(section: str, **fields) -> None:
    # only logs if debug: true. (it used to ALWAYS write prompts to /tmp which
    # is a plaintext file anyone on the machine can read, not great)
    if not _DEBUG:
        return
    with open(_DEBUG_LOG_PATH, "a") as f:
        f.write(f"\n--- {section} @ {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        for key, value in fields.items():
            f.write(f"{key}={value!r}\n")


def _vocabulary() -> list[str]:
    apps = list(CONFIG.get("allowed_apps", {}).keys())
    extra = CONFIG.get("voice", {}).get("custom_vocabulary", [])
    return list(dict.fromkeys(apps + list(extra)))  # dedupe but keep the order


def correct_transcript(raw_text: str) -> tuple[str, bool]:
    """
    Second pass over the whisper transcript using the LLM. It asks "does this
    make sense, or is there a similar sounding word that would?" This is
    different from the --prompt vocab thing in voice.py, which only nudges the
    first decode.

    If anything goes wrong (ollama down, bad json, etc) it just returns the raw
    transcript so a failure here never blocks the assistant.
    """
    if not raw_text.strip():
        return raw_text, False

    prior = session.load_history()
    context_lines = [
        f"{turn.get('role')}: {turn.get('content')}"
        for turn in prior[-4:]
        if turn.get("role") in ("user", "assistant") and turn.get("content")
    ]
    context = "\n".join(context_lines) if context_lines else "(none)"

    system_prompt = CORRECTION_SYSTEM_PROMPT.format(
        vocab=", ".join(_vocabulary()) or "(none)",
        context=context,
    )

    payload = {
        "model": CONFIG["ollama"]["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw_text},
        ],
        "stream": False,
        "keep_alive": CONFIG["ollama"]["keep_alive"],
        "think": False,
        "format": "json",
        "options": {"temperature": 0.0},
    }

    try:
        resp = requests.post(f"{CONFIG['ollama']['host']}/api/chat", json=payload, timeout=20)
        resp.raise_for_status()
        parsed = json.loads(resp.json()["message"]["content"].strip())
        corrected = str(parsed.get("corrected", "")).strip()
        if not corrected:
            return raw_text, False
        changed = bool(parsed.get("changed")) and corrected.lower() != raw_text.strip().lower()
        return corrected, changed
    except Exception as e:
        _debug("correction_failed", error=str(e), raw_text=raw_text)
        return raw_text, False


def _describe_action(name: str, args: dict) -> str:
    # text for the confirm popup. tries the tool's own describer first (so it
    # can say stuff like how many processes will close), otherwise falls back
    # to something generic like "zip files ({...})"
    specific = registry.describe(name, args)
    if specific is not None:
        return specific
    return f"{name.replace('_', ' ')} ({args})" if args else name.replace("_", " ")


def _chat(messages: list[dict]) -> dict:
    payload = {
        "model": CONFIG["ollama"]["model"],
        "messages": messages,
        "tools": registry.TOOL_SCHEMAS,
        "stream": False,
        "keep_alive": CONFIG["ollama"]["keep_alive"],
        # temperature is 0 on purpose. with the default temp the 8B model would
        # flip between calling open_app and just saying it did it, even for
        # almost the same prompt ("open firefox" vs "open, firefox")
        "options": {"temperature": 0.0},
    }
    if CONFIG["ollama"].get("disable_thinking"):
        payload["think"] = False

    resp = requests.post(f"{CONFIG['ollama']['host']}/api/chat", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


# words that make it sound like something got done, used for the check at the
# bottom of run()
_COMPLETION_CLAIM_RE = re.compile(
    r"\b(opened|closed|launched|started|running|ran|edited|deleted|removed|"
    r"zipped|created)\b",
    re.IGNORECASE,
)


def run(user_prompt: str, voice: bool = False, speak_fn=None, speak_confirmations: bool = False) -> str:
    """
    Runs one request through the model, doing tool calls as needed, and
    returns the final reply text.

    voice               - True if this came from --voice (mic input)
    speak_fn            - function that plays text as audio, or None
    speak_confirmations - whether yes/no confirmations get spoken instead of
                          shown as a popup. Separate from voice/speak_fn on
                          purpose: if you ran --voice without --speak you get
                          silent replies, so the confirmation shouldn't start
                          talking either.
    """
    prior = session.load_history()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(prior)
    messages.append({"role": "user", "content": user_prompt})

    final_reply = None
    tools_ran = set()  # names of tools that actually ran

    # capped at 6 rounds so a confused model can't loop on tool calls forever
    for _ in range(6):
        result = _chat(messages)
        message = result["message"]
        messages.append(message)

        _debug("turn", user_prompt=user_prompt, tool_calls=message.get("tool_calls"), content=message.get("content"))

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            final_reply = message.get("content", "").strip()
            break

        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"].get("arguments", {})

            tier = registry.get_tier(name)
            if tier is None:
                tool_output = f"Error: '{name}' is not a real tool."
            elif tier == "confirm":
                description = _describe_action(name, args)
                if voice and speak_confirmations and speak_fn:
                    approved = confirm.ask_voice(description, speak_fn)
                else:
                    approved = confirm.ask(description)

                if approved:
                    tool_output = str(registry.dispatch(name, args))
                    tools_ran.add(name)
                    log(f"CONFIRMED + RAN: {description}")
                else:
                    tool_output = "User declined this action. Do not retry it."
                    log(f"DECLINED: {description}")
            else:
                tool_output = str(registry.dispatch(name, args))
                tools_ran.add(name)
                if tier == "action":
                    log(f"RAN: {name}({args})")

            messages.append({
                "role": "tool",
                "content": tool_output,
            })

    if final_reply is None:
        final_reply = "Sorry, that took too many steps - try rephrasing."
    elif not tools_ran and _COMPLETION_CLAIM_RE.search(final_reply):
        # the model said it opened/closed/edited something but no tool ran.
        # small models do this a lot (describe doing it instead of calling the
        # tool) and the system prompt alone doesn't stop it, so catch it here
        final_reply = (
            "I didn't actually do that - I don't have a tool result backing "
            "it up. Can you say the request again?"
        )

    session.append_turn(user_prompt, final_reply)
    return final_reply