"""管理页的前端：单文件 HTML，原生 JS，不依赖任何 CDN。

服务器在国内、且要能离线打开，所以样式和脚本全部内联，不引外部资源。
"""

PAGE_HTML = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QQ Bot 管理台</title>
<style>
  :root{
    --bg:#12141a; --panel:#1b1e26; --panel2:#22262f; --line:#2e3340;
    --fg:#e6e8ee; --dim:#9aa3b2; --accent:#7aa2f7; --ok:#7bd88f;
    --warn:#e0af68; --err:#f7768e;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
  a{color:var(--accent)}
  .wrap{max-width:1080px;margin:0 auto;padding:18px}
  h1{font-size:19px;margin:0 0 2px}
  .sub{color:var(--dim);font-size:12px;margin-bottom:16px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
  .card .k{color:var(--dim);font-size:12px}
  .card .v{font-size:19px;font-weight:600;margin-top:2px;word-break:break-all}
  .ok{color:var(--ok)} .warn{color:var(--warn)} .err{color:var(--err)} .dim{color:var(--dim)}
  .tabs{display:flex;gap:6px;border-bottom:1px solid var(--line);margin-bottom:14px;flex-wrap:wrap}
  .tab{padding:8px 14px;cursor:pointer;border-radius:8px 8px 0 0;color:var(--dim);font-size:13px}
  .tab.on{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-bottom-color:var(--panel)}
  .panel{display:none} .panel.on{display:block}
  .box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:12px}
  .box h2{font-size:14px;margin:0 0 10px;color:var(--dim);font-weight:600}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
  th{color:var(--dim);font-weight:600;font-size:12px}
  input,textarea,select,button{font:inherit;color:var(--fg);background:var(--panel2);
       border:1px solid var(--line);border-radius:7px;padding:6px 9px}
  input,textarea,select{width:100%}
  textarea{min-height:96px;resize:vertical;font-family:ui-monospace,Consolas,monospace;font-size:12px}
  button{cursor:pointer;background:var(--accent);color:#0d1117;border:none;font-weight:600;padding:7px 14px}
  button.ghost{background:transparent;color:var(--fg);border:1px solid var(--line);font-weight:500}
  button.danger{background:var(--err);color:#1a0d10}
  button:disabled{opacity:.5;cursor:not-allowed}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .grow{flex:1;min-width:120px}
  pre{background:#0d0f14;border:1px solid var(--line);border-radius:8px;padding:10px;
      overflow:auto;max-height:420px;font-size:12px;line-height:1.5;margin:0;white-space:pre-wrap}
  .pill{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;
        border:1px solid var(--line);color:var(--dim)}
  .toast{position:fixed;right:16px;bottom:16px;background:var(--panel);border:1px solid var(--line);
         border-left:3px solid var(--accent);border-radius:8px;padding:10px 14px;display:none;max-width:60vw}
  .login{max-width:340px;margin:14vh auto;text-align:center}
  .login input{margin:10px 0}
  .muted{color:var(--dim);font-size:12px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .grid2 label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--dim)}
  .grid2 label.full{grid-column:1/-1}
  .grid2 input,.grid2 textarea,.grid2 select{width:100%}
  .grid2 textarea{min-height:auto}
</style>
</head>
<body>
<div class="wrap">
  <h1>QQ Bot 管理台</h1>
  <div class="sub" id="sub">加载中…</div>

  <div class="cards" id="cards"></div>

  <div class="tabs">
    <div class="tab on" data-t="overview">概览</div>
    <div class="tab" data-t="store">插件商店</div>
    <div class="tab" data-t="tavern">酒馆</div>
    <div class="tab" data-t="persona">人格</div>
    <div class="tab" data-t="config">配置</div>
    <div class="tab" data-t="logs">日志</div>
  </div>

  <div class="panel on" id="p-overview"><div class="box"><h2>运行中的插件</h2><div id="plugins"></div></div>
    <div class="box"><h2>说明</h2><div class="muted">
      本页只读展示运行状态；改动类操作在「人格」标签。<br>
      重启机器人请用右侧/下方按钮，容器会自动拉起（<code>restart: unless-stopped</code>）。
      <div class="row" style="margin-top:10px"><button class="danger" onclick="restart()">重启机器人</button></div>
    </div></div>
  </div>

  <div class="panel" id="p-store">
    <div class="box"><h2>已安装 <span class="pill" id="inst-count">0</span></h2>
      <div id="installed"></div>
      <div class="muted" style="margin-top:8px">
        插件装在挂载卷里（重建容器不丢）。安装后会自动热加载，失败则需重启。
      </div>
    </div>
    <div class="box">
      <h2>插件商店 <span class="pill" id="store-total">…</span></h2>
      <div class="row" style="margin-bottom:10px">
        <input class="grow" id="q" placeholder="搜索插件名 / 说明 / 作者 / 标签…"
               onkeydown="if(event.key==='Enter')storeSearch(0)">
        <select id="tagsel" style="max-width:150px" onchange="storeSearch(0)">
          <option value="">全部标签</option>
        </select>
        <label class="muted" style="white-space:nowrap">
          <input type="checkbox" id="official" style="width:auto" onchange="storeSearch(0)"> 仅官方
        </label>
        <button onclick="storeSearch(0)">搜索</button>
        <button class="ghost" onclick="refreshStore()">刷新索引</button>
      </div>
      <div id="results"></div>
      <div class="row" style="margin-top:12px">
        <button class="ghost" id="prevbtn" onclick="storeSearch(STORE_PAGE-1)">上一页</button>
        <span class="muted" id="pageinfo"></span>
        <button class="ghost" id="nextbtn" onclick="storeSearch(STORE_PAGE+1)">下一页</button>
      </div>
    </div>
    <div class="box"><h2>安装日志</h2><pre id="store-log">（还没有操作）</pre></div>
  </div>

  <div class="panel" id="p-tavern">
    <div class="box"><h2>角色卡 <span class="pill" id="tav-count">0</span></h2>
      <div id="tav-list"></div>
      <div class="muted" style="margin-top:8px">
        在 QQ 里发送 <code>/酒馆激活 [角色名]</code> 进入角色扮演，
        <code>/酒馆关闭</code> 退出。角色卡存在挂载卷里，重建容器不丢。
      </div>
    </div>
    <div class="box">
      <h2 id="tav-form-title">新建角色卡</h2>
      <div class="grid2">
        <label>角色名 *<input id="tav-name" placeholder="必填，例如：小酒馆老板娘"></label>
        <label>标签（逗号分隔）<input id="tav-tags" placeholder="内置, 日常"></label>
        <label class="full">设定 description
          <textarea id="tav-description" rows="3" placeholder="外貌、身份、背景…"></textarea></label>
        <label class="full">性格 personality
          <textarea id="tav-personality" rows="3" placeholder="说话方式、态度、底线…"></textarea></label>
        <label class="full">场景 scenario
          <textarea id="tav-scenario" rows="2" placeholder="此刻发生了什么"></textarea></label>
        <label class="full">开场白 first_mes
          <textarea id="tav-first-mes" rows="3" placeholder="角色说的第一句话，用 *星号* 表示动作"></textarea></label>
        <label class="full">对话示例 mes_example
          <textarea id="tav-mes-example" rows="4"
            placeholder="&lt;START&gt;&#10;{user}: 你怎么知道我有话要说？&#10;{char}: *她笑了一下。*「七年了。」"></textarea></label>
        <label class="full">自定义系统提示词 system_prompt（留空用默认）
          <textarea id="tav-system-prompt" rows="2" placeholder="留空即可"></textarea></label>
        <label class="full">备注 creator_notes
          <input id="tav-notes" placeholder="给自己看的说明，不影响扮演"></label>
      </div>
      <div class="row" style="margin-top:10px">
        <button onclick="saveCard()">保存角色卡</button>
        <button class="ghost" onclick="clearCardForm()">清空表单</button>
      </div>
    </div>
    <div class="box">
      <h2>世界书 <span class="pill" id="world-count">0</span></h2>
      <div class="muted" style="margin-bottom:8px">
        <b>关键词命中才会注入</b>到对话里。写长设定不会爆 token，也不会让人设跑偏。
        例：关键词填「酒馆, 老板娘」，只有聊到这些词时这条设定才生效。
      </div>
      <div id="world-list"></div>
      <div class="grid2" style="margin-top:12px">
        <label>条目名 *<input id="w-name" placeholder="例如：夜航船"></label>
        <label>关键词 *（逗号分隔）<input id="w-keys" placeholder="夜航船, 酒馆, 老板娘"></label>
        <label class="full">设定内容 *
          <textarea id="w-content" rows="4" placeholder="命中关键词时要注入的事实性设定…"></textarea></label>
      </div>
      <div class="row" style="margin-top:10px">
        <button onclick="saveWorld()">保存条目</button>
        <button class="ghost" onclick="clearWorldForm()">清空表单</button>
      </div>
    </div>

    <div class="box">
      <h2>导入 SillyTavern 世界书</h2>
      <div class="muted" style="margin-bottom:8px">
        粘贴 World Info 的 JSON。<code>entries</code> 是字典（ST 原生）或数组都支持；
        会自动识别 <code>keys</code>、<code>content</code>、<code>comment</code>（条目名）、
        <code>disable</code>（是否停用）。
      </div>
      <textarea id="world-import" rows="5" style="width:100%"
        placeholder='{"entries":{"0":{"keys":["罗德岛"],"content":"…","comment":"罗德岛"}}}'></textarea>
      <div class="row" style="margin-top:8px">
        <button onclick="importWorld()">追加导入</button>
        <button class="ghost" onclick="importWorld(true)">清空后导入</button>
        <input type="file" id="world-file" accept=".json,application/json" style="width:auto"
          onchange="importWorldFile(this)">
      </div>
    </div>

    <div class="box">
      <h2>导入 SillyTavern 角色卡</h2>
      <div class="muted" style="margin-bottom:8px">
        把 ST 的 JSON 卡内容粘进来即可，单张或多张（数组）都行。
        兼容 <code>chara_card_v2</code> 和 V1 平铺格式。
      </div>
      <textarea id="tav-import" rows="5" placeholder='{"spec":"chara_card_v2","data":{"name":"…"}}'
        style="width:100%"></textarea>
      <div class="row" style="margin-top:8px">
        <button onclick="importCards()">导入</button>
        <input type="file" id="tav-file" accept=".json,application/json" style="width:auto"
          onchange="importFile(this)">
      </div>
    </div>
    <div class="box"><h2>操作日志</h2><pre id="tav-log">（还没有操作）</pre></div>
  </div>

  <div class="panel" id="p-persona">
    <div class="box"><h2>人格列表（改动即时生效，无需重启）</h2>
      <div id="personas"></div>
      <div class="row" style="margin-top:10px">
        <button onclick="savePersonas()">保存人格文件</button>
        <button class="ghost" onclick="loadPersonas()">放弃修改</button>
        <span class="muted" id="persona-msg"></span>
      </div>
    </div>
    <div class="box"><h2>各会话当前人格</h2>
      <div id="sessions"></div>
      <div class="muted" style="margin-top:8px">
        会话键格式：<code>group:群号</code> 或 <code>private:QQ号</code>。留空表示跟随默认人格。
      </div>
    </div>
  </div>

  <div class="panel" id="p-config">
    <div class="box"><h2>配置（密钥已脱敏）</h2><div id="config"></div></div>
    <div class="box"><h2>连通性测试</h2>
      <div class="row"><button onclick="testLlm()">测 AI 接口</button>
        <button class="ghost" onclick="testSearch()">测联网搜索</button>
        <span id="test-msg" class="muted"></span></div>
    </div>
  </div>

  <div class="panel" id="p-logs">
    <div class="box"><h2>最近日志 <span class="pill" id="log-count">0</span></h2>
      <div class="row" style="margin-bottom:8px">
        <button class="ghost" onclick="loadLogs(true)">刷新</button>
        <label class="muted"><input type="checkbox" id="autolog" checked style="width:auto"> 自动刷新</label>
      </div>
      <pre id="logs">加载中…</pre>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
let PERSONAS = {};

function toast(msg, bad) {
  const el = $("#toast");
  el.textContent = msg;
  el.style.borderLeftColor = bad ? "var(--err)" : "var(--accent)";
  el.style.display = "block";
  clearTimeout(el._t);
  el._t = setTimeout(() => el.style.display = "none", 3200);
}

async function api(path, opts) {
  const r = await fetch("/admin/api" + path, Object.assign({credentials: "same-origin"}, opts || {}));
  if (r.status === 401) { location.href = "/admin"; throw new Error("未登录"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.message || ("HTTP " + r.status));
  return j;
}

document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("on"));
  document.querySelectorAll(".panel").forEach(x => x.classList.remove("on"));
  t.classList.add("on");
  $("#p-" + t.dataset.t).classList.add("on");
  if (t.dataset.t === "logs") loadLogs();
  if (t.dataset.t === "store") { loadInstalled(); storeSearch(0); }
  if (t.dataset.t === "tavern") loadTavern();
  if (t.dataset.t === "persona") loadPersonas();
  if (t.dataset.t === "config") loadConfig();
});

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

async function loadStatus() {
  try {
    const d = await api("/status");
    $("#sub").textContent = d.name + " · 启动于 " + d.started_at + " · 已运行 " + d.uptime;
    const cards = [
      ["QQ 连接", d.bots.length ? d.bots.join(", ") : "未连接", d.bots.length ? "ok" : "err"],
      ["运行时长", d.uptime, ""],
      ["内存占用", d.memory, ""],
      ["已加载插件", d.plugins.length, ""],
      ["人格数量", d.persona_count, ""],
      ["搜索后端", d.search_provider + (d.search_ready ? "" : "（未就绪）"), d.search_ready ? "ok" : "warn"],
      ["AI 接口", d.llm_ready ? "已配置" : "未配置", d.llm_ready ? "ok" : "err"],
    ];
    $("#cards").innerHTML = cards.map(c =>
      `<div class="card"><div class="k">${esc(c[0])}</div>
       <div class="v ${c[2]}">${esc(c[1])}</div></div>`).join("");
    $("#plugins").innerHTML = "<table><tr><th>插件</th><th>描述</th></tr>" +
      d.plugin_detail.map(p => `<tr><td>${esc(p.name)}</td><td class="dim">${esc(p.description)}</td></tr>`).join("") +
      "</table>";
  } catch (e) { $("#sub").textContent = "状态获取失败：" + e.message; }
}

async function loadPersonas() {
  try {
    const d = await api("/personas");
    PERSONAS = d.personas;
    const names = Object.keys(PERSONAS);
    $("#personas").innerHTML = names.map(n => {
      const p = PERSONAS[n];
      return `<div style="border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:10px">
        <div class="row">
          <input class="grow" value="${esc(n)}" oninput="renamePersona('${esc(n)}', this.value)"
                 style="max-width:180px" title="人格名">
          <input class="grow" value="${esc(p.description || "")}" placeholder="说明（/人格 列表里显示）"
                 oninput="PERSONAS['${esc(n)}'].description=this.value">
          <input value="${p.temperature == null ? "" : p.temperature}" placeholder="温度"
                 style="max-width:80px" oninput="PERSONAS['${esc(n)}'].temperature=this.value===''?null:parseFloat(this.value)">
          <button class="ghost danger" onclick="delPersona('${esc(n)}')">删除</button>
        </div>
        <textarea style="margin-top:8px" oninput="PERSONAS['${esc(n)}'].prompt=this.value"
          placeholder="系统提示词（必填）">${esc(p.prompt || "")}</textarea>
      </div>`;
    }).join("") +
    `<button class="ghost" onclick="addPersona()">+ 新增人格</button>`;

    const sessions = d.sessions || {};
    const keys = Object.keys(sessions);
    $("#sessions").innerHTML = keys.length ? "<table><tr><th>会话</th><th>当前人格</th><th></th></tr>" +
      keys.map(k => `<tr><td><code>${esc(k)}</code></td>
        <td><select onchange="setSession('${esc(k)}', this.value)">
          <option value="">（跟随默认）</option>
          ${names.map(n => `<option value="${esc(n)}" ${sessions[k] === n ? "selected" : ""}>${esc(n)}</option>`).join("")}
        </select></td>
        <td><button class="ghost" onclick="setSession('${esc(k)}','')">清除</button></td></tr>`).join("") +
      "</table>" : '<div class="muted">还没有任何会话切换过人格——群里发一次 /人格 就会出现在这里。</div>';
  } catch (e) { toast("加载人格失败：" + e.message, true); }
}

function renamePersona(oldName, newName) {
  if (!newName || oldName === newName) return;
  PERSONAS[newName] = PERSONAS[oldName];
  delete PERSONAS[oldName];
  toast("改名后请点保存");
}
function addPersona() {
  let n = "新人格", i = 1;
  while (PERSONAS[n]) n = "新人格" + (++i);
  PERSONAS[n] = {description: "", prompt: "你是……", temperature: null};
  loadPersonas();
}
function delPersona(n) {
  if (!confirm("确定删除人格「" + n + "」？")) return;
  delete PERSONAS[n];
  loadPersonas();
}

async function savePersonas() {
  const clean = {};
  for (const [k, v] of Object.entries(PERSONAS)) {
    if (!k.trim() || !v.prompt || !v.prompt.trim()) continue;
    const item = {prompt: v.prompt};
    if (v.description) item.description = v.description;
    if (v.temperature != null && !isNaN(v.temperature)) item.temperature = v.temperature;
    clean[k.trim()] = item;
  }
  try {
    const d = await api("/personas", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({personas: clean})
    });
    $("#persona-msg").textContent = d.message;
    toast(d.message);
    setTimeout(loadStatus, 400);
  } catch (e) { toast("保存失败：" + e.message, true); }
}

async function setSession(key, name) {
  try {
    await api("/session", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session: key, persona: name})
    });
    toast(name ? (key + " → " + name) : (key + " 已清除"));
  } catch (e) { toast("失败：" + e.message, true); }
}

async function loadConfig() {
  try {
    const d = await api("/config");
    $("#config").innerHTML = "<table>" + d.items.map(i =>
      `<tr><td class="dim" style="width:190px">${esc(i.k)}</td><td><code>${esc(i.v)}</code></td></tr>`).join("") +
      "</table>";
  } catch (e) { toast("加载配置失败：" + e.message, true); }
}

async function testLlm() {
  $("#test-msg").textContent = "测试中…";
  try { const d = await api("/test/llm", {method: "POST"});
    $("#test-msg").innerHTML = d.ok ? `<span class="ok">✅ ${esc(d.message)}</span>` : `<span class="err">❌ ${esc(d.message)}</span>`;
  } catch (e) { $("#test-msg").innerHTML = `<span class="err">❌ ${esc(e.message)}</span>`; }
}
async function testSearch() {
  $("#test-msg").textContent = "测试中…";
  try { const d = await api("/test/search", {method: "POST"});
    $("#test-msg").innerHTML = d.ok ? `<span class="ok">✅ ${esc(d.message)}</span>` : `<span class="err">❌ ${esc(d.message)}</span>`;
  } catch (e) { $("#test-msg").innerHTML = `<span class="err">❌ ${esc(e.message)}</span>`; }
}

async function loadLogs(manual) {
  if (!manual && !$("#autolog").checked) return;
  try {
    const d = await api("/logs?lines=300");
    const pre = $("#logs");
    const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
    pre.textContent = d.lines.join("\\n");
    $("#log-count").textContent = d.total;
    if (atBottom) pre.scrollTop = pre.scrollHeight;
  } catch (e) { /* 静默 */ }
}

async function restart() {
  if (!confirm("确定重启机器人？约 10 秒内恢复，期间不响应消息。")) return;
  try { await api("/restart", {method: "POST"}); } catch (e) {}
  toast("已发出重启指令，页面将在 15 秒后刷新");
  setTimeout(() => location.reload(), 15000);
}

// ------------------------------------------------ 插件商店
let STORE_PAGE = 0;
let STORE_LAST = 0;
let INSTALLED = [];

function storeLog(msg) {
  const el = $("#store-log");
  const t = new Date().toLocaleTimeString();
  el.textContent = "[" + t + "] " + msg + "\\n" + el.textContent;
}

async function loadInstalled() {
  try {
    const d = await api("/store/installed");
    INSTALLED = d.items.map(i => i.module_name);
    $("#inst-count").textContent = d.items.length;
    $("#installed").innerHTML = d.items.length ? "<table><tr><th>插件</th><th>说明</th><th>状态</th><th></th></tr>" +
      d.items.map(i => `<tr>
        <td><b>${esc(i.name)}</b><div class="muted">${esc(i.module_name)}</div></td>
        <td class="dim">${esc(i.desc).slice(0, 70)}</td>
        <td>${i.files_present ? '<span class="pill ok">已安装</span>' : '<span class="pill warn">文件缺失</span>'}</td>
        <td><button class="ghost" onclick="uninstallPlugin('${esc(i.module_name)}','${esc(i.name)}')">卸载</button></td>
      </tr>`).join("") + "</table>"
      : '<div class="muted">还没有装任何第三方插件。</div>';
  } catch (e) { toast("读取已装列表失败：" + e.message, true); }
}

async function storeSearch(page) {
  STORE_PAGE = Math.max(0, page || 0);
  const q = encodeURIComponent($("#q").value.trim());
  const tag = encodeURIComponent($("#tagsel").value);
  const off = $("#official").checked ? "true" : "false";
  $("#results").innerHTML = '<div class="muted">加载中…（首次会拉取约 900 条索引，约 1-2 秒）</div>';
  try {
    const d = await api(`/store?q=${q}&tag=${tag}&official=${off}&page=${STORE_PAGE}`);
    STORE_LAST = Math.max(0, Math.ceil(d.total / d.size) - 1);
    $("#store-total").textContent = d.total + " / " + d.total_registry;
    $("#pageinfo").textContent = `第 ${d.page + 1} / ${STORE_LAST + 1} 页，共 ${d.total} 个`;

    if (!$("#tagsel").options.length) {
      $("#tagsel").innerHTML = '<option value="">全部标签</option>' +
        d.tags.map(t => `<option>${esc(t)}</option>`).join("");
    }

    if (!d.items.length) {
      $("#results").innerHTML = '<div class="muted">没有匹配的插件。</div>';
    } else {
      $("#results").innerHTML = d.items.map(p => {
        const on = INSTALLED.includes(p.module_name);
        return `<div style="border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:8px">
          <div class="row">
            <div class="grow">
              <b>${esc(p.name)}</b>
              ${p.is_official ? '<span class="pill ok">官方</span>' : ''}
              ${!p.valid ? '<span class="pill err">校验未通过</span>' : ''}
              <div class="dim">${esc(p.desc).slice(0, 110)}</div>
              <div class="muted">${esc(p.author)} · v${esc(p.version)} · <code>${esc(p.project_link)}</code></div>
              <div class="muted">${(p.tags || []).map(t => `<span class="pill">${esc(t)}</span>`).join(" ")}</div>
            </div>
            <div style="text-align:right;min-width:120px">
              <button ${on ? "disabled" : ""} onclick="installPlugin('${esc(p.module_name)}','${esc(p.project_link)}','${esc(p.name)}')">
                ${on ? "已安装" : "安装"}</button>
              ${p.homepage ? `<div style="margin-top:6px"><a class="muted" href="${esc(p.homepage)}" target="_blank">项目主页↗</a></div>` : ""}
            </div>
          </div>
        </div>`;
      }).join("");
    }
    $("#prevbtn").disabled = STORE_PAGE <= 0;
    $("#nextbtn").disabled = STORE_PAGE >= STORE_LAST;
  } catch (e) {
    $("#results").innerHTML = `<div class="err">加载失败：${esc(e.message)}</div>`;
  }
}

async function installPlugin(module, project, name) {
  if (!confirm(`安装「${name}」？\\n\\n会从 PyPI 下载该插件及其依赖到挂载卷，可能需要几十秒。`)) return;
  storeLog("开始安装 " + name + "（" + project + "）…");
  toast("安装中，请看下方「安装日志」");
  try {
    const d = await api("/store/install", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({module_name: module, project_link: project})
    });
    storeLog((d.ok ? "✅ " : "❌ ") + name + "：" + d.message);
    toast(d.ok ? `「${name}」安装成功` : `安装失败：${d.message}`, !d.ok);
    if (d.ok && d.needs_restart) storeLog("⚠️ 该插件需要重启机器人才能生效（去「概览」点重启）");
    await loadInstalled();
    await storeSearch(STORE_PAGE);
  } catch (e) {
    storeLog("❌ " + name + "：" + e.message);
    toast("安装失败：" + e.message, true);
  }
}

async function uninstallPlugin(module, name) {
  if (!confirm(`卸载「${name}」？\\n\\n会从加载列表移除并删除已下载的文件。`)) return;
  storeLog("卸载 " + name + "（" + module + "）…");
  try {
    const d = await api("/store/uninstall", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({module_name: module, delete_files: true})
    });
    storeLog("✅ " + d.message);
    toast(d.message);
    if (d.needs_restart) storeLog("⚠️ 需要重启才能彻底移除（去「概览」点重启）");
    await loadInstalled();
    await storeSearch(STORE_PAGE);
  } catch (e) {
    storeLog("❌ " + e.message);
    toast("卸载失败：" + e.message, true);
  }
}

async function refreshStore() {
  storeLog("强制刷新插件索引…");
  try {
    const d = await api("/store/refresh", {method: "POST"});
    storeLog((d.ok ? "✅ " : "❌ ") + d.message);
    toast(d.message, !d.ok);
    await storeSearch(0);
  } catch (e) { storeLog("❌ " + e.message); }
}

// ------------------------------------------------ 酒馆
let TAV_CARDS = [];

function tavLog(msg) {
  const el = $("#tav-log");
  el.textContent = "[" + new Date().toLocaleTimeString() + "] " + msg + "\\n" + el.textContent;
}

async function loadTavern() {
  try {
    const d = await api("/tavern");
    TAV_CARDS = d.cards;
    renderWorld(d.world || []);
    $("#tav-count").textContent = d.cards.length;
    if (!d.cards.length) {
      $("#tav-list").innerHTML = '<div class="muted">还没有角色卡，用下面的表单建一张。</div>';
    } else {
      $("#tav-list").innerHTML = "<table><tr><th>角色名</th><th>设定</th><th>标签</th><th></th></tr>" +
        d.cards.map(c => `<tr>
          <td><b>${esc(c.key)}</b></td>
          <td class="dim">${esc((c.description || "").slice(0, 80))}</td>
          <td>${(c.tags || []).map(t => `<span class="pill">${esc(t)}</span>`).join(" ")}</td>
          <td style="white-space:nowrap">
            <button class="ghost" onclick="editCard('${esc(c.key)}')">编辑</button>
            <button class="ghost" onclick="deleteCard('${esc(c.key)}')">删除</button>
          </td>
        </tr>`).join("") + "</table>";
      const active = Object.entries(d.active || {});
      if (active.length) {
        $("#tav-list").innerHTML += '<div class="muted" style="margin-top:8px">正在扮演中：' +
          active.map(([k, v]) => `${esc(k)} → ${esc(v)}`).join("；") + "</div>";
      }
    }
  } catch (e) { toast("读取角色卡失败：" + e.message, true); }
}

const TAV_FIELDS = ["name","tags","description","personality","scenario",
                    "first_mes","mes_example","system_prompt","creator_notes"];
const TAV_IDS = {name:"#tav-name",tags:"#tav-tags",description:"#tav-description",
  personality:"#tav-personality",scenario:"#tav-scenario",first_mes:"#tav-first-mes",
  mes_example:"#tav-mes-example",system_prompt:"#tav-system-prompt",creator_notes:"#tav-notes"};

function clearCardForm() {
  TAV_FIELDS.forEach(f => $(TAV_IDS[f]).value = "");
  $("#tav-form-title").textContent = "新建角色卡";
  $("#tav-name").dataset.original = "";
}

function editCard(key) {
  const c = TAV_CARDS.find(x => x.key === key);
  if (!c) return;
  TAV_FIELDS.forEach(f => {
    const v = f === "tags" ? (c.tags || []).join(", ") : (c[f] || "");
    $(TAV_IDS[f]).value = v;
  });
  $("#tav-form-title").textContent = "编辑角色卡：" + key;
  $("#tav-name").dataset.original = key;
  $("#tav-name").scrollIntoView({behavior: "smooth", block: "center"});
}

async function saveCard() {
  const name = $("#tav-name").value.trim();
  if (!name) { toast("角色名必填", true); return; }
  const card = {};
  TAV_FIELDS.forEach(f => {
    const v = $(TAV_IDS[f]).value.trim();
    card[f] = f === "tags" ? v.split(",").map(s => s.trim()).filter(Boolean) : v;
  });
  const original = $("#tav-name").dataset.original;
  try {
    const d = await api("/tavern/card", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({card})
    });
    tavLog(d.message);
    // 改了名字等于新建一张，把旧的那张删掉
    if (original && original !== name) {
      await api("/tavern/delete", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({name: original})
      });
      tavLog("已删除旧卡「" + original + "」");
    }
    toast(d.message);
    clearCardForm();
    await loadTavern();
  } catch (e) { toast("保存失败：" + e.message, true); tavLog("❌ " + e.message); }
}

async function deleteCard(key) {
  if (!confirm(`删除角色卡「${key}」？`)) return;
  try {
    const d = await api("/tavern/delete", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name: key})
    });
    tavLog(d.message);
    toast(d.message);
    await loadTavern();
  } catch (e) { toast("删除失败：" + e.message, true); }
}

async function importCards(text) {
  const raw = (text !== undefined ? text : $("#tav-import").value).trim();
  if (!raw) { toast("请先粘贴 JSON", true); return; }
  let parsed;
  try { parsed = JSON.parse(raw); }
  catch (e) { toast("JSON 解析失败：" + e.message, true); return; }

  // 单张卡 → 包成数组
  const items = Array.isArray(parsed) ? parsed
    : (Array.isArray(parsed.cards) ? parsed.cards : [parsed]);
  try {
    const d = await api("/tavern/import", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({cards: items})
    });
    tavLog(d.message);
    toast(d.message, !d.ok);
    $("#tav-import").value = "";
    await loadTavern();
  } catch (e) { toast("导入失败：" + e.message, true); tavLog("❌ " + e.message); }
}

function importFile(input) {
  const f = input.files && input.files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => { importCards(String(reader.result)); input.value = ""; };
  reader.onerror = () => toast("读取文件失败", true);
  reader.readAsText(f, "utf-8");
}

// ------------------------------------------------ 世界书
let WORLD = [];

function renderWorld(items) {
  WORLD = items;
  $("#world-count").textContent = items.length;
  if (!items.length) {
    $("#world-list").innerHTML = '<div class="muted">还没有世界设定。用下面的表单加一条。</div>';
    return;
  }
  $("#world-list").innerHTML = "<table><tr><th>条目名</th><th>关键词</th><th>内容</th><th></th></tr>" +
    items.map(e => `<tr>
      <td><b>${esc(e.name)}</b>${e.enabled === false ? ' <span class="pill">已停用</span>' : ''}</td>
      <td>${(e.keys || []).map(k => `<span class="pill">${esc(k)}</span>`).join(" ")}</td>
      <td class="dim">${esc((e.content || "").slice(0, 60))}</td>
      <td style="white-space:nowrap">
        <button class="ghost" onclick="editWorld('${esc(e.name)}')">编辑</button>
        <button class="ghost" onclick="deleteWorld('${esc(e.name)}')">删除</button>
      </td>
    </tr>`).join("") + "</table>";
}

function clearWorldForm() {
  ["#w-name", "#w-keys", "#w-content"].forEach(s => $(s).value = "");
  $("#w-name").dataset.original = "";
}

function editWorld(name) {
  const e = WORLD.find(x => x.name === name);
  if (!e) return;
  $("#w-name").value = e.name;
  $("#w-keys").value = (e.keys || []).join(", ");
  $("#w-content").value = e.content || "";
  $("#w-name").dataset.original = name;
  $("#w-name").scrollIntoView({behavior: "smooth", block: "center"});
}

async function saveWorld() {
  const name = $("#w-name").value.trim();
  const keys = $("#w-keys").value.trim();
  const content = $("#w-content").value.trim();
  if (!name || !keys || !content) { toast("条目名、关键词、内容都要填", true); return; }
  const original = $("#w-name").dataset.original;
  try {
    const d = await api("/tavern/world", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name, keys, content, enabled: true})
    });
    if (original && original !== name) {
      await api("/tavern/world/delete", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({name: original})
      });
    }
    toast(d.message);
    tavLog(d.message);
    clearWorldForm();
    await loadTavern();
  } catch (e) { toast("保存失败：" + e.message, true); }
}

async function deleteWorld(name) {
  if (!confirm(`删除世界设定「${name}」？`)) return;
  try {
    const d = await api("/tavern/world/delete", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name})
    });
    toast(d.message); tavLog(d.message);
    await loadTavern();
  } catch (e) { toast("删除失败：" + e.message, true); }
}

async function importWorld(text, replace) {
  const raw = (text !== undefined ? text : $("#world-import").value).trim();
  if (!raw) { toast("请先粘贴 JSON", true); return; }
  let parsed;
  try { parsed = JSON.parse(raw); }
  catch (e) { toast("JSON 解析失败：" + e.message, true); return; }
  try {
    const d = await api("/tavern/world/import", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({world: parsed, replace: !!replace})
    });
    toast(d.message); tavLog(d.message);
    $("#world-import").value = "";
    await loadTavern();
  } catch (e) { toast("导入失败：" + e.message, true); tavLog("❌ " + e.message); }
}

function importWorldFile(input) {
  const f = input.files && input.files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => { importWorld(String(reader.result), false); input.value = ""; };
  reader.onerror = () => toast("读取文件失败", true);
  reader.readAsText(f, "utf-8");
}

loadStatus();
setInterval(loadStatus, 5000);
setInterval(() => { if ($("#p-logs").classList.contains("on")) loadLogs(); }, 3000);
</script>
</body>
</html>
"""

LOGIN_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · QQ Bot 管理台</title>
<style>
 body{margin:0;background:#12141a;color:#e6e8ee;font:14px/1.6 -apple-system,"Microsoft YaHei",sans-serif}
 .login{max-width:340px;margin:14vh auto;text-align:center;padding:0 16px}
 h1{font-size:18px;margin-bottom:4px}
 input{width:100%;box-sizing:border-box;margin:12px 0;padding:9px 11px;border-radius:8px;
       border:1px solid #2e3340;background:#22262f;color:#e6e8ee;font:inherit}
 button{width:100%;padding:9px;border:none;border-radius:8px;background:#7aa2f7;color:#0d1117;
        font:inherit;font-weight:600;cursor:pointer}
 .dim{color:#9aa3b2;font-size:12px}
 .err{color:#f7768e;font-size:13px;min-height:20px}
</style></head><body>
<div class="login">
  <h1>QQ Bot 管理台</h1>
  <div class="dim">请输入管理 Token</div>
  <input id="tk" type="password" placeholder="Token" autofocus>
  <button onclick="go()">登录</button>
  <div class="err" id="err"></div>
  <div class="dim" style="margin-top:18px">
    Token 取 <code>WEBADMIN_TOKEN</code>，未设置时回落到 <code>ONEBOT_ACCESS_TOKEN</code>。<br>
    服务器上执行：<code>grep WEBADMIN_TOKEN ~/bot/.env</code>
  </div>
</div>
<script>
async function go() {
  const token = document.getElementById("tk").value.trim();
  if (!token) return;
  const r = await fetch("/admin/api/login", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({token}), credentials: "same-origin"
  });
  if (r.ok) { location.href = "/admin"; }
  else { document.getElementById("err").textContent = "Token 不正确"; }
}
document.getElementById("tk").addEventListener("keydown", e => { if (e.key === "Enter") go(); });
</script>
</body></html>
"""
