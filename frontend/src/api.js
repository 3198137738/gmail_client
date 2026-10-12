// 后端接口封装：自动带 CSRF token、解析 JSON、统一错误。
//
// - 写请求（POST 等）带 X-CSRF-Token；token 来自 GET /api/session，登录后会话重建时更新。
// - 401：登录已过期，跳到登录页（记下当前地址，登录后回来）。
// - 403「页面已过期」：会话里的 CSRF token 变了（如另一个标签页重新登录），刷新 token 后重试一次。
import { store } from "./store";

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data || {};
  }
}

let onUnauthorized = null;
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

function buildUrl(path, query) {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === false) continue;
    params.append(key, value === true ? "1" : String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function refreshSession() {
  const resp = await fetch("/api/session", { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (!resp.ok) throw new ApiError("无法连接服务器", resp.status);
  const data = await resp.json();
  store.session = data;
  return data;
}

async function send(path, { method = "GET", json, form, query, keepalive = false, signal } = {}) {
  const headers = { Accept: "application/json" };
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form !== undefined) {
    body = form;   // FormData：浏览器自己设置 multipart 边界
  }
  if (method !== "GET" && store.session?.csrf_token) {
    headers["X-CSRF-Token"] = store.session.csrf_token;
  }
  return fetch(buildUrl(path, query), { method, headers, body, credentials: "same-origin", keepalive, signal });
}

export async function api(path, options = {}) {
  let resp;
  try {
    resp = await send(path, options);
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("网络连接失败，请稍后重试", 0);
  }
  if (resp.status === 403 && (options.method || "GET") !== "GET" && !options._retried) {
    const data = await resp.clone().json().catch(() => ({}));
    if (data.detail === "页面已过期") {
      await refreshSession();
      return api(path, { ...options, _retried: true });
    }
  }
  const data = await resp.json().catch(() => ({}));
  if (resp.status === 401) {
    if (store.session) store.session.authenticated = false;
    if (onUnauthorized) onUnauthorized();
    throw new ApiError(data.detail || "登录已过期，请重新登录", 401, data);
  }
  if (!resp.ok) {
    const detail = typeof data.detail === "string" ? data.detail : `请求失败（${resp.status}）`;
    throw new ApiError(detail, resp.status, data);
  }
  return data;
}

export const get = (path, query, options) => api(path, { ...options, query });
export const post = (path, json, options) => api(path, { ...options, method: "POST", json: json ?? {} });
