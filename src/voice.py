"""
Mic recording + transcription.

Records with sox's silence detection so it stops right when you stop talking
instead of always recording a fixed amount of time. If sox isn't installed it
falls back to a fixed length arecord clip.

There's a hard cap (max_record_seconds) so a noisy room (fans, hum) can't
make it record forever. At the cap it gets stopped with SIGINT, same as hitting
Ctrl+C, so sox still finishes the wav file properly.

Transcription is done with whisper.cpp. All of this only runs for the few
seconds Jarvis is actually active, nothing is listening in the background.
"""

import os
import shutil
import signal
import subprocess
import tempfile
from config_loader import CONFIG


def _record_with_silence_detection(wav_path: str, rate: int, cfg: dict) -> None:
    # sox `rec`: waits for sound to go over the threshold, then stops after a
    # bit of silence. the timeout below is the hard cap
    threshold = cfg.get("silence_threshold", "4%")
    silence_len = str(cfg.get("silence_duration_seconds", 1.2))
    max_seconds = cfg.get("max_record_seconds", 15)

    proc = subprocess.Popen(
        [
            "rec", "-q", "-r", str(rate), "-c", "1", wav_path,
            "silence",
            "1", "0.1", threshold,        # start once sound goes over the threshold
            "1", silence_len, threshold,  # stop after silence_len of quiet
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        proc.wait(timeout=max_seconds)
    except subprocess.TimeoutExpired:
        # hit the cap. SIGINT (not kill) so sox writes the wav header right
        # and the file isn't corrupt
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _trim_trailing_silence(wav_path: str, threshold: str) -> None:
    # The recorder keeps going for silence_duration_seconds after you stop
    # talking (that's how it knows you're done), so every clip has ~1 sec of
    # silence on the end. Whisper likes to hallucinate a word from the --prompt
    # vocab list in that silence, like "open firefox" turning into
    # "open firefox, spotify". Trimming most of it off before
    # transcribing helps a lot.
    # If sox isn't there or the trim fails we just use the original file.
    if not shutil.which("sox"):
        return

    trimmed = wav_path + ".trimmed.wav"
    try:
        subprocess.run(
            ["sox", wav_path, trimmed,
             "reverse", "silence", "1", "0.1", threshold, "reverse",
             "pad", "0", "0.2"],  # small buffer so the last consonant doesn't get clipped
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        os.replace(trimmed, wav_path)
    except (subprocess.CalledProcessError, OSError):
        if os.path.exists(trimmed):
            os.remove(trimmed)


def _record_fixed(wav_path: str, rate: int, seconds: int) -> None:
    subprocess.run(
        ["arecord", "-f", "S16_LE", "-r", str(rate), "-c", "1",
         "-d", str(seconds), wav_path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _vocabulary_words() -> list[str]:
    apps = list(CONFIG.get("allowed_apps", {}).keys())
    extra = CONFIG["voice"].get("custom_vocabulary", [])
    return list(dict.fromkeys(apps + list(extra)))  # dedupe, keep order


def _build_vocabulary_prompt() -> str:
    # whisper's --prompt flag nudges it toward certain words (app names, game
    # titles, etc). I keep it as just a plain comma list on purpose. whisper
    # treats the prompt like text that came before the audio, NOT like
    # instructions, so when I tried a full sentence wrapper it literally
    # repeated "may be said" back at me. A bare list is safer, but it can
    # still leak, which is why listen_and_transcribe() checks for that and
    # retries without it.
    words = _vocabulary_words()
    if not words:
        return ""
    return ", ".join(words)


def _transcribe(wav_path: str, use_vocab_prompt: bool = True) -> str:
    # runs whisper.cpp on a wav. used for both normal commands and yes/no answers
    cfg = CONFIG["voice"]
    binary = cfg["whisper_binary"]
    model = cfg["whisper_model"]

    if not os.path.exists(binary) or not os.path.exists(model):
        raise FileNotFoundError(
            "whisper.cpp binary or model not found - check config.yaml "
            "voice.whisper_binary / voice.whisper_model, or see README "
            "for build instructions."
        )

    cmd = [binary, "-m", model, "-f", wav_path,
           "-nt", "--no-prints", "-bs", "5", "-bo", "5",
           "-t", str(cfg.get("whisper_threads", 4)),
           # Hallucination settings. whisper.cpp marks a segment as "failed"
           # if it's low confidence (logprob/entropy) or looks like silence
           # (no-speech prob), and then by default retries at a higher
           # temperature, which is what makes it invent words from room noise.
           # So: no-speech threshold a bit stricter than whisper's default
           # (0.6), and -nf turns the temperature fallback off completely.
           "-nth", str(cfg.get("no_speech_threshold", 0.7)),
           "-lpt", str(cfg.get("logprob_threshold", -1.0)),
           "-et", str(cfg.get("entropy_threshold", 2.4))]
    if cfg.get("no_temperature_fallback", True):
        cmd.append("-nf")
    if use_vocab_prompt:
        prompt = _build_vocabulary_prompt()
        if prompt:
            cmd += ["--prompt", prompt]

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()


def listen_yes_no(max_seconds: int = 5) -> bool | None:
    # records a short answer. True = yes, False = no, None = couldn't tell
    # (callers treat None as a no, same as the popup does)
    cfg = CONFIG["voice"]
    rate = cfg["sample_rate"]

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    if shutil.which("rec"):
        short_cfg = dict(cfg)
        short_cfg["silence_duration_seconds"] = 0.6
        short_cfg["max_record_seconds"] = max_seconds
        _record_with_silence_detection(wav_path, rate, short_cfg)
        _trim_trailing_silence(wav_path, cfg.get("silence_threshold", "4%"))
    else:
        _record_fixed(wav_path, rate, max_seconds)

    try:
        text = _transcribe(wav_path, use_vocab_prompt=False).lower()
    finally:
        os.unlink(wav_path)

    affirmative = ("yes", "yeah", "yep", "sure", "do it", "go ahead", "confirm", "affirmative")
    negative = ("no", "nope", "don't", "do not", "cancel", "negative")

    if any(word in text for word in affirmative):
        return True
    if any(word in text for word in negative):
        return False
    return None


def _looks_like_prompt_leak(text: str, words: list[str]) -> bool:
    # The vocab prompt is full of weird made-up words that whisper has never
    # heard. On short or unclear audio it sometimes just repeats the prompt
    # back instead of transcribing. Nobody is going to say two of those words in
    # one command, so if 2+ show up it's probably a leak.
    lowered = text.lower()
    hits = sum(1 for w in words if w.lower() in lowered)
    return hits >= 2


def listen_and_transcribe() -> str:
    cfg = CONFIG["voice"]
    rate = cfg["sample_rate"]

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    if shutil.which("rec"):
        _record_with_silence_detection(wav_path, rate, cfg)
        _trim_trailing_silence(wav_path, cfg.get("silence_threshold", "4%"))
    else:
        _record_fixed(wav_path, rate, cfg["record_seconds"])

    try:
        text = _transcribe(wav_path, use_vocab_prompt=True)
        if _looks_like_prompt_leak(text, _vocabulary_words()):
            # whisper probably echoed the prompt. try again with no prompt,
            # it can't leak words it was never given (we lose the vocab boost
            # for this one command but that's fine)
            text = _transcribe(wav_path, use_vocab_prompt=False)
        return text
    finally:
        os.unlink(wav_path)