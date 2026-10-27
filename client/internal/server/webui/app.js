'use strict';

/* ---------- 全局状态 ---------- */
const state = {
  devices: [],
  sessions: {},   // serial -> { ws, pageEl, canvas, ctx, statusEl, tabEl, iw, ih, ... }
  active: null,   // 当前激活 serial
};

/* ---------- 工具 ---------- */
function $(id) { return document.getElementById(id); }
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}
function postJSON(path, body) {
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json()).catch(() => ({}));
}

/* ---------- 日志 ---------- */
async function refreshLogs() {
  try {
    const d = await api('/api/logs');
    const all = (d.logs || []).join('\n');
    const box = $('logBox');
    if (box.textContent !== all) {
      box.textContent = all;
      box.scrollTop = box.scrollHeight;
    }
  } catch (e) { /* ignore */ }
}
$('btnClearLog').onclick = () => { $('logBox').textContent = ''; };

/* ---------- 顶部状态 ---------- */
async function refreshToolStatus() {
  try {
    const s = await api('/api/status');
    $('ver').textContent = 'v' + s.version;
    $('toolStatus').textContent =
      (s.adb ? 'adb: 就绪' : 'adb: 未就绪') + '  ·  ' +
      (s.ffmpeg ? 'ffmpeg: 可用' : 'ffmpeg: 未找到(PNG模式)');
  } catch (e) {
    $('toolStatus').textContent = '控制台连接失败';
  }
}

/* ---------- 设备列表 ---------- */
function renderDevices() {
  const list = $('devList');
  list.innerHTML = '';
  state.devices.forEach(d => {
    const li = el('li');
    if (d.serial === state.active) li.classList.add('selected');
    const name = el('div', 'dev-name');
    const dot = el('span', 'dot');
    if (d.state === 'device') dot.classList.add('on');
    else if (d.state === 'unauthorized') dot.classList.add('warn');
    else dot.classList.add('off');
    name.appendChild(dot);
    name.appendChild(document.createTextNode(d.model || d.serial));
    const meta = el('div', 'dev-meta');
    const st = { device: '在线', offline: '离线', unauthorized: '未授权(请在手机确认)' }[d.state] || d.state;
    meta.textContent = `${d.serial}  ${st}  ${d.usb ? 'USB' : d.ip}`;
    li.appendChild(name);
    li.appendChild(meta);
    li.onclick = () => switchTab(d.serial);
    list.appendChild(li);
  });
}

/* ---------- 标签页 ---------- */
function switchTab(serial) {
  const sess = state.sessions[serial];
  if (!sess) return;
  state.active = serial;
  renderDevices();
  document.querySelectorAll('.tab-item').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-page').forEach(t => t.classList.add('tab-hidden'));
  sess.tabEl.classList.add('active');
  sess.pageEl.classList.remove('tab-hidden');
  // 只为当前可见标签页建立视频连接：多设备时避免同时解码 N 路 20fps 视频
  Object.keys(state.sessions).forEach(s => {
    if (s !== serial) closeWS(state.sessions[s]);
  });
  ensureWS(sess);
}

function openTab(serial, model) {
  if (state.sessions[serial]) return;
  const sess = {
    serial, model, ws: null, wsTimer: null, closing: false,
    iw: 0, ih: 0, dragging: false, dragX: 0, dragY: 0,
    pending: null, drawing: false,
    onMove: null, onUp: null,
  };
  // tab bar
  const tabEl = el('div', 'tab-item');
  const label = el('span', null, model + ' · ' + short(serial));
  const close = el('span', 'close', '✕');
  close.onclick = (e) => { e.stopPropagation(); closeTab(serial); };
  tabEl.appendChild(label);
  tabEl.appendChild(close);
  tabEl.onclick = () => switchTab(serial);

  // page
  const pageEl = el('div', 'tab-page tab-hidden');
  const toolbar = el('div', 'dev-toolbar');
  const btns = [
    ['Home', 'HOME'], ['返回', 'BACK'], ['最近任务', 'RECENTS'], ['菜单', 'MENU'],
    ['电源', 'POWER'], ['音量+', 'VOLUME_UP'], ['音量-', 'VOLUME_DOWN'],
    ['唤醒', 'WAKE'], ['截图', 'SHOT'],
  ];
  btns.forEach(([text, key]) => {
    const b = el('button', null, text);
    b.onclick = () => {
      if (key === 'SHOT') screenshot(serial);
      else if (key === 'WAKE') send(serial, { cmd: 'wake' });
      else send(serial, { cmd: 'input', type: 'key', key });
    };
    toolbar.appendChild(b);
  });
  toolbar.appendChild(el('label', null, '视频:'));
  const videoSel = el('select');
  [['auto', '自动'], ['png', 'PNG截图'], ['record', '录屏+ffmpeg'], ['agent', 'Agent串流']]
    .forEach(([v, t]) => { const o = el('option'); o.value = v; o.textContent = t; videoSel.appendChild(o); });
  videoSel.onchange = () => send(serial, { cmd: 'video', mode: videoSel.value });
  toolbar.appendChild(videoSel);
  toolbar.appendChild(el('label', null, '输入:'));
  const inputSel = el('select');
  [['shell', 'Shell'], ['agent', 'Agent手势']]
    .forEach(([v, t]) => { const o = el('option'); o.value = v; o.textContent = t; inputSel.appendChild(o); });
  inputSel.onchange = () => send(serial, { cmd: 'inputmode', mode: inputSel.value });
  toolbar.appendChild(inputSel);

  const wrap = el('div', 'canvas-wrap');
  const canvas = document.createElement('canvas');
  canvas.width = 8; canvas.height = 8;
  const ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, 8, 8);
  wrap.appendChild(canvas);
  pageEl.appendChild(toolbar);
  pageEl.appendChild(wrap);

  // 控制行
  const ctrl = el('div', 'ctrl-row');
  const textInput = el('input');
  textInput.type = 'text';
  textInput.placeholder = '输入文本（仅支持 ASCII，中文请用手机输入法）';
  textInput.onkeydown = (e) => { if (e.key === 'Enter') sendText(); };
  const sendBtn = el('button', null, '发送');
  sendBtn.onclick = sendText;
  const statusEl = el('div', 'dev-status', '连接中…');
  ctrl.appendChild(textInput);
  ctrl.appendChild(sendBtn);
  ctrl.appendChild(statusEl);
  pageEl.appendChild(ctrl);

  function sendText() {
    const t = textInput.value;
    if (!t) return;
    send(serial, { cmd: 'input', type: 'text', text: t });
    textInput.value = '';
  }

  // 鼠标输入
  canvas.addEventListener('mousedown', (e) => {
    const p = toImagePos(canvas, e);
    if (!p) return;
    sess.dragging = true;
    sess.dragX = p.x; sess.dragY = p.y;
  });
  // 监听器挂在 window 上，关闭标签页时必须摘掉，否则一直累积
  sess.onMove = (e) => {
    if (!sess.dragging) return;
    const p = toImagePos(canvas, e);
    if (p) { sess.moveX = p.x; sess.moveY = p.y; }
  };
  sess.onUp = (e) => {
    if (!sess.dragging) return;
    sess.dragging = false;
    const p = toImagePos(canvas, e);
    if (!p) return;
    const dx = p.x - sess.dragX, dy = p.y - sess.dragY;
    if (Math.abs(dx) + Math.abs(dy) < 12) {
      send(serial, { cmd: 'input', type: 'tap', x: p.x, y: p.y });
    } else {
      const dur = Math.abs(dx) + Math.abs(dy) > 400 ? 500 : 250;
      send(serial, { cmd: 'input', type: 'swipe', x1: sess.dragX, y1: sess.dragY, x2: p.x, y2: p.y, duration: dur });
    }
  };
  window.addEventListener('mousemove', sess.onMove);
  window.addEventListener('mouseup', sess.onUp);

  sess.tabEl = tabEl; sess.pageEl = pageEl; sess.canvas = canvas; sess.ctx = ctx;
  sess.statusEl = statusEl; sess.videoSel = videoSel; sess.inputSel = inputSel;
  state.sessions[serial] = sess;
  $('tabs').appendChild(tabEl);
  document.body.appendChild(pageEl); // page 挂到 body 尾部（避免样式冲突）
  switchTab(serial);
}

function closeTab(serial) {
  const sess = state.sessions[serial];
  if (!sess) return;
  closeWS(sess);
  if (sess.onMove) window.removeEventListener('mousemove', sess.onMove);
  if (sess.onUp) window.removeEventListener('mouseup', sess.onUp);
  sess.tabEl.remove();
  sess.pageEl.remove();
  delete state.sessions[serial];
  if (state.active === serial) state.active = null;
  renderDevices();
}

function short(s) {
  if (s.length <= 14) return s;
  return s.slice(0, 7) + '…' + s.slice(-7);
}

/* 把鼠标事件换算成设备像素坐标（以 canvas 实际绘制尺寸为基准） */
function toImagePos(canvas, ev) {
  const cw = canvas.width, ch = canvas.height;
  if (!cw || !ch) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const scale = Math.min(rect.width / cw, rect.height / ch);
  if (!isFinite(scale) || scale <= 0) return null;
  const dw = cw * scale, dh = ch * scale;
  const ox = (rect.width - dw) / 2, oy = (rect.height - dh) / 2;
  const x = (ev.clientX - rect.left - ox) / scale;
  const y = (ev.clientY - rect.top - oy) / scale;
  if (x < 0 || y < 0 || x > cw || y > ch) return null;
  return { x: Math.round(x), y: Math.round(y) };
}

/* ---------- WebSocket ---------- */
function ensureWS(sess) {
  if (sess.ws && (sess.ws.readyState === WebSocket.OPEN || sess.ws.readyState === WebSocket.CONNECTING)) return;
  connectWS(sess);
}

function closeWS(sess) {
  if (sess.wsTimer) { clearTimeout(sess.wsTimer); sess.wsTimer = null; }
  if (!sess.ws) return;
  const ws = sess.ws;
  sess.ws = null;
  sess.closing = true;
  try { ws.close(); } catch (e) { /* ignore */ }
  sess.closing = false;
}

function connectWS(sess) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws/device/${encodeURIComponent(sess.serial)}`);
  sess.ws = ws;
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    if (document.hidden) { ws.close(); return; }
    sess.statusEl.textContent = '已连接';
  };

  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') {
      handleState(sess, ev.data);
      return;
    }
    const buf = new Uint8Array(ev.data);
    if (buf[0] !== 0x01) return;
    // 只保留最新一帧：解码跟不上时丢掉旧帧，保证画面延迟不累积
    sess.pending = buf.subarray(1);
    if (!sess.drawing) drawLoop(sess);
  };

  ws.onclose = () => {
    if (sess.closing) return;
    sess.ws = null;
    if (state.active !== sess.serial || document.hidden) return;
    sess.statusEl.textContent = '连接断开，正在重连…';
    sess.wsTimer = setTimeout(() => ensureWS(sess), 1500);
  };
  ws.onerror = () => { /* onclose 里统一处理 */ };
}

function handleState(sess, text) {
  let st;
  try { st = JSON.parse(text.replace(/^\u0002/, '')); } catch (e) { return; }
  const res = st.width && st.height ? `${st.width}x${st.height}` : `${sess.iw}x${sess.ih}`;
  sess.statusEl.textContent =
    `${sess.model} · Android ${st.androidVer || '?'} · ${res} · 方向${st.orientation}° · ` +
    `电池${st.battery >= 0 ? st.battery + '%' : '--'}${st.charging ? '⚡' : ''} · ` +
    `视频:${modeName(st.videoMode)} · 输入:${st.inputMode === 'agent' ? 'Agent手势' : 'Shell'} · ` +
    `${st.fps} fps · ${st.viewers || 1} 路`;
  if (st.videoMode && sess.videoSel.value !== st.videoMode && st.videoMode !== 'auto') {
    sess.videoSel.value = st.videoMode;
  }
  if (st.inputMode && sess.inputSel.value !== st.inputMode) sess.inputSel.value = st.inputMode;
}

// 串行绘制：同一时刻最多一帧在解码，天然形成背压
async function drawLoop(sess) {
  if (sess.drawing) return;
  sess.drawing = true;
  while (sess.pending) {
    const data = sess.pending;
    sess.pending = null;
    try {
      let source;
      if (window.createImageBitmap) {
        // 在解码线程解码，避免逐帧创建 Blob URL / Image 造成的 GC 抖动
        source = await createImageBitmap(new Blob([data], { type: 'image/jpeg' }));
        paint(sess, source, source.width, source.height);
        source.close();
      } else {
        const url = URL.createObjectURL(new Blob([data], { type: 'image/jpeg' }));
        const img = new Image();
        await new Promise((ok, bad) => { img.onload = ok; img.onerror = bad; img.src = url; });
        paint(sess, img, img.width, img.height);
        URL.revokeObjectURL(url);
      }
    } catch (e) { /* 坏帧直接丢弃 */ }
  }
  sess.drawing = false;
}

function paint(sess, source, w, h) {
  if (sess.canvas.width !== w || sess.canvas.height !== h) {
    sess.canvas.width = w;
    sess.canvas.height = h;
  }
  sess.iw = w; sess.ih = h;
  sess.ctx.drawImage(source, 0, 0);
}

function send(serial, msg) {
  const sess = state.sessions[serial];
  if (!sess || !sess.ws || sess.ws.readyState !== WebSocket.OPEN) return;
  sess.ws.send(JSON.stringify(msg));
}

function modeName(m) {
  return { auto: '自动', png: 'PNG截图', record: '录屏', agent: 'Agent串流' }[m] || m || '?';
}

/* 页面切到后台时停掉视频，回来再连：避免最小化后仍在解码 */
document.addEventListener('visibilitychange', () => {
  const sess = state.sessions[state.active];
  if (!sess) return;
  if (document.hidden) closeWS(sess);
  else ensureWS(sess);
});

/* ---------- 设备轮询 ---------- */
async function refreshDevices() {
  let d;
  try { d = await api('/api/devices'); } catch (e) { return; }
  const devs = d.devices || [];
  const online = new Set(devs.filter(x => x.state === 'device').map(x => x.serial));
  devs.forEach(x => {
    if (x.state === 'device' && !state.sessions[x.serial]) openTab(x.serial, x.model || x.serial);
  });
  Object.keys(state.sessions).forEach(s => {
    if (!online.has(s)) closeTab(s);
  });
  state.devices = devs;
  renderDevices();
}

/* ---------- 截图 ---------- */
function screenshot(serial) {
  const a = document.createElement('a');
  a.href = `/api/device/${encodeURIComponent(serial)}/screenshot`;
  a.download = `${serial.replace(/[\\/.:]/g, '_')}.png`;
  a.click();
}

/* ---------- 模态框 ---------- */
let modalCb = null;
function showModal(title, fields, onOk) {
  $('modalTitle').textContent = title;
  const body = $('modalBody');
  body.innerHTML = '';
  const inputs = {};
  fields.forEach(f => {
    const wrap = el('div', 'field');
    wrap.appendChild(el('label', null, f.label));
    const inp = el('input');
    inp.type = f.type || 'text';
    inp.placeholder = f.placeholder || '';
    if (f.value) inp.value = f.value;
    inputs[f.key] = inp;
    wrap.appendChild(inp);
    body.appendChild(wrap);
  });
  modalCb = () => {
    const out = {};
    Object.keys(inputs).forEach(k => { out[k] = inputs[k].value.trim(); });
    onOk(out);
  };
  $('modalMask').classList.remove('hidden');
}
$('modalCancel').onclick = () => { $('modalMask').classList.add('hidden'); modalCb = null; };
$('modalOk').onclick = () => {
  $('modalMask').classList.add('hidden');
  if (modalCb) { const cb = modalCb; modalCb = null; cb(); }
};

/* ---------- 按钮 ---------- */
$('btnRefresh').onclick = refreshDevices;
$('btnConnect').onclick = () => showModal('连接设备（无线调试）',
  [{ key: 'host', label: 'IP:端口', placeholder: '192.168.1.100:5555' }],
  (v) => { if (v.host) postJSON('/api/connect', { host: v.host }); });
$('btnPair').onclick = () => showModal('无线调试配对（Android 11+）',
  [
    { key: 'host', label: '配对地址', placeholder: '192.168.1.100:37000' },
    { key: 'code', label: '配对码', placeholder: '6 位配对码' },
  ],
  (v) => { if (v.host && v.code) postJSON('/api/pair', { host: v.host, code: v.code }); });
$('btnDisconnect').onclick = () => postJSON('/api/disconnect', { host: '*' });
$('btnSettings').onclick = () => showModal('设置',
  [
    { key: 'adb_path', label: 'adb.exe 路径', placeholder: '留空自动查找', value: '' },
    { key: 'ffmpeg_path', label: 'ffmpeg.exe 路径（可选）', placeholder: '留空自动查找', value: '' },
  ],
  (v) => postJSON('/api/settings', v));

/* ---------- 启动 ---------- */
refreshToolStatus();
refreshDevices();
refreshLogs();
setInterval(refreshDevices, 3000);
setInterval(refreshToolStatus, 3000);
setInterval(refreshLogs, 2000);
