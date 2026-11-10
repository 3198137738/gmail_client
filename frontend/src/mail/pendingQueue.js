// 可撤销的操作（删除、归档、举报垃圾邮件）：先在界面上隐藏邮件并提示「撤销」，
// 几秒后（或离开页面时）才真正提交。列表和阅读区都过滤掉 hiddenIds 里的邮件；
// 连续的同一种操作合并成一次提交，换了另一种操作时先提交前一批。
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

const DONE_TEXT = {
  delete: (n) => (n > 1 ? `已删除 ${n} 封邮件` : "已删除邮件"),
  archive: (n) => (n > 1 ? `已归档 ${n} 封邮件` : "已归档"),
  spam: (n) => (n > 1 ? `已将 ${n} 封邮件举报为垃圾邮件` : "已举报为垃圾邮件"),
};
const FAIL_TEXT = { delete: "删除失败", archive: "归档失败", spam: "举报失败" };

let pending = { action: null, ids: [] };
let timer = null;

async function commit() {
  clearTimeout(timer);
  timer = null;
  const { action, ids } = pending;
  pending = { action: null, ids: [] };
  if (!ids.length) return;
  let ok = true;
  try {
    await post("/api/messages/actions", { action, ids }, { keepalive: true });
  } catch {
    ok = false;
    toast(`${FAIL_TEXT[action] || "操作失败"}，邮件已恢复显示`, { tone: "error" });
  }
  // 成功时转为 removedIds：当前列表数据还是提交前取的，直接取消隐藏会让邮件闪现一下，
  // 等列表重新加载（load 里会清空 removedIds）后再以服务器结果为准
  ids.forEach((id) => {
    hiddenIds.delete(id);
    if (ok) removedIds.add(id);
  });
  store.listVersion += 1;
  refreshNav();
}

function undo() {
  clearTimeout(timer);
  timer = null;
  pending.ids.forEach((id) => hiddenIds.delete(id));
  pending = { action: null, ids: [] };
}

// 执行一个可撤销的操作：action 为 /api/messages/actions 的 action（delete / archive / spam）
export function queueAction(action, ids) {
  if (pending.action && pending.action !== action) commit();
  pending.action = action;
  for (const id of ids) {
    if (!hiddenIds.has(id)) {
      hiddenIds.add(id);
      pending.ids.push(id);
    }
  }
  clearTimeout(timer);
  timer = setTimeout(commit, UNDO_MS);
  toast(DONE_TEXT[action](pending.ids.length), { actionText: "撤销", onAction: undo, duration: UNDO_MS });
}

export const queueDelete = (ids) => queueAction("delete", ids);

export function flushPending() {
  return commit();
}

window.addEventListener("pagehide", commit);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") commit();
});
