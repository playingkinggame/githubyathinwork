import speech_recognition as sr
import threading
import queue
import os
import asyncio
import edge_tts
import pygame
import tempfile
import tkinter as tk
import tkinter.font as tk_font
import math
import random
import datetime
import webbrowser
import psutil
import pyautogui
import json
from groq import Groq
import AppOpener

# ──────────────────────────────────────────────
#  GROQ CLIENT
# ──────────────────────────────────────────────
client = Groq(api_key=" your groq api key here ")

text_queue    = queue.Queue()
tts_queue     = queue.Queue()
is_speaking   = threading.Event()
current_state = {"mode": "listening"}
display_text  = {"user": "", "jarvis": ""}

# ══════════════════════════════════════════════
#  MEMORY SYSTEM
# ══════════════════════════════════════════════
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_memory.json")
MAX_MEMORY   = 50    # maximum conversation turns to keep in memory
memory_lock  = threading.Lock()

def load_memory() -> list:
    """Load conversation history from disk. Returns list of {role, content} dicts."""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []

def save_memory(history: list):
    """Persist conversation history to disk (thread-safe)."""
    with memory_lock:
        try:
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Memory] Save error: {e}")

def add_to_memory(history: list, role: str, content: str) -> list:
    """
    Append a message and trim to MAX_MEMORY turns.
    A 'turn' is one user + one assistant message pair.
    We keep at most MAX_MEMORY individual messages total.
    """
    history.append({"role": role, "content": content})
    if len(history) > MAX_MEMORY:
        # Always drop oldest messages but keep system prompt intact
        history = history[-MAX_MEMORY:]
    return history

def clear_memory():
    """Wipe all conversation history."""
    global conversation_history
    conversation_history = []
    save_memory([])
    log("Memory cleared")

# Load history from disk on startup
conversation_history: list = load_memory()
log_buffer = []   # temporary; real log() defined after activity_log

# ──────────────────────────────────────────────
#  ACTIVITY LOG (for right panel)
# ──────────────────────────────────────────────
activity_log = []
MAX_LOG = 14

def log(msg: str):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    activity_log.insert(0, f"[{ts}] {msg}")
    if len(activity_log) > MAX_LOG:
        activity_log.pop()

# Flush any early log messages (from before activity_log existed)
for m in log_buffer:
    log(m)
log_buffer.clear()

mem_count = len(conversation_history)
log(f"Memory loaded: {mem_count} messages")

# ──────────────────────────────────────────────
#  COMPUTER CONTROL FUNCTIONS
# ──────────────────────────────────────────────
def open_app(n):
    try:    AppOpener.open(n);  return f"Opening {n}"
    except: return f"Could not open {n}"

def close_app(n):
    try:    AppOpener.close(n); return f"Closing {n}"
    except: return f"Could not close {n}"

def take_screenshot():
    p = os.path.expanduser("~/Desktop/screenshot.png")
    pyautogui.screenshot().save(p)
    return "Screenshot saved to Desktop"

def volume_up():
    [pyautogui.press("volumeup")   for _ in range(5)]; return "Volume increased"
def volume_down():
    [pyautogui.press("volumedown") for _ in range(5)]; return "Volume decreased"
def mute_volume():
    pyautogui.press("volumemute"); return "Volume muted"
def open_website(url):
    webbrowser.open(url); return f"Opening {url}"
def shutdown_pc():
    os.system("shutdown /s /t 5"); return "Shutting down in 5 seconds"
def restart_pc():
    os.system("shutdown /r /t 5"); return "Restarting in 5 seconds"
def lock_pc():
    os.system("rundll32.exe user32.dll,LockWorkStation"); return "PC locked"
def sleep_pc():
    os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0"); return "Going to sleep"
def get_battery():
    b = psutil.sensors_battery()
    return f"Battery at {int(b.percent)}%" if b else "Battery unavailable"
def get_time():
    return f"The time is {datetime.datetime.now().strftime('%I:%M %p')}"
def get_date():
    return f"Today is {datetime.datetime.now().strftime('%B %d, %Y')}"

def handle_command(text):
    if "open youtube"    in text: return open_website("https://youtube.com")
    if "open google"     in text: return open_website("https://google.com")
    if "open instagram"  in text: return open_website("https://instagram.com")
    if "open twitter"    in text or "open x" in text: return open_website("https://x.com")
    if "search" in text and "youtube" in text:
        q = text.replace("search","").replace("on youtube","").replace("youtube","").strip()
        return open_website(f"https://www.youtube.com/results?search_query={q.replace(' ','+')}")
    if "search" in text and "google" in text:
        q = text.replace("search","").replace("on google","").replace("google","").strip()
        return open_website(f"https://www.google.com/search?q={q.replace(' ','+')}")
    if "open"  in text: return open_app(text.replace("open","").strip())
    if "close" in text: return close_app(text.replace("close","").strip())
    if "volume up"   in text or "increase volume" in text: return volume_up()
    if "volume down" in text or "decrease volume" in text: return volume_down()
    if "mute"        in text: return mute_volume()
    if "screenshot"  in text: return take_screenshot()
    if "shutdown"    in text or "shut down" in text: return shutdown_pc()
    if "restart"     in text: return restart_pc()
    if "lock"        in text: return lock_pc()
    if "sleep"       in text: return sleep_pc()
    if "battery"     in text: return get_battery()
    if "time"        in text: return get_time()
    if "date"        in text: return get_date()
    return None

# ──────────────────────────────────────────────
#  TTS
# ──────────────────────────────────────────────
def tts_worker():
    pygame.mixer.init()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while True:
        text = tts_queue.get()
        if text is None: break
        is_speaking.set(); current_state["mode"] = "speaking"
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
                tmp = f.name
            loop.run_until_complete(
                edge_tts.Communicate(text, voice="en-GB-RyanNeural").save(tmp))
            pygame.mixer.music.load(tmp)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy(): pygame.time.Clock().tick(10)
            pygame.mixer.music.unload()
        except Exception as e: print(f"TTS: {e}")
        finally:
            is_speaking.clear(); current_state["mode"] = "listening"
            try: os.remove(tmp)
            except: pass
        tts_queue.task_done()
    loop.close()

threading.Thread(target=tts_worker, daemon=True).start()
def speak(t): tts_queue.put(t)

# ──────────────────────────────────────────────
#  AI  — now with full conversation memory
# ──────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are Jarvis, a smart, loyal, and witty AI assistant inspired by Iron Man. "
    "You remember everything the user has told you across all sessions. "
    "Keep responses concise, clear, and conversational. "
    "When the user refers to something from earlier in the conversation, "
    "use that context naturally without being asked."
)

def ask_ai(prompt: str) -> str:
    global conversation_history
    try:
        # Add the new user message to history
        conversation_history = add_to_memory(conversation_history, "user", prompt)

        # Build the full message list: system prompt + entire history
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history

        r = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
        )
        reply = r.choices[0].message.content

        # Store assistant reply and persist to disk
        conversation_history = add_to_memory(conversation_history, "assistant", reply)
        save_memory(conversation_history)

        mem_size = len(conversation_history)
        log(f"Memory: {mem_size} msgs stored")
        return reply

    except Exception as e:
        return f"AI Error: {e}"

# ──────────────────────────────────────────────
#  LISTEN / PROCESS
# ──────────────────────────────────────────────
def listen():
    rec = sr.Recognizer()
    rec.dynamic_energy_threshold = True
    rec.pause_threshold = 0.8
    with sr.Microphone() as src:
        rec.adjust_for_ambient_noise(src, duration=2)
        current_state["mode"] = "listening"
        log("System initialised — listening")
        while True:
            if is_speaking.is_set(): is_speaking.wait()
            try:
                audio = rec.listen(src, timeout=None, phrase_time_limit=7)
                text_queue.put(rec.recognize_google(audio).lower())
            except: pass

def process_text():
    while True:
        text = text_queue.get()
        display_text["user"] = text
        log(f"Heard: {text[:40]}")

        # ── shutdown ────────────────────────────────────────────────
        if "stop listening" in text:
            display_text["jarvis"] = "Goodbye. Shutting down."
            speak("Goodbye. Shutting down.")
            tts_queue.join(); os._exit(0)

        # ── memory clear command ─────────────────────────────────────
        if "jarvis" in text and "clear" in text and "memory" in text:
            clear_memory()
            r = "Memory cleared. I've forgotten our previous conversations."
            display_text["jarvis"] = r; speak(r); continue

        # ── how many things do you remember ─────────────────────────
        if "jarvis" in text and "remember" in text and "how" in text:
            count = len(conversation_history)
            r = f"I currently have {count} messages in memory across our conversations."
            display_text["jarvis"] = r; speak(r); continue

        # ── normal wake-word ─────────────────────────────────────────
        if "jarvis" in text:
            prompt = text.replace("jarvis", "").strip()
            if not prompt:
                r = "Yes? How can I help you?"
                display_text["jarvis"] = r; speak(r); continue

            result = handle_command(prompt)
            if result:
                display_text["jarvis"] = result; speak(result)
                log(f"CMD: {result[:40]}")
            else:
                current_state["mode"]  = "thinking"
                display_text["jarvis"] = "Thinking..."
                log("Querying AI with memory…")
                reply = ask_ai(prompt)
                display_text["jarvis"] = reply; speak(reply)
                log(f"AI reply: {reply[:40]}…")

# ══════════════════════════════════════════════
#  COLOUR PALETTE
# ══════════════════════════════════════════════
C = {
    "bg":     "#000508",
    "panel":  "#020d16",
    "border": "#0a2a3a",
    "dim":    "#0d3348",
    "mid":    "#0e6080",
    "accent": "#00c8f0",
    "bright": "#7feeff",
    "white":  "#d8f8ff",
    "green":  "#00ff88",
    "purple": "#aa55ff",
    "yellow": "#ffe066",
    "warn":   "#ff6622",
}

MODE_PALETTE = {
    "listening": dict(core="#001520", ring="#0077bb", glow="#00aaee", speed=0.30),
    "thinking":  dict(core="#0d0022", ring="#7722cc", glow="#aa44ff", speed=0.80),
    "speaking":  dict(core="#001408", ring="#00bb44", glow="#00ff77", speed=1.20),
}

# ══════════════════════════════════════════════
#  MAIN GUI
# ══════════════════════════════════════════════
class JarvisApp:
    W, H = 1100, 780

    def __init__(self, root):
        self.root = root
        root.title("J.A.R.V.I.S — MARK VII  [MEMORY ACTIVE]")
        root.configure(bg=C["bg"])
        root.geometry(f"{self.W}x{self.H}+80+40")
        root.resizable(False, False)

        self.cv = tk.Canvas(root, width=self.W, height=self.H,
                            bg=C["bg"], highlightthickness=0)
        self.cv.pack()

        self.angle     = 0.0
        self.pulse     = 0.0
        self.tick      = 0
        self.ripples   = []
        self.particles = []
        self.scan_y    = 0
        self.hex_phase = 0.0
        self._hex_pts  = self._precompute_hex_grid()

        self._draw_static_frame()
        self.animate()
        self.update_ui()

    # ── hex grid ──────────────────────────────────────────────────────
    def _precompute_hex_grid(self):
        pts, R = [], 22
        dx = R * math.sqrt(3); dy = R * 1.5
        rows = int(self.H / dy) + 2
        cols = int(self.W / dx) + 2
        for row in range(rows):
            for col in range(cols):
                cx = col * dx + (dx/2 if row%2 else 0) - 10
                cy = row * dy - 10
                hp = []
                for i in range(6):
                    a = math.pi/3*i - math.pi/6
                    hp += [cx + R*math.cos(a), cy + R*math.sin(a)]
                pts.append(hp)
        return pts

    # ── static frame ──────────────────────────────────────────────────
    def _draw_static_frame(self):
        cv = self.cv; W, H = self.W, self.H

        cv.create_rectangle(4, 4, W-4, H-4, outline=C["border"], width=1)
        cv.create_rectangle(8, 8, W-8, H-8, outline=C["dim"],    width=1)

        cv.create_rectangle(0, 0, W, 52, fill="#000d18", outline="")
        cv.create_line(0, 52, W, 52, fill=C["mid"], width=1)
        cv.create_rectangle(0, 0, 5, 52, fill=C["accent"], outline="")
        cv.create_text(30,  26, text="J.A.R.V.I.S", anchor="w",
                       font=("Courier", 18, "bold"), fill=C["accent"])
        cv.create_text(175, 22, text="MARK VII — NEURAL INTERFACE  [MEMORY ACTIVE]",
                       anchor="w", font=("Courier", 7), fill=C["mid"])
        cv.create_text(175, 34, text="STARK INDUSTRIES  ·  ADVANCED AI DIVISION",
                       anchor="w", font=("Courier", 7), fill=C["dim"])
        cv.create_text(W-20, 26, text="SYS ONLINE", anchor="e",
                       font=("Courier", 9, "bold"), fill=C["green"])

        cv.create_rectangle(0, H-40, W, H, fill="#000d18", outline="")
        cv.create_line(0, H-40, W, H-40, fill=C["mid"], width=1)
        cv.create_rectangle(0, H-40, 5, H, fill=C["accent"], outline="")
        cv.create_text(16,   H-20, text="NEURAL NET ACTIVE", anchor="w",
                       font=("Courier", 7), fill=C["mid"])
        cv.create_text(W//2, H-20,
                       text="v7.5.0  |  LLAMA-3.3-70B  |  EDGE-TTS  |  PERSISTENT MEMORY",
                       anchor="center", font=("Courier", 7), fill=C["dim"])
        cv.create_text(W-16, H-20, text="SECURE CHANNEL", anchor="e",
                       font=("Courier", 7), fill=C["mid"])

        cv.create_line(220, 52, 220, H-40, fill=C["border"], width=1)
        cv.create_line(W-220, 52, W-220, H-40, fill=C["border"], width=1)

        cv.create_rectangle(22, 62, 210, 76, fill=C["border"], outline="")
        cv.create_text(116, 69, text="◈  SYSTEM STATUS", anchor="center",
                       font=("Courier", 7, "bold"), fill=C["accent"])

        cv.create_rectangle(W-210, 62, W-22, 76, fill=C["border"], outline="")
        cv.create_text(W-116, 69, text="◈  ACTIVITY LOG", anchor="center",
                       font=("Courier", 7, "bold"), fill=C["accent"])

        cv.create_rectangle(225, H-160, W-225, H-45,
                            fill="#010c14", outline=C["border"], width=1)

    # ── dynamic frame ─────────────────────────────────────────────────
    def _draw_frame(self):
        cv = self.cv; W, H = self.W, self.H
        mode = current_state["mode"]
        pal  = MODE_PALETTE[mode]; spd = pal["speed"]

        cv.delete("dyn")

        self.hex_phase += 0.015
        for i, pts in enumerate(self._hex_pts):
            s = math.sin(self.hex_phase + i * 0.07)
            col = C["dim"] if s > 0.85 else (C["border"] if s > 0.5 else "#050f18")
            cv.create_polygon(*pts, outline=col, fill="", tags="dyn")

        self.scan_y = (self.scan_y + 3) % (H - 90)
        cv.create_line(0, 52+self.scan_y, W, 52+self.scan_y,
                       fill="#001a28", width=2, tags="dyn")

        size = 28 + math.sin(self.pulse * 0.5) * 4
        for px,py,sx,sy in [(14,58,1,1),(W-14,58,-1,1),(14,H-46,1,-1),(W-14,H-46,-1,-1)]:
            cv.create_line(px, py, px+sx*size, py,           fill=C["accent"], width=2, tags="dyn")
            cv.create_line(px, py, px,         py+sy*size,   fill=C["accent"], width=2, tags="dyn")

        self._draw_left_panel(spd)
        self._draw_right_panel()
        self._draw_orb(pal, spd)
        self._draw_mode_strip(mode)

    # ── left panel ────────────────────────────────────────────────────
    def _draw_left_panel(self, spd):
        cv = self.cv; x0, y0, lh = 22, 82, 22
        try:    cpu = psutil.cpu_percent()
        except: cpu = 0
        try:    mem = psutil.virtual_memory().percent
        except: mem = 0

        mem_msgs = len(conversation_history)

        rows = [
            ("CPU LOAD",    f"{cpu:.0f}%",    cpu/100,          C["accent"]),
            ("RAM USAGE",   f"{mem:.0f}%",    mem/100,          C["purple"]),
            ("NET LATENCY", "12ms",           0.12,             C["green"]),
            ("AI ENGINE",   "LLAMA-3.3",      1.0,              C["yellow"]),
            ("TTS ENGINE",  "EDGE-GB",        1.0,              C["accent"]),
            ("VOICE REC",   "GOOGLE STT",     1.0,              C["green"]),
            ("MEMORY",      f"{mem_msgs} msgs",
             min(mem_msgs / MAX_MEMORY, 1.0),                   C["purple"]),
        ]
        for i, (label, val, frac, col) in enumerate(rows):
            y = y0 + i*(lh+8)
            cv.create_text(x0, y, text=label, anchor="w",
                           font=("Courier", 7), fill=C["mid"], tags="dyn")
            cv.create_text(198, y, text=val, anchor="e",
                           font=("Courier", 7, "bold"), fill=col, tags="dyn")
            cv.create_rectangle(x0, y+8, 198, y+13, fill=C["border"], outline="", tags="dyn")
            bw = int(176*frac)
            if bw > 0:
                cv.create_rectangle(x0, y+8, x0+bw, y+13, fill=col, outline="", tags="dyn")

        now  = datetime.datetime.now()
        base = y0 + len(rows)*(lh+8)
        cv.create_text(116, base+16, text=now.strftime("%H:%M:%S"),
                       anchor="center", font=("Courier", 20, "bold"),
                       fill=C["accent"], tags="dyn")
        cv.create_text(116, base+40, text=now.strftime("%a  %d %b %Y"),
                       anchor="center", font=("Courier", 8),
                       fill=C["mid"], tags="dyn")

        wbase = base + 60
        for xi in range(0, 196, 4):
            wh  = abs(math.sin(self.pulse*spd*2 + xi*0.12))*12+2
            col = C["accent"] if current_state["mode"]=="speaking" else C["border"]
            cv.create_line(x0+xi, wbase-wh, x0+xi, wbase+wh,
                          fill=col, width=2, tags="dyn")

    # ── right panel ───────────────────────────────────────────────────
    def _draw_right_panel(self):
        cv = self.cv; W = self.W
        x0, y0, lh = W-198, 82, 17

        lines = activity_log if activity_log else ["  Waiting for input…"]
        for i, line in enumerate(lines[:MAX_LOG]):
            y   = y0 + i*lh
            col = C["purple"] if "Memory" in line else (
                  C["accent"] if i==0 else (C["mid"] if i%2==0 else C["dim"]))
            cv.create_text(x0, y, text=line[:28], anchor="w",
                          font=("Courier", 7), fill=col, tags="dyn")

        dot_y = y0 + MAX_LOG*lh + 12
        for di, (label, active, col_on) in enumerate([
            ("GROQ API",   True,  C["green"]),
            ("EDGE TTS",   True,  C["green"]),
            ("GOOGLE STT", True,  C["green"]),
            ("MEMORY DB",  True,  C["purple"]),
        ]):
            dy    = dot_y + di*18
            color = col_on if active else C["warn"]
            cv.create_oval(x0, dy-4, x0+8, dy+4, fill=color, outline="", tags="dyn")
            cv.create_text(x0+14, dy, text=label, anchor="w",
                          font=("Courier", 7), fill=C["mid"], tags="dyn")
            cv.create_text(W-24, dy, text="ONLINE", anchor="e",
                          font=("Courier", 7, "bold"), fill=color, tags="dyn")

    # ── orb ───────────────────────────────────────────────────────────
    def _draw_orb(self, pal, spd):
        cv = self.cv; W, H = self.W, self.H
        mode = current_state["mode"]
        cx = W//2; cy = 52 + (H - 52 - 40 - 160)//2 + 10
        glow, ring, core, pi = pal["glow"], pal["ring"], pal["core"], self.pulse

        for i in range(10, 0, -1):
            r = 155 + i*14 + math.sin(pi*spd*1.3+i)*7
            cv.create_oval(cx-r,cy-r,cx+r,cy+r, outline=glow, width=1, tags="dyn")

        hr = 148 + math.sin(pi*spd)*6
        for i in range(6):
            a  = math.radians(self.angle*0.4 + i*60)
            a2 = math.radians(self.angle*0.4 + (i+1)*60)
            cv.create_line(cx+hr*math.cos(a), cy+hr*math.sin(a),
                          cx+hr*math.cos(a2), cy+hr*math.sin(a2),
                          fill=ring, width=2, tags="dyn")

        for i in range(4):
            as_ = self.angle*(1+i*0.3)*(-1 if i%2 else 1)
            ae  = 55 + math.sin(pi+i*1.1)*28
            r   = 160 + i*22
            cv.create_arc(cx-r,cy-r,cx+r,cy+r,
                         start=as_,extent=ae,style=tk.ARC,
                         outline=ring,width=2+(i==0),tags="dyn")
            cv.create_arc(cx-r+8,cy-r+8,cx+r-8,cy+r-8,
                         start=-as_+45,extent=ae*0.6,style=tk.ARC,
                         outline=glow,width=1,tags="dyn")

        pr = 108 + math.sin(pi*spd)*8
        cv.create_oval(cx-pr,cy-pr,cx+pr,cy+pr,
                      fill=core,outline=ring,width=3,tags="dyn")

        for frac in [0.72, 0.50, 0.30]:
            ir = pr*frac + math.sin(pi*spd*1.5)*3
            cv.create_oval(cx-ir,cy-ir,cx+ir,cy+ir,
                          fill="",outline=glow,width=1,tags="dyn")

        cl = 60
        cv.create_line(cx-pr-cl,cy,cx-pr+10,cy,fill=ring,width=1,dash=(4,4),tags="dyn")
        cv.create_line(cx+pr-10,cy,cx+pr+cl,cy,fill=ring,width=1,dash=(4,4),tags="dyn")
        cv.create_line(cx,cy-pr-cl,cx,cy-pr+10,fill=ring,width=1,dash=(4,4),tags="dyn")
        cv.create_line(cx,cy+pr-10,cx,cy+pr+cl,fill=ring,width=1,dash=(4,4),tags="dyn")

        for i in range(36):
            a  = math.radians(i*10 + self.angle*0.2)
            r1 = pr+18; r2 = r1+(6 if i%3==0 else 3)
            cv.create_line(cx+r1*math.cos(a),cy+r1*math.sin(a),
                          cx+r2*math.cos(a),cy+r2*math.sin(a),
                          fill=ring if i%3==0 else C["border"],
                          width=1,tags="dyn")

        if mode == "speaking":
            for i in range(-9,10):
                h = abs(math.sin(pi*4+i*0.55))*50+10
                x = cx+i*14
                cv.create_line(x,cy+pr+20-h,x,cy+pr+20+h,
                              fill=glow,width=3,tags="dyn")

        for rp in self.ripples[:]:
            r = rp[0]
            if r>250: self.ripples.remove(rp)
            else:
                cv.create_oval(cx-r,cy-r,cx+r,cy+r,outline=glow,width=1,tags="dyn")
                rp[0]+=2.8
        if mode=="speaking" and len(self.ripples)<6:
            if random.random()<0.10: self.ripples.append([pr+5,1.0])

        for p in self.particles[:]:
            p[1]-=p[3]; p[0]+=math.sin(p[4])*0.6; p[4]+=0.04; p[2]-=0.015
            if p[2]<=0: self.particles.remove(p); continue
            s=p[2]*3
            cv.create_oval(p[0]-s,p[1]-s,p[0]+s,p[1]+s,
                          fill=glow,outline="",tags="dyn")
        while len(self.particles)<14:
            a=random.uniform(0,2*math.pi); d=random.uniform(pr*0.7,pr)
            self.particles.append([cx+math.cos(a)*d,cy+math.sin(a)*d,
                                   random.uniform(0.4,0.9),
                                   random.uniform(0.3,1.0),
                                   random.uniform(0,6.28)])

        for r,col in [(16,core),(10,ring),(5,glow),(2,C["white"])]:
            cv.create_oval(cx-r,cy-r,cx+r,cy+r,fill=col,outline="",tags="dyn")

    # ── mode strip ────────────────────────────────────────────────────
    def _draw_mode_strip(self, mode):
        cv=self.cv; W,H=self.W,self.H; tx=W//2
        base=H-165
        pal=MODE_PALETTE[mode]; col=pal["glow"]
        labels={"listening":"◉  AWAITING INPUT",
                "thinking": "◈  NEURAL PROCESSING",
                "speaking": "◈  AUDIO OUTPUT"}
        if mode=="thinking": col = col if self.tick%6<5 else C["dim"]
        cv.create_text(tx,base,text=labels[mode],anchor="center",
                      font=("Courier",10,"bold"),fill=col,tags="dyn")
        cv.create_line(W//2-160,base,W//2-90,base,fill=col,width=1,tags="dyn")
        cv.create_line(W//2+90, base,W//2+160,base,fill=col,width=1,tags="dyn")

    # ── animate ───────────────────────────────────────────────────────
    def animate(self):
        self.angle+=1.1; self.pulse+=0.065; self.tick+=1
        self._draw_frame()
        self.root.after(28, self.animate)

    # ── ui text ───────────────────────────────────────────────────────
    def update_ui(self):
        cv=self.cv; W,H=self.W,self.H
        cv.delete("ui_text")
        u=display_text.get("user",""); j=display_text.get("jarvis","")
        tx=W//2; box_y=H-100

        if u:
            cv.create_text(tx,box_y-40,
                          text=f"YOU  ›  {u.upper()[:90]}",
                          anchor="center",font=("Courier",9),
                          fill=C["mid"],width=W-470,tags="ui_text")
        if j:
            cv.create_text(tx,box_y,
                          text=f"JARVIS  ›  {j[:200]}",
                          anchor="center",font=("Courier",11,"bold"),
                          fill=C["white"],width=W-470,tags="ui_text")

        # memory counter badge (bottom-right of text box)
        mem_count = len(conversation_history)
        cv.create_text(W-230, H-50,
                       text=f"MEM: {mem_count}/{MAX_MEMORY}",
                       anchor="e", font=("Courier", 7, "bold"),
                       fill=C["purple"], tags="ui_text")

        self.root.after(180, self.update_ui)

# ──────────────────────────────────────────────
#  BOOT
# ──────────────────────────────────────────────
threading.Thread(target=listen,       daemon=True).start()
threading.Thread(target=process_text, daemon=True).start()

root = tk.Tk()
tk.font = tk_font
app  = JarvisApp(root)
root.mainloop()