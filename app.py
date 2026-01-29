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

# Dashboard auth
DASH_USER = os.environ.get("DASH_USER", "admin")
DASH_PASS = os.environ.get("DASH_PASS", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")

# Session expiry minutes (displayed + enforced by server cookie lifetime)
SESSION_TIMEOUT_MIN = int(os.environ.get("SESSION_TIMEOUT_MIN", "30"))

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

LOG = deque(maxlen=300)
LOCK = threading.Lock()

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = SECRET_KEY or "dev-only-please-set-SECRET_KEY"

# Security cookie flags (good for public HTTPS behind Traefik)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,   # requires HTTPS (Traefik)
)

# Flask cookie lifetime (enforces expiration on browser side; also used in UI countdown)
app.permanent_session_lifetime = SESSION_TIMEOUT_MIN * 60  # seconds

THEME_JS = r"""
<script>
(function(){
  // Theme modes:
  // - "system": follow OS preference and auto-sync on changes
  // - "light"/"dark": manual override
  const KEY = "ct_theme_mode"; // "system" | "light" | "dark"
  const root = document.documentElement;
  const mq = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)");

  function apply(mode){
    if(mode === "light"){
      root.classList.add("light");
    } else if(mode === "dark"){
      root.classList.remove("light");
    } else {
      // system
      const prefersLight = mq && mq.matches;
      if(prefersLight) root.classList.add("light");
      else root.classList.remove("light");
    }
  }

  // Load saved mode, default to system
  let mode = localStorage.getItem(KEY) || "system";
  apply(mode);

  // Auto sync if system mode
  if(mq && mq.addEventListener){
    mq.addEventListener("change", () => {
      const m = localStorage.getItem(KEY) || "system";
      if(m === "system") apply("system");
    });
  } else if(mq && mq.addListener){
    mq.addListener(() => {
      const m = localStorage.getItem(KEY) || "system";
      if(m === "system") apply("system");
    });
  }

  // Hook up toggle if present
  const btn = document.getElementById("themeBtn");
  const label = document.getElementById("themeLabel");
  const hint = document.getElementById("themeHint");

  function syncUI(){
    const m = localStorage.getItem(KEY) || "system";
    if(!label) return;
    label.textContent =
      m === "system" ? "System" :
      m === "light" ? "Light" : "Dark";
    if(hint){
      hint.textContent =
        m === "system"
          ? "Auto (follows device)"
          : "Manual override";
    }
  }

  // Cycle: system -> dark -> light -> system ...
  function nextMode(current){
    if(current === "system") return "dark";
    if(current === "dark") return "light";
    return "system";
  }

  if(btn){
    btn.addEventListener("click", () => {
      const current = localStorage.getItem(KEY) || "system";
      const n = nextMode(current);
      localStorage.setItem(KEY, n);
      apply(n);
      syncUI();
    });
    syncUI();
  }
})();
</script>
"""

PWA_HEAD = r"""
<link rel="manifest" href="/static/manifest.webmanifest">
<meta name="theme-color" content="#0f172a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="icon" href="/static/icons/icon.svg">
<link rel="apple-touch-icon" href="/static/icons/icon.svg">
<script>
  // Register SW for PWA install/offline shell
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/static/sw.js").catch(()=>{});
    });
  }
</script>
"""

LANDING_TEMPLATE = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>CareTrust Watch</title>
  {PWA_HEAD}
  <style>
    :root{{
      --bg: #0f172a;
      --panel: #020617;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --muted2:#64748b;
      --border:#1f2937;
      --btn:#111827;
      --btnText:#e5e7eb;
      --ring: rgba(148,163,184,.25);
    }}
    .light{{
      --bg:#f6f7f9;
      --panel:#ffffff;
      --text:#0f172a;
      --muted:#475569;
      --muted2:#64748b;
      --border:#e5e7eb;
      --btn:#0f172a;
      --btnText:#ffffff;
      --ring: rgba(15,23,42,.15);
    }}
    body{{
      font-family:system-ui,Segoe UI,Arial;
      margin:0;
      min-height:100vh;
      display:grid;
      place-items:center;
      background:var(--bg);
      color:var(--text);
    }}
    .box{{
      position:relative;
      text-align:center;
      padding:28px 28px 22px;
      border-radius:18px;
      background:var(--panel);
      border:1px solid var(--border);
      box-shadow:0 20px 60px rgba(0,0,0,.22);
      max-width:440px;
      width:min(440px, calc(100vw - 36px));
    }}
    h1{{margin:0 0 8px 0;font-size:26px;letter-spacing:.2px}}
    p{{margin:6px 0;color:var(--muted)}}
    .small{{font-size:13px;color:var(--muted2);margin-top:10px}}

    .spinnerWrap{{display:flex;justify-content:center;margin:16px 0 8px}}
    .spinner{{
      width:38px;height:38px;
      border-radius:999px;
      border:3px solid var(--ring);
      border-top-color: var(--text);
      animation: spin 0.85s linear infinite;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

    .toggle{{
      position:absolute;top:12px;right:12px;
      display:inline-flex;align-items:center;gap:8px;
      padding:8px 10px;border-radius:12px;
      border:1px solid var(--border);
      background:transparent;color:var(--text);
      cursor:pointer;user-select:none;
    }}
    .dot{{width:10px;height:10px;border-radius:999px;background:var(--text);opacity:.8}}
  </style>
</head>
<body>
  <div class="box">
    <button class="toggle" id="themeBtn" type="button" aria-label="Toggle theme">
      <span class="dot"></span>
      <span id="themeLabel" style="font-size:13px;">System</span>
    </button>

    <h1>CareTrust Token Monitor</h1>
    <p>Telegram alerts for room status changes</p>

    <div class="spinnerWrap" aria-hidden="true"><div class="spinner"></div></div>
    <div class="small" id="themeHint">Auto (follows device)</div>
    <div class="small">Redirecting to login…</div>
  </div>

  {THEME_JS}

  <script>
    // Direct redirect without delay:
    // Let the browser paint once so spinner shows, then redirect immediately.
    requestAnimationFrame(() => window.location.replace("/login"));
  </script>
</body>
</html>
"""

LOGIN_TEMPLATE = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Login - CareTrust Watch</title>
  {PWA_HEAD}
  <style>
    :root{{
      --bg: #0f172a;
      --panel: #020617;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --border:#1f2937;
      --field:#0b1220;
    }}
    .light{{
      --bg:#f6f7f9;
      --panel:#ffffff;
      --text:#0f172a;
      --muted:#475569;
      --border:#e5e7eb;
      --field:#ffffff;
    }}
    body{{
      font-family:system-ui,Segoe UI,Arial;
      margin:0;min-height:100vh;display:grid;place-items:center;
      background:var(--bg);color:var(--text);
    }}
    .card{{
      background:var(--panel);
      border:1px solid var(--border);
      border-radius:14px;padding:22px;
      min-width:320px;max-width:420px;
      box-shadow:0 10px 30px rgba(0,0,0,.18);
      width:min(420px, calc(100vw - 36px));
    }}
    h2{{margin:0 0 12px 0}}
    label{{display:block;font-size:13px;color:var(--muted);margin:10px 0 6px}}
    input{{
      width:100%;padding:10px 12px;
      border:1px solid var(--border);
      border-radius:10px;font-size:16px;
      background:var(--field);color:var(--text);
    }}
    button{{
      margin-top:14px;width:100%;
      padding:10px 12px;border-radius:10px;
      border:1px solid var(--border);
      background:#111;color:#fff;font-size:16px;cursor:pointer;
    }}
    .err{{margin-top:10px;color:#b91c1c;font-size:14px}}
    .hint{{margin-top:10px;color:var(--muted);font-size:12px;line-height:1.35}}

    .toggle{{
      position:fixed;top:14px;right:14px;
      display:inline-flex;align-items:center;gap:8px;
      padding:8px 10px;border-radius:12px;
      border:1px solid var(--border);
      background:var(--panel);color:var(--text);
      cursor:pointer;z-index:1000;
    }}
    .dot{{width:10px;height:10px;border-radius:999px;background:var(--text);opacity:.8}}
  </style>
</head>
<body>
  <button class="toggle" id="themeBtn" type="button" aria-label="Toggle theme">
    <span class="dot"></span>
    <span id="themeLabel" style="font-size:13px;">System</span>
  </button>

  <div class="card">
    <h2>CareTrust Watch</h2>
    <form method="post">
      <label>Username</label>
      <input name="username" autocomplete="username" required />
      <label>Password</label>
      <input name="password" type="password" autocomplete="current-password" required />
      <button type="submit">Sign in</button>
      {% if error %}<div class="err">{{ error }}</div>{% endif %}
      <div class="hint">
        Tip: Click theme button to cycle <b>System → Dark → Light</b>.
      </div>
      <div class="hint" id="themeHint">Auto (follows device)</div>
    </form>
  </div>

  {THEME_JS}
</body>
</html>
"""

DASH_TEMPLATE = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>CareTrust Watch Dashboard</title>
  {PWA_HEAD}
  <style>
    :root{{
      --bg: #0f172a;
      --panel: #020617;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --border:#1f2937;
      --chip:#0b1220;
    }}
    .light{{
      --bg:#f6f7f9;
      --panel:#ffffff;
      --text:#0f172a;
      --muted:#475569;
      --border:#e5e7eb;
      --chip:#f1f5f9;
    }}
    body{{
      font-family:system-ui,Segoe UI,Arial;
      margin:24px;max-width:980px;
      background:var(--bg);color:var(--text);
    }}
    a{{color:var(--text)}}
    .top{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
    .row{{display:flex;gap:16px;flex-wrap:wrap}}
    .card{{border:1px solid var(--border);border-radius:12px;padding:16px;flex:1;min-width:280px;background:var(--panel)}}
    .k{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
    .v{{font-size:20px;margin-top:4px}}
    input,button{{font-size:16px;padding:10px 12px;border-radius:10px;border:1px solid var(--border);background:var(--chip);color:var(--text)}}
    button{{cursor:pointer}}
    .btn{{border:1px solid var(--border);background:#111;color:#fff}}
    .btn2{{border:1px solid var(--border);background:transparent;color:var(--text)}}
    pre{{white-space:pre-wrap;background:var(--chip);border:1px solid var(--border);padding:12px;border-radius:12px;max-height:360px;overflow:auto;color:var(--text)}}
    .hint{{color:var(--muted);font-size:13px}}
    .pill{{display:inline-flex;gap:8px;align-items:center;padding:8px 10px;border:1px solid var(--border);border-radius:999px;background:var(--chip);font-size:13px;color:var(--text)}}

    .toggle{{
      position:fixed;top:14px;right:14px;
      display:inline-flex;align-items:center;gap:8px;
      padding:8px 10px;border-radius:12px;
      border:1px solid var(--border);
      background:var(--panel);color:var(--text);
      cursor:pointer;z-index:1000;
    }}
    .dot{{width:10px;height:10px;border-radius:999px;background:var(--text);opacity:.8}}
    .countdown{{font-variant-numeric: tabular-nums;}}
  </style>
</head>
<body>
  <button class="toggle" id="themeBtn" type="button" aria-label="Toggle theme">
    <span class="dot"></span>
    <span id="themeLabel" style="font-size:13px;">System</span>
  </button>

  <div class="top">
    <div>
      <h2 style="margin:0">CareTrust Watch Dashboard</h2>
      <div class="hint">Telegram: <code>/startwatch Room 09</code>, <code>/stopwatch</code>, <code>/status</code></div>
      <div class="hint" id="themeHint">Auto (follows device)</div>
    </div>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <span class="pill">Session expires in: <span class="countdown" id="sessionCountdown">—</span></span>
      <a class="pill" href="/logout">Logout</a>
    </div>
  </div>

  <div class="row" style="margin-top:16px">
    <div class="card">
      <div class="k">Status</div>
      <div class="v">{{ "ON ✅" if enabled else "OFF 🛑" }}</div>
      <div class="k" style="margin-top:12px">Room</div>
      <div class="v">{{ room or "—" }}</div>
    </div>

    <div class="card">
      <div class="k">Current value</div>
      <div class="v">{{ current_value or "—" }}</div>
      <div class="k" style="margin-top:12px">Last alerted value</div>
      <div class="v">{{ last_value or "—" }}</div>
    </div>
  </div>

  <div class="card" style="margin-top:16px">
    <form method="post" action="/action">
      <div class="k">Controls</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;align-items:center">
        <input name="room" placeholder="Room 09" value="{{ room or '' }}"/>
        <button class="btn" name="do" value="start">Start</button>
        <button class="btn2" name="do" value="stop">Stop</button>
        <button class="btn2" name="do" value="setroom">Set room only</button>
      </div>
      <p class="hint">Room label must match the page (e.g. <code>Room 09</code>).</p>
    </form>
  </div>

  <div class="card" style="margin-top:16px">
    <div class="k">Event log (latest first)</div>
    <pre>{{ log_text }}</pre>
  </div>

  {THEME_JS}

  <script>
  (function(){
    // Session countdown: uses server-provided login timestamp and configured timeout
    const loginAt = Number({{ login_at_ms }}) || 0;
    const timeoutMs = Number({{ session_timeout_ms }}) || 0;
    const el = document.getElementById("sessionCountdown");

    function fmt(ms){
      if(ms <= 0) return "0:00";
      const s = Math.floor(ms/1000);
      const m = Math.floor(s/60);
      const r = s % 60;
      return m + ":" + String(r).padStart(2,"0");
    }

    function tick(){
      const now = Date.now();
      const left = (loginAt + timeoutMs) - now;
      if(el) el.textContent = fmt(left);

      if(left <= 0){
        // Session likely expired -> go to login
        window.location.replace("/login");
      }
    }

    tick();
    setInterval(tick, 1000);
  })();
  </script>
</body>
</html>
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_event(msg: str):
    with LOCK:
        LOG.appendleft(f"[{now_str()}] {msg}")


def send_telegram(text: str):
    try:
        requests.post(
            f"{API_BASE}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=TIMEOUT,
        )
    except Exception as e:
        log_event(f"Telegram send error: {e}")


def get_updates(offset=None):
    params = {"timeout": 10}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(f"{API_BASE}/getUpdates", params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("result", [])


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
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
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_page_text():
    r = requests.get(URL, timeout=TIMEOUT, headers={"Cache-Control": "no-cache"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return soup.get_text("\n")


def extract_room_value(page_text: str, room_label: str):
    lines = [ln.strip() for ln in page_text.replace("\r", "").split("\n") if ln.strip()]
    room_pattern = re.compile(rf"\b{re.escape(room_label)}\b", re.IGNORECASE)
    for i, line in enumerate(lines):
        if room_pattern.search(line):
            same_line = re.sub(room_pattern.pattern, "", line, flags=re.IGNORECASE).strip(" :-–")
            if same_line:
                return same_line[:80]
            if i + 1 < len(lines):
                return lines[i + 1][:80]
    return None


def set_watch(state, enabled: bool, room: str | None = None):
    state["enabled"] = enabled
    if room is not None:
        state["room"] = room
        state["last_value"] = None
        state["current_value"] = None

    if enabled and state.get("room"):
        log_event(f"Monitoring STARTED for {state['room']}")
        send_telegram(f"✅ Monitoring STARTED for {state['room']}")
    elif not enabled:
        log_event("Monitoring STOPPED")
        send_telegram("🛑 Monitoring STOPPED")


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def handle_commands(state):
    updates = get_updates(state.get("update_offset"))
    for u in updates:
        state["update_offset"] = u["update_id"] + 1

        msg = u.get("message", {})
        text = msg.get("text", "")
        chat_id = str(msg.get("chat", {}).get("id"))

        if chat_id != CHAT_ID:
            continue

        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""

        if cmd == "/startwatch":
            if len(parts) < 2:
                send_telegram("❌ Usage: /startwatch Room 09")
            else:
                set_watch(state, True, parts[1].strip())

        elif cmd == "/stopwatch":
            set_watch(state, False)

        elif cmd == "/status":
            status = "ON ✅" if state.get("enabled") else "OFF 🛑"
            send_telegram(
                f"📊 Status: {status}\n"
                f"Room: {state.get('room')}\n"
                f"Current: {state.get('current_value')}\n"
                f"Last alerted: {state.get('last_value')}"
            )


def watcher_loop():
    state = load_state()
    log_event("Watcher started")
    send_telegram("🤖 CareTrust watcher online.\nUse /startwatch Room 09")

    while True:
        try:
            handle_commands(state)

            if state.get("enabled") and state.get("room"):
                page_text = fetch_page_text()
                current_value = extract_room_value(page_text, state["room"])
                state["current_value"] = current_value

                if current_value:
                    last_value = state.get("last_value")
                    if last_value is None:
                        state["last_value"] = current_value
                        log_event(f"Initial value for {state['room']}: {current_value}")
                    elif current_value != last_value:
                        send_telegram(
                            f"🔔 CareTrust update\n{state['room']} changed\n"
                            f"From: {last_value}\nTo:   {current_value}"
                        )
                        log_event(f"{state['room']} changed: {last_value} -> {current_value}")
                        state["last_value"] = current_value

            save_state(state)
        except Exception as e:
            log_event(f"Watcher error: {e}")

        time.sleep(POLL_SECONDS)


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/")
def root():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    return render_template_string(LANDING_TEMPLATE)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not DASH_PASS:
        return "Set DASH_PASS env var in Coolify.", 500

    error = None
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == DASH_USER and p == DASH_PASS:
            session.permanent = True
            session["logged_in"] = True
            session["login_at"] = int(time.time())  # seconds
            nxt = request.args.get("next") or url_for("dashboard")
            return redirect(nxt)
        error = "Invalid username or password"

    return render_template_string(LOGIN_TEMPLATE, error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/dashboard")
@login_required
def dashboard():
    state = load_state()
    with LOCK:
        log_text = "\n".join(list(LOG))

    login_at = session.get("login_at") or int(time.time())
    login_at_ms = int(login_at) * 1000
    session_timeout_ms = int(SESSION_TIMEOUT_MIN) * 60 * 1000

    return render_template_string(
        DASH_TEMPLATE,
        enabled=state.get("enabled"),
        room=state.get("room"),
        current_value=state.get("current_value"),
        last_value=state.get("last_value"),
        log_text=log_text,
        login_at_ms=login_at_ms,
        session_timeout_ms=session_timeout_ms,
    )


@app.post("/action")
@login_required
def action():
    state = load_state()
    do = request.form.get("do")
    room = (request.form.get("room") or "").strip()

    if do == "start" and room:
        set_watch(state, True, room)
    elif do == "stop":
        set_watch(state, False)
    elif do == "setroom" and room:
        state["room"] = room
        state["last_value"] = None
        state["current_value"] = None
        send_telegram(f"ℹ️ Room set to {room} (monitoring {'ON' if state.get('enabled') else 'OFF'})")
        log_event(f"Room set to {room} (monitoring unchanged)")

    save_state(state)
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    t = threading.Thread(target=watcher_loop, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
