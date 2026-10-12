// 显示格式（Gmail 中文界面）：日期、文件大小、头像。
// 后端返回的时间都是服务器本地时间的 ISO 字符串（不带时区），按本地时间解析。

const WEEKDAYS = "日一二三四五六";

export function parseDate(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

// 「下午8:31」
export function cnTime(d) {
  const hour = d.getHours();
  const period = hour < 12 ? "上午" : "下午";
  const hour12 = hour % 12 || 12;
  return `${period}${hour12}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// 邮件列表日期：今天「下午8:31」，今年「10月3日」，更早「2025/10/3」
export function listDate(value) {
  const d = parseDate(value);
  if (!d) return "";
  const now = new Date();
  if (sameDay(d, now) || d > now) return cnTime(d);
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

function relativeAgo(d) {
  const seconds = (Date.now() - d.getTime()) / 1000;
  if (seconds < 0) return "";
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}小时前`;
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)}天前`;
  return "";
}

// 邮件详情日期：「10月10日周五 下午8:31 (2小时前)」，往年带年份
export function fullDate(value) {
  const d = parseDate(value);
  if (!d) return "";
  const prefix = d.getFullYear() === new Date().getFullYear() ? "" : `${d.getFullYear()}年`;
  const text = `${prefix}${d.getMonth() + 1}月${d.getDate()}日周${WEEKDAYS[d.getDay()]} ${cnTime(d)}`;
  const ago = relativeAgo(d);
  return ago ? `${text} (${ago})` : text;
}

const pad = (n) => String(n).padStart(2, "0");

// 「2026-10-12 14:03」，withSeconds 时带秒
export function dateTime(value, withSeconds = false) {
  const d = parseDate(value);
  if (!d) return "";
  const base = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return withSeconds ? `${base}:${pad(d.getSeconds())}` : base;
}

// 「2026年10月12日 14:03」
export function longDate(value) {
  const d = parseDate(value);
  if (!d) return "";
  return `${d.getFullYear()}年${pad(d.getMonth() + 1)}月${pad(d.getDate())}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 「10-12 14:03」
export function shortDateTime(value) {
  const d = parseDate(value);
  if (!d) return "";
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// 头像配色：按名称稳定地选一种颜色，同一发件人颜色固定
const TONES = ["#1a73e8", "#e37400", "#188038", "#d93025", "#9334e6", "#12838e"];
export function avatarColor(name) {
  let sum = 0;
  for (const ch of name || "") sum += ch.codePointAt(0);
  return TONES[sum % TONES.length];
}

export function initial(name) {
  const text = (name || "").trim();
  return text ? Array.from(text)[0].toUpperCase() : "?";
}

// 附件类型图标分类
export function attachmentKind(mime) {
  const mt = (mime || "").toLowerCase();
  if (mt.includes("pdf")) return "pdf";
  if (mt.startsWith("image/")) return "image";
  if (mt.includes("sheet") || mt.includes("excel") || mt.includes("csv")) return "sheet";
  if (mt.includes("zip") || mt.includes("compressed")) return "zip";
  return "doc";
}
