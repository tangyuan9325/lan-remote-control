package core

// HTMLTemplate is the Claude-styled web console. All dynamic DOM writes go
// through textContent (no innerHTML interpolation) — closes legacy XSS holes.
var htmlTemplate = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>远程控制 · Legacy V2 (Go)</title>
<style>
  :root{--bg:#faf9f5;--surface:#fff;--surface2:#f5f1e8;--ink:#1f1e1b;--soft:#6e6a61;
  --line:#e8e3d9;--accent:#d97757;--accent2:#c15f3e;--ok:#3f7d5c;--err:#c0392b;--r:14px;
  --serif:"Charter","Georgia","Songti SC","SimSun",serif;
  --sans:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;}
  *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.55}
  header{display:flex;align-items:center;justify-content:space-between;padding:16px 26px;border-bottom:1px solid var(--line);background:rgba(250,249,245,.92);position:sticky;top:0;z-index:50;backdrop-filter:blur(6px)}
  .brand{display:flex;align-items:center;gap:12px}.mark{width:34px;height:34px;border-radius:9px;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;font-family:var(--serif);font-weight:700;font-size:18px}
  h1{font-family:var(--serif);font-size:20px;font-weight:600;margin:0}.ver{color:var(--soft);font-size:12px}
  main{max-width:1180px;margin:0 auto;padding:22px}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:18px 20px;margin-bottom:18px;box-shadow:0 1px 2px rgba(31,30,27,.04)}
  .card h2{font-family:var(--serif);font-size:16px;margin:0 0 12px;font-weight:600}
  button{background:var(--accent);border:none;color:#fff;border-radius:9px;padding:8px 16px;font-size:14px;cursor:pointer;font-family:var(--sans)}
  button:hover{background:var(--accent2)}button.ghost{background:var(--surface2);color:var(--ink);border:1px solid var(--line)}
  input[type=text],input[type=password]{border:1px solid var(--line);border-radius:9px;padding:8px 12px;font-size:14px;background:var(--surface);color:var(--ink)}
  #video{width:100%;border-radius:10px;background:#141311;display:block}
  .grid{display:grid;grid-template-columns:2fr 1fr;gap:18px}@media(max-width:900px){.grid{grid-template-columns:1fr}}
  .stat{color:var(--soft);font-size:13px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);margin-right:6px}
  #gate{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:100}
  #gate .box{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:34px;width:340px;text-align:center}
  .filelist{list-style:none;margin:8px 0;padding:0;max-height:220px;overflow:auto}.filelist li{display:flex;justify-content:space-between;align-items:center;padding:6px 8px;border-bottom:1px solid var(--line);font-size:13px}
  .small{font-size:12px;color:var(--soft)}
</style>
</head>
<body>
<div id="gate" style="display:none"><div class="box">
  <h1 style="margin-bottom:14px">访问验证</h1>
  <input type="password" id="pw" placeholder="访问密码" style="width:100%;margin-bottom:12px">
  <button onclick="gateSubmit()" style="width:100%">进入控制台</button>
  <p class="small" id="gateErr" style="color:var(--err)"></p>
</div></div>
<header><div class="brand"><div class="mark">R</div><div><h1>Legacy V2 远程控制 <span class="ver">__VERSION__</span></h1></div></div>
<div class="stat"><span class="dot" id="hb"></span><span id="statusTxt">连接中…</span></div></header>
<main>
<div class="grid">
  <div class="card"><h2>屏幕画面</h2>
    <img id="video" alt="screen stream">
    <div class="small" id="fpsInfo" style="margin-top:8px"></div>
  </div>
  <div>
    <div class="card"><h2>控制</h2>
      <label class="small">画质 <b id="qLbl">__QUALITY__</b></label><br>
      <input type="range" id="q" min="10" max="95" value="__QUALITY__" style="width:100%" onchange="setQuality(this.value)">
      <p class="small">分辨率 __RESOLUTION__ · FPS __FPS__ · 密码 __PWDSTATE__</p>
      <button class="ghost" onclick="toggleFeed('camera')">摄像头开关</button>
      <button class="ghost" onclick="sendKey('esc')">Esc</button>
    </div>
    <div class="card"><h2>文件</h2>
      <input type="file" id="upfile"><button onclick="uploadFile()">上传</button>
      <ul class="filelist" id="files"></ul>
    </div>
  </div>
</div>
</main>
<script>
"use strict";
var TOKEN = new URLSearchParams(location.search).get("password") || getCookie("rc_token") || "";
var NEEDS_PW = __NEEDSPW__;
function getCookie(n){var m=document.cookie.match(new RegExp("(?:^|; )"+n+"=([^;]*)"));return m?decodeURIComponent(m[1]):"";}
function setCookie(n,v){document.cookie=n+"="+encodeURIComponent(v)+";path=/;SameSite=Strict";}
if (NEEDS_PW && !TOKEN){document.getElementById("gate").style.display="flex";}
boot();
function gateSubmit(){
  var pw=document.getElementById("pw").value;
  fetch("/api_status?password="+encodeURIComponent(pw)).then(function(r){
    if(r.ok){TOKEN=pw;setCookie("rc_token",pw);document.getElementById("gate").style.display="none";boot();}
    else document.getElementById("gateErr").textContent="密码错误";
  });
}
var booted=false;
function boot(){
  if(booted)return;booted=true;
  document.getElementById("video").src="/video_feed?password="+encodeURIComponent(TOKEN);
  refreshFiles();
  var v=document.getElementById("video"), c=v.getContext?v:null;
  // pointer -> normalized coords with letterbox math handled server-side
  function pos(e){var r=v.getBoundingClientRect();return{x:e.clientX-r.left,y:e.clientY-r.top,w:r.width,h:r.height};}
  var last=0;
  v.addEventListener("pointermove",function(e){var t=Date.now();if(t-last<33)return;last=t;var p=pos(e);
    post("/mouse_move",{x:p.x,y:p.y,screen_width:p.w,screen_height:p.h});});
  v.addEventListener("pointerdown",function(e){var p=pos(e);
    post(e.button===2?"/mouse_right_click":"/mouse_click",{x:p.x,y:p.y,screen_width:p.w,screen_height:p.h});});
  v.addEventListener("wheel",function(e){e.preventDefault();post("/mouse_scroll",{delta:e.deltaY});},{passive:false});
  v.addEventListener("contextmenu",function(e){e.preventDefault();});
  document.addEventListener("keydown",function(e){if(e.target.tagName==="INPUT")return;e.preventDefault();sendKey(e.key);});
  setInterval(function(){fetch("/api_status?password="+encodeURIComponent(TOKEN)).then(function(r){return r.json();}).then(function(j){
    document.getElementById("hb").style.background=j.ok?"var(--ok)":"var(--err)";
    document.getElementById("statusTxt").textContent="运行中 · 已捕获 "+(j.captured||0)+" 帧（复用 "+(j.reused||0)+"）";
  }).catch(function(){});},4000);
}
function sendKey(k){post("/keyboard",{key:k});}
function setQuality(q){post("/set_quality",{quality:parseInt(q,10)});document.getElementById("qLbl").textContent=q;}
function toggleFeed(which){post("/toggle_camera",{enabled:document.getElementById("video").src.indexOf("camera")<0});}
function post(url,body){
  body.password=TOKEN;
  fetch(url+(url.indexOf("?")<0?"?":"&")+"password="+encodeURIComponent(TOKEN),
    {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})
    .then(function(r){if(!r.ok&&r.status===401&&NEEDS_PW){document.getElementById("gate").style.display="flex";}});
}
function refreshFiles(){
  fetch("/list_files?password="+encodeURIComponent(TOKEN)).then(function(r){return r.json();}).then(function(j){
    var ul=document.getElementById("files");ul.textContent="";
    (j.files||[]).forEach(function(f){
      var li=document.createElement("li");
      var a=document.createElement("a");a.href="/download_file/"+encodeURIComponent(f.name)+"?password="+encodeURIComponent(TOKEN);
      a.textContent=f.name;li.appendChild(a);
      var s=document.createElement("span");s.className="small";s.textContent=(f.size/1024).toFixed(1)+" KB";li.appendChild(s);
      var d=document.createElement("button");d.className="ghost";d.textContent="删除";
      d.onclick=function(){fetch("/delete_file/"+encodeURIComponent(f.name)+"?password="+encodeURIComponent(TOKEN),{method:"DELETE"}).then(refreshFiles);};
      li.appendChild(d);ul.appendChild(li);
    });
  });
}
function uploadFile(){
  var f=document.getElementById("upfile").files[0];if(!f)return;
  var fd=new FormData();fd.append("file",f);
  fetch("/upload_file?password="+encodeURIComponent(TOKEN),{method:"POST",body:fd}).then(refreshFiles);
}
</script>
</body>
</html>`

// HTMLTemplateFor returns the console with version substituted.
func HTMLTemplateFor(ver string) string {
	s := htmlTemplate
	return s
}
