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
  // 当前打开的写邮件窗口：{ key, params }，params 为 reply_to / mode / forward / draft
  compose: null,
  // 写邮件窗口里有尚未保存到草稿的修改
  composeBusy: false,
  // 写邮件窗口处于「撤销发送」等待期或正在发送（窗口已隐藏）
  composePending: false,
  // 递增时让已打开的写邮件窗口恢复显示（从最小化还原）
  composeFocus: 0,
  // 快捷键帮助
  shortcutHelp: false,
});

export function setThemeMode(mode) {
  prefs.theme = mode;
}

let composeSeq = 0;
// 打开写邮件窗口。params：{ reply_to, mode: 'all', forward, draft }
// 只保留一个窗口：上一封还在等待发送时不打开新窗口；已打开的窗口有未保存的内容时恢复显示它，
// 否则（内容已存进草稿）直接替换。返回是否打开了新窗口。
export function openCompose(params = {}) {
  if (store.compose && (store.composePending || store.composeBusy)) {
    store.composeFocus += 1;
    return false;
  }
  composeSeq += 1;
  store.compose = { key: composeSeq, params };
  return true;
}

export function closeCompose() {
  store.compose = null;
  store.composeBusy = false;
  store.composePending = false;
}

export function navAccount(id) {
  return store.nav?.accounts.find((a) => a.id === id) || null;
}
