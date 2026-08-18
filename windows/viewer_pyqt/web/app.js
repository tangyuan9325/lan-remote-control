// LAN Remote Control - Frontend Logic v1.2
(function () {
  'use strict';
  let bridge = null;
  let autoRefreshTimer = null;
  let pendingDevice = null;
  let currentPath = '';
  let voiceActive = false;
  let downloadSize = 0;
  const $ = (id) => document.getElementById(id);
  const deviceView = $('deviceView');
  const remoteView = $('remoteView');
  const deviceList = $('deviceList');
  const screenImage = $('screenImage');
  const screenOverlay = $('screenOverlay');
  const remoteStatus = $('remoteStatus');
  const remoteTitle = $('remoteTitle');

  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.remove('hidden');
    setTimeout(() => t.classList.add('hidden'), 2500);
  }

  function initBridge() {
    if (typeof QWebChannel === 'undefined') { console.error('QWebChannel not available'); return; }
    new QWebChannel(qt.webChannelTransport, function (channel) {
      bridge = channel.objects.bridge;
      bridge.devicesFound.connect(onDevicesFound);
      bridge.frameReady.connect(onFrame);
      bridge.statusChanged.connect(onStatus);
      bridge.connectionClosed.connect(onDisconnected);
      bridge.fileListReceived.connect(onFileList);
      bridge.fileDownloadStart.connect(onDownloadStart);
      bridge.fileDownloadProgress.connect(onDownloadProgress);
      bridge.fileDownloadComplete.connect(onDownloadComplete);
      bridge.fileUploadDone.connect(onUploadDone);
      bridge.fileError.connect(onFileError);
      bridge.voiceReady.connect(onVoiceReady);
      bridge.voiceError.connect(onVoiceError);
      refreshDevices();
      startAutoRefresh();
    });
  }

  function refreshDevices() { if (bridge) bridge.discoverDevices(); }
  function onDevicesFound(jsonStr) { renderDevices(JSON.parse(jsonStr)); }

  function renderDevices(devices) {
    if (devices.length === 0) {
      deviceList.innerHTML = `<div class="empty-state"><div class="empty-icon">🔍</div><h3>未找到设备</h3><p>确保被控端已启动且在同一局域网</p></div>`;
      return;
    }
    deviceList.innerHTML = devices.map(d => `
      <div class="device-card" data-ip="${d.ip}" data-port="${d.port}"
           data-pwd="${d.password_required ? '1' : '0'}" data-name="${d.hostname}">
        <div class="card-header">
          <div class="device-icon">🖥️</div>
          <div><div class="device-name">${esc(d.hostname)}</div><div class="device-ip">${d.ip}:${d.port}</div></div>
        </div>
        <div class="device-meta">
          <span class="tag">${esc(d.os || 'Unknown')}</span>
          ${d.password_required ? '<span class="tag lock">🔒 需密码</span>' : ''}
        </div>
        <div class="card-arrow">›</div>
      </div>`).join('');
    deviceList.querySelectorAll('.device-card').forEach(card => {
      card.addEventListener('click', () => {
        const ip = card.dataset.ip, port = parseInt(card.dataset.port);
        const needPwd = card.dataset.pwd === '1', name = card.dataset.name;
        needPwd ? showPasswordModal(name, ip, port) : connectTo(ip, port, '');
      });
    });
  }

  function startAutoRefresh() {
    stopAutoRefresh();
    if ($('autoRefresh').checked) autoRefreshTimer = setInterval(refreshDevices, 5000);
  }
  function stopAutoRefresh() {
    if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
  }

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
    if (text.startsWith('Connected')) remoteStatus.className = 'status-dot connected';
    else if (text.startsWith('Error')) remoteStatus.className = 'status-dot error';
  }
  function onFrame(b64) {
    screenImage.src = 'data:image/jpeg;base64,' + b64;
    screenImage.style.display = 'block';
    screenOverlay.classList.add('hidden');
  }
  function onDisconnected() {
    remoteStatus.className = 'status-dot error';
    remoteTitle.textContent = '已断开连接';
    stopVoice();
  }
  function disconnect() {
    stopVoice();
    if (bridge) bridge.disconnect();
    showDeviceView();
    startAutoRefresh();
    refreshDevices();
  }
  function showRemoteView() { deviceView.classList.remove('active'); remoteView.classList.add('active'); }
  function showDeviceView() {
    remoteView.classList.remove('active'); deviceView.classList.add('active');
    screenImage.src = ''; screenImage.style.display = 'none';
    screenOverlay.classList.remove('hidden');
  }

  function normPos(e) {
    const r = screenImage.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))
    };
  }
  function sendInput(ev) { if (bridge) bridge.sendInput(JSON.stringify(ev)); }

  screenImage.addEventListener('mousemove', e => { const p = normPos(e); sendInput({type:'mouse_move',x:p.x,y:p.y}); });
  screenImage.addEventListener('mousedown', e => {
    const p = normPos(e); const b = e.button===2?'right':(e.button===1?'middle':'left');
    sendInput({type:'mouse_down',x:p.x,y:p.y,button:b});
  });
  screenImage.addEventListener('mouseup', e => {
    const p = normPos(e); const b = e.button===2?'right':(e.button===1?'middle':'left');
    sendInput({type:'mouse_up',x:p.x,y:p.y,button:b});
  });
  screenImage.addEventListener('dblclick', e => { const p = normPos(e); sendInput({type:'mouse_double',x:p.x,y:p.y,button:'left'}); });
  screenImage.addEventListener('contextmenu', e => e.preventDefault());
  screenImage.addEventListener('wheel', e => { e.preventDefault(); sendInput({type:'mouse_scroll',dx:0,dy:e.deltaY>0?-1:1}); }, {passive:false});
  document.addEventListener('keydown', e => {
    if (!remoteView.classList.contains('active')) return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    sendInput({type:'key_down',key:normKey(e)});
  });
  document.addEventListener('keyup', e => {
    if (!remoteView.classList.contains('active')) return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    sendInput({type:'key_up',key:normKey(e)});
  });
  function normKey(e) {
    const m = {'Control':'ctrl','Alt':'alt','Shift':'shift','Meta':'cmd','Enter':'enter','Escape':'esc',' ':'space','Backspace':'backspace','Delete':'delete','ArrowUp':'up','ArrowDown':'down','ArrowLeft':'left','ArrowRight':'right','Home':'home','End':'end','PageUp':'pageup','PageDown':'pagedown','CapsLock':'capslock','Tab':'tab'};
    if (m[e.key]) return m[e.key];
    if (e.key.length === 1) return e.key.toLowerCase();
    return e.key.toLowerCase();
  }

  // ===== File Manager =====
  function openFileManager() {
    currentPath = '';
    $('fileModal').classList.remove('hidden');
    loadFileList('');
  }
  function closeFileManager() { $('fileModal').classList.add('hidden'); }
  function loadFileList(path) {
    currentPath = path;
    $('filePath').textContent = path || '/ (根目录)';
    $('fileList').innerHTML = '<div class="empty-state"><p>加载中...</p></div>';
    if (bridge) bridge.listFiles(path);
  }
  function onFileList(jsonStr) {
    const data = JSON.parse(jsonStr);
    const files = data.files || [];
    currentPath = data.path || currentPath;
    $('filePath').textContent = currentPath || '/ (根目录)';
    if (files.length === 0) { $('fileList').innerHTML = '<div class="empty-state"><p>空目录</p></div>'; return; }
    $('fileList').innerHTML = files.map(f => `
      <div class="file-item" data-name="${esc(f.name)}" data-dir="${f.is_dir}" data-path="${esc(currentPath ? currentPath + '/' + f.name : f.name)}">
        <span class="file-icon">${f.is_dir ? '📁' : '📄'}</span>
        <span class="file-name">${esc(f.name)}</span>
        <span class="file-size">${f.is_dir ? '' : formatSize(f.size)}</span>
        <span class="file-date">${f.modified || ''}</span>
      </div>`).join('');
    $('fileList').querySelectorAll('.file-item').forEach(item => {
      item.addEventListener('click', () => {
        if (item.dataset.dir === 'true') { loadFileList(item.dataset.path); }
        else { if (confirm(`下载文件: ${item.dataset.name}?`)) { downloadFile(item.dataset.path); } }
      });
    });
  }
  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes/1024).toFixed(1) + ' KB';
    if (bytes < 1073741824) return (bytes/1048576).toFixed(1) + ' MB';
    return (bytes/1073741824).toFixed(1) + ' GB';
  }
  function downloadFile(path) {
    downloadSize = 0;
    $('fileProgress').classList.remove('hidden');
    $('progressFill').style.width = '0%';
    $('progressText').textContent = '0%';
    if (bridge) bridge.downloadFile(path);
  }
  function onDownloadStart(jsonStr) {
    const data = JSON.parse(jsonStr);
    downloadSize = data.size || 0;
    toast(`开始下载: ${data.name}`);
  }
  function onDownloadProgress(received) {
    if (downloadSize > 0) {
      const pct = Math.min(100, Math.round(received / downloadSize * 100));
      $('progressFill').style.width = pct + '%';
      $('progressText').textContent = pct + '%';
    }
  }
  function onDownloadComplete(jsonStr) {
    const data = JSON.parse(jsonStr);
    $('fileProgress').classList.add('hidden');
    toast(`下载完成: ${data.name} → ${data.local_path}`);
  }
  function onUploadDone(jsonStr) {
    const data = JSON.parse(jsonStr);
    toast(`上传完成: ${data.path}`);
    loadFileList(currentPath);
  }
  function onFileError(err) {
    $('fileProgress').classList.add('hidden');
    toast('错误: ' + err);
  }

  // ===== Voice Chat =====
  function toggleVoice() { voiceActive ? stopVoice() : startVoice(); }
  function startVoice() { if (bridge) bridge.startVoice(); }
  function stopVoice() {
    if (bridge && voiceActive) bridge.stopVoice();
    voiceActive = false;
    $('voiceBtn').classList.remove('voice-active');
    $('voiceIndicator').classList.add('hidden');
  }
  function onVoiceReady() {
    voiceActive = true;
    $('voiceBtn').classList.add('voice-active');
    $('voiceIndicator').classList.remove('hidden');
    toast('语音通话已开启');
  }
  function onVoiceError(err) { toast('语音错误: ' + err); stopVoice(); }

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
  $('autoRefresh').addEventListener('change', () => { $('autoRefresh').checked ? startAutoRefresh() : stopAutoRefresh(); });
  $('manualBtn').addEventListener('click', () => {
    $('ipInput').value = ''; $('portInput').value = '9001'; $('pwdInput').value = '';
    showModal('modal'); setTimeout(() => $('ipInput').focus(), 100);
  });
  $('modalCancel').addEventListener('click', () => hideModal('modal'));
  $('modalConnect').addEventListener('click', () => {
    const ip = $('ipInput').value.trim(), port = parseInt($('portInput').value) || 9001;
    if (!ip) return;
    hideModal('modal'); connectTo(ip, port, $('pwdInput').value);
  });
  $('backBtn').addEventListener('click', disconnect);
  $('disconnectBtn').addEventListener('click', disconnect);
  $('keyboardBtn').addEventListener('click', () => {
    $('kbInput').value = ''; showModal('kbModal'); setTimeout(() => $('kbInput').focus(), 100);
  });
  $('kbCancel').addEventListener('click', () => hideModal('kbModal'));
  $('kbSend').addEventListener('click', () => {
    if ($('kbInput').value) sendInput({type:'key_type',text:$('kbInput').value});
    hideModal('kbModal');
  });
  $('fileBtn').addEventListener('click', openFileManager);
  $('fileClose').addEventListener('click', closeFileManager);
  $('fileUp').addEventListener('click', () => {
    if (currentPath) {
      const parts = currentPath.replace(/\\/g, '/').split('/').filter(Boolean);
      parts.pop();
      loadFileList(parts.join('/') || (navigator.platform.includes('Win') ? '' : '/'));
    }
  });
  $('uploadBtn').addEventListener('click', () => { if (bridge) bridge.uploadFileDialog(); });
  $('voiceBtn').addEventListener('click', toggleVoice);
  $('pwdCancel').addEventListener('click', () => { pendingDevice = null; hideModal('pwdModal'); });
  $('pwdConfirm').addEventListener('click', () => {
    if (pendingDevice) connectTo(pendingDevice.ip, pendingDevice.port, $('pwdModalInput').value);
    pendingDevice = null; hideModal('pwdModal');
  });
  ['ipInput','portInput','pwdInput'].forEach(id => {
    $(id).addEventListener('keydown', e => { if (e.key === 'Enter') $('modalConnect').click(); });
  });
  $('pwdModalInput').addEventListener('keydown', e => { if (e.key === 'Enter') $('pwdConfirm').click(); });

  function esc(str) { const d = document.createElement('div'); d.textContent = str; return d.innerHTML; }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initBridge);
  else initBridge();
})();
