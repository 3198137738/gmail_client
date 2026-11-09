// 全局状态：登录会话、左侧导航数据、界面状态、写邮件窗口。
import { reactive } from "vue";
import { prefs } from "./prefs";

export const store = reactive({
  // GET /api/session：authenticated、csrf_token、config 等
  session: null,
  // GET /api/messages/nav：账号、未读数、标签、草稿数
  nav: null,
  // 当前查看的文件夹 / 账号（列表页、详情页设置），用于高亮左侧导航和生成文件夹链接
  view: { kind: null, accountId: null, unreadOnly: false, section: null },
  // 分栏阅读时，详情页里改动了邮件（删除、星标、已读等）后递增，通知左侧列表刷新
  listVersion: 0,
  // 最近一次显示的邮件列表地址：没带 back 参数打开邮件时（附件中心、通知等）回到这里
  lastListPath: null,
  // 外壳上的弹层：命令面板（Ctrl+K）、账号切换器、手机端的文件夹 / 「我的」面板
  palette: false,
  accountSwitcher: false,
  mobileSheet: null,   // null / "folders" / "me"
  // 实时推送连接状态：off / connecting / connected / offline
  realtime: "off",
  realtimeTitle: "",
  // 打开的写邮件窗口（可以同时开几个）：
  // { key, params, state: normal / min / max, busy（有未保存到草稿的修改）, pending（撤销等待期 / 发送中，窗口隐藏）, title }
  // params 为 reply_to / mode / forward / draft
  composes: [],
  // 最近操作的写邮件窗口（快捷键、聚焦用）
  composeActive: null,
  // 打开的窗口已达上限且都有未保存内容时递增（写信区提示）
  composeLimitHit: 0,
  // 快捷键帮助
  shortcutHelp: false,
});

export function setThemeMode(mode) {
  prefs.theme = mode;
}

let composeSeq = 0;
// 最多同时打开的写邮件窗口
export const MAX_COMPOSES = 3;

function sameTarget(a, b) {
  return ["draft", "reply_to", "forward"].some((k) => a[k] && String(a[k]) === String(b[k])) && (a.mode || "") === (b.mode || "");
}

// 打开写邮件窗口。params：{ reply_to, mode: 'all', forward, draft }
// 同一封草稿 / 回复 / 转发已经打开时恢复显示那个窗口；打开新窗口时把其他窗口最小化。
// 已达到上限时，没有未保存内容的最早的窗口让位，否则提示。返回是否打开了新窗口。
export function openCompose(params = {}) {
  const existing = store.composes.find((c) => !c.pending && sameTarget(c.params, params));
  if (existing) {
    focusCompose(existing.key);
    return false;
  }
  const open = store.composes.filter((c) => !c.pending);
  if (open.length >= MAX_COMPOSES) {
    const idle = open.find((c) => !c.busy);
    if (!idle) {
      store.composeLimitHit = (store.composeLimitHit || 0) + 1;
      return false;
    }
    closeCompose(idle.key);
  }
  composeSeq += 1;
  for (const c of store.composes) if (c.state !== "min") c.state = "min";
  store.composes.push({ key: composeSeq, params, state: "normal", busy: false, pending: false, title: "" });
  store.composeActive = composeSeq;
  return true;
}

// 展开一个窗口：电脑端同时只展开一个，其余最小化（并排放不下）
export function focusCompose(key) {
  const target = store.composes.find((c) => c.key === key);
  if (!target) return;
  for (const c of store.composes) if (c !== target && c.state !== "min") c.state = "min";
  if (target.state === "min") target.state = "normal";
  store.composeActive = key;
}

// 关闭写邮件窗口；不传 key 时关闭当前（最近操作的）窗口
export function closeCompose(key = store.composeActive) {
  store.composes = store.composes.filter((c) => c.key !== key);
  if (store.composeActive === key) store.composeActive = store.composes[store.composes.length - 1]?.key ?? null;
}

export function composeOf(key) {
  return store.composes.find((c) => c.key === key) || null;
}

export function navAccount(id) {
  return store.nav?.accounts.find((a) => a.id === id) || null;
}
