"""
Yes/no confirmation for anything in the "confirm" tier.

Uses a zenity popup if it's installed (works from a hotkey, no terminal
needed), otherwise asks in the terminal. If neither works it says NO, so a
confirm action never just runs because the dialog failed to show up.
"""

import shutil
import subprocess


def ask(action_description: str) -> bool:
    if shutil.which("zenity"):
        result = subprocess.run(
            [
                "zenity", "--question",
                "--title=Jarvis - confirmation needed",
                f"--text=Jarvis wants to: {action_description}\n\nAllow this?",
            ]
        )
        return result.returncode == 0

    # no zenity, so ask in the terminal
    try:
        answer = input(f"Jarvis wants to: {action_description}\nAllow this? [y/N]: ")
        return answer.strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False


def ask_voice(action_description: str, speak_fn) -> bool:
    # voice version for --voice runs: says the question out loud and listens
    # for a spoken yes/no. same rule as ask(): if it can't tell, the answer is no
    import voice

    speak_fn(f"Should I {action_description}? Say yes or no.")
    answer = voice.listen_yes_no()

    if answer is None:
        speak_fn("I didn't catch a clear answer, so I won't do that.")
        return False

    return answer