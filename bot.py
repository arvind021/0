"""
╔══════════════════════════════════════════════════════════════╗
║     BOT HOSTING PLATFORM — Multi-User + Admin Edition       ║
║   Login/Register + SQLite + Admin Dashboard + CPU/RAM       ║
╚══════════════════════════════════════════════════════════════╝

Requirements:
    pip install flask flask-socketio psutil

Run:
    python bot_hosting_platform.py

Open: http://localhost:5000

Admin login:  username = admin   password = admin123
(Change ADMIN_USERNAME and ADMIN_PASSWORD below!)
"""

import os, sys, signal, hashlib, secrets, sqlite3, threading, subprocess, time
from datetime import datetime
from functools import wraps
from flask import Flask, render_template_string, request, jsonify, session
from flask_socketio import SocketIO, emit, join_room
import psutil

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
socketio = SocketIO(app, cors_allowed_origins="*")

DB_PATH    = "bothost.db"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"   # ← ZAROOR BADLO!

# ─── Database ────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            username  TEXT UNIQUE NOT NULL,
            password  TEXT NOT NULL,
            is_banned INTEGER DEFAULT 0,
            created   TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS bots (
            id        TEXT PRIMARY KEY,
            user_id   INTEGER NOT NULL,
            name      TEXT NOT NULL,
            type      TEXT NOT NULL,
            token     TEXT DEFAULT '',
            script    TEXT DEFAULT '',
            status    TEXT DEFAULT 'stopped',
            created   TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS bot_logs (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id  TEXT NOT NULL,
            line    TEXT NOT NULL,
            ts      TEXT DEFAULT (datetime('now'))
        );
        """)

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ─── Process tracker ─────────────────────────────────────────
processes = {}   # bot_id -> Popen

def require_login(f):
    @wraps(f)
    def d(*a, **kw):
        if "user_id" not in session:
            return jsonify({"error": "Not logged in"}), 401
        return f(*a, **kw)
    return d

def require_admin(f):
    @wraps(f)
    def d(*a, **kw):
        if not session.get("is_admin"):
            return jsonify({"error": "Admin only"}), 403
        return f(*a, **kw)
    return d

# ─── Bot runner ───────────────────────────────────────────────

def add_log(bot_id, line):
    ts  = datetime.now().strftime("%H:%M:%S")
    msg = f"[{ts}] {line}"
    with get_db() as db:
        db.execute("INSERT INTO bot_logs(bot_id,line) VALUES(?,?)", (bot_id, msg))
        db.execute("""DELETE FROM bot_logs WHERE bot_id=? AND id NOT IN
                      (SELECT id FROM bot_logs WHERE bot_id=? ORDER BY id DESC LIMIT 200)""",
                   (bot_id, bot_id))
    socketio.emit("log_update", {"bot_id": bot_id, "line": msg}, room=bot_id)

def run_bot(bot_id, script, token):
    path = f"/tmp/bothost_{bot_id}.py"
    with open(path, "w") as f:
        f.write(script)
    env = os.environ.copy()
    env["BOT_TOKEN"] = token or ""
    try:
        proc = subprocess.Popen(
            [sys.executable, path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, preexec_fn=os.setsid
        )
        processes[bot_id] = proc
        with get_db() as db:
            db.execute("UPDATE bots SET status='running' WHERE id=?", (bot_id,))
        add_log(bot_id, f"✅ Started (PID {proc.pid})")
        socketio.emit("status_update", {"bot_id": bot_id, "status": "running"}, room=bot_id)
        for line in iter(proc.stdout.readline, ""):
            if line.strip():
                add_log(bot_id, line.strip())
        proc.wait()
        with get_db() as db:
            db.execute("UPDATE bots SET status='stopped' WHERE id=?", (bot_id,))
        processes.pop(bot_id, None)
        add_log(bot_id, f"⚠️ Stopped (exit {proc.returncode})")
        socketio.emit("status_update", {"bot_id": bot_id, "status": "stopped"}, room=bot_id)
    except Exception as e:
        add_log(bot_id, f"❌ Error: {e}")
        socketio.emit("status_update", {"bot_id": bot_id, "status": "error"}, room=bot_id)

def kill_bot(bot_id):
    proc = processes.get(bot_id)
    if proc:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass
        processes.pop(bot_id, None)
    with get_db() as db:
        db.execute("UPDATE bots SET status='stopped' WHERE id=?", (bot_id,))
    add_log(bot_id, "⛔ Force stopped by admin.")
    socketio.emit("status_update", {"bot_id": bot_id, "status": "stopped"}, room=bot_id)

# ─── HTML ─────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BotHost</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.0/socket.io.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;800&display=swap');
:root{--bg:#0a0a0f;--surf:#12121a;--brd:#1e1e2e;--acc:#7c3aed;--acc2:#06b6d4;--grn:#10b981;--red:#ef4444;--yel:#f59e0b;--txt:#e2e8f0;--mut:#64748b}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:'Syne',sans-serif;min-height:100vh}
input,select,textarea{background:#0d0d16;border:1px solid var(--brd);border-radius:8px;color:var(--txt);padding:10px 14px;font-family:'Space Mono',monospace;font-size:.84rem;outline:none;width:100%;transition:border .2s}
input:focus,select:focus,textarea:focus{border-color:var(--acc)}
textarea{resize:vertical;min-height:130px}
.btn{padding:9px 20px;border:none;border-radius:8px;font-family:'Syne',sans-serif;font-weight:700;font-size:.85rem;cursor:pointer;transition:all .2s}
.btn-primary{background:var(--acc);color:#fff}.btn-primary:hover{background:#6d28d9}
.btn-green{background:var(--grn);color:#fff}
.btn-red{background:var(--red);color:#fff}
.btn-ghost{background:#1e1e2e;color:#94a3b8}
.btn-sm{padding:5px 12px;font-size:.76rem}

/* Auth */
#auth-overlay{position:fixed;inset:0;background:#0a0a0f;z-index:999;display:flex;align-items:center;justify-content:center}
.auth-box{background:var(--surf);border:1px solid var(--brd);border-radius:20px;padding:36px 40px;width:380px;max-width:95vw}
.auth-logo{font-size:1.5rem;font-weight:800;background:linear-gradient(90deg,#7c3aed,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:4px}
.auth-sub{font-size:.75rem;color:var(--mut);font-family:'Space Mono',monospace;margin-bottom:24px}
.auth-tabs{display:flex;margin-bottom:24px;border:1px solid var(--brd);border-radius:8px;overflow:hidden}
.auth-tab{flex:1;padding:9px;text-align:center;font-size:.82rem;font-weight:600;cursor:pointer;color:var(--mut);background:transparent;transition:all .2s}
.auth-tab.active{background:var(--acc);color:#fff}
.form-label{font-size:.72rem;color:var(--mut);font-family:'Space Mono',monospace;text-transform:uppercase;display:block;margin-bottom:5px}
.auth-err{color:var(--red);font-size:.78rem;font-family:'Space Mono',monospace;margin-top:8px;min-height:18px}

/* Header */
header{background:linear-gradient(135deg,#1a0533,#0a0a1f 50%,#001a2e);padding:14px 28px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--brd)}
.logo{font-size:1.3rem;font-weight:800;background:linear-gradient(90deg,#7c3aed,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.user-pill{display:flex;align-items:center;gap:10px;background:#1e1e2e;border-radius:99px;padding:6px 14px 6px 8px;font-size:.8rem}
.avatar{width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,#7c3aed,#06b6d4);display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700}
.avatar.admin-av{background:linear-gradient(135deg,#f59e0b,#ef4444)}

/* Nav tabs */
.nav-tabs{display:flex;gap:4px;padding:0 28px;background:#0d0d16;border-bottom:1px solid var(--brd)}
.nav-tab{padding:12px 20px;font-size:.82rem;font-weight:600;cursor:pointer;color:var(--mut);border-bottom:2px solid transparent;transition:all .2s}
.nav-tab.active{color:var(--acc2);border-bottom-color:var(--acc2)}

/* Layout */
.container{max-width:1080px;margin:0 auto;padding:26px 18px}
.stats-bar{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
.stat-card{background:var(--surf);border:1px solid var(--brd);border-radius:12px;padding:14px 18px}
.stat-val{font-size:1.6rem;font-weight:800}
.stat-label{font-size:.7rem;color:var(--mut);font-family:'Space Mono',monospace;margin-top:3px}
.deploy-card{background:var(--surf);border:1px solid var(--brd);border-radius:16px;padding:24px;margin-bottom:24px}
.section-title{font-size:.78rem;font-weight:600;color:var(--acc2);letter-spacing:.07em;text-transform:uppercase;margin-bottom:14px}
.form-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:14px}
.template-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.tmpl-btn{padding:5px 13px;border:1px solid var(--brd);background:transparent;color:var(--acc2);border-radius:6px;font-size:.72rem;cursor:pointer;font-family:'Space Mono',monospace;transition:background .2s}
.tmpl-btn:hover{background:var(--brd)}

/* Bot card */
.bot-card{background:var(--surf);border:1px solid var(--brd);border-radius:14px;padding:18px 22px;display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:start;margin-bottom:12px}
.bot-icon{width:46px;height:46px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:1.3rem}
.bot-icon.telegram{background:#1a3a5c}.bot-icon.discord{background:#2a1a5c}.bot-icon.whatsapp{background:#0f3a2a}.bot-icon.custom{background:#1e1e2e}
.bot-name{font-size:.95rem;font-weight:700}
.bot-meta{font-size:.72rem;color:var(--mut);font-family:'Space Mono',monospace;margin-top:3px}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}
.status-dot.running{background:var(--grn);box-shadow:0 0 6px var(--grn);animation:pulse 1.5s infinite}
.status-dot.stopped{background:var(--mut)}.status-dot.error{background:var(--red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.log-box{background:#060610;border:1px solid var(--brd);border-radius:8px;padding:10px 12px;font-family:'Space Mono',monospace;font-size:.68rem;color:#94a3b8;max-height:120px;overflow-y:auto;margin-top:10px;grid-column:1/-1;line-height:1.65}
.bot-actions{display:flex;flex-direction:column;gap:6px}
.empty-state{text-align:center;padding:50px 20px;color:var(--mut)}

/* Admin panel */
.admin-panel{display:none}
.user-row{background:var(--surf);border:1px solid var(--brd);border-radius:12px;padding:16px 20px;display:grid;grid-template-columns:auto 1fr auto auto;gap:16px;align-items:center;margin-bottom:10px}
.user-ava{width:38px;height:38px;border-radius:50%;background:linear-gradient(135deg,#534AB7,#1D9E75);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.85rem}
.user-info .uname{font-size:.9rem;font-weight:700}
.user-info .umeta{font-size:.7rem;color:var(--mut);font-family:'Space Mono',monospace;margin-top:2px}
.bot-count-pill{background:#1e1e2e;border-radius:99px;padding:4px 12px;font-size:.75rem;font-family:'Space Mono',monospace;color:var(--acc2)}
.running-pill{background:#0f3a2a;border-radius:99px;padding:4px 12px;font-size:.75rem;font-family:'Space Mono',monospace;color:var(--grn)}
.banned-badge{background:#3a0f0f;color:var(--red);border-radius:99px;padding:2px 8px;font-size:.68rem;font-family:'Space Mono',monospace}

/* Server metrics */
.metrics-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px}
.metric-card{background:var(--surf);border:1px solid var(--brd);border-radius:14px;padding:20px}
.metric-title{font-size:.72rem;color:var(--mut);font-family:'Space Mono',monospace;text-transform:uppercase;margin-bottom:12px}
.metric-val{font-size:1.8rem;font-weight:800;margin-bottom:8px}
.progress-bar{height:6px;background:#1e1e2e;border-radius:99px;overflow:hidden}
.progress-fill{height:100%;border-radius:99px;transition:width .5s}
.badges{display:flex;gap:7px}
.badge{padding:3px 10px;border-radius:99px;font-size:.68rem;font-family:'Space Mono',monospace;font-weight:700}
.badge.tg{background:#1a3a5c;color:#06b6d4}.badge.dc{background:#2a1a5c;color:#818cf8}.badge.wa{background:#0f3a2a;color:#10b981}
.mg{margin-bottom:14px}
</style>
</head>
<body>

<!-- Auth Overlay -->
<div id="auth-overlay">
  <div class="auth-box">
    <div class="auth-logo">⚡ BotHost</div>
    <div class="auth-sub">Free bot hosting platform</div>
    <div class="auth-tabs">
      <div class="auth-tab active" id="tab-login" onclick="switchTab('login')">Login</div>
      <div class="auth-tab" id="tab-register" onclick="switchTab('register')">Register</div>
    </div>
    <div class="mg"><label class="form-label">Username</label><input type="text" id="auth-user" placeholder="yourname"></div>
    <div class="mg"><label class="form-label">Password</label><input type="password" id="auth-pass" placeholder="••••••••" onkeydown="if(event.key==='Enter')doAuth()"></div>
    <div class="auth-err" id="auth-err"></div>
    <button class="btn btn-primary" style="width:100%;margin-top:10px" onclick="doAuth()" id="auth-btn">Login</button>
  </div>
</div>

<!-- Main App -->
<div id="app" style="display:none">
  <header>
    <div class="logo">⚡ BotHost</div>
    <div style="display:flex;align-items:center;gap:12px">
      <div class="badges">
        <span class="badge tg">📨 Telegram</span>
        <span class="badge dc">🎮 Discord</span>
        <span class="badge wa">💬 WhatsApp</span>
      </div>
      <div class="user-pill">
        <div class="avatar" id="u-avatar">?</div>
        <span id="u-name">—</span>
        <span style="color:var(--mut);cursor:pointer;font-size:.72rem;margin-left:4px" onclick="logout()">✕</span>
      </div>
    </div>
  </header>

  <!-- Nav tabs (admin ke liye extra tab) -->
  <div class="nav-tabs" id="nav-tabs">
    <div class="nav-tab active" onclick="showTab('user-panel')">🤖 My Bots</div>
    <div class="nav-tab" id="admin-tab" style="display:none" onclick="showTab('admin-panel')">🛡️ Admin Panel</div>
  </div>

  <!-- User Panel -->
  <div id="user-panel" class="container">
    <div class="stats-bar">
      <div class="stat-card"><div class="stat-val" id="s-total" style="color:var(--acc2)">0</div><div class="stat-label">My Bots</div></div>
      <div class="stat-card"><div class="stat-val" id="s-run" style="color:var(--grn)">0</div><div class="stat-label">Running</div></div>
      <div class="stat-card"><div class="stat-val" id="s-stop" style="color:var(--mut)">0</div><div class="stat-label">Stopped</div></div>
      <div class="stat-card"><div class="stat-val" id="s-uptime" style="color:var(--yel)">—</div><div class="stat-label">Server Uptime</div></div>
    </div>

    <div class="deploy-card">
      <div class="section-title">🚀 Deploy New Bot</div>
      <div class="form-row">
        <div><label class="form-label">Bot Name</label><input id="b-name" placeholder="My Bot"></div>
        <div><label class="form-label">Platform</label>
          <select id="b-type">
            <option value="telegram">📨 Telegram</option>
            <option value="discord">🎮 Discord</option>
            <option value="whatsapp">💬 WhatsApp</option>
            <option value="custom">⚙️ Custom</option>
          </select>
        </div>
        <div><label class="form-label">Token / API Key</label><input id="b-token" placeholder="Paste token here"></div>
      </div>
      <div style="margin-bottom:8px"><label class="form-label">Bot Script (Python)</label></div>
      <div class="template-row">
        <button class="tmpl-btn" onclick="tpl('tg')">📨 Telegram Echo</button>
        <button class="tmpl-btn" onclick="tpl('dc')">🎮 Discord Ping</button>
        <button class="tmpl-btn" onclick="tpl('ping')">🏓 Alive Loop</button>
        <button class="tmpl-btn" onclick="tpl('blank')">📄 Blank</button>
      </div>
      <textarea id="b-script" placeholder="# Paste your Python bot code here..."></textarea>
      <button class="btn btn-primary" style="margin-top:12px" onclick="deploy()">⚡ Deploy Bot</button>
    </div>

    <div class="section-title">🤖 My Bots</div>
    <div id="bots-list"></div>
  </div>

  <!-- Admin Panel -->
  <div id="admin-panel" class="container" style="display:none">

    <!-- Server Metrics -->
    <div class="section-title">📊 Server Resources (Live)</div>
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-title">CPU Usage</div>
        <div class="metric-val" id="m-cpu" style="color:var(--acc2)">—%</div>
        <div class="progress-bar"><div class="progress-fill" id="pb-cpu" style="background:var(--acc2);width:0%"></div></div>
        <div style="font-size:.7rem;color:var(--mut);font-family:'Space Mono',monospace;margin-top:6px" id="m-cores">— cores</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">RAM Usage</div>
        <div class="metric-val" id="m-ram" style="color:var(--grn)">—%</div>
        <div class="progress-bar"><div class="progress-fill" id="pb-ram" style="background:var(--grn);width:0%"></div></div>
        <div style="font-size:.7rem;color:var(--mut);font-family:'Space Mono',monospace;margin-top:6px" id="m-ram-detail">— / — GB</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">Disk Usage</div>
        <div class="metric-val" id="m-disk" style="color:var(--yel)">—%</div>
        <div class="progress-bar"><div class="progress-fill" id="pb-disk" style="background:var(--yel);width:0%"></div></div>
        <div style="font-size:.7rem;color:var(--mut);font-family:'Space Mono',monospace;margin-top:6px" id="m-disk-detail">— / — GB</div>
      </div>
    </div>

    <!-- Platform Stats -->
    <div class="stats-bar" style="grid-template-columns:repeat(4,1fr)">
      <div class="stat-card"><div class="stat-val" id="a-users" style="color:var(--acc2)">0</div><div class="stat-label">Total Users</div></div>
      <div class="stat-card"><div class="stat-val" id="a-bots" style="color:var(--txt)">0</div><div class="stat-label">Total Bots</div></div>
      <div class="stat-card"><div class="stat-val" id="a-running" style="color:var(--grn)">0</div><div class="stat-label">Running Now</div></div>
      <div class="stat-card"><div class="stat-val" id="a-banned" style="color:var(--red)">0</div><div class="stat-label">Banned Users</div></div>
    </div>

    <!-- Users Table -->
    <div class="section-title">👥 All Users</div>
    <div id="admin-users-list"></div>
  </div>
</div>

<script>
const TMPLS = {
  tg:`import os,time,requests
TOKEN=os.environ.get("BOT_TOKEN","")
API=f"https://api.telegram.org/bot{TOKEN}"
offset=0
print("Telegram bot started!")
while True:
    try:
        r=requests.get(f"{API}/getUpdates",params={"offset":offset,"timeout":10},timeout=15)
        for u in r.json().get("result",[]):
            offset=u["update_id"]+1
            msg=u.get("message",{})
            cid=msg.get("chat",{}).get("id")
            txt=msg.get("text","")
            if cid and txt:
                print(f"MSG: {txt}")
                requests.post(f"{API}/sendMessage",json={"chat_id":cid,"text":f"Echo: {txt}"})
    except Exception as e:
        print(f"Err: {e}")
    time.sleep(1)`,
  dc:`import os,time,requests
TOKEN=os.environ.get("BOT_TOKEN","")
H={"Authorization":f"Bot {TOKEN}"}
print("Discord bot started!")
while True:
    try:
        r=requests.get("https://discord.com/api/v10/users/@me",headers=H)
        print("Connected:",r.json().get("username","?") if r.ok else "Check token!")
    except Exception as e:
        print(f"Err: {e}")
    time.sleep(30)`,
  ping:`import time
print("Alive bot started!")
n=0
while True:
    n+=1
    print(f"Ping #{n} at {time.strftime('%H:%M:%S')}")
    time.sleep(5)`,
  blank:`import os,time
TOKEN=os.environ.get("BOT_TOKEN","")
print("Bot started! Token loaded:",bool(TOKEN))
while True:
    time.sleep(10)`
};

let bots={}, socket=io(), authMode="login", isAdmin=false;
let metricsInterval=null;

function switchTab(m){
  authMode=m;
  document.getElementById("tab-login").classList.toggle("active",m==="login");
  document.getElementById("tab-register").classList.toggle("active",m==="register");
  document.getElementById("auth-btn").textContent=m==="login"?"Login":"Register";
  document.getElementById("auth-err").textContent="";
}

async function doAuth(){
  const u=document.getElementById("auth-user").value.trim();
  const p=document.getElementById("auth-pass").value;
  if(!u||!p){document.getElementById("auth-err").textContent="Fill both fields!";return;}
  const res=await fetch(`/api/${authMode}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:u,password:p})});
  const d=await res.json();
  if(d.success) loginSuccess(d.username, d.is_admin);
  else document.getElementById("auth-err").textContent=d.error||"Error";
}

function loginSuccess(username, admin){
  isAdmin=!!admin;
  document.getElementById("auth-overlay").style.display="none";
  document.getElementById("app").style.display="block";
  document.getElementById("u-name").textContent=username+(admin?" 🛡️":"");
  const av=document.getElementById("u-avatar");
  av.textContent=username[0].toUpperCase();
  if(admin) av.classList.add("admin-av");
  if(admin){
    document.getElementById("admin-tab").style.display="block";
  }
  loadBots();
  updateUptime();
}

function showTab(tab){
  document.getElementById("user-panel").style.display=tab==="user-panel"?"block":"none";
  document.getElementById("admin-panel").style.display=tab==="admin-panel"?"block":"none";
  document.querySelectorAll(".nav-tab").forEach((t,i)=>{
    t.classList.toggle("active",(i===0&&tab==="user-panel")||(i===1&&tab==="admin-panel"));
  });
  if(tab==="admin-panel"){
    loadAdminData();
    if(!metricsInterval) metricsInterval=setInterval(loadMetrics,3000);
    loadMetrics();
  } else {
    if(metricsInterval){clearInterval(metricsInterval);metricsInterval=null;}
  }
}

async function logout(){
  await fetch("/api/logout",{method:"POST"});
  location.reload();
}

function tpl(k){document.getElementById("b-script").value=TMPLS[k]||"";}

async function deploy(){
  const name=document.getElementById("b-name").value.trim()||"Unnamed Bot";
  const type=document.getElementById("b-type").value;
  const token=document.getElementById("b-token").value.trim();
  const script=document.getElementById("b-script").value.trim();
  if(!script){alert("Script is empty!");return;}
  const r=await fetch("/api/deploy",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name,type,token,script})});
  const d=await r.json();
  if(d.success){
    bots[d.bot.id]=d.bot;
    socket.emit("join_bot",{bot_id:d.bot.id});
    render();
    document.getElementById("b-name").value="";
    document.getElementById("b-token").value="";
    document.getElementById("b-script").value="";
  } else alert(d.error||"Deploy failed");
}

async function startBot(id){await fetch(`/api/bot/${id}/start`,{method:"POST"});}
async function stopBot(id){
  await fetch(`/api/bot/${id}/stop`,{method:"POST"});
  if(bots[id]){bots[id].status="stopped";render();}
}
async function deleteBot(id){
  if(!confirm("Delete this bot?"))return;
  await fetch(`/api/bot/${id}/delete`,{method:"POST"});
  delete bots[id];render();
}

// Admin actions
async function adminForceStop(botId){
  if(!confirm("Force stop this bot?"))return;
  const r=await fetch(`/api/admin/bot/${botId}/stop`,{method:"POST"});
  const d=await r.json();
  if(d.success){alert("Bot stopped!");loadAdminData();}
}
async function adminBanUser(uid, ban){
  const msg=ban?"Ban this user? Their bots will be stopped.":"Unban this user?";
  if(!confirm(msg))return;
  const r=await fetch(`/api/admin/user/${uid}/ban`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ban})});
  const d=await r.json();
  if(d.success) loadAdminData();
}
async function adminDeleteUser(uid){
  if(!confirm("Delete this user AND all their bots? This cannot be undone!"))return;
  const r=await fetch(`/api/admin/user/${uid}/delete`,{method:"POST"});
  const d=await r.json();
  if(d.success) loadAdminData();
}

function render(){
  const el=document.getElementById("bots-list");
  const keys=Object.keys(bots);
  if(!keys.length){
    el.innerHTML=`<div class="empty-state"><div style="font-size:2.5rem;margin-bottom:10px">🤖</div>No bots yet — deploy your first one above!</div>`;
    updateStats();return;
  }
  el.innerHTML=keys.map(id=>{
    const b=bots[id];
    const icons={telegram:"📨",discord:"🎮",whatsapp:"💬",custom:"⚙️"};
    const status=b.status||"stopped";
    const logs=(b.logs||[]).slice(-15).map(l=>`<div>${esc(l)}</div>`).join("");
    return `<div class="bot-card" id="card-${id}">
      <div class="bot-icon ${b.type}">${icons[b.type]||"⚙️"}</div>
      <div>
        <div class="bot-name">${esc(b.name)}</div>
        <div class="bot-meta">
          <span class="status-dot ${status}" id="dot-${id}"></span>
          <span id="st-${id}">${status.toUpperCase()}</span>
          &nbsp;·&nbsp;${id}&nbsp;·&nbsp;${b.type}
        </div>
        <div class="log-box" id="log-${id}">${logs||'<span style="color:#4a5568">No logs yet...</span>'}</div>
      </div>
      <div class="bot-actions">
        <button class="btn btn-green btn-sm" onclick="startBot('${id}')">▶ Start</button>
        <button class="btn btn-red btn-sm" onclick="stopBot('${id}')">■ Stop</button>
        <button class="btn btn-ghost btn-sm" onclick="deleteBot('${id}')">🗑 Del</button>
      </div>
    </div>`;
  }).join("");
  updateStats();
}

function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}

function updateStats(){
  const all=Object.values(bots);
  document.getElementById("s-total").textContent=all.length;
  document.getElementById("s-run").textContent=all.filter(b=>b.status==="running").length;
  document.getElementById("s-stop").textContent=all.filter(b=>b.status!=="running").length;
}

async function updateUptime(){
  const r=await fetch("/api/uptime");
  const d=await r.json();
  if(d.uptime) document.getElementById("s-uptime").textContent=d.uptime;
}

async function loadBots(){
  const r=await fetch("/api/bots");
  if(!r.ok)return;
  bots=await r.json();
  Object.keys(bots).forEach(id=>socket.emit("join_bot",{bot_id:id}));
  render();
}

async function loadMetrics(){
  const r=await fetch("/api/admin/metrics");
  if(!r.ok)return;
  const d=await r.json();
  document.getElementById("m-cpu").textContent=d.cpu_percent+"%";
  document.getElementById("pb-cpu").style.width=d.cpu_percent+"%";
  document.getElementById("pb-cpu").style.background=d.cpu_percent>80?"var(--red)":d.cpu_percent>60?"var(--yel)":"var(--acc2)";
  document.getElementById("m-cores").textContent=d.cpu_cores+" cores / "+d.cpu_freq+"MHz";
  document.getElementById("m-ram").textContent=d.ram_percent+"%";
  document.getElementById("pb-ram").style.width=d.ram_percent+"%";
  document.getElementById("pb-ram").style.background=d.ram_percent>85?"var(--red)":d.ram_percent>70?"var(--yel)":"var(--grn)";
  document.getElementById("m-ram-detail").textContent=d.ram_used+" / "+d.ram_total+" GB";
  document.getElementById("m-disk").textContent=d.disk_percent+"%";
  document.getElementById("pb-disk").style.width=d.disk_percent+"%";
  document.getElementById("pb-disk").style.background=d.disk_percent>85?"var(--red)":d.disk_percent>70?"var(--yel)":"var(--yel)";
  document.getElementById("m-disk-detail").textContent=d.disk_used+" / "+d.disk_total+" GB";
}

async function loadAdminData(){
  const r=await fetch("/api/admin/users");
  if(!r.ok)return;
  const d=await r.json();
  document.getElementById("a-users").textContent=d.stats.total_users;
  document.getElementById("a-bots").textContent=d.stats.total_bots;
  document.getElementById("a-running").textContent=d.stats.running_bots;
  document.getElementById("a-banned").textContent=d.stats.banned_users;

  const el=document.getElementById("admin-users-list");
  if(!d.users.length){
    el.innerHTML=`<div class="empty-state">No users yet.</div>`;return;
  }
  el.innerHTML=d.users.map(u=>{
    const runningBots=u.bots.filter(b=>b.status==="running");
    return `<div class="user-row">
      <div class="user-ava">${u.username[0].toUpperCase()}</div>
      <div class="user-info">
        <div class="uname">${esc(u.username)} ${u.is_banned?'<span class="banned-badge">BANNED</span>':''}</div>
        <div class="umeta">Joined: ${u.created} &nbsp;·&nbsp; User ID: ${u.id}</div>
        <div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:6px">
          ${u.bots.map(b=>`
            <div style="background:#0d0d16;border:1px solid var(--brd);border-radius:8px;padding:5px 10px;font-size:.7rem;font-family:'Space Mono',monospace;display:flex;align-items:center;gap:6px">
              <span class="status-dot ${b.status}" style="flex-shrink:0"></span>
              ${esc(b.name)}
              ${b.status==="running"?`<button class="btn btn-red" style="padding:2px 8px;font-size:.65rem" onclick="adminForceStop('${b.id}')">Force Stop</button>`:''}
            </div>`).join("")}
          ${u.bots.length===0?'<span style="font-size:.72rem;color:var(--mut);font-family:Space Mono,monospace">No bots</span>':''}
        </div>
      </div>
      <div style="display:flex;gap:6px;flex-direction:column;align-items:flex-end">
        <span class="bot-count-pill">${u.bots.length} bots</span>
        ${runningBots.length?`<span class="running-pill">${runningBots.length} running</span>`:''}
      </div>
      <div style="display:flex;flex-direction:column;gap:6px">
        ${u.is_banned
          ?`<button class="btn btn-green btn-sm" onclick="adminBanUser(${u.id},false)">Unban</button>`
          :u.username!=='admin'?`<button class="btn btn-red btn-sm" onclick="adminBanUser(${u.id},true)">Ban</button>`:''}
        ${u.username!=='admin'?`<button class="btn btn-ghost btn-sm" onclick="adminDeleteUser(${u.id})">Delete</button>`:''}
      </div>
    </div>`;
  }).join("");
}

socket.on("log_update",({bot_id,line})=>{
  if(!bots[bot_id])return;
  bots[bot_id].logs=bots[bot_id].logs||[];
  bots[bot_id].logs.push(line);
  const el=document.getElementById(`log-${bot_id}`);
  if(el){el.innerHTML+=`<div>${esc(line)}</div>`;el.scrollTop=el.scrollHeight;}
});

socket.on("status_update",({bot_id,status})=>{
  if(!bots[bot_id])return;
  bots[bot_id].status=status;
  const st=document.getElementById(`st-${bot_id}`);
  const dot=document.getElementById(`dot-${bot_id}`);
  if(st)st.textContent=status.toUpperCase();
  if(dot)dot.className=`status-dot ${status}`;
  updateStats();
});

fetch("/api/me").then(r=>r.json()).then(d=>{
  if(d.username) loginSuccess(d.username, d.is_admin);
});
</script>
</body>
</html>
"""

# ─── SocketIO ─────────────────────────────────────────────────

@socketio.on("join_bot")
def on_join(data):
    join_room(data["bot_id"])

# ─── Routes ───────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/me")
def me():
    if "user_id" in session:
        with get_db() as db:
            u = db.execute("SELECT username FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if u:
            return jsonify({"username": u["username"], "is_admin": session.get("is_admin", False)})
    return jsonify({"username": None})

@app.route("/api/uptime")
def uptime():
    secs = int(time.time() - psutil.boot_time())
    h, m = divmod(secs // 60, 60)
    d, h = divmod(h, 24)
    return jsonify({"uptime": f"{d}d {h}h {m}m"})

@app.route("/api/register", methods=["POST"])
def register():
    d = request.json
    u = d.get("username", "").strip()
    p = d.get("password", "")
    if not u or not p:
        return jsonify({"error": "Fill all fields"})
    if len(u) < 3:
        return jsonify({"error": "Username min 3 chars"})
    if len(p) < 4:
        return jsonify({"error": "Password min 4 chars"})
    if u == ADMIN_USERNAME:
        return jsonify({"error": "Username not available"})
    try:
        with get_db() as db:
            db.execute("INSERT INTO users(username,password) VALUES(?,?)", (u, hash_pw(p)))
            uid = db.execute("SELECT id FROM users WHERE username=?", (u,)).fetchone()["id"]
        session["user_id"] = uid
        session["username"] = u
        session["is_admin"] = False
        return jsonify({"success": True, "username": u, "is_admin": False})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username already taken"})

@app.route("/api/login", methods=["POST"])
def login():
    d = request.json
    u = d.get("username", "").strip()
    p = d.get("password", "")
    # Admin check
    if u == ADMIN_USERNAME and p == ADMIN_PASSWORD:
        session["user_id"] = 0
        session["username"] = u
        session["is_admin"] = True
        return jsonify({"success": True, "username": u, "is_admin": True})
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE username=? AND password=?",
                         (u, hash_pw(p))).fetchone()
    if not row:
        return jsonify({"error": "Wrong username or password"})
    if row["is_banned"]:
        return jsonify({"error": "Your account has been banned"})
    session["user_id"] = row["id"]
    session["username"] = row["username"]
    session["is_admin"] = False
    return jsonify({"success": True, "username": row["username"], "is_admin": False})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route("/api/bots")
@require_login
def get_bots():
    uid = session["user_id"]
    if uid == 0:
        return jsonify({})
    with get_db() as db:
        rows = db.execute("SELECT * FROM bots WHERE user_id=? ORDER BY created DESC", (uid,)).fetchall()
    result = {}
    for r in rows:
        bid = r["id"]
        with get_db() as db:
            logs = db.execute(
                "SELECT line FROM bot_logs WHERE bot_id=? ORDER BY id DESC LIMIT 50", (bid,)
            ).fetchall()
        is_running = bid in processes and processes[bid].poll() is None
        result[bid] = {
            "id": bid, "name": r["name"], "type": r["type"],
            "token": r["token"], "script": r["script"],
            "status": "running" if is_running else "stopped",
            "logs": [l["line"] for l in reversed(logs)]
        }
    return jsonify(result)

@app.route("/api/deploy", methods=["POST"])
@require_login
def deploy():
    d = request.json
    uid = session["user_id"]
    import random, string
    bid = "bot_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    with get_db() as db:
        db.execute(
            "INSERT INTO bots(id,user_id,name,type,token,script) VALUES(?,?,?,?,?,?)",
            (bid, uid, d.get("name", "Bot"), d.get("type", "custom"),
             d.get("token", ""), d.get("script", ""))
        )
    add_log(bid, f"🚀 Deployed!")
    return jsonify({"success": True, "bot": {
        "id": bid, "name": d.get("name", "Bot"), "type": d.get("type", "custom"),
        "status": "stopped", "logs": []
    }})

@app.route("/api/bot/<bot_id>/start", methods=["POST"])
@require_login
def start(bot_id):
    uid = session["user_id"]
    with get_db() as db:
        row = db.execute("SELECT * FROM bots WHERE id=? AND user_id=?", (bot_id, uid)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    if bot_id in processes and processes[bot_id].poll() is None:
        return jsonify({"error": "Already running"})
    t = threading.Thread(target=run_bot, args=(bot_id, row["script"], row["token"]), daemon=True)
    t.start()
    return jsonify({"success": True})

@app.route("/api/bot/<bot_id>/stop", methods=["POST"])
@require_login
def stop(bot_id):
    uid = session["user_id"]
    with get_db() as db:
        row = db.execute("SELECT id FROM bots WHERE id=? AND user_id=?", (bot_id, uid)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    kill_bot(bot_id)
    return jsonify({"success": True})

@app.route("/api/bot/<bot_id>/delete", methods=["POST"])
@require_login
def delete(bot_id):
    uid = session["user_id"]
    kill_bot(bot_id)
    with get_db() as db:
        db.execute("DELETE FROM bots WHERE id=? AND user_id=?", (bot_id, uid))
        db.execute("DELETE FROM bot_logs WHERE bot_id=?", (bot_id,))
    return jsonify({"success": True})

# ─── Admin Routes ─────────────────────────────────────────────

@app.route("/api/admin/metrics")
@require_admin
def admin_metrics():
    cpu  = psutil.cpu_percent(interval=0.3)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    freq = psutil.cpu_freq()
    return jsonify({
        "cpu_percent": round(cpu, 1),
        "cpu_cores":   psutil.cpu_count(),
        "cpu_freq":    round(freq.current) if freq else 0,
        "ram_percent": round(ram.percent, 1),
        "ram_used":    round(ram.used / 1e9, 2),
        "ram_total":   round(ram.total / 1e9, 2),
        "disk_percent": round(disk.percent, 1),
        "disk_used":    round(disk.used / 1e9, 1),
        "disk_total":   round(disk.total / 1e9, 1),
    })

@app.route("/api/admin/users")
@require_admin
def admin_users():
    with get_db() as db:
        users = db.execute("SELECT * FROM users ORDER BY created DESC").fetchall()
        all_bots = db.execute("SELECT * FROM bots ORDER BY created DESC").fetchall()
    user_list = []
    total_running = 0
    for u in users:
        user_bots = [b for b in all_bots if b["user_id"] == u["id"]]
        bots_data = []
        for b in user_bots:
            is_running = b["id"] in processes and processes[b["id"]].poll() is None
            if is_running:
                total_running += 1
            bots_data.append({
                "id": b["id"], "name": b["name"], "type": b["type"],
                "status": "running" if is_running else "stopped"
            })
        user_list.append({
            "id": u["id"], "username": u["username"],
            "is_banned": bool(u["is_banned"]),
            "created": u["created"][:10],
            "bots": bots_data
        })
    return jsonify({
        "users": user_list,
        "stats": {
            "total_users":  len(users),
            "total_bots":   len(all_bots),
            "running_bots": total_running,
            "banned_users": sum(1 for u in users if u["is_banned"])
        }
    })

@app.route("/api/admin/bot/<bot_id>/stop", methods=["POST"])
@require_admin
def admin_stop_bot(bot_id):
    kill_bot(bot_id)
    return jsonify({"success": True})

@app.route("/api/admin/user/<int:uid>/ban", methods=["POST"])
@require_admin
def admin_ban_user(uid):
    ban = request.json.get("ban", True)
    with get_db() as db:
        db.execute("UPDATE users SET is_banned=? WHERE id=?", (1 if ban else 0, uid))
        if ban:
            user_bots = db.execute("SELECT id FROM bots WHERE user_id=?", (uid,)).fetchall()
            for b in user_bots:
                kill_bot(b["id"])
    return jsonify({"success": True})

@app.route("/api/admin/user/<int:uid>/delete", methods=["POST"])
@require_admin
def admin_delete_user(uid):
    with get_db() as db:
        user_bots = db.execute("SELECT id FROM bots WHERE user_id=?", (uid,)).fetchall()
        for b in user_bots:
            kill_bot(b["id"])
            db.execute("DELETE FROM bot_logs WHERE bot_id=?", (b["id"],))
        db.execute("DELETE FROM bots WHERE user_id=?", (uid,))
        db.execute("DELETE FROM users WHERE id=?", (uid,))
    return jsonify({"success": True})

# ─── Start ────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("╔══════════════════════════════════════════════════╗")
    print("║   ⚡ BotHost — Admin Edition Ready!              ║")
    print("╠══════════════════════════════════════════════════╣")
    print("║  Open:        http://localhost:8080              ║")
    print(f"║  Admin login: {ADMIN_USERNAME} / {ADMIN_PASSWORD:<28}║")
    print("║  Database:    bothost.db                         ║")
    print("╚══════════════════════════════════════════════════╝")
    socketio.run(app, host="0.0.0.0", port=8080, debug=False)
