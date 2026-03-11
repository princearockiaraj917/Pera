"""
server.py — Flask bridge between the HTML UI and app.py backend
Run: python server.py
Then open: http://localhost:5000
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import threading
import queue
import os
import sys
import io
import wave
import time

app = Flask(__name__, static_folder=".")
CORS(app)

companion     = None
message_queue = queue.Queue()
setup_done    = False

# mic state
_mic_active = False
_mic_thread = None

# ── Redirect print() from backend into the message queue ──────────────────────
class QueueWriter:
    def __init__(self): pass
    def write(self, msg):
        if msg and msg.strip():
            message_queue.put({"type": "print", "text": msg.strip()})
    def flush(self): pass

sys.stdout = QueueWriter()


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return send_from_directory(".", "index.html")



@app.route("/setup", methods=["POST"])
def setup():
    global companion, setup_done
    data = request.json

    from app import EmotionalCompanion

    name     = data.get("name", "Friend")
    language = data.get("language", "en")
    gender   = data.get("voice_gender", "female")
    voice_on = data.get("voice_on", True)

    companion = EmotionalCompanion(user_name=name)
    companion.language             = language
    companion.voice.language       = language
    companion.voice_input.language = "ta-IN" if language == "ta" else "en-IN"
    companion.voice.set_gender(gender)
    companion.voice.enabled        = voice_on

    _patch_companion(companion)
    setup_done = True

    if language == "ta":
        greeting = (f"வணக்கம் {name}! நான் பேரா. நீங்கள் இங்கே வந்தது மிகவும் சந்தோஷமாக இருக்கிறது. "
                    f"இப்போது எப்படி உணர்கிறீர்கள்?")
    else:
        greeting = (f"Hey {name}! I'm PERA, and I'm really glad you're here. "
                    f"I'm just a friend who listens — no judgment at all. "
                    f"How are you feeling today?")

    # Return greeting in JSON only (not queue) — prevents duplicate display
    threading.Thread(target=companion.voice.speak_async, args=(greeting,), daemon=True).start()
    return jsonify({"status": "ok", "greeting": greeting})


@app.route("/chat", methods=["POST"])
def chat():
    if not companion:
        return jsonify({"error": "Not set up"}), 400
    data       = request.json
    user_input = data.get("message", "").strip()
    if not user_input:
        return jsonify({"status": "empty"})
    if companion.handle_command(user_input):
        return jsonify({"type": "command", "status": "ok"})
    threading.Thread(target=_run_chat, args=(user_input,), daemon=True).start()
    return jsonify({"status": "processing"})


@app.route("/poll", methods=["GET"])
def poll():
    messages = []
    try:
        while True:
            messages.append(message_queue.get_nowait())
    except queue.Empty:
        pass
    return jsonify({"messages": messages})


@app.route("/command", methods=["POST"])
def command():
    if not companion:
        return jsonify({"error": "Not set up"}), 400
    cmd = request.json.get("cmd", "")
    companion.handle_command(cmd)
    return jsonify({"status": "ok"})


# ══════════════════════════════════════════════════════════════════════════════
#  MIC — Python-side recording (pyaudio + Google STT)
#  Browser sends /mic/start when button pressed, /mic/stop when released.
#  Python records via pyaudio, transcribes, pushes result to poll queue.
#  This avoids ALL webm/ffmpeg browser audio format issues completely.
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/mic/start", methods=["POST"])
def mic_start():
    global _mic_active, _mic_thread
    if _mic_active:
        return jsonify({"status": "already_recording"})
    _mic_active = True
    _mic_thread = threading.Thread(target=_record_loop, daemon=True)
    _mic_thread.start()
    return jsonify({"status": "recording"})


@app.route("/mic/stop", methods=["POST"])
def mic_stop():
    global _mic_active
    _mic_active = False
    return jsonify({"status": "stopped"})


def _record_loop():
    """
    Records via pyaudio while _mic_active is True.
    On stop, wraps in WAV, transcribes via Google STT, pushes mic_result to queue.
    """
    global _mic_active
    try:
        import pyaudio
        import speech_recognition as sr

        sample_rate  = 16000
        chunk_size   = 1024
        pa     = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=sample_rate,
            input=True,
            frames_per_buffer=chunk_size,
        )

        audio_frames = []
        while _mic_active:
            data = stream.read(chunk_size, exception_on_overflow=False)
            audio_frames.append(data)

        stream.stop_stream()
        stream.close()
        pa.terminate()

        if not audio_frames:
            message_queue.put({"type": "mic_error", "text": "Nothing recorded."})
            return

        # Wrap raw PCM in WAV container (in memory)
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)          # paInt16 = 2 bytes
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(audio_frames))
        wav_buf.seek(0)

        # Transcribe with Google STT
        recognizer = sr.Recognizer()
        language   = companion.voice_input.language if companion else "en-IN"

        with sr.AudioFile(wav_buf) as source:
            audio = recognizer.record(source)

        text = recognizer.recognize_google(audio, language=language)
        message_queue.put({"type": "mic_result", "text": text})

    except Exception as e:
        err = str(e)
        if "UnknownValueError" in err or "understand" in err.lower():
            message_queue.put({"type": "mic_error",
                                "text": "Could not understand. Try speaking clearly or type instead."})
        elif "RequestError" in err:
            message_queue.put({"type": "mic_error",
                                "text": "No internet for speech recognition. Type instead."})
        else:
            message_queue.put({"type": "mic_error", "text": f"Mic error: {err}"})


# ══════════════════════════════════════════════════════════════════════════════
#  CRISIS REPLY
# ══════════════════════════════════════════════════════════════════════════════

_reply_event = threading.Event()
_reply_value = [None]

@app.route("/crisis_reply", methods=["POST"])
def crisis_reply():
    _reply_value[0] = request.json.get("message", "")
    _reply_event.set()
    return jsonify({"status": "ok"})


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _run_chat(user_input):
    try:
        companion.chat(user_input)
    except Exception as e:
        message_queue.put({"type": "ai", "text": f"Something went wrong: {e}"})


def _patch_companion(c):
    def ui_show_and_speak(text):
        message_queue.put({"type": "ai", "text": text})
        threading.Thread(target=c.voice.speak_async, args=(text,), daemon=True).start()

    def ui_say(text):
        # Strictly use companion.language — never translate in English mode
        if c.language == "ta" and c.translator.available:
            display = c.translator.to_tamil(text)
        else:
            display = text   # English — NO translation ever
        message_queue.put({"type": "crisis", "text": display})
        if c.voice.enabled:
            c.voice.speak(display)
        return display

    def ui_run_3pov(mode):
        message_queue.put({"type": "crisis_on", "mode": mode})
        perspectives = c._pov_lines(mode)
        for i, pov in enumerate(perspectives):
            message_queue.put({"type": "pov_label", "text": pov["label"]})
            _say_blocking(c, pov["intro"])
            _say_blocking(c, pov["message"])
            _say_blocking(c, pov["question"])
            reply = _wait_reply()
            if reply:
                reply_en = c.translator.to_english(reply) if c.language == "ta" else reply
                ack = c._pov_acknowledge(reply_en, i)
                _say_blocking(c, ack)
        final = (f"I am really glad you talked to me, {c.user_name}. "
                 f"Please call 14416 or 104 — they are free and available 24 hours. "
                 f"I am here whenever you need to talk more.")
        _say_blocking(c, final)
        message_queue.put({"type": "crisis_off"})

    def ui_trigger():
        message_queue.put({"type": "emergency"})
        original_trigger()

    original_trigger     = c._trigger_emergency
    c._show_and_speak    = ui_show_and_speak
    c._say               = ui_say
    c._run_3pov_convo    = ui_run_3pov
    c._trigger_emergency = ui_trigger


def _wait_reply():
    message_queue.put({"type": "waiting_reply"})
    _reply_event.clear()
    _reply_value[0] = None
    _reply_event.wait(timeout=120)
    return _reply_value[0]


def _say_blocking(c, text):
    # Strictly English unless Tamil mode — never auto-translate in English mode
    if c.language == "ta" and c.translator.available:
        display = c.translator.to_tamil(text)
    else:
        display = text
    message_queue.put({"type": "crisis", "text": display})
    if c.voice.enabled:
        c.voice.speak(display)


# ══════════════════════════════════════════════════════════════════════════════
#  LAUNCH
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import webbrowser
    sys.__stdout__.write("\n✅  Server starting at http://localhost:5000\n")
    sys.__stdout__.write("   Opening browser automatically...\n\n")
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(host="localhost", port=5000, debug=False, threaded=True)
