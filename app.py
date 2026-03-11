import ollama
import subprocess
import re
import datetime
import os
import random
import threading
import time
import asyncio

from config import (
    SYSTEM_PROMPT, CRISIS_KEYWORDS, CRISIS_RESPONSE,
    VIOLENCE_KEYWORDS, VIOLENCE_RESPONSE,
    AFFIRMATIONS, CONNECTION_PROMPTS, TN_CONTEXT
)
from memory import UserMemory


# ══════════════════════════════════════════════════════════════════════════════
#  VOICE INPUT  — Push-to-Talk (hold SPACE to record, release to send)
#  pip install SpeechRecognition pyaudio keyboard
#
#  WHY PUSH-TO-TALK:
#    The AI voice output and mic input overlap if always-listening.
#    Holding SPACE gives the user full control — record only when ready,
#    so it never accidentally captures the AI speaking.
# ══════════════════════════════════════════════════════════════════════════════

class VoiceInput:
    """
    Push-to-talk voice input.
    - HOLD SPACE  → mic records
    - RELEASE     → transcribes and returns text
    - ENTER       → fallback to type instead
    """

    def __init__(self):
        self.enabled = False
        self.available = False

        try:
            import speech_recognition as sr
            self.sr         = sr
            self.recognizer = sr.Recognizer()
            self.recognizer.energy_threshold          = 300
            self.recognizer.dynamic_energy_threshold  = True
            self.recognizer.pause_threshold           = 0.8
            self.sr_available = True
        except ImportError:
            self.sr_available = False

        try:
            import keyboard
            self.keyboard     = keyboard
            self.kb_available = True
        except ImportError:
            self.kb_available = False

        self.available = self.sr_available and self.kb_available
        self.language  = "en-IN"   # switched to "ta-IN" for Tamil mode

    def listen(self, prompt: str = "You") -> str:
        """
        Show push-to-talk hint.
        If user holds SPACE → record until released → transcribe → return.
        If user presses ENTER immediately → fallback to typed input.
        """
        if not self.enabled or not self.available:
            return input(f"{prompt}: ").strip()

        print(f"\n{prompt}: [ Hold SPACE to speak  |  Press ENTER to type ]  ", end="", flush=True)

        # Wait for either SPACE or ENTER
        while True:
            if self.keyboard.is_pressed("space"):
                return self._record_until_release()
            if self.keyboard.is_pressed("enter"):
                print()
                return input(f"{prompt}: ").strip()
            time.sleep(0.02)   # tiny poll interval — no CPU burn

    def _record_until_release(self) -> str:
        """Record audio while SPACE is held. Transcribe on release."""
        print("🔴 Recording... (release SPACE when done)", end="\r", flush=True)

        audio_data = []
        sample_rate = 16000

        try:
            import pyaudio
            pa     = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                input=True,
                frames_per_buffer=1024,
            )

            # Capture while SPACE is held
            while self.keyboard.is_pressed("space"):
                chunk = stream.read(1024, exception_on_overflow=False)
                audio_data.append(chunk)

            stream.stop_stream()
            stream.close()
            pa.terminate()

        except Exception as e:
            print(f"\n  (mic error: {e} — type your message)")
            return input("You: ").strip()

        if not audio_data:
            print("  (nothing recorded — type your message)          ")
            return input("You: ").strip()

        print("⏳ Transcribing...                              ", end="\r", flush=True)

        # Convert raw bytes → AudioData for SpeechRecognition
        import io, wave
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)   # paInt16 = 2 bytes
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(audio_data))
        wav_buf.seek(0)

        try:
            audio = self.sr.AudioData(wav_buf.read(), sample_rate, 2)
            lang  = getattr(self, "language", "en-IN")
            text  = self.recognizer.recognize_google(audio, language=lang).strip()
            print(f"You: {text}                                        ")
            return text

        except self.sr.UnknownValueError:
            print("  (could not understand — type your message)      ")
            return input("You: ").strip()

        except self.sr.RequestError:
            print("  (Google speech service error — type your message)")
            return input("You: ").strip()

        except Exception:
            return input("You: ").strip()


# ══════════════════════════════════════════════════════════════════════════════
#  VOICE ENGINE  — Edge TTS (Microsoft Neural Voices, natural Indian accent)
#  pip install edge-tts
# ══════════════════════════════════════════════════════════════════════════════

class HumanVoice:
    """
    Speaks using Microsoft Edge TTS neural voices.
    Sounds dramatically more natural than SAPI/pyttsx3.
    Uses Indian English voices (en-IN) to match Tamil Nadu context.

    Female: en-IN-NeerjaNeural
    Male  : en-IN-PrabhatNeural

    Each speak_async() fires a background thread — terminal stays responsive.
    """

    # English voices (en-IN)
    VOICE_MAP = {
        "female": "en-IN-NeerjaNeural",
        "male":   "en-IN-PrabhatNeural",
    }

    # Tamil voices (ta-IN)
    TAMIL_VOICE_MAP = {
        "female": "ta-IN-PallaviNeural",
        "male":   "ta-IN-ValluvarNeural",
    }

    def __init__(self):
        self.enabled  = True
        self.gender   = "female"
        self.language = "en"   # "en" or "ta"

    # ── Text preparation ───────────────────────────────────────────────────────

    def _prepare(self, text: str) -> str:
        # Strip markdown
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'_+',  '', text)
        text = re.sub(r'#+\s*', '', text)
        text = re.sub(r'\[.*?\]\(.*?\)', '', text)

        # Expand contractions
        for short, full in {
            "I'm":    "I am",
            "you're": "you are",
            "it's":   "it is",
            "that's": "that is",
            "don't":  "do not",
            "won't":  "will not",
            "can't":  "cannot",
            "isn't":  "is not",
            "wasn't": "was not",
            "they're":"they are",
            "we're":  "we are",
            "I've":   "I have",
            "you've": "you have",
            "I'll":   "I will",
            "you'll": "you will",
        }.items():
            text = text.replace(short, full)

        return text.strip()

    # ── Edge TTS speech ────────────────────────────────────────────────────────

    def _speak_now(self, text: str):
        """Generate audio with Edge TTS and play it via Windows MediaPlayer."""
        try:
            import edge_tts
            vmap  = self.TAMIL_VOICE_MAP if self.language == "ta" else self.VOICE_MAP
            voice = vmap.get(self.gender, "en-IN-NeerjaNeural")
            out_file = os.path.join(os.environ.get("TEMP", "."), "_companion_tts.mp3")

            async def _generate():
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(out_file)

            asyncio.run(_generate())

            # Estimate duration: ~140 words per minute for Indian English
            word_count    = len(text.split())
            sleep_seconds = max(2, int(word_count / 140 * 60) + 2)

            # Play MP3 via PowerShell Windows Media Player
            ps_script = f"""
Add-Type -AssemblyName presentationCore
$mp = New-Object System.Windows.Media.MediaPlayer
$mp.Open([uri]'{out_file}')
$mp.Play()
Start-Sleep -Seconds {sleep_seconds}
$mp.Stop()
$mp.Close()
"""
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            ).wait()

        except ImportError:
            print("⚠️  edge-tts not installed. Run: pip install edge-tts")
        except Exception:
            pass   # Never crash the app over a voice error

    # ── Public API ─────────────────────────────────────────────────────────────

    def speak_async(self, text: str):
        """Speak in background — terminal stays responsive immediately."""
        if not self.enabled or not text.strip():
            return
        clean = self._prepare(text)
        t = threading.Thread(target=self._speak_now, args=(clean,), daemon=True)
        t.start()

    def speak(self, text: str):
        """Speak and wait until finished — used for farewell and crisis convos."""
        if not self.enabled or not text.strip():
            return
        clean = self._prepare(text)
        self._speak_now(clean)

    def set_gender(self, gender: str):
        self.gender = gender.lower()


# ══════════════════════════════════════════════════════════════════════════════
#  TRANSLATOR  — deep-translator (Google Translate, free, no API key)
#  pip install deep-translator
#
#  FLOW (Tamil mode):
#    User speaks Tamil  →  transcribed as Tamil text
#    Tamil text         →  translated to English  →  Ollama thinks in English
#    English reply      →  translated to Tamil    →  spoken by Tamil TTS voice
# ══════════════════════════════════════════════════════════════════════════════

class Translator:
    """
    Wraps deep-translator's GoogleTranslator.
    Falls back silently — if translation fails, returns original text.
    """

    def __init__(self):
        self.available = False
        try:
            from deep_translator import GoogleTranslator
            self.GoogleTranslator = GoogleTranslator
            self.available = True
        except ImportError:
            print("⚠️  deep-translator not installed. Run: pip install deep-translator")

    def to_english(self, text: str) -> str:
        """Translate any language → English."""
        if not self.available or not text.strip():
            return text
        try:
            return self.GoogleTranslator(source="auto", target="en").translate(text)
        except Exception:
            return text   # silent fallback

    def to_tamil(self, text: str) -> str:
        """Translate English → Tamil."""
        if not self.available or not text.strip():
            return text
        try:
            return self.GoogleTranslator(source="en", target="ta").translate(text)
        except Exception:
            return text   # silent fallback


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN COMPANION
# ══════════════════════════════════════════════════════════════════════════════

class EmotionalCompanion:

    def __init__(self, user_id: str = "default_user", user_name: str = "Friend"):
        self.user_id     = user_id
        self.user_name   = user_name
        self.memory      = UserMemory(user_id)
        self.voice       = HumanVoice()
        self.voice_input = VoiceInput()
        self.translator  = Translator()
        self.language    = "en"   # "en" or "ta" — set at startup
        self.model       = "llama3.2:3b"
        self.message_count = 0
        self.conversation_history = []
        self.options   = {
            "temperature":    0.9,
            "top_p":          0.95,
            "num_predict":    150,
            "repeat_penalty": 1.2,
        }

    WHATSAPP_CONTACT = "+919003250656"
    HELPLINES        = ["104", "14416"]

    # ══════════════════════════════════════════════════════════════════════════
    #  CRISIS DETECTION
    # ══════════════════════════════════════════════════════════════════════════

    def _is_crisis(self, text: str) -> bool:
        """Fuzzy suicide/crisis detection — handles typos."""
        t = text.lower()
        if any(k in t for k in CRISIS_KEYWORDS):
            return True
        fuzzy_patterns = [
            r'suic', r'suci', r'suis', r'sucd',
            r'do\s+suicide', r'do\s+sucide',
            r'commit\s+su',
            r'kill\s+my',
            r'end\s+my\s+life',
            r'want\s+to\s+die',
            r'not\s+worth\s+liv',
            r'not\s+worth(y)?',
            r'harm\s+my',
            r'hurt\s+my\s*s',
            r'no\s+reason\s+to\s+live',
            r'better\s+off\s+dead',
            r'end\s+it\s+all',
            r'unworthy', r'worthless',
            r'give\s+up\s+on\s+life',
            r'no\s+point\s+(in\s+)?liv',
            r'life\s+is\s+(not\s+)?worth',
            r'(don.t|do\s+not)\s+want\s+to\s+(be\s+)?alive',
            r'tired\s+of\s+(being\s+)?alive',
            r'dont\s+want\s+to\s+live',
            r'self.harm', r'self.hurt',
        ]
        return any(re.search(p, t) for p in fuzzy_patterns)

    def _is_violence(self, text: str) -> bool:
        """Violence toward OTHERS — always excludes self-harm phrases."""
        t = text.lower()
        if self._is_crisis(t):
            return False
        return any(k in t for k in VIOLENCE_KEYWORDS)

    # ══════════════════════════════════════════════════════════════════════════
    #  EMERGENCY SYSTEM
    # ══════════════════════════════════════════════════════════════════════════

    def _trigger_emergency(self):
        """WhatsApp alert + open phone dialers — parallel background threads."""

        def _open_whatsapp():
            try:
                from twilio.rest import Client
                import twilio_config as tc
                client = Client(tc.ACCOUNT_SID, tc.AUTH_TOKEN)
                client.messages.create(
                    from_=tc.FROM_NUMBER,
                    to=tc.TO_NUMBER,
                    body=(
                        f"URGENT ALERT from Emotional Support Companion: "
                        f"{self.user_name} may be in crisis and needs immediate support. "
                        f"Please check on {self.user_name} RIGHT AWAY. "
                        f"Crisis helplines: 14416 Tamil Nadu Mental Health | 104 Health Helpline"
                    )
                )
                print("✅ WhatsApp alert sent via Twilio!")
            except ImportError:
                print("⚠️  Run: pip install twilio")
            except Exception as e:
                print(f"WhatsApp alert error: {e}")

        def _open_dialers():
            time.sleep(2)
            for number in self.HELPLINES:
                try:
                    subprocess.Popen(
                        ["powershell", "-NoProfile", "-NonInteractive",
                         "-ExecutionPolicy", "Bypass",
                         "-Command", f"Start-Process 'ms-phone:{number}'"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    time.sleep(2)
                except Exception:
                    pass

        threading.Thread(target=_open_whatsapp, daemon=True).start()
        threading.Thread(target=_open_dialers,  daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════════
    #  CHAT
    # ══════════════════════════════════════════════════════════════════════════

    def chat(self, user_input: str):
        self.message_count += 1

        # If Tamil mode — translate input to English for crisis detection + Ollama
        english_input = user_input
        if self.language == "ta":
            english_input = self.translator.to_english(user_input)
            if english_input != user_input:
                print(f"  (translated: {english_input})\n")

        # IMPORTANT: crisis check MUST run before violence check
        if self._is_crisis(english_input):
            self._log_crisis("suicide", english_input)
            self._trigger_emergency()
            self._run_3pov_convo(mode="selfharm")
            return

        if self._is_violence(english_input):
            self._log_crisis("violence", english_input)
            self._trigger_emergency()
            self._run_3pov_convo(mode="violence")
            return

        self.memory.extract_insights_from_message(english_input)

        system = SYSTEM_PROMPT
        ctx = self.memory.get_context()
        if ctx:
            system += f"\n\nWhat you remember about this person:\n{ctx}"
        if self.message_count % 6 == 0:
            system += f"\n\nGently suggest real human connection: {random.choice(CONNECTION_PROMPTS)}"
        if self.message_count % 3 == 0:
            system += f"\n\nWeave in this affirmation naturally: {random.choice(AFFIRMATIONS)}"

        self.conversation_history.append({"role": "user", "content": english_input})

        try:
            res = ollama.chat(
                model=self.model,
                messages=[{"role": "system", "content": system}] + self.conversation_history,
                options=self.options,
            )
            response = res["message"]["content"].strip()
        except Exception as e:
            response = f"I am having a little trouble right now. Give me a moment. {e}"

        self.conversation_history.append({"role": "assistant", "content": response})
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]

        # Translate reply to Tamil if in Tamil mode
        display_response = response
        if self.language == "ta":
            display_response = self.translator.to_tamil(response)

        self._show_and_speak(display_response)

    def _show_and_speak(self, text: str):
        print(f"\n💙 Companion: {text}\n")
        self.voice.speak_async(text)

    # ══════════════════════════════════════════════════════════════════════════
    #  3-PERSPECTIVE CONVERSATIONAL CRISIS RESPONSE
    # ══════════════════════════════════════════════════════════════════════════

    def _pov_lines(self, mode: str) -> list:
        """
        3 perspectives as a list of dicts.
        Each dict has: label, intro, message, question.
        Delivered one at a time — user replies between each.
        """
        n = self.user_name

        if mode == "selfharm":
            return [
                {
                    "label":    "PERSPECTIVE 1 — YOUR OWN FUTURE",
                    "intro":    f"Hey {n}, I'm not going anywhere. I want to talk to you — not lecture you, just talk like a friend. Can you hear me out for a second?",
                    "message":  (
                        f"{n}, whatever is hurting you right now — that problem is still going to be there after. "
                        f"The pain doesn't disappear with the body, da. The thing that made you feel this way — "
                        f"the person, the situation, the feeling of being stuck — it stays exactly where it is. "
                        f"You would be taking yourself out of the picture, but the picture doesn't change. "
                        f"The consequences are permanent. There is no coming back to fix anything, to prove them wrong, "
                        f"to see what your life could have become. You would be closing every door — "
                        f"including the ones you haven't even seen yet."
                    ),
                    "question": f"Tell me honestly {n} — what is it that's making you feel like there's no other way out? What happened?",
                },
                {
                    "label":    "PERSPECTIVE 2 — THE PEOPLE WHO LOVE YOU",
                    "intro":    f"I hear you, {n}. Thank you for telling me that. I want to share one more thing — about the people in your life.",
                    "message":  (
                        f"Think about whoever loves you most — your amma, your friend, your anyone. "
                        f"The moment they find out, {n}, that moment breaks them. Not for a day — forever. "
                        f"They will replay every single conversation wondering what they missed, "
                        f"blaming themselves for the rest of their lives. "
                        f"Grief from losing someone this way does not heal the same way. It hollows people out. "
                        f"The ones who love you will carry that weight at every birthday, every festival, "
                        f"every quiet evening. You matter to them more than you know right now."
                    ),
                    "question": f"Is there even one person — just one — who you know genuinely cares about you, {n}?",
                },
                {
                    "label":    "PERSPECTIVE 3 — YOUR PLACE IN THIS WORLD",
                    "intro":    f"Still here with you, {n}. One last thing I need you to hear — and I really mean this.",
                    "message":  (
                        f"This feeling you have right now — that nothing will ever get better, that you do not matter — "
                        f"that feeling is lying to you. I know it feels completely real. But it is temporary, "
                        f"even when it does not feel like it. "
                        f"People have felt exactly what you are feeling and come out the other side "
                        f"to a life they could not have imagined in that dark moment. "
                        f"You are not a burden, {n}. You are a person in real pain. Those are two very different things. "
                        f"Pain — with help, slowly — can be worked through. "
                        f"You have things in you that the world still needs."
                    ),
                    "question": f"What would your life need to look like for it to feel worth staying for, {n}? Even one small thing — what would that be?",
                },
            ]

        elif mode == "violence":
            return [
                {
                    "label":    "PERSPECTIVE 1 — YOUR OWN FUTURE",
                    "intro":    f"Wait, {n}. Before anything else — I am not judging you. I just need you to think with me for one minute. Can you do that?",
                    "message":  (
                        f"Whatever anger or hurt is driving this right now — I hear it, {n}. It is real. "
                        f"But the anger will fade. It always does. What is left after? "
                        f"The consequences do not fade. A police case, a criminal record, jail time — "
                        f"that follows you for life. Every job, every relationship, every chance you ever wanted — "
                        f"this one act can close all of those doors permanently. "
                        f"And the worst part? The problem that made you this angry? It will still be there. "
                        f"You will be sitting with the same pain, plus a hundred new ones on top."
                    ),
                    "question": f"What actually happened, {n}? Tell me — what pushed you to this point?",
                },
                {
                    "label":    "PERSPECTIVE 2 — HOW THE WORLD WILL SEE YOU",
                    "intro":    f"I am still here, {n}. I hear you. But I want you to think about something else too.",
                    "message":  (
                        f"Right now, people who know you — your family, your friends, people from your area — "
                        f"they have a version of you in their heads. A real version of who you are. "
                        f"This one act rewrites that completely, {n}. "
                        f"They will not see the pain that brought you here. They will only see the act. "
                        f"Your amma, your siblings — they will have to face questions, shame, whispers in the street. "
                        f"Not because of who you are as a person, but because of one moment at your worst. "
                        f"You are so much more than your worst moment, {n}. "
                        f"But you have to choose not to let it become the one that defines you."
                    ),
                    "question": f"Is there someone specific who pushed you to this edge, {n}? What did they actually do?",
                },
                {
                    "label":    "PERSPECTIVE 3 — THE OTHER PERSON",
                    "intro":    f"One more thing, {n} — and I need you to really sit with this one.",
                    "message":  (
                        f"The person you are thinking of hurting — they have a life too. "
                        f"Maybe they wronged you badly. I am not saying what they did was okay. "
                        f"But they have a family. Someone who loves them and is waiting for them to come home tonight. "
                        f"They have their own fears, their own story, people who depend on them. "
                        f"Hurting them does not undo what they did to you — "
                        f"it just adds more damage to an already broken situation. "
                        f"And you, {n} — you would have to live with that image for the rest of your life. "
                        f"That is a weight that does not leave. You deserve better than that."
                    ),
                    "question": f"Is there any part of you — even a small part — that is hoping there is another way through this, {n}?",
                },
            ]

        return []

    def _pov_acknowledge(self, user_reply: str, pov_index: int) -> str:
        """Short warm acknowledgement after user responds to a perspective question."""
        n           = self.user_name
        reply_lower = user_reply.lower()

        opening_words   = ["yes", "yeah", "i know", "okay", "ok", "i think",
                           "maybe", "true", "you're right", "right", "i guess"]
        resisting_words = ["no", "nope", "don't care", "doesn't matter",
                           "whatever", "nothing", "i don't", "shut up"]

        is_opening   = any(w in reply_lower for w in opening_words)
        is_resisting = any(w in reply_lower for w in resisting_words)

        if is_opening:
            choices = [
                f"I hear you, {n}. That takes courage to say. Keep going with me.",
                f"Yeah. I thought so, {n}. You are stronger than you think right now.",
                f"Exactly, {n}. And that matters more than you realize.",
            ]
        elif is_resisting:
            choices = [
                f"I know it feels that way, {n}. I am not giving up on this conversation.",
                f"That is okay. You do not have to agree. Just stay with me a bit longer, {n}.",
                f"You are still here talking to me, {n}. That means something.",
            ]
        else:
            choices = [
                f"Thank you for telling me that, {n}. I am listening.",
                f"Okay {n}, I hear you. Let me share one more thing.",
                f"I get it, {n}. Here is something else I want you to think about.",
            ]

        return random.choice(choices)

    def _say(self, text: str) -> str:
        """
        Translate to Tamil if needed, print, and speak BLOCKING (no overlap).
        Returns the displayed text.
        Used exclusively inside _run_3pov_convo so voices never clash.
        """
        display = self.translator.to_tamil(text) if self.language == "ta" else text
        print(f"💙 Companion: {display}\n")
        self.voice.speak(display)   # blocking — waits until speech finishes
        return display

    def _run_3pov_convo(self, mode: str):
        """
        Delivers the 3-perspective response as a real back-and-forth conversation.
        One perspective at a time → speaks FULLY → waits for user reply → next.
        Uses blocking speak() so voices NEVER overlap.
        """
        perspectives = self._pov_lines(mode)

        # ── Show helplines immediately ─────────────────────────────────────────
        print("\n💙 Companion:")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        if mode == "selfharm":
            if self.language == "ta":
                print("  🆘 உடனடியாக தொடர்பு கொள்ளுங்கள்:")
            else:
                print("  🆘 Please reach out right now if you need immediate help:")
            print("    📞 14416  — Tamil Nadu Mental Health Helpline (Free, 24/7)")
            print("    📞 104    — Health Helpline (Free, 24/7)")
        else:
            if self.language == "ta":
                print("  🚨 எதுவும் செய்வதற்கு முன் நிறுத்தி மூச்சை எடுங்கள்.")
            else:
                print("  🚨 Please pause and breathe before you do anything.")
            print("    📞 14416  — Tamil Nadu Mental Health Helpline (Free, 24/7)")
        if self.language == "ta":
            print(f"  💬 அவசர எச்சரிக்கை உங்கள் தொடர்பு நபருக்கு அனுப்பப்பட்டது.")
        else:
            print(f"  💬 Emergency alert has been sent to your emergency contact.")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        time.sleep(0.5)

        prompt_label = "நீங்கள்" if self.language == "ta" else "You"

        # ── Walk through each perspective one by one ───────────────────────────
        for i, pov in enumerate(perspectives):

            print(f"\n  ── {pov['label']} ──\n")
            time.sleep(0.3)

            # Intro — speak fully, then move on
            self._say(pov["intro"])

            # Main message — speak fully, then move on
            self._say(pov["message"])

            # Question — speak fully, THEN wait for user reply
            self._say(pov["question"])

            try:
                user_reply = self.voice_input.listen(prompt_label).strip()
            except (EOFError, KeyboardInterrupt):
                user_reply = ""

            # Acknowledge — blocking so it finishes before next perspective
            if user_reply:
                # Translate reply to English for keyword matching
                reply_for_ack = self.translator.to_english(user_reply) if self.language == "ta" else user_reply
                ack_en = self._pov_acknowledge(reply_for_ack, i)
                self._say(ack_en)

            if i < len(perspectives) - 1:
                print("  " + "─" * 54)

        # ── Final closing message ──────────────────────────────────────────────
        final_en = (
            f"I am really glad you talked to me, {self.user_name}. "
            f"Please reach out to someone who can physically be there with you right now. "
            f"Call 14416 or 104 — they are free, available 24 hours, and they will not judge you. "
            f"I am here whenever you need to talk more."
        )
        self._say(final_en)
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    # ══════════════════════════════════════════════════════════════════════════
    #  LOGGING
    # ══════════════════════════════════════════════════════════════════════════

    def _log_crisis(self, kind: str, text: str):
        os.makedirs("logs", exist_ok=True)
        with open("logs/crisis_alerts.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] TYPE={kind} INPUT={text[:100]}\n")

    # ══════════════════════════════════════════════════════════════════════════
    #  COMMANDS
    # ══════════════════════════════════════════════════════════════════════════

    def handle_command(self, cmd: str) -> bool:
        cmd = cmd.strip().lower()

        if cmd == "memory":
            print(f"\n📖 Memory:\n{self.memory.get_context() or 'Nothing stored yet.'}\n")
            return True

        if cmd == "voice female":
            self.voice.set_gender("female")
            print("🎙️  Switched to female voice — Neerja (Indian English).\n")
            self.voice.speak_async("Done! Switched to female voice.")
            return True

        if cmd == "voice male":
            self.voice.set_gender("male")
            print("🎙️  Switched to male voice — Prabhat (Indian English).\n")
            self.voice.speak_async("Done! Switched to male voice.")
            return True

        if cmd == "voice off":
            self.voice.enabled = False
            print("🔇 Voice disabled. Type 'voice on' to re-enable.\n")
            return True

        if cmd == "voice on":
            self.voice.enabled = True
            print("🔊 Voice enabled.\n")
            self.voice.speak_async("I am back!")
            return True

        if cmd == "mic on":
            if self.voice_input.available:
                self.voice_input.enabled = True
                print("🎙️  Push-to-talk enabled — hold SPACE to speak, release to send.\n")
                self.voice.speak_async("Push to talk is on. Hold the space bar to speak to me.")
            else:
                missing = []
                if not self.voice_input.sr_available:
                    missing.append("SpeechRecognition pyaudio")
                if not self.voice_input.kb_available:
                    missing.append("keyboard")
                print(f"⚠️  Missing packages. Run: pip install {' '.join(missing)}\n")
            return True

        if cmd == "mic off":
            self.voice_input.enabled = False
            print("⌨️  Switched back to keyboard input.\n")
            return True

        if cmd == "clear":
            self.conversation_history = []
            print("🗑️  Conversation cleared.\n")
            return True

        if cmd == "help":
            print("""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Commands:
    memory       — Show what I remember about you
    voice female — Switch to female voice (Neerja)
    voice male   — Switch to male voice (Prabhat)
    voice on/off — Enable / disable voice output
    mic on/off   — Enable / disable microphone input
    clear        — Start a fresh conversation
    quit / exit  — Leave
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")
            return True

        return False


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("""
╔══════════════════════════════════════════════════════╗
║        Tamil Nadu Emotional Support Companion        ║
║           Your friend. Always here. 💙               ║
╚══════════════════════════════════════════════════════╝

Type  'help'  for commands  |  'quit' to exit
""")

    # ── Step 1: Language ───────────────────────────────────────────────────────
    print("முதலில் — Which language do you prefer?")
    print("  [1] English (default)")
    print("  [2] Tamil / தமிழ்\n")
    lang_choice = input("Your choice (press Enter for English): ").strip()
    use_tamil   = (lang_choice == "2")

    if use_tamil:
        print("✅ Tamil mode selected — தமிழ் / Tamil\n")
    else:
        print("✅ English mode selected.\n")

    # ── Step 2: Voice gender ───────────────────────────────────────────────────
    if use_tamil:
        print("குரல் தேர்வு — Which voice?")
        print("  [1] Female — Pallavi (Tamil, default)")
        print("  [2] Male   — Valluvar (Tamil)")
        print("  [3] No voice (text only)\n")
    else:
        print("Before we begin — which voice feels more comfortable?")
        print("  [1] Female voice — Neerja (Indian English, default)")
        print("  [2] Male voice   — Prabhat (Indian English)")
        print("  [3] No voice (text only)\n")

    choice = input("Your choice (press Enter for default): ").strip()

    # ── Step 3: Input method ───────────────────────────────────────────────────
    print("\nHow would you like to talk to me?")
    print("  [1] Type  (default)")
    print("  [2] Speak (hold SPACE to record)\n")
    input_choice = input("Your choice (press Enter for default): ").strip()

    # ── Step 4: Name ───────────────────────────────────────────────────────────
    if use_tamil:
        print("\nஉங்கள் பெயர் என்ன? (Enter தட்டி skip செய்யலாம்)")
    else:
        print("\nWhat should I call you? (press Enter to skip)")
    user_name = input("Your name: ").strip()
    if not user_name:
        user_name = "நண்பா" if use_tamil else "Friend"

    # ── Build companion ────────────────────────────────────────────────────────
    companion = EmotionalCompanion(user_name=user_name)

    # Apply language setting everywhere
    if use_tamil:
        companion.language             = "ta"
        companion.voice.language       = "ta"
        companion.voice_input.language = "ta-IN"
    else:
        companion.language             = "en"
        companion.voice.language       = "en"
        companion.voice_input.language = "en-IN"

    # Apply voice gender
    if choice == "2":
        companion.voice.set_gender("male")
        if use_tamil:
            print("✅ ஆண் குரல் — Valluvar (Tamil) தேர்ந்தெடுக்கப்பட்டது.\n")
        else:
            print("✅ Male voice selected — Prabhat (Indian English).\n")
    elif choice == "3":
        companion.voice.enabled = False
        print("✅ Text-only mode.\n")
    else:
        companion.voice.set_gender("female")
        if use_tamil:
            print("✅ பெண் குரல் — Pallavi (Tamil) தேர்ந்தெடுக்கப்பட்டது.\n")
        else:
            print("✅ Female voice selected — Neerja (Indian English).\n")

    # Apply input method
    if input_choice == "2":
        if companion.voice_input.available:
            companion.voice_input.enabled = True
            if use_tamil:
                print("✅ Push-to-talk இயக்கப்பட்டது — SPACE பிடித்து பேசுங்கள்.\n")
            else:
                print("✅ Push-to-talk enabled — hold SPACE to speak, release to send.\n")
        else:
            missing = []
            if not companion.voice_input.sr_available:
                missing.append("SpeechRecognition pyaudio")
            if not companion.voice_input.kb_available:
                missing.append("keyboard")
            print(f"⚠️  Missing packages: pip install {' '.join(missing)}")
            print("   Falling back to keyboard input.\n")
    else:
        if use_tamil:
            print("✅ Keyboard input. 'mic on' என்று தட்டி மாற்றலாம்.\n")
        else:
            print("✅ Keyboard input selected. Type  'mic on'  to switch to voice.\n")

    # ── Greeting ───────────────────────────────────────────────────────────────
    if use_tamil:
        greeting = (
            f"வணக்கம் {user_name}, நீங்கள் இங்கே வந்தது மிகவும் சந்தோஷமாக இருக்கிறது. "
            f"நான் உங்கள் நண்பன் மட்டுமே — எந்த தீர்ப்பும் இல்லாமல் கேட்கிறேன். "
            f"இப்போது எப்படி உணர்கிறீர்கள்?"
        )
    else:
        greeting = (
            f"Hey {user_name}, I am really glad you are here. "
            f"I am just a friend who listens — no judgment at all. "
            f"How are you feeling today?"
        )

    print(f"💙 Companion: {greeting}\n")
    companion.voice.speak_async(greeting)

    # ── Main loop ──────────────────────────────────────────────────────────────
    while True:
        try:
            user_input = companion.voice_input.listen("நீங்கள்" if use_tamil else "You").strip()
        except (EOFError, KeyboardInterrupt):
            if use_tamil:
                print("\n💙 உங்களை நீங்களே கவனித்துக்கொள்ளுங்கள். நீங்கள் முக்கியம். 🙏\n")
            else:
                print("\n💙 Take care of yourself. You matter. 🙏\n")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "bye", "விடை", "போகிறேன்"):
            if use_tamil:
                farewell = (
                    f"சரியாக கவனித்துக்கொள்ளுங்கள் {user_name}. "
                    f"உங்களை நேசிக்கும் மனிதர்கள் இருக்கிறார்கள். "
                    f"எப்போது வேண்டுமானாலும் திரும்பி வாருங்கள்."
                )
            else:
                farewell = (
                    "Take good care okay? Remember, real people around you love you. "
                    "Come back anytime you need a listening ear."
                )
            print(f"\n💙 Companion: {farewell}\n")
            companion.voice.speak(farewell)
            break

        if companion.handle_command(user_input):
            continue

        companion.chat(user_input)


if __name__ == "__main__":
    main()
