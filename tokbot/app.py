import os
import time
import json
import re
import threading
from collections import deque
from datetime import datetime
from functools import wraps

import requests
from bs4 import BeautifulSoup
from flask import (
    Flask, request, redirect, url_for, render_template_string,
    session, jsonify
)

URL = "https://www.caretrust.mv/Home/TokenStatus"

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = str(os.environ["CHAT_ID"])

POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "15"))
STATE_PATH = os.environ.get("STATE_PATH", "/app/data/caretrust_state.json")
TIMEOUT = int(os.environ.get("TIMEOUT", "20"))

DASH_USER = os.environ.get("DASH_USER", "admin")
DASH_PASS = os.environ.get("DASH_PASS", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")

SESSION_TIMEOUT_MIN = int(os.environ.get("SESSION_TIMEOUT_MIN", "30"))

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

LOG = deque(maxlen=300)
LOCK = threading.Lock()

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = SECRET_KEY or "dev-only-change-me"

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
)

app.permanent_session_lifetime = SESSION_TIMEOUT_MIN * 60


THEME_JS = """
<script>
(function(){
  const KEY = "ct_theme_mode";
  const root = document.documentElement;
  const mq = window.matchMedia("(prefers-color-scheme: light)");

  function apply(mode){
    if(mode === "light") root.classList.add("light");
    else if(mode === "dark") root.classList.remove("light");
    else {
      if(mq.matches) root.classList.add("light");
      else root.classList.remove("light");
    }
  }

  function nextMode(m){
    if(m==="system") return "dark";
    if(m==="dark") return "light";
    return "system";
  }

  let mode = localStorage.getItem(KEY) || "system";
  apply(mode);

  const btn = document.getElementById("themeBtn");
  const label = document.getElementById("themeLabel");
  const hint = document.getElementById("themeHint");

  function sync(){
    const m = localStorage.getItem(KEY) || "system";
    if(label) label.textContent = m.charAt(0).toUpperCase()+m.slice(1);
    if(hint) hint.textContent = m==="system" ? "Auto (follows device)" : "Manual override";
  }

  if(btn){
    btn.onclick = ()=>{
      const m = localStorage.getItem(KEY) || "system";
      const n = nextMode(m);
      localStorage.setItem(KEY,n);
      apply(n);
      sync();
    };
    sync();
  }

  mq.addEventListener("change", ()=> {
    if((localStorage.getItem(KEY)||"system")==="system") apply("system");
  });
})();
</script>
"""

PWA_HEAD = """
<link rel="manifest" href="/static/manifest.webmanifest">
<meta name="theme-color" content="#0f172a">
<link rel="icon" href="/static/icons/icon.svg">
<script>
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/sw.js").catch(()=>{});
}
</script>
"""

LANDING_TEMPLATE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CareTrust Watch</title>
""" + PWA_HEAD + """
<style>
body{font-family:system-ui;background:#0f172a;color:#fff;display:grid;place-items:center;height:100vh;margin:0}
.box{background:#020617;border:1px solid #1f2937;padding:28px;border-radius:18px;text-align:center}
.spinner{width:36px;height:36px;border-radius:50%;border:3px solid #475569;border-top-color:#fff;animation:spin .8s linear infinite;margin:12px auto}
@keyframes spin{to{transform:rotate(360deg)}}
.toggle{position:absolute;top:12px;right:12px}
</style>
</head>
<body>
<div class="box">
<button id="themeBtn">Theme</button>
<h2>CareTrust Token Monitor</h2>
<div class="spinner"></div>
<div id="themeHint">Auto (follows device)</div>
<div>Redirecting to login…</div>
</div>
""" + THEME_JS + """
<script>
requestAnimationFrame(()=>location.replace("/login"));
</script>
</body>
</html>
"""

LOGIN_TEMPLATE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login</title>
""" + PWA_HEAD + """
<style>
body{font-family:system-ui;background:#0f172a;color:#fff;display:grid;place-items:center;height:100vh;margin:0}
.card{background:#020617;padding:22px;border-radius:14px;border:1px solid #1f2937;width:320px}
input,button{width:100%;padding:10px;margin-top:8px}
.err{color:#f87171;margin-top:10px}
</style>
</head>
<body>
<button id="themeBtn">Theme</button>
<div class="card">
<h3>Login</h3>
<form method="post">
<input name="username" placeholder="Username" required>
<input name="password" type="password" placeholder="Password" required>
<button>Sign in</button>
{% if error %}
<div class="err">{{ error }}</div>
{% endif %}
<div id="themeHint">Auto (follows device)</div>
</form>
</div>
""" + THEME_JS + """
</body>
</html>
"""

DASH_TEMPLATE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard</title>
""" + PWA_HEAD + """
<style>
body{font-family:system-ui;background:#0f172a;color:#fff;margin:20px}
.card{background:#020617;border:1px solid #1f2937;padding:16px;border-radius:12px;margin-bottom:12px}
pre{background:#000;padding:10px}
</style>
</head>
<body>
<button id="themeBtn">Theme</button>
<h2>Dashboard</h2>
<div>Session expires in: <span id="sessionCountdown"></span></div>

<div class="card">Status: {{ "ON" if enabled else "OFF" }}<br>Room: {{ room }}</div>
<div class="card">Current: {{ current_value }}<br>Last: {{ last_value }}</div>

<form method="post" action="/action">
<input name="room" value="{{ room or '' }}">
<button name="do" value="start">Start</button>
<button name="do" value="stop">Stop</button>
<button name="do" value="setroom">Set room</button>
</form>

<div class="card"><pre>{{ log_text }}</pre></div>

<a href="/logout">Logout</a>

""" + THEME_JS + """

<script>
const loginAt={{ login_at_ms }};
const timeout={{ session_timeout_ms }};
function tick(){
  const left=(loginAt+timeout)-Date.now();
  document.getElementById("sessionCountdown").innerText =
    Math.max(0,Math.floor(left/60000))+":"+String(Math.floor(left/1000)%60).padStart(2,"0");
  if(left<=0) location.replace("/login");
}
setInterval(tick,1000);tick();
</script>

</body>
</html>
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_event(msg):
    with LOCK:
        LOG.appendleft(f"[{now_str()}] {msg}")


def send_telegram(text):
    try:
        requests.post(f"{API_BASE}/sendMessage", data={"chat_id": CHAT_ID, "text": text}, timeout=TIMEOUT)
    except Exception as e:
        log_event(f"Telegram error: {e}")


def load_state():
    try:
        with open(STATE_PATH) as f:
            s = json.load(f)
    except Exception:
        s = {}
    s.setdefault("enabled", False)
    s.setdefault("room", None)
    s.setdefault("last_value", None)
    s.setdefault("current_value", None)
    s.setdefault("update_offset", None)
    return s


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def fetch_page_text():
    r = requests.get(URL, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser").get_text("\n")


def extract_room_value(text, room):
    lines=[l.strip() for l in text.splitlines() if l.strip()]
    for i,l in enumerate(lines):
        if room.lower() in l.lower():
            if i+1<len(lines): return lines[i+1]
    return None


def login_required(fn):
    @wraps(fn)
    def wrap(*a,**k):
        if not session.get("logged_in"):
            return redirect("/login")
        return fn(*a,**k)
    return wrap


def watcher_loop():
    state = load_state()
    send_telegram("🤖 CareTrust watcher online.")
    while True:
        try:
            if state["enabled"] and state["room"]:
                val = extract_room_value(fetch_page_text(), state["room"])
                state["current_value"] = val
                if val and val != state.get("last_value"):
                    send_telegram(f"{state['room']} changed: {state.get('last_value')} → {val}")
                    log_event(f"{state['room']} changed {state.get('last_value')} -> {val}")
                    state["last_value"] = val
            save_state(state)
        except Exception as e:
            log_event(str(e))
        time.sleep(POLL_SECONDS)


@app.get("/")
def root():
    if session.get("logged_in"):
        return redirect("/dashboard")
    return render_template_string(LANDING_TEMPLATE)


@app.route("/login", methods=["GET","POST"])
def login():
    error=None
    if request.method=="POST":
        if request.form["username"]==DASH_USER and request.form["password"]==DASH_PASS:
            session.permanent=True
            session["logged_in"]=True
            session["login_at"]=int(time.time())
            return redirect("/dashboard")
        error="Invalid username or password"
    return render_template_string(LOGIN_TEMPLATE,error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/dashboard")
@login_required
def dashboard():
    state=load_state()
    with LOCK:
        log_text="\n".join(LOG)
    login_at=session.get("login_at",int(time.time()))
    return render_template_string(
        DASH_TEMPLATE,
        enabled=state["enabled"],
        room=state["room"],
        current_value=state["current_value"],
        last_value=state["last_value"],
        log_text=log_text,
        login_at_ms=login_at*1000,
        session_timeout_ms=SESSION_TIMEOUT_MIN*60*1000
    )


@app.post("/action")
@login_required
def action():
    state=load_state()
    room=request.form.get("room","").strip()
    do=request.form.get("do")
    if do=="start" and room:
        state["enabled"]=True
        state["room"]=room
        state["last_value"]=None
        send_telegram(f"Monitoring STARTED for {room}")
    elif do=="stop":
        state["enabled"]=False
        send_telegram("Monitoring STOPPED")
    elif do=="setroom" and room:
        state["room"]=room
    save_state(state)
    return redirect("/dashboard")


if __name__=="__main__":
    threading.Thread(target=watcher_loop,daemon=True).start()
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)))
