// 搜索记录：最近搜过的关键词，保存在浏览器本地（localStorage），最新的在前。
import { ref } from "vue";

const KEY = "gm:search-history";
const MAX = 10;

function load() {
  try {
    const items = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(items) ? items.filter((x) => typeof x === "string" && x.trim()).slice(0, MAX) : [];
  } catch {
    return [];
  }
}

export const searchHistory = ref(load());

function save() {
  try {
    localStorage.setItem(KEY, JSON.stringify(searchHistory.value));
  } catch {
    /* 隐私模式等情况下忽略 */
  }
}

// 记下一次搜索：已有的同一关键词移到最前面
export function rememberSearch(q) {
  const text = (q || "").trim();
  if (!text) return;
  searchHistory.value = [text, ...searchHistory.value.filter((x) => x !== text)].slice(0, MAX);
  save();
}

export function forgetSearch(q) {
  searchHistory.value = searchHistory.value.filter((x) => x !== q);
  save();
}

export function clearSearchHistory() {
  searchHistory.value = [];
  save();
}
