// 收件人地址的拆分、解析和格式化（写邮件的地址输入框用）。

export const EMAIL_RE = /^[^\s@<>",;]+@[^\s@<>",;]+\.[^\s@<>",;]+$/;

// 按逗号 / 分号拆分地址，忽略引号和尖括号内的分隔符
export function splitAddresses(str) {
  const parts = [];
  let buf = "";
  let quote = false;
  let angle = false;
  for (const ch of str || "") {
    if (ch === '"') quote = !quote;
    else if (ch === "<") angle = true;
    else if (ch === ">") angle = false;
    if ((ch === "," || ch === ";") && !quote && !angle) {
      if (buf.trim()) parts.push(buf.trim());
      buf = "";
    } else {
      buf += ch;
    }
  }
  if (buf.trim()) parts.push(buf.trim());
  return parts;
}

// 「"名字" <a@b.com>」→ { name, email }
export function parseAddress(token) {
  const m = token.match(/^(.*)<([^>]+)>\s*$/);
  if (m) {
    return { name: m[1].trim().replace(/^"|"$/g, "").trim(), email: m[2].trim() };
  }
  return { name: "", email: token.trim().replace(/^<|>$/g, "") };
}

export function formatAddress(a) {
  return a.name ? `"${a.name.replace(/"/g, "")}" <${a.email}>` : a.email;
}

export function parseAddressList(str) {
  const seen = new Set();
  const result = [];
  for (const token of splitAddresses(str)) {
    const addr = parseAddress(token);
    const key = addr.email.toLowerCase();
    if (!addr.email || seen.has(key)) continue;
    seen.add(key);
    result.push(addr);
  }
  return result;
}

export const isValidEmail = (email) => EMAIL_RE.test(email);
