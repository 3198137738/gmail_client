// 简单的模糊匹配（命令面板、账号切换器）：输入的字符按顺序出现即可匹配，
// 连续命中、命中开头 / 单词开头得分更高。返回得分（越大越好），不匹配返回 -1。
export function fuzzyScore(text, query) {
  const t = (text || "").toLowerCase();
  const q = (query || "").toLowerCase().replace(/\s+/g, "");
  if (!q) return 0;
  const direct = t.indexOf(q);
  if (direct !== -1) return 1000 - direct * 2 - (t.length - q.length) * 0.1;
  let score = 0;
  let ti = 0;
  let prev = -2;
  for (const ch of q) {
    const found = t.indexOf(ch, ti);
    if (found === -1) return -1;
    score += found === prev + 1 ? 8 : 1;
    if (found === 0 || /[\s@._\-/]/.test(t[found - 1])) score += 5;
    prev = found;
    ti = found + 1;
  }
  return score - t.length * 0.05;
}

// 在多个字段里取最高分
export function bestScore(fields, query) {
  let best = -1;
  for (const f of fields) {
    const s = fuzzyScore(f, query);
    if (s > best) best = s;
  }
  return best;
}
