/* ============================================================
 * 门诊排队 · 智能导诊前端
 * - 优先调用真实后端 /api/v1/chat；后端不可用时降级为本地模拟
 * - 会话历史：后端 SQLite 优先；离线/mock 模式保存到 localStorage
 * - 所有用户输入都用 textContent / 转义后渲染，避免 XSS
 * ============================================================ */

const IS_FILE = location.protocol === "file:";
const DEFAULT_API_BASE = IS_FILE ? "http://127.0.0.1:8000" : location.origin;
let API_BASE = DEFAULT_API_BASE;
let API = API_BASE + "/api/v1/chat";
let HISTORY_API = API_BASE + "/api/v1/history";
let backendOnline = false;
let lastBackendFailure = 0;
let reconnectTimer = null;
let reconnectToastShown = false;
let healthPromise = null;

function normalizeBase(value) {
  return String(value || "").replace(/\/+$/, "");
}

function setApiBase(base) {
  const next = normalizeBase(base);
  if (!next || next === API_BASE) return;
  API_BASE = next;
  API = API_BASE + "/api/v1/chat";
  HISTORY_API = API_BASE + "/api/v1/history";
  try { localStorage.setItem("triage.apiBase", API_BASE); } catch (_) { /* ignore */ }
}

try {
  const savedBase = localStorage.getItem("triage.apiBase");
  if (savedBase) setApiBase(savedBase);
} catch (_) { /* ignore */ }
const HISTORY_KEY = "triage.history.v1";
const SAVED_LOCATION_KEY = "triage.savedLocation.v1";
const MAX_LOCAL_HISTORY = 30;
let memorySavedLocation = null;

let sessionId = newId();
let mode = "detect";          // detect | backend | mock
let chatFinished = false;     // 本轮是否已出报告/急症拦截
let lastReport = null;
let selectedHospital = null;  // 医院服务页当前医院
let lastRegistrationInfo = null;
let currentNavigation = null;
let navActiveFloor = "";
let lastUserText = "";
let conversation = [];        // 当前会话的展示消息（本地历史用）
let historyCache = [];
let historySource = "backend"; // backend | local
let currentHistory = null;
let sending = false;
let reportTimer = null;
let memoryHistory = [];       // localStorage 不可用时的本次页面兜底
let storageWritable = true;    // 是否成功写入过 localStorage
let confirmResolver = null;    // 自定义确认框的 Promise resolver

const $ = (id) => document.getElementById(id);

/* ---------- 小工具 ---------- */
function newId() {
  return (crypto && crypto.randomUUID) ? crypto.randomUUID() : "s-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function showToast(text, ms = 2200) {
  const el = $("toast");
  if (!el) return;
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => el.classList.remove("show"), ms);
}

function askConfirm(title, text, okText = "删除") {
  return new Promise(resolve => {
    confirmResolver = resolve;
    $("toast").classList.remove("show");
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = text;
    $("confirmOk").textContent = okText;
    $("confirmModal").classList.add("show");
  });
}

function resolveConfirm(ok) {
  $("confirmModal").classList.remove("show");
  const resolver = confirmResolver;
  confirmResolver = null;
  if (resolver) resolver(Boolean(ok));
}

function setMode(next, _detail = "") {
  // 顶部状态栏已按设计移除；保留 mode 供请求路由/历史回退判断
  mode = next;
}

function formatTime(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const diff = Date.now() - d.getTime();
  if (diff >= 0 && diff < 60 * 1000) return "刚刚";
  if (diff >= 0 && diff < 60 * 60 * 1000) return Math.floor(diff / 60000) + " 分钟前";
  const now = new Date();
  const sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
  if (sameDay) return "今天 " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  return (d.getMonth() + 1) + "月" + d.getDate() + "日";
}

function titleOf(item) {
  return (item && item.title) || "未命名会话";
}

/* ---------- 视图切换 ---------- */
function show(viewId) {
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  const view = $(viewId);
  if (view) view.classList.add("active");
  $("navbar").style.display = "flex";

  const isChat = viewId === "view-chat";
  const isEmergency = viewId === "view-emergency";
  const isHistory = viewId === "view-history";

  $("navBack").style.display = (isEmergency || isHistory) ? "inline-flex" : "none";
  $("navHome").style.display = isChat ? "inline-flex" : "none";
  $("navHistory").style.display = (isChat || viewId === "view-start") ? "inline-flex" : "none";
  $("navClear").style.display = isHistory ? "inline-block" : "none";
  $("navTitle").textContent = isEmergency ? "急症提示" : isHistory ? "历史记录" : "智能导诊";

  if (isChat) {
    $("msgInput").focus();
  }
}

function goChat() {
  if (chatFinished) newSession();
  show("view-chat");
  if ($("chatList").children.length === 0) {
    addBubble("ai", "你好，我是 AI 智能导诊助手。请详细描述您的症状，我会先判断是否需要急症拦截，再为您推荐合适的科室。");
  }
  $("msgInput").focus();
}

function goHome(finished = false) {
  closeReport();
  closeHistoryDetail();
  closeHospitalModal();
  if (finished || chatFinished) newSession();
  show("view-start");
  loadHistoryItems(true);
}

function newSession() {
  sessionId = newId();
  chatFinished = false;
  conversation = [];
  lastReport = null;
  lastUserText = "";
  clearTimeout(reportTimer);
  resetState();
  $("chatList").innerHTML = "";
  $("msgInput").value = "";
  $("msgInput").disabled = false;
  $("sendBtn").disabled = false;
  closeReport();
}

function backFromHistory() {
  if ($("view-emergency").classList.contains("active")) {
    backToChat();
  } else {
    goHome(false);
  }
}

function backToChat() {
  // 急症拦截后“返回修改症状”：开新会话，避免旧状态污染新一轮分诊
  newSession();
  show("view-chat");
  addBubble("ai", "已开始新的对话。请重新描述您想咨询的症状；如果仍有急症表现，请立即就医或拨打 120。");
  $("msgInput").focus();
}

/* ---------- 对话气泡 ---------- */
function addBubble(role, text) {
  const list = $("chatList");
  if (!list) return null;
  const row = document.createElement("div");
  row.className = "msg-row " + role;
  const avatar = document.createElement("div");
  avatar.className = "avatar " + role;
  avatar.textContent = role === "ai" ? "AI" : "👤";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(avatar);
  row.appendChild(bubble);
  list.appendChild(row);
  list.scrollTop = list.scrollHeight;
  return row;
}

function addTyping() {
  const list = $("chatList");
  if (!list) return;
  removeTyping();
  const row = document.createElement("div");
  row.className = "msg-row ai";
  row.id = "typingRow";
  row.innerHTML = '<div class="avatar ai">AI</div><div class="bubble"><span class="typing"><span></span><span></span><span></span></span></div>';
  list.appendChild(row);
  list.scrollTop = list.scrollHeight;
}

function removeTyping() {
  const row = $("typingRow");
  if (row) row.remove();
}

function addError(message, retryText) {
  const list = $("chatList");
  if (!list) return;
  const row = document.createElement("div");
  row.className = "msg-row ai";
  const avatar = document.createElement("div");
  avatar.className = "avatar ai";
  avatar.textContent = "!";
  const box = document.createElement("div");
  box.className = "bubble";
  const err = document.createElement("div");
  err.className = "error-box";
  err.textContent = message;
  if (retryText) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "重试";
    btn.onclick = () => { err.remove(); sendMsg(retryText); };
    err.appendChild(document.createElement("br"));
    err.appendChild(btn);
  }
  box.appendChild(err);
  row.appendChild(avatar);
  row.appendChild(box);
  list.appendChild(row);
  list.scrollTop = list.scrollHeight;
}

/* ---------- 发送 ---------- */
async function sendMsg(retryText) {
  if (sending) return;
  const isRetry = typeof retryText === "string";
  const text = (isRetry ? retryText : $("msgInput").value).trim();
  if (!text) { showToast("请先描述症状"); return; }
  if (text.length > 4000) { showToast("最多输入 4000 个字符"); return; }
  if (isRetry && !conversation.some(m => m.role === "user" && m.content === text)) {
    // 异常重试时保持用户消息只出现一次
    addBubble("user", text);
    conversation.push({ role: "user", content: text });
  } else if (!isRetry) {
    addBubble("user", text);
    conversation.push({ role: "user", content: text });
    $("msgInput").value = "";
  }
  lastUserText = text;
  sending = true;
  $("sendBtn").disabled = true;
  $("msgInput").disabled = true;
  addTyping();
  try {
    const resp = await dispatch(text);
    removeTyping();
    handleResponse(resp);
  } catch (err) {
    removeTyping();
    const detail = err && err.status ? `（HTTP ${err.status}）` : "";
    addError("服务暂时不可用" + detail + "，请稍后重试，或检查后端是否启动。", text);
    setMode("mock", "本地演示模式（服务暂不可用）");
  } finally {
    sending = false;
    $("sendBtn").disabled = false;
    $("msgInput").disabled = false;
    $("msgInput").focus();
  }
}

async function fetchJSON(url, options = {}, timeout = 15000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    // 历史记录必须读最新数据：禁用浏览器缓存，避免刚生成的报告在列表里还是“进行中”
    const resp = await fetch(url, { cache: "no-store", ...options, signal: controller.signal });
    if (resp.status === 204) return null;
    if (!resp.ok) {
      let detail = "";
      try {
        const body = await resp.json();
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || "");
      } catch (_) { /* ignore */ }
      const err = new Error(detail || ("HTTP " + resp.status));
      err.status = resp.status;
      throw err;
    }
    return await resp.json();
  } finally {
    clearTimeout(timer);
  }
}

async function dispatch(text) {
  // 即使当前是 mock，也允许在失败 3 秒后再试一次后端；
  // 这样后端后来启动时，不必刷新页面也能自动连上。
  const canTryBackend = mode !== "mock" || Date.now() - lastBackendFailure > 3000;
  if (canTryBackend) {
    const wasMock = mode === "mock";
    try {
      const resp = await fetchJSON(API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text }),
      }, mode === "backend" ? 15000 : 4000);
      backendOnline = true;
      lastBackendFailure = 0;
      stopReconnect();
      setMode("backend");
      if (wasMock) showToast("已连接后端服务", 1800);
      return resp;
    } catch (err) {
      if (err && err.status === 422) {
        throw new Error("输入不合法，请检查后重试");
      }
      const wasBackend = mode === "backend";
      lastBackendFailure = Date.now();
      setMode("mock");
      startReconnect();
      // 只在“原本在线、突然断开”时提示；本来就是离线不重复打扰
      if (wasBackend) showToast("服务暂不可用，已切换本地演示模式");
    }
  }
  return mockDispatch(text);
}

/* ---------- 响应处理 ---------- */
function handleResponse(resp) {
  if (!resp || !resp.type) {
    addError("服务返回格式异常，请稍后重试。", lastUserText);
    return;
  }

  if (resp.type === "urgent") {
    const msg = resp.message || "检测到急症风险，请立即就医或拨打 120。";
    conversation.push({ role: "assistant", content: msg });
    chatFinished = true;
    setMode(mode === "backend" ? "backend" : "mock", mode === "backend" ? "在线服务 · 急症拦截" : "本地演示模式（记录保存在本机）");
    saveLocalConversation("interrupted", resp.data || null);
    refreshHistorySoon();
    show("view-emergency");
    return;
  }

  if (resp.type === "question") {
    const msg = resp.message || "请补充更多信息。";
    addBubble("ai", msg);
    conversation.push({ role: "assistant", content: msg });
    setMode(mode === "backend" ? "backend" : "mock", mode === "backend" ? "在线服务 · 多轮追问" : "本地演示模式（记录保存在本机）");
    saveLocalConversation("collecting", null);
    refreshHistorySoon();
    return;
  }

  if (resp.type === "result") {
    const d = resp.data || {};
    chatFinished = true;
    lastReport = d;
    const dept = d.recommended_department || "综合门诊";
    const msg = "已为您生成导诊报告：" + dept + "。请查看下方报告卡片。";
    addBubble("ai", msg);
    conversation.push({ role: "assistant", content: msg });
    if (mode === "backend") {
      if (d.degraded) {
        setMode("backend", "在线 · 模型降级");
      } else if (d.llm_provider === "openai") {
        setMode("backend", "在线 · 真实模型" + (d.llm_model ? " · " + d.llm_model : ""));
      } else {
        setMode("backend", "在线 · 稳定模式");
      }
    } else {
      // 后端不可用时 dispatch 已切到 mock，不能在这里误改回 backend
      setMode("mock", "本地演示模式");
    }
    setReportMode(d);
    saveLocalConversation("ready", d);
    refreshHistorySoon();
    if (mode === "backend") {
      showToast("已保存到历史记录，可在首页“最近咨询”或历史记录中查看", 3000);
    } else {
      showToast(
        storageWritable
          ? "已保存到历史记录，可在首页“最近咨询”或历史记录中查看"
          : "浏览器限制了本地存储，历史仅在本次页面打开期间可见",
        3000
      );
    }
    clearTimeout(reportTimer);
    reportTimer = setTimeout(() => showReport(d), 260);
    return;
  }

  addError("暂不支持该响应类型，请稍后重试。", lastUserText);
}

/* ---------- 报告 ---------- */
function setReportMode(d) {
  const ai = $("rAiMode");
  if (!ai) return;
  if (d && d.llm_provider === "openai" && !d.degraded) {
    ai.textContent = "真实模型" + (d.llm_model ? " · " + d.llm_model : "");
    ai.style.color = "#1f6f5c";
    ai.style.background = "#eaf5ef";
  } else if (d && d.degraded) {
    ai.textContent = "模型超时 · 已降级";
    ai.style.color = "#8a5a00";
    ai.style.background = "#fff3d6";
  } else {
    ai.textContent = "本地稳定模式";
    ai.style.color = "#6b7280";
    ai.style.background = "#f1f3f5";
  }
}

function showReport(d) {
  $("toast").classList.remove("show");
  lastReport = d || {};
  $("rDept").textContent = lastReport.recommended_department || "综合门诊";
  const alts = (lastReport.alternative_departments || []).join("、");
  $("rAlt").textContent = alts ? "备选科室：" + alts : "";

  const basis = (lastReport.basis && lastReport.basis.length) ? lastReport.basis : ["根据您描述的症状初步判断"];
  $("rBasisList").innerHTML = basis.map(b => "<li>" + escapeHtml(b) + "</li>").join("");

  $("rScript").textContent = lastReport.dialogue_script || "医生您好，我最近身体不适，想请您看看。";
  const reminders = (lastReport.reminders && lastReport.reminders.length) ? lastReport.reminders : ["建议携带既往病历和检查报告", "如症状加重请及时就医"];
  $("rReminders").innerHTML = reminders.map(r => "<li>" + escapeHtml(r) + "</li>").join("");
  $("rDisclaimer").textContent = (lastReport.disclaimer || "本产品仅供分诊参考，不替代医生诊断。") + " 急重症请立即拨打 120。";
  setReportMode(lastReport);
  $("reportOverlay").classList.add("show");
}

function closeReport() {
  $("reportOverlay").classList.remove("show");
}

function copyScript() {
  const script = (lastReport && lastReport.dialogue_script)
    ? lastReport.dialogue_script
    : "医生您好，我最近身体不适，想挂相关科室看看。";
  const done = (ok) => {
    // 直接改按钮文字，不弹遮罩/浮层，不挡科室，也不挡其他按钮
    const btn = $("copyScriptBtn");
    if (!btn) return;
    const original = btn.dataset.originalText || btn.textContent;
    btn.dataset.originalText = original;
    btn.textContent = ok ? "已复制 ✓" : "复制失败";
    btn.disabled = true;
    setTimeout(() => {
      btn.textContent = original;
      btn.disabled = false;
    }, 1600);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(script).then(() => done(true)).catch(() => done(fallbackCopy(script)));
  } else {
    done(fallbackCopy(script));
  }
}

function fallbackCopy(text) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch (_) {
    return false;
  }
}

/* ---------- 医院推荐（定位 + 就近排序 + 地图链接） ---------- */
// 与后端 hospitals 表保持一致的离线兜底数据；坐标是 GCJ-02 近似值，仅用于演示排序。
const HOSPITAL_FALLBACK = [
  { name: "福建医科大学附属协和医院", level: "三甲", address: "福州市鼓楼区新权路29号", latitude: 26.0748, longitude: 119.3009, registration_channel: "114预约挂号平台/医院公众号/医院官网" },
  { name: "福建医科大学附属第一医院", level: "三甲", address: "福州市台江区茶亭中路20号", latitude: 26.0663, longitude: 119.2962, registration_channel: "114预约挂号平台/医院公众号" },
  { name: "福建省立医院", level: "三甲", address: "福州市鼓楼区东街134号", latitude: 26.0813, longitude: 119.2986, registration_channel: "114预约挂号平台/医院公众号" },
  { name: "福州市第一医院", level: "三甲", address: "福州市台江区达道路190号", latitude: 26.0575, longitude: 119.3074, registration_channel: "114预约挂号平台/医院公众号" },
  { name: "福建省人民医院", level: "三甲", address: "福州市台江区八一七中路602号", latitude: 26.0587, longitude: 119.2926, registration_channel: "114预约挂号平台/医院公众号" },
  { name: "福建省妇幼保健院", level: "三甲", address: "福州市鼓楼区道山路18号", latitude: 26.0784, longitude: 119.2952, registration_channel: "医院公众号/医院官网" },
  { name: "福建医科大学附属协和医院(旗山院区)", level: "三甲", address: "福州市闽侯县上街镇学府南路6号", latitude: 26.047071, longitude: 119.19115, registration_channel: "114预约挂号平台/医院公众号" },
  { name: "福建中医药大学附属第三人民医院", level: "三甲", address: "福州市闽侯县上街镇国宾大道363号", latitude: 26.075751, longitude: 119.185216, registration_channel: "114预约挂号平台/医院公众号" },
  { name: "中国人民解放军联勤保障部队第九〇〇医院仓山院区", level: "三甲", address: "福州市仓山区洪山桥郭厝里90号", latitude: 26.073252, longitude: 119.246017, registration_channel: "医院公众号" },
  { name: "福州大学附属省立医院(金山院区)", level: "三甲", address: "福州市仓山区金山街道金榕南路516号", latitude: 26.039761, longitude: 119.27061, registration_channel: "114预约挂号平台/医院公众号" },
];

// 离线也能用的常见地点。坐标来自高德 POI（GCJ-02），用于单文件/后端不可用时兜底。
const LOCAL_LOCATIONS = [
  {
    keys: ["旗山校区", "福大旗山", "福州大学旗山校区", "福州大学", "福大", "大学城", "上街"],
    label: "福州大学旗山校区",
    address: "福州市闽侯县上街镇乌龙江北大道2号",
    lat: 26.057439,
    lng: 119.193555,
  },
  { keys: ["鼓楼区", "鼓楼"], label: "鼓楼区", address: "福州市鼓楼区", lat: 26.082, lng: 119.296 },
  { keys: ["台江区", "台江"], label: "台江区", address: "福州市台江区", lat: 26.063, lng: 119.305 },
  { keys: ["仓山区", "仓山"], label: "仓山区", address: "福州市仓山区", lat: 26.040, lng: 119.300 },
  { keys: ["晋安区", "晋安"], label: "晋安区", address: "福州市晋安区", lat: 26.090, lng: 119.320 },
];

function lookupLocalLocation(text) {
  const normalized = String(text || "").replace(/\s+/g, "");
  if (!normalized) return null;
  for (const item of LOCAL_LOCATIONS) {
    if (item.keys.some(k => normalized.includes(k))) return item;
  }
  return null;
}

function haversineKm(lat1, lng1, lat2, lng2) {
  const toRad = (x) => x * Math.PI / 180;
  const R = 6371.0088;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

// 浏览器 Geolocation 返回 WGS-84，高德 POI/距离使用 GCJ-02。
// 不转换会带来几十到几百米偏移，福州市区足够让“最近医院”排序出错。
function outOfChina(lat, lng) {
  return lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271;
}

function transformLat(x, y) {
  let ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  ret += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
  ret += (20.0 * Math.sin(y * Math.PI) + 40.0 * Math.sin(y / 3.0 * Math.PI)) * 2.0 / 3.0;
  ret += (160.0 * Math.sin(y / 12.0 * Math.PI) + 320 * Math.sin(y * Math.PI / 30.0)) * 2.0 / 3.0;
  return ret;
}

function transformLng(x, y) {
  let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  ret += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
  ret += (20.0 * Math.sin(x * Math.PI) + 40.0 * Math.sin(x / 3.0 * Math.PI)) * 2.0 / 3.0;
  ret += (150.0 * Math.sin(x / 12.0 * Math.PI) + 300.0 * Math.sin(x / 30.0 * Math.PI)) * 2.0 / 3.0;
  return ret;
}

function wgs84ToGcj02(lat, lng) {
  if (outOfChina(lat, lng)) return { lat, lng };
  const a = 6378245.0;
  const ee = 0.00669342162296594323;
  let dLat = transformLat(lng - 105.0, lat - 35.0);
  let dLng = transformLng(lng - 105.0, lat - 35.0);
  const radLat = lat / 180.0 * Math.PI;
  let magic = Math.sin(radLat);
  magic = 1 - ee * magic * magic;
  const sqrtMagic = Math.sqrt(magic);
  dLat = (dLat * 180.0) / ((a * (1 - ee)) / (magic * sqrtMagic) * Math.PI);
  dLng = (dLng * 180.0) / (a / sqrtMagic * Math.cos(radLat) * Math.PI);
  return { lat: lat + dLat, lng: lng + dLng };
}

function hospitalMapUrl(lat, lng, name) {
  return "https://uri.amap.com/marker?position=" + Number(lng).toFixed(6) + "," + Number(lat).toFixed(6) +
    "&name=" + encodeURIComponent(name || "医院") +
    "&coordinate=gaode&callnative=1&src=menzhen-paidui";
}

function localNearbyHospitals(lat, lng) {
  return HOSPITAL_FALLBACK.map(h => ({
    ...h,
    distance_km: Math.round(haversineKm(lat, lng, h.latitude, h.longitude) * 10) / 10,
    map_url: hospitalMapUrl(h.latitude, h.longitude, h.name),
    source: "local",
  })).sort((a, b) => a.distance_km - b.distance_km);
}

function loadSavedLocation() {
  try {
    const raw = localStorage.getItem(SAVED_LOCATION_KEY);
    if (raw) {
      const data = JSON.parse(raw);
      if (data && typeof data.lat === "number" && typeof data.lng === "number") {
        memorySavedLocation = data;
        return data;
      }
    }
  } catch (_) { /* ignore */ }
  try {
    const raw = sessionStorage.getItem(SAVED_LOCATION_KEY);
    if (raw) {
      const data = JSON.parse(raw);
      if (data && typeof data.lat === "number" && typeof data.lng === "number") {
        memorySavedLocation = data;
        return data;
      }
    }
  } catch (_) { /* ignore */ }
  return memorySavedLocation;
}

function storeSavedLocation(location) {
  memorySavedLocation = location;
  try {
    localStorage.setItem(SAVED_LOCATION_KEY, JSON.stringify(location));
    return "local";
  } catch (_) { /* localStorage 可能被禁用 */ }
  try {
    sessionStorage.setItem(SAVED_LOCATION_KEY, JSON.stringify(location));
    return "session";
  } catch (_) {
    return "memory";
  }
}

function clearSavedLocation() {
  memorySavedLocation = null;
  try { localStorage.removeItem(SAVED_LOCATION_KEY); } catch (_) { /* ignore */ }
  try { sessionStorage.removeItem(SAVED_LOCATION_KEY); } catch (_) { /* ignore */ }
}

async function hydrateSavedLocation() {
  if (loadSavedLocation()) return memorySavedLocation;
  if (mode === "mock") return null;
  try {
    const data = await fetchJSON(API_BASE + "/api/v1/preferences/location", {}, 3000);
    if (!data || typeof data.latitude !== "number" || typeof data.longitude !== "number") return null;
    const location = {
      label: data.label,
      address: data.address || "",
      lat: data.latitude,
      lng: data.longitude,
      savedAt: data.updated_at || new Date().toISOString(),
    };
    storeSavedLocation(location);
    renderSavedLocationBar();
    return location;
  } catch (_) {
    return null;
  }
}

async function syncSavedLocationToBackend(location) {
  if (mode === "mock") return false;
  try {
    await fetchJSON(API_BASE + "/api/v1/preferences/location", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        label: location.label,
        address: location.address || "",
        latitude: location.lat,
        longitude: location.lng,
      }),
    }, 4000);
    return true;
  } catch (_) {
    return false; /* 后端不可用时只存本地 */
  }
}

async function deleteSavedLocationFromBackend() {
  if (mode === "mock") return;
  try {
    await fetchJSON(API_BASE + "/api/v1/preferences/location", { method: "DELETE" }, 3000);
  } catch (_) { /* ignore */ }
}

function renderSavedLocationBar() {
  const saved = loadSavedLocation();
  const bar = $("savedLocationBar");
  if (!saved) {
    bar.style.display = "none";
    return;
  }
  $("savedLocationLabel").textContent = saved.label || saved.address || "已保存位置";
  bar.style.display = "flex";
}

function showHospitalEmpty(title, desc, subText) {
  $("hospitalPrompt").style.display = "block";
  $("hospitalLoading").style.display = "none";
  $("hospitalList").innerHTML = "";
  $("historyEmptyTitle").textContent = title;
  $("historyEmptyDesc").textContent = desc;
  $("historyEmpty").style.display = "block";
  $("hospitalSub").textContent = subText;
}

async function useHistoryLocation() {
  let saved = loadSavedLocation();
  if (!saved) {
    await hydrateSavedLocation();
    saved = loadSavedLocation();
  }
  if (!saved) {
    showHospitalEmpty(
      "暂无历史位置",
      "输入具体地点并解析后，保存的位置会显示在这里。",
      "历史位置为空"
    );
    return;
  }
  $("historyEmpty").style.display = "none";
  $("hospitalPrompt").style.display = "none";
  loadHospitals(saved.lat, saved.lng, saved.label || "历史位置");
}

async function forgetSavedLocation() {
  const ok = await askConfirm("删除保存的位置？", "删除后下次需要重新输入或定位。", "删除");
  if (!ok) return;
  clearSavedLocation();
  await deleteSavedLocationFromBackend();
  renderSavedLocationBar();
  showToast("已删除保存的位置");
}

function showHospitalRecommend() {
  $("toast").classList.remove("show");
  $("hospitalModal").classList.add("show");
  $("hospitalPrompt").style.display = "block";
  $("historyEmpty").style.display = "none";
  $("hospitalLoading").style.display = "none";
  $("hospitalList").innerHTML = "";
  $("hospitalSub").textContent = "获取位置后，按直线距离参考推荐附近医院";
  $("hospitalAddress").value = "";
  renderSavedLocationBar();
  hydrateSavedLocation();
}

function closeHospitalModal() {
  closeHospitalService();
  $("hospitalModal").classList.remove("show");
}

async function offerSaveLocation(data) {
  const lat = data.lat != null ? data.lat : data.latitude;
  const lng = data.lng != null ? data.lng : data.longitude;
  const label = data.label || data.address || "该位置";
  const ok = await askConfirm(
    "保存这个位置？",
    "下次打开医院推荐时可以直接使用：" + label,
    "保存"
  );
  if (!ok) return;
  const location = {
    label,
    address: data.address || "",
    lat,
    lng,
    savedAt: new Date().toISOString(),
  };
  const storage = storeSavedLocation(location);
  const backendSaved = await syncSavedLocationToBackend(location);
  renderSavedLocationBar();
  if (storage === "memory" && !backendSaved) {
    showToast("位置只保存在本次会话，浏览器限制本地存储");
  } else {
    showToast("位置已保存");
  }
}

async function resolveAddress() {
  const address = $("hospitalAddress").value.trim();
  if (!address) {
    showToast("请输入具体位置，如：福州大学旗山校区");
    return;
  }
  // 旗山校区/大学城/鼓楼等常见地点内置解析，离线也能用
  const local = lookupLocalLocation(address);
  if (local) {
    $("hospitalLoading").style.display = "none";
    $("hospitalPrompt").style.display = "none";
    await loadHospitals(local.lat, local.lng, local.label);
    await offerSaveLocation(local);
    return;
  }
  // 页面加载时后端可能还没启动；点“解析位置”时再检测一次，后端后来启动也能自动恢复
  if (mode === "detect" || mode === "mock") {
    $("hospitalSub").textContent = "正在连接后端…";
    const ok = await checkHealth();
    if (!ok || mode === "mock") {
      showToast("仍无法连接后端，请确认服务已启动；也可以手动选择区域", 4000);
      return;
    }
  }
  $("hospitalLoading").style.display = "block";
  $("hospitalList").innerHTML = "";
  $("hospitalSub").textContent = "正在解析位置…";
  try {
    const data = await fetchJSON(
      API_BASE + "/api/v1/hospitals/geocode?address=" + encodeURIComponent(address),
      {},
      8000
    );
    $("hospitalLoading").style.display = "none";
    $("hospitalPrompt").style.display = "none";
    await loadHospitals(data.latitude, data.longitude, data.label || address);
    await offerSaveLocation(data);
  } catch (err) {
    $("hospitalLoading").style.display = "none";
    $("hospitalPrompt").style.display = "block";
    const status = err && err.status;
    let msg = "无法连接后端，请先启动后端服务，或手动选择区域";
    if (status === 503) {
      msg = "后端未配置高德 Key，无法解析地点；请手动选择区域";
    } else if (status === 404) {
      msg = "高德没有找到这个地点，请换一个更具体的地址";
    }
    showToast(msg, 4200);
  }
}

function requestLocation() {
  if (!navigator.geolocation) {
    showHospitalEmpty(
      "暂无当前位置",
      "当前浏览器不支持定位，请手动输入具体地点或使用历史位置。",
      "未获取到当前位置"
    );
    return;
  }
  $("hospitalPrompt").style.display = "none";
  $("hospitalLoading").style.display = "block";
  $("hospitalList").innerHTML = "";
  $("hospitalSub").textContent = "正在获取当前位置…";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const accuracy = pos.coords.accuracy;
      // 精度太差就不直接推荐，改为让用户输入具体位置
      if (accuracy && accuracy > 1200) {
        $("hospitalLoading").style.display = "none";
        $("hospitalPrompt").style.display = "block";
        const accText = accuracy >= 1000 ? (accuracy / 1000).toFixed(1) + "km" : Math.round(accuracy) + "m";
        $("hospitalSub").textContent = "定位精度约 ±" + accText + "，请补充具体位置";
        $("hospitalAddress").focus();
        showToast("定位不够精确，请输入具体位置", 3000);
        return;
      }
      // 浏览器是 WGS-84，高德是 GCJ-02；先转换再查距离
      const gcj = wgs84ToGcj02(pos.coords.latitude, pos.coords.longitude);
      loadHospitals(gcj.lat, gcj.lng, "当前位置", accuracy);
    },
    () => showHospitalEmpty(
      "暂无当前位置",
      "没有获得定位权限，请手动输入具体地点或使用历史位置。",
      "未获取到当前位置"
    ),
    { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 }
  );
}

async function loadHospitals(lat, lng, label, accuracy = null) {
  $("hospitalLoading").style.display = "block";
  $("historyEmpty").style.display = "none";
  $("hospitalList").innerHTML = "";
  let sub = label + "附近 · 按直线距离参考排序";
  if (accuracy) {
    sub += " · 定位精度约 " + (accuracy >= 1000 ? "±" + (accuracy / 1000).toFixed(1) + "km" : "±" + Math.round(accuracy) + "m");
  }
  $("hospitalSub").textContent = sub;

  let items = null;
  let source = "local";
  if (mode !== "mock") {
    try {
      const data = await fetchJSON(
        API_BASE + "/api/v1/hospitals/nearby?lat=" + encodeURIComponent(lat) +
        "&lng=" + encodeURIComponent(lng) + "&limit=6",
        {},
        6000
      );
      if (data && Array.isArray(data.items) && data.items.length) {
        items = data.items;
        source = data.source || "local";
      }
    } catch (_) { /* 后端不可用：本地兜底 */ }
  }
  if (!items) {
    items = localNearbyHospitals(lat, lng);
    source = "local";
  }
  $("hospitalLoading").style.display = "none";
  renderHospitals(items, label, source, accuracy);
}

const REGISTRATION_DATA = {
  shengli: {
    site: "http://www.fjsl.com.cn",
    siteName: "福建省立医院官网",
    phone: "968808",
    release: "每天 15:00 放第 14 天号源（以医院公告为准）",
    channels: [
      "微信公众号“福建省立医院”→“掌上省立”→“预约挂号”",
      "支付宝搜索“福建省立医院”，绑定就诊卡后预约",
      "电话预约：968808",
      "院内自助机 / 门诊服务中心现场预约（东街、金山院区）",
    ],
    steps: [
      "微信或支付宝搜索医院，完成实名绑定/建档",
      "选择科室、医生和就诊时段",
      "确认预约并在线支付（如有）",
      "就诊日按短信提示提前到院取号候诊",
    ],
    notes: [
      "实名制就诊，首次就诊需先建档或绑定就诊卡",
      "可预约提前 1-14 天；当日上午可预约下午号源",
      "取号超时可能作废；无法就诊请提前一天退号",
    ],
  },
  shengli_jinshan: {
    site: "http://www.fjsl.com.cn",
    siteName: "福建省立医院官网",
    phone: "968808",
    release: "每天 15:00 放第 14 天号源（以医院公告为准）",
    channels: [
      "微信公众号“福建省立医院”，进入“便民服务”→“预约挂号”",
      "支付宝搜索“福建省立医院”，绑定就诊卡后预约",
      "电话预约：968808；金山院区咨询：0591-88618800",
      "金山院区门诊楼 1 层医院服务中心 / 各楼层自助机",
    ],
    steps: [
      "选择“金山院区”，再选科室、医生和时段",
      "完成实名绑定/建档并确认预约",
      "就诊日到金山院区门诊楼取号",
      "按预约时段到对应诊区候诊",
    ],
    notes: [
      "金山院区地址：福州市仓山区金山街道金榕南路 516 号",
      "院区门诊时间以当日公告为准",
      "取号超时可能作废；无法就诊请提前退号",
    ],
  },
  xiehe: {
    site: "http://www.fjxiehe.com",
    siteName: "福建医科大学附属协和医院官网",
    phone: "0591-86218613",
    release: "每天 15:00 更新第 14 天号源（以医院公告为准）",
    channels: [
      "微信公众号“福建医科大学附属协和医院”→“预约挂号”",
      "院内各楼层自助机",
      "16 号楼门诊一楼综合服务中心人工窗口",
      "电话预约：0591-86218613",
    ],
    steps: [
      "公众号实名注册/绑定就诊卡",
      "选择院区、科室、医生和时段",
      "确认预约；首次就诊按提示建档",
      "就诊日提前到院取号，按叫号就诊",
    ],
    notes: [
      "所有号源提前 14 天开放，15:00 开始预约",
      "就诊日截止预约：上午 07:30，下午 13:30",
      "现场/电话预约时间以医院公告为准；无法就诊请提前取消",
    ],
  },
  xiehe_qishan: {
    site: "http://www.fjxiehe.com",
    siteName: "福建医科大学附属协和医院官网",
    phone: "0591-86218613",
    release: "每天 15:00 更新第 14 天号源（以医院公告为准）",
    channels: [
      "微信公众号“福建医科大学附属协和医院”→选择“旗山院区”",
      "医院官网预约入口",
      "旗山院区门诊楼自助机 / 人工窗口现场挂号",
      "电话预约：0591-86218613",
    ],
    steps: [
      "公众号选择“旗山院区”，再选科室和医生",
      "确认预约并完成实名建档",
      "就诊日到闽侯县上街镇学府南路 6 号",
      "门诊三楼对应诊区取号候诊",
    ],
    notes: [
      "旗山院区地址：福州市闽侯县上街镇学府南路 6 号",
      "门诊时间：8:30-12:00，13:30-16:30；急诊 24 小时",
      "部分专科周末/节假日安排不同，以公众号当日号源为准",
    ],
  },
  fuyi: {
    site: "https://www.fyyy.com",
    siteName: "福建医科大学附属第一医院官网",
    phone: "968808",
    release: "每天 7:30 开放 14 天内号源（以医院公告为准）",
    channels: [
      "微信公众号“福建医科大学附属第一医院”",
      "网络预约：www.yihu.com",
      "电话预约：968808",
      "院内自助机 / 门诊一楼服务中心 / 医生工作站",
    ],
    steps: [
      "公众号或网络平台实名注册",
      "选择院区、科室、医生和时段",
      "确认预约，按提示建档/充值",
      "就诊日提前 30 分钟到院取号",
    ],
    notes: [
      "放号时间早上 7:30，可预约提前 1-14 天",
      "现场取号：上午 07:00-11:40，下午 13:00-17:10",
      "一个月内违约超过 3 次可能被限制预约 30 天",
    ],
  },
  shengrenmin: {
    site: "https://www.srmyy.com",
    siteName: "福建省人民医院官网",
    phone: "0591-83940303",
    release: "每天 15:00 放第 14 天号源（以医院公告为准）",
    channels: [
      "微信公众号“福建省人民医院”→“智慧医院”→“掌上医院”",
      "电话预约：0591-83940303（工作日 8:00-12:00，14:00-21:00）",
      "门诊大楼自助机",
      "1 号楼门诊大厅“一站式服务中心”现场预约",
      "诊间预约（医生工作站复诊预约）",
    ],
    steps: [
      "公众号进入“掌上医院”，实名绑定就诊卡",
      "按科室或医生搜索号源",
      "选择时段并确认预约",
      "就诊日到 1 号楼门诊大厅取号",
    ],
    notes: [
      "普通预约提前 14 天开放，每日 15:00 放号",
      "诊间预约可提前 30 天",
      "预约后超过半小时未取号，号源可能作废",
    ],
  },
  shenger: {
    site: "https://fjhospital.com",
    siteName: "福建省第二人民医院官网",
    phone: "0591-87878130",
    release: "以医院当日公告为准",
    channels: [
      "微信公众号“福建省第二人民医院”",
      "医院官网预约入口",
      "电话预约：0591-87878130",
      "院内自助机 / 门诊现场预约",
    ],
    steps: [
      "公众号或官网实名注册/绑定就诊卡",
      "选择科室、医生和时段",
      "确认预约并记录预约号",
      "就诊日提前到院取号候诊",
    ],
    notes: [
      "地址：福州市五四路 282 号",
      "门诊安排和放号时间以医院当日公告为准",
      "发热门诊电话：0591-87873291",
    ],
  },
  fuyou: {
    site: "https://www.fjsfy.com",
    siteName: "福建省妇幼保健院官网",
    phone: "968808",
    release: "提前 1-14 天预约（以医院公告为准）",
    channels: [
      "微信公众号“福建省妇幼保健院”",
      "“福建省妇幼保健院”手机 APP / 抖音号",
      "电话预约：968808（08:00-12:00，14:00-18:00）",
      "医疗保健综合楼一楼就医服务中心现场预约",
      "院内自助机 / 医生工作站",
    ],
    steps: [
      "公众号/APP 实名注册并绑定就诊卡",
      "选择院区、科室、医生和时段",
      "确认预约，按提示建档",
      "就诊日到院取号候诊",
    ],
    notes: [
      "需持有医院就诊卡或已注册的社保卡",
      "现场预约：周一至周五上班时间，周六仅上午",
      "以医院当日公告为准；无法就诊请提前取消",
    ],
  },
  sanyuan: {
    site: "http://www.fjsdsrmyy.com",
    siteName: "福建中医药大学附属第三人民医院官网",
    phone: "0591-22867120",
    release: "以医院当日公告为准",
    channels: [
      "医院微信公众号（服务号）→“预约挂号”",
      "医院官网预约入口",
      "咨询电话：0591-22867120",
      "院内自助机 / 门诊现场挂号",
    ],
    steps: [
      "公众号实名注册/绑定就诊卡",
      "选择科室、医生和时段",
      "确认预约",
      "就诊日到闽侯县上街镇国宾大道 363 号取号",
    ],
    notes: [
      "地址：福州市闽侯县上街镇国宾大道 363 号",
      "24 小时急诊电话：0591-83120120",
      "具体放号时间以医院公告为准",
    ],
  },
  hospital900: {
    site: "",
    siteName: "医院微信公众号",
    phone: "0591-22859015",
    release: "以医院当日公告为准",
    channels: [
      "微信公众号“联勤保障部队第900医院”→“预约挂号”",
      "电话预约：0591-22859015",
      "院内自助机 / 门诊现场挂号",
    ],
    steps: [
      "公众号实名注册/绑定就诊信息",
      "选择仓山院区、科室和时段",
      "确认预约并记录预约号",
      "就诊日提前到院取号",
    ],
    notes: [
      "仓山院区地址：福州市仓山区洪山桥郭厝里 90 号",
      "军队医院就诊政策/医保结算以医院现场为准",
      "放号时间以医院公众号当日公告为准",
    ],
  },
};

// 院内导航试点：3 家医院；楼层图为示意，不声称真实室内定位。
const NAV_DATA = {
  xiehe_qishan: {
    label: "协和旗山院区",
    sub: "门诊楼 · 1F/3F",
    note: "仅部分科室公开了楼层信息；内科诊区楼层以挂号单和院内导视为准。",
    entrances: [
      { id: "south", name: "门诊楼南门" },
      { id: "west", name: "西门" },
    ],
    destinations: [
      { id: "dept", name: "内科诊区" },
      { id: "cashier", name: "收费处" },
      { id: "pharmacy", name: "药房" },
    ],
    floors: [
      {
        id: "1F", name: "1F 门诊大厅",
        pois: [
          { id: "south", name: "南门", type: "entrance", x: 15, y: 82 },
          { id: "west", name: "西门", type: "entrance", x: 8, y: 34 },
          { id: "hall", name: "大厅/导诊", type: "service", x: 48, y: 52 },
          { id: "elevator1", name: "电梯", type: "elevator", x: 82, y: 38 },
          { id: "cashier1", name: "收费处", type: "cashier", x: 76, y: 78 },
          { id: "pharmacy1", name: "药房", type: "pharmacy", x: 24, y: 24 },
        ],
      },
      {
        id: "3F", name: "3F 内科诊区",
        pois: [
          { id: "elevator3", name: "电梯", type: "elevator", x: 82, y: 38 },
          { id: "dept3", name: "内科诊区", type: "department", x: 30, y: 62 },
          { id: "nurse3", name: "护士站", type: "service", x: 55, y: 64 },
        ],
      },
    ],
    routes: [
      {
        from: "south", to: "dept", duration: "约 4 分钟（示意）", floors: ["1F", "3F"],
        steps: [
          { floor: "1F", text: "从门诊楼南门进入，直走到一楼大厅" },
          { floor: "1F", text: "穿过大厅，右手边乘电梯上 3F" },
          { floor: "3F", text: "出电梯左转，沿走廊到内科诊区" },
        ],
        paths: { "1F": [[15, 82], [48, 52], [82, 38]], "3F": [[82, 38], [55, 64], [30, 62]] },
      },
      {
        from: "west", to: "dept", duration: "约 4 分钟（示意）", floors: ["1F", "3F"],
        steps: [
          { floor: "1F", text: "从西门进入，沿主通道走到一楼大厅" },
          { floor: "1F", text: "在大厅右侧乘电梯上 3F" },
          { floor: "3F", text: "出电梯左转，沿走廊到内科诊区" },
        ],
        paths: { "1F": [[8, 34], [48, 52], [82, 38]], "3F": [[82, 38], [55, 64], [30, 62]] },
      },
      {
        from: "south", to: "cashier", duration: "约 2 分钟（示意）", floors: ["1F"],
        steps: [
          { floor: "1F", text: "从南门进入，直走到一楼大厅" },
          { floor: "1F", text: "大厅右前方为收费处" },
        ],
        paths: { "1F": [[15, 82], [48, 52], [76, 78]] },
      },
      {
        from: "south", to: "pharmacy", duration: "约 3 分钟（示意）", floors: ["1F"],
        steps: [
          { floor: "1F", text: "从南门进入，先到一楼大厅" },
          { floor: "1F", text: "沿大厅左侧通道前往药房" },
        ],
        paths: { "1F": [[15, 82], [48, 52], [24, 24]] },
      },
    ],
  },
  shengli_jinshan: {
    label: "省立医院金山院区",
    sub: "门诊楼 · 1F/3F",
    note: "消化内科按官方公开信息在 3F；药房/收费在 1F；房间位置为示意。",
    entrances: [{ id: "south", name: "门诊楼南门" }],
    destinations: [
      { id: "dept", name: "内科诊区" },
      { id: "cashier", name: "收费处" },
      { id: "pharmacy", name: "药房" },
    ],
    floors: [
      {
        id: "1F", name: "1F 门诊大厅",
        pois: [
          { id: "south", name: "南门", type: "entrance", x: 18, y: 84 },
          { id: "hall", name: "大厅/导诊", type: "service", x: 50, y: 52 },
          { id: "elevator1", name: "电梯", type: "elevator", x: 82, y: 34 },
          { id: "cashier1", name: "收费处", type: "cashier", x: 72, y: 72 },
          { id: "pharmacy1", name: "药房", type: "pharmacy", x: 24, y: 26 },
        ],
      },
      {
        id: "3F", name: "3F 内科诊区",
        pois: [
          { id: "elevator3", name: "电梯", type: "elevator", x: 82, y: 34 },
          { id: "dept3", name: "内科诊区", type: "department", x: 30, y: 64 },
          { id: "nurse3", name: "护士站", type: "service", x: 56, y: 62 },
        ],
      },
    ],
    routes: [],
  },
  xiehe_yushan: {
    label: "协和医院于山院区",
    sub: "门诊楼 · 1F/4F",
    note: "消化内科按官方公开索引在 4F；收费处在 1B，本图暂未覆盖；房间位置为示意。",
    entrances: [
      { id: "east", name: "门诊楼东门" },
      { id: "south", name: "门诊楼南门" },
    ],
    destinations: [
      { id: "dept", name: "内科诊区" },
      { id: "pharmacy", name: "药房" },
    ],
    floors: [
      {
        id: "1F", name: "1F 门诊大厅",
        pois: [
          { id: "east", name: "东门", type: "entrance", x: 88, y: 78 },
          { id: "south", name: "南门", type: "entrance", x: 18, y: 84 },
          { id: "hall", name: "大厅/导诊", type: "service", x: 50, y: 52 },
          { id: "elevator1", name: "电梯", type: "elevator", x: 78, y: 30 },
          { id: "pharmacy1", name: "药房", type: "pharmacy", x: 24, y: 26 },
        ],
      },
      {
        id: "4F", name: "4F 内科诊区",
        pois: [
          { id: "elevator4", name: "电梯", type: "elevator", x: 78, y: 30 },
          { id: "dept4", name: "内科诊区", type: "department", x: 30, y: 64 },
          { id: "nurse4", name: "护士站", type: "service", x: 56, y: 62 },
        ],
      },
    ],
    routes: [],
  },
  shengrenmin: {
    label: "福建省人民医院",
    sub: "1号楼 · 1F/3F",
    note: "脾胃病科/消化内镜按官方公开索引在 3F；1F 收费/药房以现场为准；房间位置为示意。",
    entrances: [{ id: "south", name: "1号楼南门" }],
    destinations: [
      { id: "dept", name: "内科诊区" },
      { id: "cashier", name: "收费处" },
      { id: "pharmacy", name: "药房" },
    ],
    floors: [
      {
        id: "1F", name: "1F 门诊大厅",
        pois: [
          { id: "south", name: "南门", type: "entrance", x: 18, y: 84 },
          { id: "hall", name: "大厅/导诊", type: "service", x: 50, y: 52 },
          { id: "elevator1", name: "电梯", type: "elevator", x: 82, y: 34 },
          { id: "cashier1", name: "收费处", type: "cashier", x: 72, y: 72 },
          { id: "pharmacy1", name: "药房", type: "pharmacy", x: 24, y: 26 },
        ],
      },
      {
        id: "3F", name: "3F 内科诊区",
        pois: [
          { id: "elevator3", name: "电梯", type: "elevator", x: 82, y: 34 },
          { id: "dept3", name: "内科诊区", type: "department", x: 30, y: 64 },
          { id: "nurse3", name: "护士站", type: "service", x: 56, y: 62 },
        ],
      },
    ],
    routes: [],
  },
  shengli: {
    label: "省立医院东街院区",
    sub: "2号楼 · 1F/4F",
    note: "消化内科按官方公开楼层信息在 4F；收费处/药房为示意位置，具体以现场为准。",
    entrances: [
      { id: "east", name: "门诊楼东门" },
      { id: "south", name: "门诊楼南门" },
    ],
    destinations: [
      { id: "dept", name: "内科诊区" },
      { id: "cashier", name: "收费处" },
      { id: "pharmacy", name: "药房" },
    ],
    floors: [
      {
        id: "1F", name: "1F 门诊大厅",
        pois: [
          { id: "east", name: "东门", type: "entrance", x: 88, y: 78 },
          { id: "south", name: "南门", type: "entrance", x: 18, y: 84 },
          { id: "hall", name: "大厅/导诊", type: "service", x: 50, y: 52 },
          { id: "elevator1", name: "电梯", type: "elevator", x: 78, y: 30 },
          { id: "cashier1", name: "收费处", type: "cashier", x: 70, y: 70 },
          { id: "pharmacy1", name: "药房", type: "pharmacy", x: 26, y: 26 },
        ],
      },
      {
        id: "4F", name: "4F 内科诊区",
        pois: [
          { id: "elevator4", name: "电梯", type: "elevator", x: 78, y: 30 },
          { id: "dept4", name: "内科诊区", type: "department", x: 30, y: 64 },
          { id: "nurse4", name: "护士站", type: "service", x: 56, y: 62 },
        ],
      },
    ],
    routes: [
      {
        from: "east", to: "dept", duration: "约 4 分钟（示意）", floors: ["1F", "4F"],
        steps: [
          { floor: "1F", text: "从东门进入，沿主通道到一楼大厅" },
          { floor: "1F", text: "在大厅左前方乘电梯上 4F" },
          { floor: "4F", text: "出电梯右转，沿走廊到内科诊区" },
        ],
        paths: { "1F": [[88, 78], [50, 52], [78, 30]], "4F": [[78, 30], [56, 62], [30, 64]] },
      },
      {
        from: "south", to: "dept", duration: "约 5 分钟（示意）", floors: ["1F", "4F"],
        steps: [
          { floor: "1F", text: "从南门进入，直走到一楼大厅" },
          { floor: "1F", text: "穿过大厅，左前方乘电梯上 4F" },
          { floor: "4F", text: "出电梯右转，沿走廊到内科诊区" },
        ],
        paths: { "1F": [[18, 84], [50, 52], [78, 30]], "4F": [[78, 30], [56, 62], [30, 64]] },
      },
      {
        from: "east", to: "cashier", duration: "约 2 分钟（示意）", floors: ["1F"],
        steps: [
          { floor: "1F", text: "从东门进入，走到一楼大厅" },
          { floor: "1F", text: "大厅右侧为收费处" },
        ],
        paths: { "1F": [[88, 78], [50, 52], [70, 70]] },
      },
      {
        from: "east", to: "pharmacy", duration: "约 3 分钟（示意）", floors: ["1F"],
        steps: [
          { floor: "1F", text: "从东门进入，先到一楼大厅" },
          { floor: "1F", text: "沿大厅左侧通道前往药房" },
        ],
        paths: { "1F": [[88, 78], [50, 52], [26, 26]] },
      },
    ],
  },
  fuyi: {
    label: "附一医院茶亭院区",
    sub: "门诊楼 · 1F/2F",
    note: "消化内科按门诊楼 2F 核对；完整官方楼层信息待补，具体以挂号单和现场为准。",
    entrances: [
      { id: "south", name: "门诊楼南门" },
      { id: "north", name: "门诊楼北门" },
    ],
    destinations: [
      { id: "dept", name: "内科诊区" },
      { id: "cashier", name: "收费处" },
      { id: "pharmacy", name: "药房" },
    ],
    floors: [
      {
        id: "1F", name: "1F 门诊大厅",
        pois: [
          { id: "south", name: "南门", type: "entrance", x: 18, y: 84 },
          { id: "north", name: "北门", type: "entrance", x: 82, y: 14 },
          { id: "hall", name: "大厅/导诊", type: "service", x: 50, y: 52 },
          { id: "elevator1", name: "电梯", type: "elevator", x: 80, y: 34 },
          { id: "cashier1", name: "收费处", type: "cashier", x: 72, y: 72 },
          { id: "pharmacy1", name: "药房", type: "pharmacy", x: 24, y: 28 },
        ],
      },
      {
        id: "2F", name: "2F 内科诊区",
        pois: [
          { id: "elevator2", name: "电梯", type: "elevator", x: 80, y: 34 },
          { id: "dept2", name: "内科诊区", type: "department", x: 30, y: 64 },
          { id: "nurse2", name: "护士站", type: "service", x: 56, y: 62 },
        ],
      },
    ],
    routes: [
      {
        from: "south", to: "dept", duration: "约 4 分钟（示意）", floors: ["1F", "2F"],
        steps: [
          { floor: "1F", text: "从南门进入，直走到一楼大厅" },
          { floor: "1F", text: "在大厅右前方乘电梯上 2F" },
          { floor: "2F", text: "出电梯左转，沿走廊到内科诊区" },
        ],
        paths: { "1F": [[18, 84], [50, 52], [80, 34]], "2F": [[80, 34], [56, 62], [30, 64]] },
      },
      {
        from: "north", to: "dept", duration: "约 3 分钟（示意）", floors: ["1F", "2F"],
        steps: [
          { floor: "1F", text: "从北门进入，沿通道到一楼大厅" },
          { floor: "1F", text: "在大厅右前方乘电梯上 2F" },
          { floor: "2F", text: "出电梯左转，沿走廊到内科诊区" },
        ],
        paths: { "1F": [[82, 14], [50, 52], [80, 34]], "2F": [[80, 34], [56, 62], [30, 64]] },
      },
      {
        from: "south", to: "cashier", duration: "约 2 分钟（示意）", floors: ["1F"],
        steps: [
          { floor: "1F", text: "从南门进入，走到一楼大厅" },
          { floor: "1F", text: "大厅右前方为收费处" },
        ],
        paths: { "1F": [[18, 84], [50, 52], [72, 72]] },
      },
      {
        from: "south", to: "pharmacy", duration: "约 3 分钟（示意）", floors: ["1F"],
        steps: [
          { floor: "1F", text: "从南门进入，先到一楼大厅" },
          { floor: "1F", text: "沿大厅左侧通道前往药房" },
        ],
        paths: { "1F": [[18, 84], [50, 52], [24, 28]] },
      },
    ],
  },
};

function genericRegistration(name, fallbackChannel) {
  return {
    site: "",
    siteName: "",
    phone: "",
    release: "以医院当日公告为准",
    channels: [
      fallbackChannel || "医院官方微信公众号 / 官网",
      "建议优先使用医院官方渠道，避免第三方代挂号",
    ],
    steps: [
      "通过医院官方公众号或官网实名注册",
      "选择科室、医生和就诊时段",
      "确认预约并记录预约号",
      "就诊日提前到院取号候诊",
    ],
    notes: [
      "挂号渠道和放号时间以医院当日公告为准",
      "首次就诊通常需要建档/绑定就诊卡",
    ],
  };
}

function registrationFor(name, fallbackChannel) {
  const n = String(name || "");
  let key = "";
  if (/协和/.test(n) && /旗山/.test(n)) key = "xiehe_qishan";
  else if (/协和/.test(n)) key = "xiehe";
  else if (/省立/.test(n) && /金山/.test(n)) key = "shengli_jinshan";
  else if (/省立/.test(n)) key = "shengli";
  else if (/附属第一/.test(n)) key = "fuyi";
  else if (/妇幼/.test(n)) key = "fuyou";
  else if (/第二人民/.test(n)) key = "shenger";
  else if (/第三人民/.test(n)) key = "sanyuan";
  else if (/九〇〇|900|联勤保障/.test(n)) key = "hospital900";
  else if (/人民医院/.test(n)) key = "shengrenmin";
  const data = REGISTRATION_DATA[key] || genericRegistration(n, fallbackChannel);
  return { ...data, name: n || "医院" };
}

function showRegistrationInfo(name, fallbackChannel, department = "") {
  const info = registrationFor(name, fallbackChannel);
  lastRegistrationInfo = { info, department };
  $("regTitle").textContent = info.name + " - 挂号指引";
  const deptPrefix = department ? "推荐科室：" + department + " · " : "";
  $("regSub").textContent = deptPrefix + (info.site ? "官方渠道 · 以医院当日公告为准" : "以医院官方渠道/当日公告为准");
  const channels = info.channels.map(c =>
    '<div class="reg-channel"><span class="dot"></span><span>' + escapeHtml(c) + '</span></div>'
  ).join("");
  const steps = info.steps.map(s => '<li>' + escapeHtml(s) + '</li>').join("");
  const notes = info.notes.map(n => '<li>' + escapeHtml(n) + '</li>').join("");
  const links = [];
  if (info.site) {
    links.push('<a class="reg-link" href="' + escapeHtml(info.site) + '" target="_blank" rel="noopener">' + escapeHtml(info.siteName || info.site) + '</a>');
  }
  if (info.phone) {
    links.push('<span class="reg-tag">电话 ' + escapeHtml(info.phone) + '</span>');
  }
  const linkHtml = links.length ? '<div class="reg-meta">' + links.join("") + '</div>' : "";
  $("regBody").innerHTML =
    '<div class="reg-section">' +
      '<div class="reg-section-title">官方预约渠道</div>' +
      channels + linkHtml +
    '</div>' +
    '<div class="reg-section">' +
      '<div class="reg-section-title">放号时间</div>' +
      '<div class="reg-text">' + escapeHtml(info.release) + '</div>' +
    '</div>' +
    '<div class="reg-section">' +
      '<div class="reg-section-title">预约步骤</div>' +
      '<ol class="reg-list">' + steps + '</ol>' +
    '</div>' +
    '<div class="reg-section">' +
      '<div class="reg-section-title">注意事项</div>' +
      '<ul class="reg-list">' + notes + '</ul>' +
    '</div>' +
    '<div class="reg-note">以上信息整理自医院官网/官方公众号公开内容，具体号源、放号时间和停诊安排以医院当日公告为准。</div>';
  $("regModal").classList.add("show");
}

function closeRegModal() {
  $("regModal").classList.remove("show");
}

function copyRegistrationInfo() {
  if (!lastRegistrationInfo) {
    showToast("暂无可复制的挂号信息");
    return;
  }
  const { info, department } = lastRegistrationInfo;
  const lines = [
    "医院：" + info.name,
    department ? "科室：" + department : "",
    "放号时间：" + info.release,
    "官方渠道：",
    ...info.channels.map(c => "- " + c),
    info.site ? "官网：" + info.site : "",
    info.phone ? "电话：" + info.phone : "",
    "提示：仅提供官方渠道指引，不提供真实号源。",
  ].filter(Boolean);
  const ok = fallbackCopy(lines.join("\n"));
  showToast(ok ? "挂号信息文字版已复制" : "复制失败，请手动选择文本", 1800);
}

/* ---------- 医院服务页 / 挂号指引 / 院内导航 ---------- */
function openHospitalService(hospital) {
  if (!hospital) return;
  selectedHospital = hospital;
  const dept = (lastReport && lastReport.recommended_department) || "全科";
  const dist = hospital.distance_km == null ? "" : "直线约 " + Number(hospital.distance_km).toFixed(1) + " km";
  const level = hospital.level && hospital.level !== "地图检索" ? hospital.level : "医院";
  const meta = [level, dist].filter(Boolean).join(" · ");
  $("serviceHospital").innerHTML =
    '<div class="sh-name">' + escapeHtml(hospital.name || "医院") + '</div>' +
    '<div class="sh-meta">' + escapeHtml(meta) + '</div>' +
    '<div class="sh-address">' + escapeHtml(hospital.address || "地址以医院官方为准") + '</div>';
  $("serviceDept").textContent = "推荐科室：" + dept;
  $("hospitalServiceModal").classList.add("show");
}

function closeHospitalService() {
  $("hospitalServiceModal").classList.remove("show");
  closeNavigation();
  closeRegModal();
}

function openSelectedHospitalMap() {
  if (!selectedHospital) {
    showToast("请先选择医院");
    return;
  }
  const url = selectedHospital.map_url || hospitalMapUrl(
    selectedHospital.latitude,
    selectedHospital.longitude,
    selectedHospital.name
  );
  window.open(url, "_blank", "noopener");
}

function copySelectedHospitalAddress() {
  if (!selectedHospital) {
    showToast("请先选择医院");
    return;
  }
  const text = [
    selectedHospital.name,
    selectedHospital.address || "",
  ].filter(Boolean).join("\n");
  const ok = fallbackCopy(text);
  showToast(ok ? "医院地址已复制" : "复制失败，请手动选择文本", 1800);
}

function openRegistrationGuide() {
  if (!selectedHospital) {
    showToast("请先选择医院");
    return;
  }
  const dept = (lastReport && lastReport.recommended_department) || "";
  showRegistrationInfo(selectedHospital.name, selectedHospital.registration_channel, dept);
}

function navigationFor(name) {
  const n = String(name || "");
  if (/协和/.test(n) && /旗山/.test(n)) return NAV_DATA.xiehe_qishan;
  if (/协和/.test(n)) return NAV_DATA.xiehe_yushan;
  if (/省立/.test(n) && /金山/.test(n)) return NAV_DATA.shengli_jinshan;
  if (/省立/.test(n)) return NAV_DATA.shengli;
  if (/附属第一/.test(n)) return NAV_DATA.fuyi;
  if (/人民医院/.test(n) && !/第二/.test(n) && !/第三/.test(n)) return NAV_DATA.shengrenmin;
  return null;
}

function fillNavSelect(select, options) {
  select.innerHTML = options.map(o =>
    '<option value="' + escapeHtml(o.id) + '">' + escapeHtml(o.name) + '</option>'
  ).join("");
}

function openHospitalNavigation() {
  if (!selectedHospital) {
    showToast("请先选择医院");
    return;
  }
  const data = navigationFor(selectedHospital.name);
  if (!data) {
    showToast("暂无该医院的详细院内导航数据，可先打开高德地图", 3200);
    return;
  }
  const dept = (lastReport && lastReport.recommended_department) || "内科诊区";
  const destinations = data.destinations.map(d =>
    d.id === "dept" ? { ...d, name: dept } : d
  );
  currentNavigation = { data, hospital: selectedHospital, destinations };
  fillNavSelect($("navFrom"), data.entrances);
  fillNavSelect($("navTo"), destinations);
  $("navFrom").value = data.entrances[0].id;
  $("navTo").value = destinations[0].id;
  navActiveFloor = data.floors[0].id;
  $("navSub").textContent = selectedHospital.name + " · " + data.sub;
  $("navNote").textContent = (data.note ? data.note + " " : "") +
    "蓝色实线为推荐路线；楼层图为示意，具体以挂号单及院内导视为准。";
  $("navModal").classList.add("show");
  renderNavigation();
}

function closeNavigation() {
  $("navModal").classList.remove("show");
}

function lPath(a, b) {
  if (a[0] === b[0] || a[1] === b[1]) return [a, b];
  const horizontalFirst = Math.abs(b[0] - a[0]) >= Math.abs(b[1] - a[1]);
  const corner = horizontalFirst ? [b[0], a[1]] : [a[0], b[1]];
  return [a, corner, b];
}

function cleanPath(points) {
  return points.filter((p, i) => i === 0 || p[0] !== points[i - 1][0] || p[1] !== points[i - 1][1]);
}

function pathLength(points) {
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    total += Math.abs(points[i][0] - points[i - 1][0]) + Math.abs(points[i][1] - points[i - 1][1]);
  }
  return total;
}

function navRouteFor(data, destinations, fromId, toId) {
  const from = data.entrances.find(e => e.id === fromId) || data.entrances[0];
  const to = destinations.find(d => d.id === toId) || destinations[0];
  const first = data.floors[0];
  const last = data.floors[data.floors.length - 1];
  const sameFloor = to.id !== "dept";
  const targetFloor = sameFloor ? first : last;
  const fromPoi = first.pois.find(p => p.id === fromId) || { x: 50, y: 82, id: fromId };
  const destType = to.id === "dept" ? "department" : to.id;
  const destPoi = targetFloor.pois.find(p => p.type === destType) ||
    targetFloor.pois.find(p => p.type === "service") ||
    { x: 50, y: 50 };

  const door = navDoorPoint(fromPoi);
  const entry = [fromPoi.x, fromPoi.y];
  const dest = [destPoi.x, destPoi.y];
  const paths = {};
  const steps = [];
  let total = 0;

  if (sameFloor) {
    const route = cleanPath([door, entry, ...lPath(entry, dest).slice(1)]);
    paths[first.id] = route;
    total = pathLength(route);
    steps.push({ floor: first.id, text: "从" + from.name + "进入门诊楼" });
    steps.push({ floor: first.id, text: "沿通道前往" + to.name });
    return {
      from: fromId,
      to: toId,
      duration: "约 " + Math.max(2, Math.round(total / 18)) + " 分钟（示意）",
      floors: [first.id],
      steps,
      paths,
    };
  }

  const elevatorFirst = first.pois.find(p => p.type === "elevator");
  const elevatorLast = targetFloor.pois.find(p => p.type === "elevator");
  const firstPath = [door, entry];
  if (elevatorFirst) {
    if (fromPoi.y > 70) {
      // 南门进入时走上方横向通道，避开收费处图标和标签
      const laneY = 65;
      firstPath.push(
        [entry[0], laneY],
        [elevatorFirst.x, laneY],
        [elevatorFirst.x, elevatorFirst.y]
      );
    } else {
      firstPath.push(...lPath(entry, [elevatorFirst.x, elevatorFirst.y]).slice(1));
    }
  }
  paths[first.id] = cleanPath(firstPath);
  total += pathLength(paths[first.id]);
  steps.push({ floor: first.id, text: "从" + from.name + "进入门诊楼" });
  steps.push({ floor: first.id, text: "沿通道到电梯，乘电梯到" + targetFloor.name });

  const lastPath = [];
  if (elevatorLast) lastPath.push([elevatorLast.x, elevatorLast.y]);
  lastPath.push(...lPath(lastPath[0] || entry, dest).slice(1));
  paths[targetFloor.id] = cleanPath(lastPath);
  total += pathLength(paths[targetFloor.id]);
  steps.push({ floor: targetFloor.id, text: "出电梯后前往" + to.name });

  return {
    from: fromId,
    to: toId,
    duration: "约 " + Math.max(3, Math.round(total / 18)) + " 分钟（示意）",
    floors: [first.id, targetFloor.id],
    steps,
    paths,
  };
}

function renderNavigation() {
  if (!currentNavigation) return;
  const { data, destinations } = currentNavigation;
  const fromId = $("navFrom").value;
  const toId = $("navTo").value;
  const route = navRouteFor(data, destinations, fromId, toId);
  currentNavigation.route = route;
  const from = data.entrances.find(e => e.id === fromId) || data.entrances[0];
  const to = destinations.find(d => d.id === toId) || destinations[0];
  const duration = route.duration.replace("（示意）", "");
  $("navSummary").innerHTML =
    '<span class="route">' + escapeHtml(from.name + " → " + to.name) + '</span>' +
    '<span class="duration">' + escapeHtml(duration) + '</span>';

  const floors = route.floors && route.floors.length
    ? route.floors
    : data.floors.map(f => f.id);
  if (!floors.includes(navActiveFloor)) navActiveFloor = floors[0];
  $("navSub").textContent = currentNavigation.hospital.name + " · " + floors.join(" / ");
  $("navFloorTabs").style.display = floors.length > 1 ? "inline-flex" : "none";
  $("navFloorTabs").innerHTML = floors.map(id => {
    const floor = data.floors.find(f => f.id === id);
    return '<button type="button" class="' + (id === navActiveFloor ? "active" : "") +
      '" onclick="setNavFloor(\'' + escapeHtml(id) + '\')">' + escapeHtml(floor ? floor.name : id) + '</button>';
  }).join("");

  $("navSteps").innerHTML = route.steps.map(s =>
    '<li><strong>' + escapeHtml(s.floor) + '</strong> ' + escapeHtml(s.text) + '</li>'
  ).join("");
  renderNavMap(navActiveFloor, route);
}

function setNavFloor(floorId) {
  navActiveFloor = floorId;
  renderNavigation();
}

const NAV_TYPES = {
  entrance: { color: "#2563eb", glyph: "门", name: "入口" },
  elevator: { color: "#7c3aed", glyph: "梯", name: "电梯" },
  department: { color: "#dc2626", glyph: "科", name: "科室" },
  cashier: { color: "#d97706", glyph: "¥", name: "收费" },
  pharmacy: { color: "#16a34a", glyph: "药", name: "药房" },
  service: { color: "#64748b", glyph: "台", name: "服务台" },
};

function axisSegments(start, end, gaps) {
  const sorted = (gaps || []).slice().sort((a, b) => a[0] - b[0]);
  const out = [];
  let pos = start;
  for (const gap of sorted) {
    if (gap[0] > pos) out.push([pos, Math.min(gap[0], end)]);
    pos = Math.max(pos, gap[1]);
  }
  if (pos < end) out.push([pos, end]);
  return out.filter(([a, b]) => b - a > 0.6);
}

function navWallPath(floor) {
  const b = 4;
  const r = 96;
  const gaps = { top: [], right: [], bottom: [], left: [] };
  floor.pois.filter(p => p.type === "entrance").forEach(p => {
    if (p.y > 70) gaps.bottom.push([p.x - 6, p.x + 6]);
    else if (p.y < 30) gaps.top.push([p.x - 6, p.x + 6]);
    else if (p.x < 20) gaps.left.push([p.y - 6, p.y + 6]);
    else if (p.x > 80) gaps.right.push([p.y - 6, p.y + 6]);
  });
  const walls = [];
  axisSegments(b, r, gaps.top).forEach(([a, z]) => walls.push("M" + a + " " + b + " L" + z + " " + b));
  axisSegments(b, r, gaps.bottom).forEach(([a, z]) => walls.push("M" + a + " " + r + " L" + z + " " + r));
  axisSegments(b, r, gaps.left).forEach(([a, z]) => walls.push("M" + b + " " + a + " L" + b + " " + z));
  axisSegments(b, r, gaps.right).forEach(([a, z]) => walls.push("M" + r + " " + a + " L" + r + " " + z));
  return walls.join(" ");
}

function navDoorPoint(p) {
  if (p.y > 70) return [p.x, 96];
  if (p.y < 30) return [p.x, 4];
  if (p.x < 20) return [4, p.y];
  if (p.x > 80) return [96, p.y];
  return [p.x, p.y];
}

function rectsOverlap(a, b, pad = 1) {
  return a.x < b.x + b.w + pad && a.x + a.w + pad > b.x &&
    a.y < b.y + b.h + pad && a.y + a.h + pad > b.y;
}

function segmentHitsRect(a, b, rect, pad = 1) {
  const x1 = Math.min(a[0], b[0]);
  const x2 = Math.max(a[0], b[0]);
  const y1 = Math.min(a[1], b[1]);
  const y2 = Math.max(a[1], b[1]);
  if (a[1] === b[1]) {
    return a[1] >= rect.y - pad && a[1] <= rect.y + rect.h + pad &&
      x2 >= rect.x - pad && x1 <= rect.x + rect.w + pad;
  }
  if (a[0] === b[0]) {
    return a[0] >= rect.x - pad && a[0] <= rect.x + rect.w + pad &&
      y2 >= rect.y - pad && y1 <= rect.y + rect.h + pad;
  }
  return !(x2 < rect.x - pad || x1 > rect.x + rect.w + pad ||
    y2 < rect.y - pad || y1 > rect.y + rect.h + pad);
}

function placeNavLabel(p, width, routeSegments, placedRects, otherPois) {
  const height = 5.6;
  const candidates = [
    { x: p.x - width / 2, y: p.y - 8 - height / 2, prefs: 0 },
    { x: p.x - width / 2, y: p.y + 9 - height / 2, prefs: 1 },
    { x: p.x - width - 6, y: p.y - height / 2, prefs: 2 },
    { x: p.x + 6, y: p.y - height / 2, prefs: 3 },
  ];
  let best = null;
  for (const c of candidates) {
    const rect = {
      x: Math.max(3, Math.min(97 - width, c.x)),
      y: Math.max(3, Math.min(97 - height, c.y)),
      w: width, h: height,
    };
    let score = c.prefs;
    if (routeSegments.some(seg => segmentHitsRect(seg[0], seg[1], rect, 1.2))) score += 100;
    if (placedRects.some(other => rectsOverlap(rect, other, 1))) score += 80;
    if (otherPois.some(q => q !== p &&
      q.x >= rect.x - 2 && q.x <= rect.x + rect.w + 2 &&
      q.y >= rect.y - 2 && q.y <= rect.y + rect.h + 2)) score += 30;
    if (!best || score < best.score) best = { rect, score };
  }
  placedRects.push(best.rect);
  return best.rect;
}

function doorGapLabelRect(p, width) {
  const height = 5.6;
  let x = p.x - width / 2;
  let y = p.y - height / 2;
  if (p.y > 70) {
    // 南门：贴在底墙缺口内侧
    y = 96 - height - 1;
  } else if (p.y < 30) {
    // 北门：贴在顶墙缺口内侧
    y = 5;
  } else if (p.x < 20) {
    // 西门：左墙缺口内侧，垂直居中；给内部路线留出空间
    x = 2;
  } else if (p.x > 80) {
    // 东门：右墙缺口内侧，垂直居中；给内部路线留出空间
    x = 96 - width - 2;
  }
  return {
    x: Math.max(3, Math.min(97 - width, x)),
    y: Math.max(3, Math.min(97 - height, y)),
    w: width,
    h: height,
  };
}

function renderNavMap(floorId, route) {
  if (!currentNavigation) return;
  const data = currentNavigation.data;
  const floor = data.floors.find(f => f.id === floorId);
  if (!floor) {
    $("navMap").innerHTML = "";
    return;
  }

  const firstFloorId = data.floors[0].id;
  const fromPoi = floorId === firstFloorId
    ? floor.pois.find(p => p.id === route.from)
    : null;
  let path = (route.paths && route.paths[floorId]) || null;
  if (path && fromPoi) {
    const door = navDoorPoint(fromPoi);
    if (!path[0] || path[0][0] !== door[0] || path[0][1] !== door[1]) {
      path = [door, ...path];
    }
  }
  const points = path ? path.map(p => p[0] + "," + p[1]).join(" ") : "";
  const pathHalo = points
    ? '<polyline points="' + points + '" fill="none" stroke="#dbeafe" stroke-width="4.6" stroke-linecap="round" stroke-linejoin="round"/>'
    : "";
  const pathHtml = points
    ? '<polyline points="' + points + '" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
    : "";

  const destType = route.to === "dept" ? "department" : route.to;
  const destPoi = floor.pois.find(p => p.type === destType);
  const startPoi = floor.pois.find(p => p.id === route.from);

  const routeSegments = [];
  if (path) {
    for (let i = 1; i < path.length; i++) routeSegments.push([path[i - 1], path[i]]);
  }
  const placedRects = [];
  const labels = floor.pois.map(p => {
    const type = NAV_TYPES[p.type] || NAV_TYPES.service;
    const isStart = p === startPoi;
    const isDest = p === destPoi;
    const prefix = isStart ? "起点 " : isDest ? "终点 " : "";
    const text = prefix + p.name;
    const color = isStart ? "#2563eb" : isDest ? "#dc2626" : type.color;
    const isDoor = p.type === "entrance";
    const width = (isDoor && !isStart)
      ? text.length * 3.1 + 4.6
      : text.length * 3.35 + 5.4;
    const glyphSize = p.type === "cashier" ? 4.7 : 3.2;
    const marker = isDoor
      ? ""
      : '<circle cx="' + p.x + '" cy="' + p.y + '" r="4.4" fill="#fff" stroke="' + color + '" stroke-width="1.7"/>' +
        '<text x="' + p.x + '" y="' + (p.y + (p.type === "cashier" ? 1.6 : 1.2)) + '" text-anchor="middle" font-size="' + glyphSize + '" font-weight="700" fill="' + color + '">' + type.glyph + '</text>';
    // 没走的门固定在墙洞中间；走的门才参与避让布局
    let rect;
    if (isDoor && !isStart) {
      rect = doorGapLabelRect(p, width);
      placedRects.push(rect);
    } else {
      rect = placeNavLabel(p, width, routeSegments, placedRects, floor.pois);
    }
    return '<g>' +
      marker +
      '<rect x="' + rect.x + '" y="' + rect.y + '" width="' + rect.w + '" height="' + rect.h + '" rx="2.8" fill="#fff" stroke="' + color + '" stroke-opacity=".35" stroke-width=".45"/>' +
      '<text x="' + (rect.x + rect.w / 2) + '" y="' + (rect.y + rect.h / 2 + 1.2) + '" text-anchor="middle" font-size="3.35" font-weight="600" fill="' + color + '">' + escapeHtml(text) + '</text>' +
      '</g>';
  }).join("");

  $("navMap").innerHTML =
    '<svg viewBox="0 0 100 100" role="img" aria-label="' + escapeHtml(floor.name) + ' 示意图">' +
      '<rect x="4" y="4" width="92" height="92" rx="3" fill="#ffffff"/>' +
      '<path d="M12 52 H88 M52 12 V88" stroke="#f1f5f9" stroke-width="7" fill="none"/>' +
      '<path d="' + navWallPath(floor) + '" fill="none" stroke="#cbd5e1" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="square"/>' +
      pathHalo + pathHtml + labels +
    '</svg>';
}

function copyNavigationRoute() {
  if (!currentNavigation || !currentNavigation.route) {
    showToast("暂无路线可复制");
    return;
  }
  const { data, destinations, route } = currentNavigation;
  const from = data.entrances.find(e => e.id === route.from) || data.entrances[0];
  const to = destinations.find(d => d.id === route.to) || destinations[0];
  const lines = [
    "医院：" + selectedHospital.name,
    "路线：" + from.name + " → " + to.name,
    "预计：" + route.duration,
    "步骤：",
    ...route.steps.map((s, i) => (i + 1) + ". [" + s.floor + "] " + s.text),
    "提示：楼层图为示意，具体以院内导视为准。",
  ];
  const ok = fallbackCopy(lines.join("\n"));
  showToast(ok ? "文字路线已复制" : "复制失败，请手动选择文本", 1800);
}

function renderHospitals(items, label, source, accuracy = null) {
  const list = $("hospitalList");
  if (!items || !items.length) {
    list.innerHTML = '<div class="hospital-empty">附近暂无可推荐医院。<br>可以尝试手动选择区域，或直接通过 114 平台查询。</div>';
    return;
  }
  let warning = "";
  if (accuracy && accuracy > 1500) {
    const acc = accuracy >= 1000 ? (accuracy / 1000).toFixed(1) + "km" : Math.round(accuracy) + "m";
    warning = '<div class="hospital-warning">当前浏览器定位精度约 ±' + acc + '，距离仅供参考；建议点“手动选择区域”重新定位。</div>';
  }
  list.innerHTML = warning + items.map((h, idx) => {
    const dist = h.distance_km == null ? "距离未知" : "直线约 " + Number(h.distance_km).toFixed(1) + " km";
    return '<div class="hospital-item" data-index="' + idx + '" role="button" tabindex="0" aria-label="选择' + escapeHtml(h.name || "医院") + '">' +
      '<div class="hn">' + escapeHtml(h.name || "医院") + '</div>' +
      '<div class="hm"><span>' + escapeHtml(h.level || "医院") + '</span><span>' + dist + '</span>' +
      '<span>' + (source === "amap" ? "高德地图" : "本地医院库") + '</span></div>' +
      '<div class="ha">' + escapeHtml(h.address || "地址以医院官方为准") + '</div>' +
      '<div class="hselect">›</div>' +
      '</div>';
  }).join("");
  list.querySelectorAll(".hospital-item").forEach(card => {
    const open = () => openHospitalService(items[Number(card.dataset.index)]);
    card.addEventListener("click", open);
    card.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    });
  });
}

function call120() {
  if (/Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)) {
    window.location.href = "tel:120";
  } else {
    alert("演示环境：请立即拨打 120 急救电话。");
  }
}

function simulateNormal() {
  if (sending) return;
  if (chatFinished) newSession();
  $("msgInput").value = "我肚子疼了两天，偶尔反酸，我是成年人";
  sendMsg();
}

function simulateEmergency() {
  const text = "我突然剧烈胸痛，还出冷汗";
  addBubble("user", text);
  conversation.push({ role: "user", content: text });
  handleResponse({
    type: "urgent",
    message: "您描述的症状属于紧急情况，请立即到急诊科就诊或拨打 120。",
    data: { level: "emergency", label: "紧急", keyword: "胸痛伴出冷汗" },
  });
}

/* ============================================================
 * 历史记录
 * ============================================================ */
function loadLocalHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (raw) {
      const data = JSON.parse(raw);
      if (Array.isArray(data)) {
        memoryHistory = data;
        storageWritable = true;
        return data;
      }
    }
  } catch (_) {
    storageWritable = false;
  }
  // 隐私模式、file:// 限制或存储满时，至少保证本次页面内能看历史
  return memoryHistory.slice();
}

function persistLocalHistory(items) {
  const trimmed = items.slice(0, MAX_LOCAL_HISTORY);
  memoryHistory = trimmed;
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(trimmed));
    storageWritable = true;
    return true;
  } catch (_) {
    storageWritable = false;
    return false;
  }
}

function saveLocalConversation(status, result) {
  if (!conversation.length) return false;
  const now = new Date().toISOString();
  const firstUser = conversation.find(m => m.role === "user");
  const id = sessionId;
  const current = loadLocalHistory();
  const prev = current.find(x => x.id === id);
  const items = current.filter(x => x.id !== id);
  items.unshift({
    id,
    title: ((firstUser && firstUser.content) || "未命名会话").slice(0, 28),
    status,
    department: result && result.recommended_department ? result.recommended_department : null,
    urgent_level: result && result.level ? result.level : null,
    message_count: conversation.length,
    created_at: (prev && prev.created_at) || now,
    updated_at: now,
    messages: conversation.slice(),
    result: result || null,
  });
  return persistLocalHistory(items);
}

async function loadHistoryItems(force = false) {
  if (!force && historyCache.length) {
    renderRecent();
    return historyCache;
  }
  const local = loadLocalHistory();
  if (mode !== "mock") {
    try {
      const data = await fetchJSON(HISTORY_API + "?limit=50", {}, 5000);
      const items = (data && data.items) || [];
      // 后端为空但本机有记录：可能是历史表写入失败，先给用户看本机缓存
      if (items.length || !local.length) {
        historyCache = items;
        historySource = "backend";
        renderRecent();
        if ($("view-history").classList.contains("active")) renderHistoryPage();
        return historyCache;
      }
    } catch (_) {
      // 后端不可用 → 本地历史兜底
    }
  }
  historyCache = local.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
  historySource = "local";
  renderRecent();
  if ($("view-history").classList.contains("active")) renderHistoryPage();
  return historyCache;
}

function refreshHistorySoon() {
  clearTimeout(refreshHistorySoon._timer);
  refreshHistorySoon._timer = setTimeout(() => loadHistoryItems(true), 350);
}

function statusOf(item) {
  if (item.status === "interrupted") return ["急症拦截", "urgent"];
  if (item.status === "ready") return [item.department || "已生成报告", "result"];
  return ["进行中", "collecting"];
}

function renderRecent() {
  const section = $("recentSection");
  const list = $("recentList");
  if (!section || !list) return;
  const items = historyCache.slice(0, 3);
  if (!items.length) {
    section.style.display = "none";
    return;
  }
  section.style.display = "block";
  list.innerHTML = items.map(item => {
    const [label, cls] = statusOf(item);
    return '<div class="recent-item" role="button" tabindex="0" data-id="' + escapeHtml(item.id) + '" onclick="openHistoryDetail(\'' + escapeHtml(item.id) + '\')">' +
      '<div class="rt">' + escapeHtml(titleOf(item)) + "</div>" +
      '<div class="rm"><span class="badge ' + cls + '">' + escapeHtml(label) + "</span><span>" + escapeHtml(formatTime(item.updated_at)) + "</span></div>" +
      "</div>";
  }).join("");
}

function renderHistoryPage() {
  const list = $("historyList");
  if (!list) return;
  if (!historyCache.length) {
    list.innerHTML = '<div class="history-empty"><span class="big">🕘</span>还没有历史记录<br>完成一次咨询后，会自动保存在这里。</div>';
    return;
  }
  list.innerHTML = historyCache.map(item => {
    const [label, cls] = statusOf(item);
    return '<div class="history-item" role="button" tabindex="0" data-id="' + escapeHtml(item.id) + '" onclick="openHistoryDetail(\'' + escapeHtml(item.id) + '\')">' +
      '<div class="row"><span class="hi-title">' + escapeHtml(titleOf(item)) + '</span><span class="hi-time">' + escapeHtml(formatTime(item.updated_at)) + "</span></div>" +
      '<div class="hi-meta"><span class="badge ' + cls + '">' + escapeHtml(label) + "</span>" +
      "<span>" + (Number(item.message_count) || 0) + " 条消息</span>" +
      '<button class="hi-del" type="button" onclick="event.stopPropagation();deleteHistoryItem(\'' + escapeHtml(item.id) + '\')">删除</button></div>' +
      "</div>";
  }).join("");
}

async function openHistory() {
  show("view-history");
  $("historyList").innerHTML = '<div class="history-empty">正在加载…</div>';
  await loadHistoryItems(true);
  renderHistoryPage();
}

async function openHistoryDetail(id) {
  let detail = historyCache.find(x => x.id === id) || null;
  if (historySource === "backend") {
    try {
      detail = await fetchJSON(HISTORY_API + "/" + encodeURIComponent(id), {}, 8000);
    } catch (_) {
      showToast("历史详情加载失败，请稍后重试");
      return;
    }
  }
  if (!detail) {
    showToast("没有找到这条记录");
    return;
  }
  currentHistory = detail;
  $("historyDetailTitle").textContent = titleOf(detail);
  $("historyDetailMeta").textContent = formatTime(detail.updated_at) + " · " + ((detail.message_count || (detail.messages || []).length) + " 条消息");
  renderTranscript(detail.messages || []);
  const label = detail.status === "interrupted" ? "该会话触发了急症拦截，建议直接就医或拨打 120，不建议继续在线分诊。"
    : "继续对话会恢复上次的多轮上下文；如果后端已重启，仍可从历史快照恢复。";
  $("historyDetailNote").textContent = label;
  const resume = $("historyResumeBtn");
  if (detail.status === "interrupted" || historySource === "local") {
    resume.style.display = "none";
  } else {
    resume.style.display = "block";
    resume.textContent = "继续对话";
  }
  $("historyDeleteBtn").style.display = "block";
  $("historyDetailOverlay").classList.add("show");
}

function renderTranscript(messages) {
  const wrap = $("historyTranscript");
  wrap.innerHTML = "";
  if (!messages || !messages.length) {
    wrap.innerHTML = '<div class="history-empty">这条记录没有可展示的消息。</div>';
    return;
  }
  messages.forEach(m => {
    const row = document.createElement("div");
    row.className = "msg-row " + (m.role === "user" ? "user" : "ai");
    const avatar = document.createElement("div");
    avatar.className = "avatar " + (m.role === "user" ? "user" : "ai");
    avatar.textContent = m.role === "user" ? "👤" : "AI";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = m.content || "";
    row.appendChild(avatar);
    row.appendChild(bubble);
    wrap.appendChild(row);
  });
}

function closeHistoryDetail() {
  $("historyDetailOverlay").classList.remove("show");
}

async function deleteCurrentHistory() {
  if (!currentHistory) return;
  const id = currentHistory.id;
  const ok = await askConfirm("删除这条历史？", "删除后不可恢复。", "删除");
  if (!ok) return;
  const deleted = await deleteHistoryItem(id, true);
  if (deleted) closeHistoryDetail();
}

async function deleteHistoryItem(id, silent = false) {
  if (!silent) {
    const ok = await askConfirm("删除这条历史？", "删除后不可恢复。", "删除");
    if (!ok) return false;
  }
  let backendDeleted = false;
  if (historySource === "backend") {
    try {
      await fetchJSON(HISTORY_API + "/" + encodeURIComponent(id), { method: "DELETE" }, 8000);
      backendDeleted = true;
    } catch (_) {
      // 后端不可用也要让按钮生效：先删本机缓存，避免“点了没反应”
      showToast("后端暂不可用，已从本机历史移除");
    }
  }
  const local = loadLocalHistory().filter(x => x.id !== id);
  persistLocalHistory(local);
  historyCache = historyCache.filter(x => x.id !== id);
  renderRecent();
  renderHistoryPage();
  if (!silent) showToast(backendDeleted ? "已删除" : "已从本机历史移除");
  return true;
}

async function clearAllHistory() {
  const local = loadLocalHistory();
  if (!historyCache.length && !local.length) { showToast("暂无历史记录"); return; }
  const ok = await askConfirm("清空全部历史？", "所有历史记录将被删除，此操作不可恢复。", "清空");
  if (!ok) return;
  let backendCleared = false;
  if (historySource === "backend") {
    try {
      await fetchJSON(HISTORY_API, { method: "DELETE" }, 8000);
      backendCleared = true;
    } catch (_) {
      showToast("后端暂不可用，已清空本机历史");
    }
  }
  persistLocalHistory([]);
  historyCache = [];
  renderRecent();
  renderHistoryPage();
  showToast(backendCleared ? "历史记录已清空" : "本机历史已清空");
}

async function resumeCurrentHistory() {
  if (!currentHistory || historySource !== "backend") {
    showToast("本地演示模式暂不支持继续对话，请重新咨询");
    return;
  }
  try {
    const data = await fetchJSON(HISTORY_API + "/" + encodeURIComponent(currentHistory.id) + "/restore", { method: "POST" }, 8000);
    sessionId = data.session_id;
    conversation = (data.messages || []).map(m => ({ role: m.role, content: m.content }));
    chatFinished = false;
    $("chatList").innerHTML = "";
    conversation.forEach(m => addBubble(m.role === "user" ? "user" : "ai", m.content));
    closeHistoryDetail();
    show("view-chat");
    setMode("backend", "在线服务 · 已恢复历史会话");
    showToast("已恢复上次对话");
    $("msgInput").focus();
  } catch (err) {
    const msg = err && err.status === 409 ? "该历史会话缺少可恢复状态，请重新咨询。" : "恢复失败，请稍后重试。";
    showToast(msg);
  }
}

/* ============================================================
 * 本地模拟后端（无后端时保证可演示）
 * ============================================================ */
const EMERGENCY = ["胸痛伴出冷汗", "剧烈胸痛", "大出血", "昏迷", "抽搐", "呼吸困难", "窒息", "煤气中毒", "心脏骤停", "胎动", "卡住了", "撕裂样疼痛", "雷击样头痛", "血压200以上", "烂苹果味"];
const URGENT = ["高烧", "口角歪斜", "说话含糊", "剧烈腹痛"];
const PART_MAP = [["后脑勺","头部"],["脑袋","头部"],["额头","面部"],["下巴","面部"],["腮帮","面部"],["胸部","胸部"],["胸口","胸部"],["心口","胸部"],["心脏","胸部"],["乳房","乳房"],["乳腺","乳房"],["乳头","乳房"],["腹部","腹部"],["肚子","腹部"],["眼睛","眼部"],["耳朵","耳鼻喉"],["嗓子","咽喉"],["咽喉","咽喉"],["喉咙","咽喉"],["肩膀","肩膀"],["颈椎","颈椎"],["脖子","颈椎"],["背部","腰背"],["手臂","手臂"],["手腕","手腕"],["手指","手指"],["下肢","下肢"],["脚踝","脚踝"],["脚腕","脚踝"],["脚趾","脚趾"],["皮肤","皮肤"],["口腔","口腔"],["生殖器","生殖器"],["阴茎","生殖器"],["龟头","生殖器"],["包皮","生殖器"],["睾丸","生殖器"],["蛋蛋","生殖器"],["阴囊","生殖器"],["阴唇","生殖器"],["外阴","生殖器"],["阴道","生殖器"],["前列腺","生殖器"],["白带","生殖器"],["下面","生殖器"],["私处","生殖器"],["裆部","生殖器"],["腹股沟","腹股沟"],["肛门","肛门"],["屁眼","肛门"],["痔疮","肛门"],["屁股","臀部"],["泌尿","尿道"],["头","头部"],["腮","面部"],["胸","胸部"],["胃","腹部"],["腹","腹部"],["眼","眼部"],["耳","耳鼻喉"],["鼻","耳鼻喉"],["关节","关节"],["膝盖","关节"],["肩","肩膀"],["颈","颈椎"],["脖","颈椎"],["背","腰背"],["腰","腰背"],["手","手臂"],["腿","下肢"],["脚","下肢"],["牙","口腔"],["尿","尿道"]];
const NATURE = [["闷","闷"],["胀","胀"],["痛","痛"],["疼","痛"],["烧心","烧灼"],["反酸","反酸"],["痒","痒"],["麻","麻"],["头晕","头晕"],["眩晕","眩晕"],["乏力","乏力"],["恶心","恶心"],["呕吐","呕吐"],["咳嗽","咳嗽"],["发烧","发烧"],["发热","发热"],["腹泻","腹泻"],["心慌","心慌"],["心悸","心悸"],["喘","气短"],["气短","气短"],["酸","酸"],["灼","烧灼"],["晕","头晕"],["咳","咳嗽"],["吐","呕吐"],["泻","腹泻"],["烧","发烧"],["扭伤","扭伤"],["挫伤","扭伤"]];
const ACCOMPANY = ["出冷汗","发热","发烧","腹泻","呕吐","恶心","反酸","耳鸣","失眠"];
const POPULATION = ["成年人","成人","老人","老年人","儿童","孩子","小孩","孕妇"];
const DURATION_RE = /([一二两三四五六七八九十0-9]+(?:天|周|个月|小时|月|年|日))/;
const DEPT_RULES = [
  { kw: ["阴茎","龟头","包皮","睾丸","蛋蛋","阴囊","前列腺","尿痛","尿频","尿急","血尿","下体","私处","裆部","腹股沟","尿不尽","尿失禁","下面痒","私处痒","阴部痒","下面疼","下面痛"], dept: "泌尿外科", alt: ["普通内科","全科"] },
  { kw: ["阴道","外阴","阴唇","白带","子宫","卵巢","盆腔炎","更年期","月经不调","月经","痛经"], dept: "妇科", alt: ["全科"] },
  { kw: ["乳房","乳腺","乳头","溢乳","乳房肿块","乳房胀痛"], dept: "乳腺外科", alt: ["普通内科","全科"] },
  { kw: ["肛门","痔疮","肛裂","便血","屁眼","脱肛"], dept: "肛肠科", alt: ["普通外科","全科"] },
  { kw: ["胸口闷","胸闷","心慌","心悸","胸痛","气短"], dept: "心血管内科", alt: ["呼吸内科","急诊科"] },
  { kw: ["肚子疼","肚子痛","腹痛","反酸","烧心","腹泻","拉肚子","胃痛"], dept: "消化内科", alt: ["全科"] },
  { kw: ["咳嗽","咳痰","发烧","发热","喉咙痛","咽痛","流鼻涕","鼻塞"], dept: "呼吸内科", alt: ["普通内科"] },
  { kw: ["头痛","头晕","眩晕"], dept: "神经内科", alt: ["全科"] },
  { kw: ["皮肤","痒","疹","风团","湿疹","荨麻疹"], dept: "皮肤科", alt: ["变态反应科"] },
  { kw: ["脚踝","脚腕","扭伤","骨折","脚趾","手指","手腕","腰椎","落枕"], dept: "骨科", alt: ["疼痛科","康复科"] },
  { kw: ["孩子","儿童","小孩"], dept: "儿科", alt: ["全科"] },
  { kw: ["眼睛","眼","视物模糊"], dept: "眼科", alt: ["全科"] },
  { kw: ["牙痛","牙龈","口腔溃疡","智齿"], dept: "口腔科", alt: ["普通内科"] },
  { kw: ["鼻塞","流鼻血","耳鸣","听力下降","咽喉炎"], dept: "耳鼻喉科", alt: ["呼吸内科"] },
];

let S = { collected: { part: null, nature: null, duration: null, population: null, accompanying: [] }, asked: [], turns: 0, history: [] };
function resetState() {
  S = { collected: { part: null, nature: null, duration: null, population: null, accompanying: [] }, asked: [], turns: 0, history: [] };
}
resetState();

function extract(text) {
  const r = { part: null, nature: null, duration: null, population: null, accompanying: [] };
  for (const [w, v] of PART_MAP) { if (text.includes(w)) { r.part = v; break; } }
  for (const [w, v] of NATURE) { if (text.includes(w)) { r.nature = v; break; } }
  const m = text.match(DURATION_RE); if (m) r.duration = m[1];
  for (const w of ACCOMPANY) { if (text.includes(w) && !r.accompanying.includes(w)) r.accompanying.push(w); }
  for (const w of POPULATION) { if (text.includes(w)) { r.population = w; break; } }
  return r;
}

function matchDept(fullText) {
  for (const rule of DEPT_RULES) if (rule.kw.some(k => fullText.includes(k))) return rule;
  return null;
}

function buildDoctorScript() {
  const c = S.collected;
  const place = c.part || "身体";
  const natMap = { "痛": "疼痛", "闷": "发闷", "胀": "胀痛", "酸": "酸痛", "麻": "发麻", "痒": "瘙痒" };
  const nature = natMap[c.nature] || c.nature || "不适";
  let s = "医生您好，";
  if (c.duration) s += "我最近" + c.duration + "出现" + place + nature;
  else s += "我最近" + place + nature;
  if (c.accompanying && c.accompanying.length) s += "，还伴有" + c.accompanying.join("、");
  s += "。";
  if (c.population && !["成人", "成年人"].includes(c.population)) s += "我是" + c.population + "。";
  s += "想请您看看需要做什么检查、日常要注意什么，谢谢。";
  return s;
}

function mockDispatch(message) {
  S.history.push({ role: "user", content: message });
  for (const kw of EMERGENCY) {
    if (message.includes(kw)) return { type: "urgent", data: { level: "emergency", label: "紧急", keyword: kw, llm_provider: "mock" }, message: "您描述的症状属于紧急情况，请立即到急诊科就诊或拨打 120。" };
  }
  for (const kw of URGENT) {
    if (message.includes(kw)) return { type: "urgent", data: { level: "urgent", label: "次紧急", keyword: kw, llm_provider: "mock" }, message: "症状需要尽快就医，建议前往急诊或相应科室。" };
  }
  const correction = /说错了|搞错|弄错|改口|更正/.test(message) || /不是.{0,20}(?:而是|是)/.test(message);
  let current = message;
  if (correction) {
    S.collected = { part: null, nature: null, duration: null, population: null, accompanying: [] };
    S.asked = []; S.turns = 0;
    const parts = message.split(/(?:而是|说错了[，,：:]?|搞错[了]?[，,：:]?|改口[，,：:]?|更正[，,：:]?)/);
    current = parts[parts.length - 1] || message;
  }
  const info = extract(current);
  for (const f of ["part", "nature", "duration", "population"]) if (!S.collected[f] && info[f]) S.collected[f] = info[f];
  for (const a of info.accompanying) if (!S.collected.accompanying.includes(a)) S.collected.accompanying.push(a);

  const Q = [["part", "您具体是哪个部位不舒服呢？"], ["nature", "这种不适是什么感觉呢？比如是痛、闷、胀还是其他？"], ["duration", "这个症状大概持续多长时间了？"], ["population", "请问就诊对象是成人、老人、儿童还是孕妇呢？"]];
  const missing = Q.filter(([f, q]) => !S.collected[f] && !S.asked.includes(q));
  if (missing.length && S.turns < 3) {
    S.turns++;
    const q = missing[0][1];
    S.asked.push(q);
    return { type: "question", message: q, data: { collected: S.collected } };
  }

  const fullText = S.history.map(h => h.content).join(" ");
  const rule = matchDept(fullText);
  const dept = rule ? rule.dept : "综合门诊";
  const alt = rule ? rule.alt : [];
  return {
    type: "result",
    data: {
      recommended_department: dept,
      alternative_departments: alt,
      basis: [rule ? "根据您描述的" + (S.collected.part || "症状") + (S.collected.nature || "") + "（" + (S.collected.duration || "近期") + "），建议优先就诊" + dept + "。" : "根据您描述的症状初步判断，建议先挂综合门诊/全科评估。"],
      booking_guide: "可前往医院官方挂号平台或到院挂号窗口，选择「" + dept + "」进行挂号。",
      navigation: "挂号完成后，请根据医院院内导视前往" + dept + "所在楼层，如有疑问可询问导诊台。",
      dialogue_script: buildDoctorScript(),
      reminders: ["建议携带既往病历和检查报告", "如症状加重请及时就医"],
      disclaimer: "本产品仅供分诊参考，不替代医生诊断。",
      llm_provider: "mock",
      degraded: false,
    },
  };
}

/* ============================================================
 * 初始化 / 快捷键
 * ============================================================ */
$("msgInput").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMsg();
  }
});

$("hospitalAddress").addEventListener("keydown", e => {
  if (e.key === "Enter") {
    e.preventDefault();
    resolveAddress();
  }
});

$("navFrom").addEventListener("change", () => {
  navActiveFloor = "";
  renderNavigation();
});
$("navTo").addEventListener("change", () => {
  navActiveFloor = "";
  renderNavigation();
});

document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    if ($("confirmModal").classList.contains("show")) {
      resolveConfirm(false);
      return;
    }
    if ($("navModal").classList.contains("show")) {
      closeNavigation();
      return;
    }
    if ($("regModal").classList.contains("show")) {
      closeRegModal();
      return;
    }
    if ($("hospitalServiceModal").classList.contains("show")) {
      closeHospitalService();
      return;
    }
    if ($("hospitalModal").classList.contains("show")) {
      closeHospitalModal();
      return;
    }
    closeReport();
    closeHistoryDetail();
    return;
  }
  // 历史卡片键盘可达：Enter / Space 打开详情
  if (e.key === "Enter" || e.key === " ") {
    const target = e.target;
    if (target && target.closest && target.closest("button")) return;
    const card = target && target.closest ? target.closest(".recent-item, .history-item") : null;
    if (card && card.dataset.id) {
      e.preventDefault();
      openHistoryDetail(card.dataset.id);
    }
  }
});

(function initClock() {
  const el = document.querySelector(".statusbar span");
  if (!el) return;
  const tick = () => {
    const d = new Date();
    el.textContent = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  };
  tick();
  setInterval(tick, 30000);
})();

function apiCandidates() {
  const list = [API_BASE, DEFAULT_API_BASE, "http://127.0.0.1:8000", "http://localhost:8000"];
  return [...new Set(list.map(normalizeBase).filter(Boolean))];
}

async function probeHealth(base) {
  try {
    const h = await fetchJSON(
      base + "/api/v1/health?t=" + Date.now(),
      { mode: "cors", cache: "no-store" },
      2200
    );
    return h && h.status === "ok" ? h : null;
  } catch (_) {
    return null;
  }
}

function stopReconnect() {
  if (reconnectTimer) {
    clearInterval(reconnectTimer);
    reconnectTimer = null;
  }
}

function startReconnect() {
  if (reconnectTimer) return;
  // 后端没启动时每 5 秒静默探测；启动后自动切回在线，不用刷新页面
  reconnectTimer = setInterval(() => {
    if (!backendOnline) checkHealth(true);
  }, 5000);
}

function onBackendConnected(h) {
  const wasMock = mode === "mock";
  backendOnline = true;
  lastBackendFailure = 0;
  stopReconnect();
  if (h && h.llm_ready) {
    setMode("backend", "在线 · 真实模型" + (h.llm_model ? " · " + h.llm_model : ""));
  } else {
    setMode("backend", "在线服务 · 本地稳定模式");
  }
  if (wasMock) showToast("已连接后端服务", 1800);
  if ($("view-history").classList.contains("active")) loadHistoryItems(true);
}

async function checkHealth(force = false) {
  if (healthPromise) return healthPromise;
  healthPromise = (async () => {
    const bases = apiCandidates();
    for (const base of bases) {
      const h = await probeHealth(base);
      if (h) {
        setApiBase(base);
        onBackendConnected(h);
        return true;
      }
    }
    const wasOnline = backendOnline;
    backendOnline = false;
    lastBackendFailure = Date.now();
    setMode("mock", "本地演示模式");
    startReconnect();
    if (wasOnline) showToast("后端连接中断，已切本地演示模式", 2400);
    return false;
  })();
  try {
    return await healthPromise;
  } finally {
    healthPromise = null;
  }
}

window.addEventListener("focus", () => checkHealth(true));
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) checkHealth(true);
});

show("view-start");
checkHealth();
loadHistoryItems(true);

/* 调试/演示直达：#chat / #emergency / #history / #report */
(function initFromHash() {
  const h = (location.hash || "").replace("#", "");
  if (h === "chat") {
    show("view-chat");
    addBubble("ai", "你好，我是 AI 智能导诊助手。请详细描述您的症状。");
  } else if (h === "emergency") {
    show("view-emergency");
  } else if (h === "history") {
    openHistory();
  } else if (h === "report") {
    show("view-chat");
    addBubble("ai", "你好，我是 AI 智能导诊助手。请详细描述您的症状。");
    addBubble("user", "医生，我肚子疼了三天了，在右下边，有点难受。");
    addBubble("ai", "了解到您右下腹痛。请问疼痛是持续性的还是阵发性的？有没有伴随发烧或恶心呕吐？");
    addBubble("user", "一直疼，而且感觉有点发烧。");
    setTimeout(() => showReport({
      recommended_department: "消化内科",
      alternative_departments: ["全科"],
      basis: ["您描述的右下腹持续疼痛伴发热，建议优先到消化内科评估；如疼痛剧烈或加重，请及时急诊。"],
      booking_guide: "可前往医院官方挂号平台或到院挂号窗口，选择「消化内科」进行挂号。",
      navigation: "挂号完成后，请根据院内导视前往消化内科所在楼层。",
      dialogue_script: "医生您好，我肚子疼了三天了，在右下边，一直疼，而且感觉有点发烧，想请您看看需要做什么检查。",
      reminders: ["建议携带既往病历和检查报告", "如疼痛加剧、发热加重请及时就医"],
      disclaimer: "本产品仅供分诊参考，不替代医生诊断。",
      llm_provider: "mock",
      degraded: false,
    }), 300);
  }
})();




