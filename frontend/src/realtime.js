// 实时推送：整个页面共用一条 SSE 连接（/events/stream），把服务器事件分发给各页面。
//
// 事件：new_mail（新邮件，带各文件夹数量）、mail_changed（在手机 / Gmail 网页上读过、删除的邮件
// 已同步到本地）、sync_done（某账号同步结束）、purge_done（批量永久删除屏蔽地址的邮件结束）、
// open（连接建立，first 表示首次连接）。
import { store } from "./store";

const listeners = new Map();

export function onRealtime(type, fn) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(fn);
  return () => listeners.get(type)?.delete(fn);
}

function emit(type, detail) {
  for (const fn of listeners.get(type) || []) {
    try {
      fn(detail);
    } catch (err) {
      console.error(err);
    }
  }
}

// 浏览器只在网络断开时自动重连；服务器返回错误（会话过期、部署期间 502）时
// EventSource 会彻底关闭，需要自己退避重建，否则状态一直停在「正在重连」。
const RETRY_MIN_MS = 5000;
const RETRY_MAX_MS = 60000;
let retryMs = RETRY_MIN_MS;
let retryTimer = null;
let firstOpen = true;
let es = null;

function setState(state, title) {
  store.realtime = state;
  store.realtimeTitle = title;
}

function scheduleReconnect() {
  if (retryTimer) return;
  setState("offline", `实时连接已断开，${Math.round(retryMs / 1000)} 秒后重试…`);
  retryTimer = setTimeout(() => {
    retryTimer = null;
    connect();
  }, retryMs);
  retryMs = Math.min(retryMs * 2, RETRY_MAX_MS);
}

function connect() {
  if (es) es.close();
  setState("connecting", "正在连接实时推送…");
  es = new EventSource("/events/stream");
  es.onopen = () => {
    retryMs = RETRY_MIN_MS;
    setState("connected", "实时已连接");
    emit("open", { first: firstOpen });
    firstOpen = false;
  };
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) scheduleReconnect();
    else setState("connecting", "实时连接已断开，正在重连…");   // 网络中断：EventSource 会自动重连
  };
  es.onmessage = (e) => {
    let data;
    try {
      data = JSON.parse(e.data);
    } catch {
      return;
    }
    if (data && data.type && data.type !== "ready") emit(data.type, data);
  };
}

export function startRealtime() {
  if (es || !store.session?.config?.realtime_enabled) return;
  if (!window.EventSource) {
    setState("offline", "浏览器不支持实时推送");
    return;
  }
  connect();
}

export function stopRealtime() {
  clearTimeout(retryTimer);
  retryTimer = null;
  if (es) es.close();
  es = null;
  firstOpen = true;
  setState("off", "");
}

// 标签页在后台超过 10 秒后切回前台：浏览器可能冻结了后台标签页，期间的事件会丢失，通知各页面补刷新
let hiddenAt = document.hidden ? Date.now() : null;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    hiddenAt = Date.now();
    return;
  }
  if (hiddenAt !== null && Date.now() - hiddenAt > 10000) emit("resume", {});
  hiddenAt = null;
});
