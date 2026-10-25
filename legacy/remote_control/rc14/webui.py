# -*- coding: utf-8 -*-
"""Claude-style web console for RemoteControl 1.4 (refactor).

Design language: warm cream background, serif headings, terracotta accent,
soft cards with hairline borders — a calm "Anthropic/Claude" look replacing
the old neon-gradient dark theme. All dynamic DOM writes go through
textContent (no innerHTML interpolation) to close the legacy XSS holes.
"""

HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>远程控制 · Remote Control</title>
<style>
  :root {
    --bg: #faf9f5;            /* Claude cream */
    --surface: #ffffff;
    --surface-2: #f5f1e8;     /* warm sand panels */
    --ink: #1f1e1b;           /* near-black text */
    --ink-soft: #6e6a61;      /* secondary text */
    --line: #e8e3d9;          /* hairline border */
    --accent: #d97757;        /* Anthropic terracotta */
    --accent-deep: #c15f3e;
    --ok: #3f7d5c;
    --warn: #b4690e;
    --err: #c0392b;
    --radius: 14px;
    --serif: "Charter", "Georgia", "Songti SC", "SimSun", serif;
    --sans: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    --mono: "SF Mono", Consolas, "Courier New", monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 0;
    background: var(--bg); color: var(--ink);
    font-family: var(--sans); font-size: 15px; line-height: 1.55;
  }
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 28px; border-bottom: 1px solid var(--line);
    background: rgba(250,249,245,.9); position: sticky; top: 0; z-index: 50;
    backdrop-filter: blur(6px);
  }
  .brand { display: flex; align-items: center; gap: 12px; }
  .brand .mark {
    width: 34px; height: 34px; border-radius: 9px;
    background: var(--accent); color: #fff;
    display: flex; align-items: center; justify-content: center;
    font-family: var(--serif); font-weight: 700; font-size: 18px;
  }
  .brand h1 {
    font-family: var(--serif); font-size: 21px; font-weight: 600;
    margin: 0; letter-spacing: .2px;
  }
  .brand .ver { color: var(--ink-soft); font-size: 12px; margin-left: 6px; }
  .conn-badge {
    display: inline-flex; align-items: center; gap: 8px;
    font-size: 13px; color: var(--ink-soft);
    background: var(--surface-2); border: 1px solid var(--line);
    padding: 6px 14px; border-radius: 999px;
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); }
  .dot.off { background: var(--err); }

  main { max-width: 1180px; margin: 0 auto; padding: 26px 22px 60px; }

  .meta-row {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px; margin-bottom: 20px;
  }
  .meta-card {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--radius); padding: 12px 16px;
  }
  .meta-card .k { font-size: 12px; color: var(--ink-soft); }
  .meta-card .v { font-size: 15px; font-weight: 600; margin-top: 2px; word-break: break-all; }

  .layout { display: grid; grid-template-columns: minmax(0,1fr) 300px; gap: 20px; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }

  .card {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--radius); overflow: hidden;
  }
  .card + .card { margin-top: 20px; }
  .card-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 18px; border-bottom: 1px solid var(--line);
    background: linear-gradient(180deg, #fff, var(--surface-2));
  }
  .card-head h2 {
    margin: 0; font-family: var(--serif); font-size: 16px; font-weight: 600;
  }
  .card-body { padding: 18px; }

  .screen-wrap { position: relative; background: #141311; }
  .screen-wrap img {
    width: 100%; display: block; cursor: crosshair; aspect-ratio: 16/9;
    object-fit: contain;
  }
  .screen-hint {
    position: absolute; inset: 0; display: flex; align-items: center;
    justify-content: center; color: #cfc9bd; font-size: 14px;
    pointer-events: none; background: rgba(20,19,17,.55);
  }
  .screen-hint.hidden { display: none; }

  .btn {
    appearance: none; border: 1px solid var(--line); background: var(--surface);
    color: var(--ink); border-radius: 10px; padding: 9px 16px; font-size: 14px;
    cursor: pointer; transition: all .15s ease; font-family: var(--sans);
  }
  .btn:hover { border-color: var(--accent); color: var(--accent-deep); }
  .btn.primary {
    background: var(--accent); border-color: var(--accent); color: #fff;
  }
  .btn.primary:hover { background: var(--accent-deep); color: #fff; }
  .btn.danger:hover { border-color: var(--err); color: var(--err); }
  .btn.small { padding: 5px 10px; font-size: 12px; border-radius: 8px; }
  .btn.active { background: var(--surface-2); border-color: var(--accent); color: var(--accent-deep); }
  .btn-row { display: flex; flex-wrap: wrap; gap: 10px; }

  .kv-form { display: grid; grid-template-columns: 84px 1fr; gap: 10px 14px; align-items: center; }
  .kv-form label { color: var(--ink-soft); font-size: 13px; }
  input[type=text], input[type=password], select {
    width: 100%; padding: 8px 10px; border: 1px solid var(--line);
    border-radius: 8px; background: var(--surface); color: var(--ink);
    font-size: 14px; outline: none;
  }
  input:focus, select:focus { border-color: var(--accent); }
  input[type=range] { width: 100%; accent-color: var(--accent); }

  .status-line { margin-top: 12px; font-size: 13px; color: var(--ink-soft); min-height: 20px; }
  .status-line.ok { color: var(--ok); }
  .status-line.err { color: var(--err); }

  /* modal */
  .overlay {
    position: fixed; inset: 0; background: rgba(31,30,27,.45);
    display: none; align-items: center; justify-content: center; z-index: 100;
  }
  .overlay.open { display: flex; }
  .modal {
    width: min(680px, 92vw); max-height: 84vh; overflow: auto;
    background: var(--surface); border-radius: var(--radius);
    border: 1px solid var(--line); box-shadow: 0 24px 60px rgba(31,30,27,.25);
  }
  .modal-head {
    display: flex; justify-content: space-between; align-items: center;
    padding: 16px 20px; border-bottom: 1px solid var(--line);
  }
  .modal-head h3 { margin: 0; font-family: var(--serif); font-size: 17px; }
  .modal-close {
    border: none; background: var(--surface-2); width: 30px; height: 30px;
    border-radius: 8px; cursor: pointer; font-size: 16px; color: var(--ink-soft);
  }
  .modal-close:hover { color: var(--err); }
  .modal-body { padding: 20px; }

  .term {
    width: 100%; height: 240px; background: #141311; color: #e6ddcd;
    font-family: var(--mono); font-size: 13px; border-radius: 10px;
    padding: 12px; overflow-y: auto; white-space: pre-wrap; word-break: break-all;
    margin-top: 12px;
  }
  .term .cmd { color: var(--accent); }
  .term .fail { color: #ff9d8a; }

  .file-list { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
  .file-item {
    display: flex; align-items: center; justify-content: space-between;
    padding: 9px 14px; border-bottom: 1px solid var(--line); font-size: 13px;
  }
  .file-item:last-child { border-bottom: none; }
  .file-item:nth-child(odd) { background: var(--surface-2); }
  .file-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .file-size { color: var(--ink-soft); margin: 0 14px; white-space: nowrap; }

  .dropzone {
    border: 1.5px dashed var(--accent); border-radius: 10px; padding: 22px;
    text-align: center; color: var(--ink-soft); cursor: pointer;
    transition: background .2s;
  }
  .dropzone.dragover { background: rgba(217,119,87,.08); }
  .progress {
    height: 8px; border-radius: 999px; background: var(--surface-2);
    overflow: hidden; margin-top: 12px; display: none;
  }
  .progress > div { height: 100%; width: 0%; background: var(--accent); transition: width .2s; }

  footer { text-align: center; color: var(--ink-soft); font-size: 12px; padding: 24px; }
</style>
</head>
<body>
<header>
  <div class="brand">
    <div class="mark">R</div>
    <h1>远程控制<span class="ver">v{{ version }}</span></h1>
  </div>
  <div class="conn-badge"><span class="dot" id="live-dot"></span><span id="live-text">服务在线</span></div>
</header>

<main>
  <div class="meta-row">
    <div class="meta-card"><div class="k">本机 IP</div><div class="v">{{ ip }}</div></div>
    <div class="meta-card"><div class="k">端口</div><div class="v">{{ port }}</div></div>
    <div class="meta-card"><div class="k">推流分辨率</div><div class="v" id="resolution-info">{{ resolution }}</div></div>
    <div class="meta-card"><div class="k">帧率 / 画质</div><div class="v">{{ fps }} FPS · {{ quality }}%</div></div>
    <div class="meta-card"><div class="k">访问密码</div><div class="v">{{ pwd_state }}</div></div>
  </div>

  <div class="layout">
    <section>
      <div class="card">
        <div class="card-head">
          <h2>远程屏幕</h2>
          <div class="btn-row">
            <button class="btn small" id="btn-stream" onclick="toggleStream()">⏸ 暂停画面</button>
            <button class="btn small" id="btn-fs" onclick="enterFullscreen()">⛶ 全屏</button>
          </div>
        </div>
        <div class="screen-wrap" id="screen-wrap">
          <img id="screen" alt="远程屏幕">
          <div class="screen-hint" id="screen-hint">画面已暂停 — 点击「继续」开始采集（无观看时不消耗算力）</div>
        </div>
        <div class="card-body">
          <div class="btn-row">
            <button class="btn" onclick="sendKey('esc')">Esc</button>
            <button class="btn" onclick="sendKey('enter')">Enter</button>
            <button class="btn" onclick="sendKey('win')">Win</button>
            <button class="btn" onclick="sendKey('alt+tab')">Alt+Tab</button>
            <button class="btn" onclick="sendKey('ctrl+c')">Ctrl+C</button>
            <button class="btn" onclick="sendKey('ctrl+v')">Ctrl+V</button>
            <button class="btn" onclick="toggleKeyboardPanel()">⌨ 文本输入</button>
          </div>
          <div class="status-line" id="status">就绪：移动鼠标控制远端光标，左键单击 / 右键菜单均已映射。</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>快捷操作</h2></div>
        <div class="card-body">
          <div class="btn-row">
            <button class="btn" onclick="openModal('m-cmd')">💻 命令终端</button>
            <button class="btn" onclick="openModal('m-files')">📁 文件传输</button>
            <button class="btn" id="btn-cam" onclick="openModal('m-cam')">📷 摄像头</button>
            <button class="btn" id="btn-mic" onclick="toggleMic()">🎤 麦克风监听</button>
            <button class="btn danger" onclick="showSysMenu()">⚙ 系统电源</button>
          </div>
        </div>
      </div>
    </section>

    <aside>
      <div class="card">
        <div class="card-head"><h2>串流设置</h2></div>
        <div class="card-body">
          <div class="kv-form">
            <label>画质</label>
            <input type="range" id="q-range" min="10" max="95" value="{{ quality }}"
                   oninput="document.getElementById('q-val').textContent=this.value+'%'">
          </div>
          <div style="text-align:right;margin-top:4px"><span id="q-val">{{ quality }}%</span></div>
          <div class="btn-row" style="margin-top:10px">
            <button class="btn primary" onclick="applyQuality()">应用画质</button>
          </div>
          <div class="status-line" id="settings-status"></div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>会话信息</h2></div>
        <div class="card-body">
          <div class="kv-form">
            <label>延迟估算</label><div class="v" id="lat">—</div>
            <label>已接收帧</label><div class="v" id="frames">0</div>
            <label>运行时长</label><div class="v" id="uptime">0s</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>安全提示</h2></div>
        <div class="card-body" style="font-size:13px;color:var(--ink-soft)">
          本服务仅限受信局域网使用。建议在被控端启动参数中设置
          <code>--password</code>；命令执行默认关闭（需 <code>--enable-cmd</code>）。
        </div>
      </div>
    </aside>
  </div>
</main>

<!-- ============ Modals ============ -->
<div class="overlay" id="m-cmd">
  <div class="modal">
    <div class="modal-head"><h3>命令终端</h3><button class="modal-close" onclick="closeModal('m-cmd')">×</button></div>
    <div class="modal-body">
      <div id="cmd-disabled-note" style="display:none;color:var(--warn);font-size:13px;margin-bottom:10px">
        ⚠ 命令执行已在服务端禁用（安全默认）。如需使用，请在被控端启动时添加 --enable-cmd。
      </div>
      <div style="display:flex;gap:10px">
        <input type="text" id="cmd-input" placeholder="输入命令后回车执行…" onkeydown="if(event.key==='Enter')runCmd()">
        <button class="btn primary" onclick="runCmd()">执行</button>
      </div>
      <div class="term" id="cmd-term"></div>
    </div>
  </div>
</div>

<div class="overlay" id="m-files">
  <div class="modal">
    <div class="modal-head"><h3>文件传输</h3><button class="modal-close" onclick="closeModal('m-files')">×</button></div>
    <div class="modal-body">
      <div class="dropzone" id="dropzone">点击或拖拽文件到此处上传<input type="file" id="file-input" hidden></div>
      <div class="progress" id="up-progress"><div id="up-bar"></div></div>
      <div class="status-line" id="up-status"></div>
      <h3 style="font-family:var(--serif);font-size:15px;margin:18px 0 8px">已传输文件</h3>
      <div class="file-list" id="file-list"></div>
      <div class="btn-row" style="margin-top:12px"><button class="btn" onclick="loadFiles()">刷新列表</button></div>
    </div>
  </div>
</div>

<div class="overlay" id="m-cam">
  <div class="modal">
    <div class="modal-head"><h3>摄像头</h3><button class="modal-close" onclick="closeCamera()">×</button></div>
    <div class="modal-body">
      <img id="cam-feed" alt="摄像头画面" style="width:100%;border-radius:10px;background:#141311;display:none">
      <div class="btn-row"><button class="btn primary" id="cam-toggle" onclick="toggleCam()">开启摄像头</button></div>
      <div class="status-line" id="cam-status">摄像头默认关闭，开启后仅在观看时采集。</div>
    </div>
  </div>
</div>

<div class="overlay" id="m-kbd">
  <div class="modal" style="width:min(480px,92vw)">
    <div class="modal-head"><h3>文本输入</h3><button class="modal-close" onclick="closeModal('m-kbd')">×</button></div>
    <div class="modal-body">
      <input type="text" id="kbd-input" placeholder="在此输入的字符将发送到远端…"
             onkeydown="if(event.key==='Enter'){sendText();event.preventDefault();}">
      <div class="btn-row" style="margin-top:12px"><button class="btn primary" onclick="sendText()">发送</button></div>
    </div>
  </div>
</div>

<div class="overlay" id="m-sys">
  <div class="modal" style="width:min(420px,92vw)">
    <div class="modal-head"><h3>系统电源</h3><button class="modal-close" onclick="closeModal('m-sys')">×</button></div>
    <div class="modal-body">
      <div class="btn-row">
        <button class="btn danger" onclick="sysAction('sleep')">😴 睡眠</button>
        <button class="btn danger" onclick="sysAction('restart')">🔁 重启</button>
        <button class="btn danger" onclick="sysAction('shutdown')">⏻ 关机</button>
      </div>
      <div class="status-line" id="sys-status" style="color:var(--warn)">危险操作：请确认远端允许后再执行。</div>
    </div>
  </div>
</div>

<footer>RemoteControl v{{ version }} · Claude-styled refactor · 局域网专用</footer>

<script>
"use strict";
/* ---------------- state ---------------- */
const S = {
  streaming: false, frames: 0, startedAt: Date.now(),
  micOn: false, camOn: false, cmdEnabled: {{ cmd_enabled_js }},
  lastMoveSent: 0, pendingFrameURL: null,
};
const $ = (id) => document.getElementById(id);
const screenImg = $('screen');

/* ---------------- auth ---------------- */
function token() {
  const m = /(?:^|; )rc_token=([^;]+)/.exec(document.cookie);
  if (m) return m[1];
  const u = new URLSearchParams(location.search).get('password');
  return u || '';
}
function authQS(extra) {
  const t = token();
  return (t ? 'password=' + encodeURIComponent(t) + (extra ? '&' + extra : '') : (extra || ''));
}
async function postJSON(url, body) {
  const opts = { headers: { 'Content-Type': 'application/json' }, method: 'POST' };
  if (body !== undefined) opts.body = JSON.stringify(body);
  if (url.indexOf('password=') === -1 && token()) url += (url.indexOf('?') >= 0 ? '&' : '?') + 'password=' + encodeURIComponent(token());
  const r = await fetch(url, opts);
  if (r.status === 401) { setStatus('密码错误或未提供（401），请通过 http://IP:PORT/?password=xxx 访问', true); throw new Error('unauthorized'); }
  return r.json();
}

/* ---------------- status helpers ---------------- */
function setStatus(msg, isErr) {
  const el = $('status'); el.textContent = msg; el.className = 'status-line' + (isErr ? ' err' : '');
}

/* ---------------- video stream ---------------- */
function startStream() {
  stopStream();
  S.streaming = true;
  screenImg.src = '/video_feed?' + authQS('_=' + Date.now());
  screenImg.onload = () => {
    S.frames++; $('frames').textContent = S.frames;
    $('live-dot').className = 'dot';
    if (S.lastFrameAt) { const dt = Date.now() - S.lastFrameAt; $('lat').textContent = Math.max(1, Math.round(dt)) + ' ms'; }
    S.lastFrameAt = Date.now();
  };
  screenImg.onerror = () => { $('live-dot').className = 'dot off'; };
  $('screen-hint').classList.add('hidden');
  $('btn-stream').textContent = '⏸ 暂停画面';
}
function stopStream() {
  S.streaming = false;
  if (screenImg.src) screenImg.removeAttribute('src');
  $('screen-hint').classList.remove('hidden');
  $('btn-stream').textContent = '▶ 继续画面';
}
function toggleStream() { S.streaming ? stopStream() : startStream(); }

setInterval(() => {
  const s = Math.round((Date.now() - S.startedAt) / 1000);
  $('uptime').textContent = s >= 60 ? Math.floor(s / 60) + 'm' + (s % 60) + 's' : s + 's';
}, 1000);

/* ---------------- mouse ---------------- */
function mapPos(e) {
  const rect = screenImg.getBoundingClientRect();
  return {
    x: e.clientX - rect.left, y: e.clientY - rect.top,
    screen_width: rect.width, screen_height: rect.height,
  };
}
let moveTimer = null;
screenImg.addEventListener('mousemove', (e) => {
  const now = Date.now();
  if (now - S.lastMoveSent < 60) return;   // ~16 req/s cap
  S.lastMoveSent = now;
  postJSON('/mouse_move', mapPos(e)).catch(() => {});
});
screenImg.addEventListener('click', (e) => { postJSON('/mouse_click', mapPos(e)).catch(() => {}); });
screenImg.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  postJSON('/mouse_right_click', mapPos(e)).catch(() => {});
});
screenImg.addEventListener('wheel', (e) => {
  e.preventDefault();
  postJSON('/mouse_scroll', { delta: e.deltaY }).catch(() => {});
}, { passive: false });

/* touch support (phones/tablets) */
screenImg.addEventListener('touchstart', (e) => {
  const t = e.touches[0];
  postJSON('/mouse_move', mapPos({ clientX: t.clientX, clientY: t.clientY })).catch(() => {});
}, { passive: true });

/* ---------------- keyboard ---------------- */
function sendKey(key) { postJSON('/keyboard', { key }).catch(() => {}); }
function toggleKeyboardPanel() { openModal('m-kbd'); $('kbd-input').focus(); }
function sendText() {
  const v = $('kbd-input').value;
  if (!v) return;
  postJSON('/keyboard', { text: v }).then(() => setStatus('已发送文本：' + v)).catch(() => {});
  $('kbd-input').value = '';
}
document.addEventListener('keydown', (e) => {
  // forward keys only when no modal/text field is focused
  if (document.querySelector('.overlay.open')) return;
  if (['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
  const k = e.key.toLowerCase();
  let key = null;
  if (k === 'escape') key = 'esc';
  else if (k === 'enter') key = 'enter';
  else if (k === 'backspace') key = 'backspace';
  else if (k === 'tab') key = 'tab';
  else if (k === 'delete') key = 'delete';
  else if (['arrowup','arrowdown','arrowleft','arrowright'].includes(k)) key = k.replace('arrow','');
  else if (/^[a-z0-9]$/.test(k)) key = k;
  if (key) { sendKey(key); e.preventDefault(); }
});

/* ---------------- settings ---------------- */
async function applyQuality() {
  const q = parseInt($('q-range').value, 10);
  try {
    const r = await postJSON('/set_quality', { quality: q });
    $('settings-status').textContent = r.status === 'success' ? ('已应用：' + q + '%') : ('失败：' + (r.message||''));
    $('settings-status').className = 'status-line ok';
  } catch (e) { $('settings-status').textContent = '设置失败'; }
}

/* ---------------- fullscreen ---------------- */
function enterFullscreen() {
  const el = $('screen-wrap');
  if (el.requestFullscreen) el.requestFullscreen();
  else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
}

/* ---------------- modals ---------------- */
function openModal(id) { $(id).classList.add('open'); }
function closeModal(id) { $(id).classList.remove('open'); }
document.querySelectorAll('.overlay').forEach(o => o.addEventListener('click', (e) => { if (e.target === o) o.classList.remove('open'); }));

/* ---------------- cmd terminal ---------------- */
function termAppend(cls, text) {
  const term = $('cmd-term');
  const div = document.createElement('div');
  if (cls) div.className = cls;
  div.textContent = text;
  term.appendChild(div);
  term.scrollTop = term.scrollHeight;
}
async function runCmd() {
  if (!S.cmdEnabled) { termAppend('fail', '✗ 命令执行已被服务端禁用 (--enable-cmd)'); return; }
  const input = $('cmd-input'); const command = input.value.trim();
  if (!command) return;
  termAppend('cmd', '> ' + command);
  input.value = '';
  try {
    const data = await postJSON('/execute_cmd', { command });
    termAppend(data.status === 'success' ? '' : 'fail', (data.output || '(无输出)').replace(/\\n/g, '\n'));
  } catch (err) { termAppend('fail', '✗ 请求失败: ' + err.message); }
}

/* ---------------- files ---------------- */
function fmtSize(n) {
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n/1024).toFixed(1) + ' KB';
  if (n < 1073741824) return (n/1048576).toFixed(1) + ' MB';
  return (n/1073741824).toFixed(2) + ' GB';
}
async function loadFiles() {
  const list = $('file-list'); list.textContent = '';
  try {
    const data = await (await fetch('/list_files?' + authQS())).json();
    if (!data.success) { list.textContent = '加载失败: ' + data.error; return; }
    if (!data.files.length) { list.textContent = '（空）'; return; }
    for (const f of data.files) {
      const row = document.createElement('div'); row.className = 'file-item';
      const name = document.createElement('span'); name.className = 'file-name'; name.textContent = f.name;
      const size = document.createElement('span'); size.className = 'file-size'; size.textContent = fmtSize(f.size);
      const dl = document.createElement('a'); dl.href = '/download_file/' + encodeURIComponent(f.name) + '?' + authQS();
      dl.target = '_blank';
      const dlBtn = document.createElement('button'); dlBtn.className = 'btn small'; dlBtn.textContent = '下载'; dl.appendChild(dlBtn);
      const delBtn = document.createElement('button'); delBtn.className = 'btn small danger'; delBtn.textContent = '删除';
      delBtn.onclick = async () => {
        if (!confirm('删除 ' + f.name + ' ?')) return;
        await fetch('/delete_file/' + encodeURIComponent(f.name) + '?' + authQS(), { method: 'DELETE' });
        loadFiles();
      };
      row.append(name, size, dl, delBtn); list.appendChild(row);
    }
  } catch (e) { list.textContent = '加载失败: ' + e.message; }
}
function uploadFile(file) {
  const fd = new FormData(); fd.append('file', file);
  const prog = $('up-progress'), bar = $('up-bar'), st = $('up-status');
  prog.style.display = 'block'; bar.style.width = '0%';
  st.textContent = '正在上传: ' + file.name + ' (' + fmtSize(file.size) + ')';
  const xhr = new XMLHttpRequest();
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) bar.style.width = (e.loaded/e.total*100).toFixed(1) + '%'; };
  xhr.onload = () => {
    try {
      const r = JSON.parse(xhr.responseText);
      st.textContent = r.success ? ('✓ 上传成功: ' + file.name) : ('✗ 上传失败: ' + r.error);
      st.className = 'status-line ' + (r.success ? 'ok' : 'err');
      if (r.success) loadFiles();
    } catch (e) { st.textContent = '✗ 响应解析失败'; st.className = 'status-line err'; }
    setTimeout(() => { prog.style.display = 'none'; }, 1500);
  };
  xhr.onerror = () => { st.textContent = '✗ 网络错误'; st.className = 'status-line err'; };
  xhr.open('POST', '/upload_file?' + authQS());
  xhr.send(fd);
}
(function wireDropzone() {
  const dz = $('dropzone'), fi = $('file-input');
  dz.onclick = () => fi.click();
  fi.onchange = () => { if (fi.files.length) uploadFile(fi.files[0]); fi.value = ''; };
  ['dragover','dragenter'].forEach(ev => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('dragover'); }));
  ['dragleave','drop'].forEach(ev => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('dragover'); }));
  dz.addEventListener('drop', (e) => { if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]); });
})();

/* ---------------- camera ---------------- */
async function toggleCam() {
  if (!S.camOn) {
    try {
      const r = await postJSON('/toggle_camera', { enabled: true });
      S.camOn = !!r.enabled;
      $('cam-feed').src = '/camera_feed?' + authQS('_=' + Date.now());
      $('cam-feed').style.display = 'block';
      $('cam-toggle').textContent = '关闭摄像头';
      $('cam-status').textContent = '摄像头采集中（仅观看时占用资源）。';
    } catch (e) { $('cam-status').textContent = '开启失败：设备不可用或被占用。'; }
  } else {
    await postJSON('/toggle_camera', { enabled: false }).catch(() => {});
    S.camOn = false; closeCameraFeed();
  }
}
function closeCameraFeed() { $('cam-feed').removeAttribute('src'); $('cam-feed').style.display = 'none'; $('cam-toggle').textContent = '开启摄像头'; }
function closeCamera() { if (S.camOn) { postJSON('/toggle_camera', { enabled: false }).catch(()=>{}); S.camOn = false; } closeCameraFeed(); closeModal('m-cam'); }

/* ---------------- microphone ---------------- */
async function toggleMic() {
  const btn = $('btn-mic');
  if (!S.micOn) {
    try {
      const r = await postJSON('/toggle_microphone', { enabled: true });
      S.micOn = !!r.enabled; btn.classList.add('active'); btn.textContent = '🎤 停止监听';
      pollAudio();
    } catch (e) { setStatus('麦克风开启失败', true); }
  } else {
    await postJSON('/toggle_microphone', { enabled: false }).catch(() => {});
    S.micOn = false; btn.classList.remove('active'); btn.textContent = '🎤 麦克风监听';
  }
}
let audioCtx = null;
async function pollAudio() {
  while (S.micOn) {
    try {
      const r = await fetch('/audio_feed?' + authQS());
      if (!r.ok) break;
      const buf = await r.arrayBuffer();
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
      const int16 = new Int16Array(buf);
      const f32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768;
      const ab = audioCtx.createBuffer(1, f32.length, 16000);
      ab.copyToChannel(f32, 0);
      const src = audioCtx.createBufferSource(); src.buffer = ab; src.connect(audioCtx.destination); src.start();
    } catch (e) { break; }
    await new Promise(res => setTimeout(res, 180));
  }
}

/* ---------------- system ---------------- */
function showSysMenu() { openModal('m-sys'); }
async function sysAction(action) {
  if (!confirm('确定要对被控端执行 ' + action.toUpperCase() + ' 吗？')) return;
  try { await postJSON('/system', { action }); $('sys-status').textContent = '指令已下发: ' + action; }
  catch (e) { $('sys-status').textContent = '执行失败'; }
}

/* ---------------- boot ---------------- */
(async function boot() {
  // password gate: if server has a password and we lack a token, ask once
  try {
    const probe = await fetch('/api_status');
    const j = await probe.json();
    if (j.requires_password && !token()) {
      const p = prompt('此被控端需要访问密码：');
      if (p) { document.cookie = 'rc_token=' + encodeURIComponent(p) + '; path=/'; }
    }
    if (!j.cmd_enabled) $('cmd-disabled-note').style.display = 'block';
    $('resolution-info').textContent = j.resolution || '{{ resolution }}';
  } catch (e) {}
  startStream();
  loadFiles();
})();
</script>
</body>
</html>
'''
