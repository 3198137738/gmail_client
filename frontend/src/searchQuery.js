// 高级搜索：搜索语法（from: subject: has:attachment after: ……，与后端 services/search.py 一致）
// 与筛选表单之间的互相转换。

const TOKEN_RE = /(\w+):"([^"]*)"|(\w+):(\S+)|"([^"]*)"|(\S+)/g;

export function emptyFilters() {
  return { from: "", to: "", subject: "", words: "", after: null, before: null, hasAttachment: false, unread: false, starred: false };
}

function parseDay(value) {
  const m = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/.exec(value);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]).getTime() : null;
}

function formatDay(ts) {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function parseQuery(q) {
  const f = emptyFilters();
  const words = [];
  for (const m of (q || "").matchAll(TOKEN_RE)) {
    const key = (m[1] || m[3] || "").toLowerCase();
    const value = m[1] ? m[2] : m[4];
    if (key === "from" || key === "to" || key === "subject") f[key] = value;
    else if (key === "has" && value.toLowerCase() === "attachment") f.hasAttachment = true;
    else if (key === "is" && value.toLowerCase() === "unread") f.unread = true;
    else if (key === "is" && value.toLowerCase() === "starred") f.starred = true;
    else if (key === "after" && parseDay(value) !== null) f.after = parseDay(value);
    // 语法里 before 不含当天，表单里「到」显示的是包含在内的最后一天
    else if (key === "before" && parseDay(value) !== null) f.before = parseDay(value) - 86400000;
    else if (key) words.push(`${key}:${value}`);
    else words.push(m[5] ?? m[6]);
  }
  f.words = words.join(" ");
  return f;
}

const quote = (v) => (/\s/.test(v) ? `"${v.replace(/"/g, "")}"` : v);

export function buildQuery(f) {
  const parts = [];
  for (const key of ["from", "to", "subject"]) if (f[key].trim()) parts.push(`${key}:${quote(f[key].trim())}`);
  if (f.after) parts.push(`after:${formatDay(f.after)}`);
  // 「到」这一天也包含在内：before 用后一天
  if (f.before) parts.push(`before:${formatDay(f.before + 86400000)}`);
  if (f.hasAttachment) parts.push("has:attachment");
  if (f.unread) parts.push("is:unread");
  if (f.starred) parts.push("is:starred");
  if (f.words.trim()) parts.push(f.words.trim());
  return parts.join(" ");
}

