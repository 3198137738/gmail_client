// 可撤销的删除：先在界面上隐藏邮件并提示「撤销」，几秒后（或离开页面时）才真正删除。
// 列表和阅读区都过滤掉 hiddenIds 里的邮件；连续删除会合并成一次提交。
import { reactive } from "vue";
import { post } from "../api";
import { store } from "../store";
import { toast } from "../ui";
import { refreshNav } from "../composables/common";

export const UNDO_MS = 5000;
export const hiddenIds = reactive(new Set());
// 已经离开当前视图的邮件（延后、恢复、屏蔽等）：在列表重新加载前先隐藏，界面立即响应
export const removedIds = reactive(new Set());

export function markRemoved(ids) {
  ids.forEach((id) => removedIds.add(id));
}

let pending = [];
let timer = null;

async function commit() {
  clearTimeout(timer);
  timer = null;
  const ids = pending;
  pending = [];
  if (!ids.length) return;
  try {
    await post("/api/messages/actions", { action: "delete", ids }, { keepalive: true });
  } catch {
    toast("删除失败，邮件已恢复显示", { tone: "error" });
  }
  ids.forEach((id) => hiddenIds.delete(id));
  store.listVersion += 1;
  refreshNav();
}

function undo() {
  clearTimeout(timer);
  timer = null;
  pending.forEach((id) => hiddenIds.delete(id));
  pending = [];
}

// 删除（移到「已删除」）并显示可撤销的提示
export function queueDelete(ids) {
  for (const id of ids) {
    if (!hiddenIds.has(id)) {
      hiddenIds.add(id);
      pending.push(id);
    }
  }
  clearTimeout(timer);
  timer = setTimeout(commit, UNDO_MS);
  toast(pending.length > 1 ? `已删除 ${pending.length} 封邮件` : "已删除邮件", {
    actionText: "撤销",
    onAction: undo,
    duration: UNDO_MS,
  });
}

export function flushPending() {
  return commit();
}

window.addEventListener("pagehide", commit);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") commit();
});
