// 预先加载邮件详情（悬停在邮件上、键盘移动光标时）：打开时立即显示，不用等网络。
// 用 peek=1 请求，不会把邮件标为已读；真正打开时阅读区仍会再请求一次（标为已读、取最新状态）。
import { get } from "../api";

const MAX = 6;
const TTL_MS = 60_000;
const cache = new Map();   // id -> { at, data } 或 { at, promise }

function saveData() {
  return !!navigator.connection?.saveData;
}

export function prefetchMessage(id, back) {
  if (!id || saveData()) return;
  const hit = cache.get(id);
  if (hit && Date.now() - hit.at < TTL_MS) return;
  const promise = get(`/api/messages/${id}`, { back, peek: 1 })
    .then((data) => {
      cache.set(id, { at: Date.now(), data });
      return data;
    })
    .catch(() => {
      cache.delete(id);
    });
  cache.set(id, { at: Date.now(), promise });
  while (cache.size > MAX) cache.delete(cache.keys().next().value);
}

// 已预先加载好的详情（没有或已过期时返回 null）
export function cachedMessage(id) {
  const hit = cache.get(id);
  if (!hit?.data || Date.now() - hit.at > TTL_MS) return null;
  return hit.data;
}

export function forgetMessage(id) {
  cache.delete(id);
}
