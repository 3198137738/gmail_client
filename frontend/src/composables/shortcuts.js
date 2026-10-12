// 键盘快捷键（与 Gmail 一致）。按 ? 查看全部。
// 在输入框 / 编辑区里打字时不触发；带 Ctrl / Alt / ⌘ 的组合键交给浏览器。
import { onBeforeUnmount, onMounted } from "vue";
import { useRouter } from "vue-router";
import { openCompose, store } from "../store";

export const SHORTCUT_HELP = [
  ["通用", [
    ["c", "写邮件"],
    ["/", "搜索邮件"],
    ["?", "显示快捷键帮助"],
    ["g 然后 i", "转到收件箱"],
    ["g 然后 s", "转到已加星标"],
    ["g 然后 t", "转到已发送"],
    ["g 然后 a", "转到所有邮件"],
  ]],
  ["邮件列表", [
    ["j / k", "下一封 / 上一封"],
    ["o 或 Enter", "打开邮件"],
    ["x", "选中 / 取消选中"],
    ["s", "加星标 / 取消星标"],
    ["#", "删除"],
    ["Shift + I", "标记为已读"],
    ["Shift + U", "标记为未读"],
    ["Esc", "取消选择"],
  ]],
  ["阅读邮件", [
    ["u", "返回列表"],
    ["k / j", "较新 / 较旧的一封"],
    ["r", "回复"],
    ["a", "回复全部"],
    ["f", "转发"],
    ["s", "加星标 / 取消星标"],
    ["#", "删除"],
    ["Shift + U", "标记为未读"],
  ]],
];

// 当前页面注册的快捷键（列表页、详情页），后注册的优先
const pageHandlers = [];

export function usePageShortcuts(map) {
  onMounted(() => pageHandlers.push(map));
  onBeforeUnmount(() => {
    const i = pageHandlers.indexOf(map);
    if (i !== -1) pageHandlers.splice(i, 1);
  });
}

function isTyping(target) {
  if (!target || !target.closest) return false;
  if (target.isContentEditable) return true;
  return !!target.closest("input, textarea, select, [contenteditable='true'], [contenteditable='']");
}

function hasOpenOverlay() {
  return !!document.querySelector(".n-modal-container .n-modal, .n-dialog");
}

const GO_TARGETS = { i: "inbox", s: "starred", t: "sent", a: "all" };

export function useGlobalShortcuts() {
  const router = useRouter();
  let pendingG = 0;

  function onKeyDown(ev) {
    if (ev.ctrlKey || ev.altKey || ev.metaKey || ev.defaultPrevented) return;
    if (ev.key === "Escape" && store.shortcutHelp) {
      store.shortcutHelp = false;
      return;
    }
    if (isTyping(ev.target) || hasOpenOverlay()) return;
    const key = ev.key;

    if (pendingG && Date.now() - pendingG < 1500 && GO_TARGETS[key]) {
      pendingG = 0;
      ev.preventDefault();
      router.push({ path: "/messages/", query: { kind: GO_TARGETS[key], account_id: store.view.accountId ?? "" } });
      return;
    }
    pendingG = 0;

    if (key === "g") {
      pendingG = Date.now();
      return;
    }
    if (key === "?") {
      ev.preventDefault();
      store.shortcutHelp = !store.shortcutHelp;
      return;
    }
    if (key === "c") {
      ev.preventDefault();
      openCompose();
      return;
    }
    if (key === "/") {
      const input = document.querySelector("input[data-search]");
      if (input) {
        ev.preventDefault();
        input.focus();
        input.select();
      }
      return;
    }
    for (let i = pageHandlers.length - 1; i >= 0; i--) {
      const handler = pageHandlers[i][key];
      if (handler) {
        if (handler(ev) !== false) ev.preventDefault();
        return;
      }
    }
  }

  onMounted(() => document.addEventListener("keydown", onKeyDown));
  onBeforeUnmount(() => document.removeEventListener("keydown", onKeyDown));
}
