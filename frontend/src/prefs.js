// 本机偏好（外观、布局、手势等），存在 localStorage：手机和电脑可以各用各的设置。
// 服务器端的设置（会话视图、撤销发送秒数等）不在这里。
import { reactive, watch } from "vue";

const KEY = "gm:prefs:v1";

const DEFAULTS = {
  theme: "auto",            // light / dark / auto（跟随系统）
  density: "comfortable",   // comfortable / compact
  folderPanel: true,        // 电脑端显示文件夹 / 账号面板
  folderPanelWidth: 248,
  listWidth: 420,
  readingPane: "right",     // right（右侧阅读）/ off（打开邮件时整页阅读）
  afterAction: "next",      // 阅读中归档 / 删除后：next（打开下一封）/ list（回到列表）
  swipeLeft: "delete",      // 手机端左滑 / 右滑的动作
  swipeRight: "toggleRead",
  recentAccounts: [],       // 最近切换过的账号 id（账号切换器置顶）
  collapsedGroups: [],      // 文件夹面板里折叠的账号分组
};

function read(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function load() {
  let saved = {};
  try {
    saved = JSON.parse(read(KEY) || "{}") || {};
  } catch {
    saved = {};
  }
  const prefs = { ...DEFAULTS, ...saved };
  // 从旧版的单独设置迁移一次
  if (!read(KEY)) {
    const theme = read("gm:theme");
    if (["light", "dark", "auto"].includes(theme)) prefs.theme = theme;
    if (read("gm:nav-collapsed") === "1") prefs.folderPanel = false;
    try {
      const groups = JSON.parse(read("gm:collapsed-groups") || "[]");
      if (Array.isArray(groups)) prefs.collapsedGroups = groups;
    } catch {
      /* 忽略 */
    }
  }
  return prefs;
}

export const prefs = reactive(load());

watch(
  prefs,
  (value) => {
    try {
      localStorage.setItem(KEY, JSON.stringify(value));
    } catch {
      /* 隐私模式等情况下忽略 */
    }
  },
  { deep: true },
);

export function rememberAccount(id) {
  if (!id) return;
  prefs.recentAccounts = [id, ...prefs.recentAccounts.filter((x) => x !== id)].slice(0, 5);
}
