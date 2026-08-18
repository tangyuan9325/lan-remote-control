// LAN Remote Control - Frontend Logic
(function () {
  'use strict';

  let bridge = null;
  let autoRefreshTimer = null;
  let pendingDevice = null;
  let lastMousePos = { x: 0, y: 0 };

  // ===== DOM refs =====
  const $ = (id) => document.getElementById(id);
  const deviceView = $('deviceView');
  const remoteView = $('remoteView');
  const deviceList = $('deviceList');
  const screenImage = $('screenImage');
  const screenOverlay = $('screenOverlay');
  const remoteStatus = $('remoteStatus');
  const remoteTitle = $('remoteTitle');

  // ===== Init QWebChannel =====
  function initBridge() {
    if (typeof QWebChannel === 'undefined') {
      console.error('QWebChannel not available');
      return;
    }
    new QWebChannel(qt.webChannelTransport, function (channel) {
      bridge = channel.objects.bridge;

      // Connect signals
      bridge.devicesFound.connect(onDevicesFound);
      bridge.frameReady.connect(onFrame);
      bridge.statusChanged.connect(onStatus);
      bridge.connectionClosed.connect(onDisconnected);

      // Start discovery
      refreshDevices();
      startAutoRefresh();
    });
  }

  // ===== Device discovery =====
  function refreshDevices() {
    if (bridge) bridge.discoverDevices();
  }

  function onDevicesFound(jsonStr) {
    const devices = JSON.parse(jsonStr);
    renderDevices(devices);
  }

  function renderDevices(devices) {
    if (devices.length === 0) {
      deviceList.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">🔍</div>
          <h3>未找到设备</h3>
          <p>确保被控端已启动且在同一局域网，点击刷新重试</p>
        </div>`;
      return;
    }
    deviceList.innerHTML = devices.map(d => `
      <div class="device-card" data-ip="${d.ip}" data-port="${d.port}"
           data-pwd="${d.password_required ? '1' : '0'}" data-name="${d.hostname}">
        <div class="card-header">
          <div class="device-icon">🖥️</div>
          <div>
            <div class="device-name">${escapeHtml(d.hostname)}</div>
            <div class="device-ip">${d.ip}:${d.port}</div>
          </div>
        </div>
        <div class="device-meta">
          <span class="tag">${escapeHtml(d.os || 'Unknown')}</span>
          ${d.password_required ? '<span class="tag lock">🔒 需密码</span>' : ''}
        </div>
        <div class="card-arrow">›</div>
      </div>
    `).join('');

    deviceList.querySelectorAll('.device-card').forEach(card => {
      card.addEventListener('click', () => {
        const ip = card.dataset.ip;
        const port = parseInt(card.dataset.port);
        const needPwd = card.dataset.pwd === '1';
        const name = card.dataset.name;
        if (needPwd) {
          showPasswordModal(name, ip, port);
        } else {
          connectTo(ip, port, '');
        }
      });
    });
  }

  // ===== Auto refresh =====
  function startAutoRefresh() {
    stopAutoRefresh();
    if ($('autoRefresh').checked) {
      autoRefreshTimer = setInterval(refreshDevices, 5000);
    }
  }
  function stopAutoRefresh() {
    if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
  }

  // ===== Connection =====
  function connectTo(ip, port, password) {
    stopAutoRefresh();
    showRemoteView();
    remoteStatus.className = 'status-dot connecting';
    remoteTitle.textContent = `连接中 ${ip}:${port}...`;
    screenOverlay.classList.remove('hidden');
    screenImage.style.display = 'none';
    bridge.connect(ip, port, password);
  }

  function onStatus(text) {
    remoteTitle.textContent = text;
    if (text.startsWith('Connected')) {
      remoteStatus.className = 'status-dot connected';
    } else if (text.startsWith('Error')) {
      remoteStatus.className = 'status-dot error';
    }
  }

  function onFrame(base64) {
    screenImage.src = 'data:image/jpeg;base64,' + base64;
    screenImage.style.display = 'block';
    screenOverlay.classList.add('hidden');
  }

  function onDisconnected() {
    remoteStatus.className = 'status-dot error';
    remoteTitle.textContent = '已断开连接';
  }

  function disconnect() {
    if (bridge) bridge.disconnect();
    showDeviceView();
    startAutoRefresh();
    refreshDevices();
  }

  // ===== View switching =====
  function showRemoteView() {
    deviceView.classList.remove('active');
    remoteView.classList.add('active');
  }
  function showDeviceView() {
    remoteView.classList.remove('active');
    deviceView.classList.add('active');
    screenImage.src = '';
    screenImage.style.display = 'none';
    screenOverlay.classList.remove('hidden');
  }

  // ===== Input forwarding =====
  function getNormalizedPos(e) {
    const rect = screenImage.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    return {
      x: Math.max(0, Math.min(1, x)),
      y: Math.max(0, Math.min(1, y))
    };
  }

  function sendInput(event) {
    if (bridge) bridge.sendInput(JSON.stringify(event));
  }

  screenImage.addEventListener('mousemove', (e) => {
    const pos = getNormalizedPos(e);
    lastMousePos = pos;
    sendInput({ type: 'mouse_move', x: pos.x, y: pos.y });
  });

  screenImage.addEventListener('mousedown', (e) => {
    const pos = getNormalizedPos(e);
    lastMousePos = pos;
    const btn = e.button === 2 ? 'right' : (e.button === 1 ? 'middle' : 'left');
    sendInput({ type: 'mouse_down', x: pos.x, y: pos.y, button: btn });
  });

  screenImage.addEventListener('mouseup', (e) => {
    const pos = getNormalizedPos(e);
    lastMousePos = pos;
    const btn = e.button === 2 ? 'right' : (e.button === 1 ? 'middle' : 'left');
    sendInput({ type: 'mouse_up', x: pos.x, y: pos.y, button: btn });
  });

  screenImage.addEventListener('dblclick', (e) => {
    const pos = getNormalizedPos(e);
    sendInput({ type: 'mouse_double', x: pos.x, y: pos.y, button: 'left' });
  });

  screenImage.addEventListener('contextmenu', (e) => e.preventDefault());

  screenImage.addEventListener('wheel', (e) => {
    e.preventDefault();
    const dy = e.deltaY > 0 ? -1 : 1;
    sendInput({ type: 'mouse_scroll', dx: 0, dy: dy });
  }, { passive: false });

  document.addEventListener('keydown', (e) => {
    if (!remoteView.classList.contains('active')) return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    sendInput({ type: 'key_down', key: normalizeKey(e) });
  });

  document.addEventListener('keyup', (e) => {
    if (!remoteView.classList.contains('active')) return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    sendInput({ type: 'key_up', key: normalizeKey(e) });
  });

  function normalizeKey(e) {
    const map = {
      'Control': 'ctrl', 'Alt': 'alt', 'Shift': 'shift',
      'Meta': 'cmd', 'Enter': 'enter', 'Escape': 'esc',
      ' ': 'space', 'Backspace': 'backspace', 'Delete': 'delete',
      'ArrowUp': 'up', 'ArrowDown': 'down', 'ArrowLeft': 'left', 'ArrowRight': 'right',
      'Home': 'home', 'End': 'end', 'PageUp': 'pageup', 'PageDown': 'pagedown',
      'CapsLock': 'capslock', 'Tab': 'tab'
    };
    if (map[e.key]) return map[e.key];
    if (e.key.length === 1) return e.key.toLowerCase();
    return e.key.toLowerCase();
  }

  // ===== Modals =====
  function showModal(id) { $(id).classList.remove('hidden'); }
  function hideModal(id) { $(id).classList.add('hidden'); }

  function showPasswordModal(name, ip, port) {
    pendingDevice = { ip, port };
    $('pwdDeviceName').textContent = name;
    $('pwdModalInput').value = '';
    showModal('pwdModal');
    setTimeout(() => $('pwdModalInput').focus(), 100);
  }

  // ===== Event bindings =====
  $('refreshBtn').addEventListener('click', refreshDevices);
  $('autoRefresh').addEventListener('change', () => {
    if ($('autoRefresh').checked) startAutoRefresh();
    else stopAutoRefresh();
  });
  $('manualBtn').addEventListener('click', () => {
    $('ipInput').value = '';
    $('portInput').value = '9001';
    $('pwdInput').value = '';
    showModal('modal');
    setTimeout(() => $('ipInput').focus(), 100);
  });
  $('modalCancel').addEventListener('click', () => hideModal('modal'));
  $('modalConnect').addEventListener('click', () => {
    const ip = $('ipInput').value.trim();
    const port = parseInt($('portInput').value) || 9001;
    const pwd = $('pwdInput').value;
    if (!ip) return;
    hideModal('modal');
    connectTo(ip, port, pwd);
  });

  $('backBtn').addEventListener('click', disconnect);
  $('disconnectBtn').addEventListener('click', disconnect);

  $('keyboardBtn').addEventListener('click', () => {
    $('kbInput').value = '';
    showModal('kbModal');
    setTimeout(() => $('kbInput').focus(), 100);
  });
  $('kbCancel').addEventListener('click', () => hideModal('kbModal'));
  $('kbSend').addEventListener('click', () => {
    const text = $('kbInput').value;
    if (text) sendInput({ type: 'key_type', text: text });
    hideModal('kbModal');
  });

  $('pwdCancel').addEventListener('click', () => { pendingDevice = null; hideModal('pwdModal'); });
  $('pwdConfirm').addEventListener('click', () => {
    if (pendingDevice) {
      connectTo(pendingDevice.ip, pendingDevice.port, $('pwdModalInput').value);
    }
    pendingDevice = null;
    hideModal('pwdModal');
  });

  ['ipInput', 'portInput', 'pwdInput'].forEach(id => {
    $(id).addEventListener('keydown', (e) => {
      if (e.key === 'Enter') $('modalConnect').click();
    });
  });
  $('pwdModalInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') $('pwdConfirm').click();
  });

  // ===== Utils =====
  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ===== Boot =====
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBridge);
  } else {
    initBridge();
  }
})();
