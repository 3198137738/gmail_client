// 粘贴内容清洗：只保留常见的格式标签和少量样式，去掉 Office / 网页带来的 class、脚本、事件、mso- 样式等。
// （服务端发信前还会再清洗一次，这里主要是让粘贴后的格式干净、可编辑。）
const ALLOWED_TAGS = new Set([
  "a", "b", "strong", "i", "em", "u", "s", "strike", "del", "br", "p", "div", "span", "font",
  "ul", "ol", "li", "blockquote", "pre", "code", "h1", "h2", "h3", "h4", "h5", "h6",
  "table", "thead", "tbody", "tr", "td", "th", "hr", "img", "sub", "sup",
]);
const DROP_WITH_CONTENT = new Set(["script", "style", "head", "meta", "link", "title", "iframe", "object", "embed", "template", "xml", "o:p"]);
const ALLOWED_STYLES = new Set([
  "color", "background-color", "font-weight", "font-style", "text-decoration", "text-align",
  "font-size", "font-family", "margin-left", "padding-left", "border", "border-collapse",
  "width", "max-width", "vertical-align",
]);
const ALLOWED_ATTRS = { a: ["href", "title"], img: ["src", "alt", "width", "height"], td: ["colspan", "rowspan"], th: ["colspan", "rowspan"], font: ["color", "size", "face"] };

function cleanStyle(value) {
  return value
    .split(";")
    .map((d) => d.trim())
    .filter(Boolean)
    .map((d) => {
      const i = d.indexOf(":");
      return i > 0 ? [d.slice(0, i).trim().toLowerCase(), d.slice(i + 1).trim()] : null;
    })
    .filter((d) => d && ALLOWED_STYLES.has(d[0]) && !/expression|url\(|javascript:/i.test(d[1]))
    .map(([k, v]) => `${k}: ${v}`)
    .join("; ");
}

function safeUrl(value, { image = false } = {}) {
  const v = (value || "").trim();
  if (/^(https?:|mailto:)/i.test(v)) return v;
  if (image && /^data:image\/(png|jpe?g|gif|webp|bmp);base64,/i.test(v)) return v;
  return null;
}

function walk(node, doc) {
  for (const child of [...node.childNodes]) {
    if (child.nodeType === Node.COMMENT_NODE) {
      child.remove();
      continue;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) continue;
    const tag = child.tagName.toLowerCase();
    if (DROP_WITH_CONTENT.has(tag)) {
      child.remove();
      continue;
    }
    walk(child, doc);
    if (!ALLOWED_TAGS.has(tag)) {
      // 不认识的标签：保留内容，去掉标签本身
      child.replaceWith(...child.childNodes);
      continue;
    }
    const keep = ALLOWED_ATTRS[tag] || [];
    for (const attr of [...child.attributes]) {
      const name = attr.name.toLowerCase();
      if (name === "style") {
        const style = cleanStyle(attr.value);
        if (style) child.setAttribute("style", style);
        else child.removeAttribute("style");
      } else if (!keep.includes(name)) {
        child.removeAttribute(attr.name);
      }
    }
    if (tag === "a") {
      const href = safeUrl(child.getAttribute("href"));
      if (href) child.setAttribute("href", href);
      else child.removeAttribute("href");
    }
    if (tag === "img") {
      const src = safeUrl(child.getAttribute("src"), { image: true });
      if (!src) child.remove();
      else child.style.maxWidth = "100%";
    }
  }
}

export function sanitizePastedHtml(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const body = doc.body;
  walk(body, doc);
  return body.innerHTML;
}

export function escapeText(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\r?\n/g, "<br>");
}
