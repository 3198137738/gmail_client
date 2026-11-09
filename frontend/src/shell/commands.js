// 命令面板（Ctrl+K）的命令：写邮件、跳转文件夹 / 页面 / 设置、切换账号、外观等。
// 每条命令：{ id, group, label, hint?, icon?, keywords?, shortcut?, run() }
import { computed } from "vue";
import {
  AccountCircleOutlined,
  BrightnessAutoOutlined,
  DarkModeOutlined,
  DensityMediumOutlined,
  EditOutlined,
  KeyboardOutlined,
  LabelOutlined,
  LayersOutlined,
  LightModeOutlined,
  LogOutOutlined,
  NotificationsActiveOutlined,
  SettingsOutlined,
  SyncOutlined,
  ViewSidebarOutlined,
} from "@vicons/material";
import { router } from "../router";
import { openCompose, setThemeMode, store } from "../store";
import { prefs } from "../prefs";
import { logout, syncAll } from "../actions";
import { desktopNotify, setDesktopNotify } from "../desktopNotify";
import { PAGES, accounts, folders, labels, switchAccount } from "./navModel";

const SETTINGS = [
  { path: "/accounts/", label: "账号和导入", keywords: "account 添加 授权 签名 分组" },
  { path: "/accounts/rules", label: "自动规则", keywords: "rule filter 过滤器" },
  { path: "/accounts/templates", label: "模板", keywords: "template 快捷回复" },
  { path: "/messages/blocked-senders", label: "屏蔽的地址", keywords: "block 屏蔽 信任" },
  { path: "/accounts/security", label: "安全", keywords: "security 两步验证 2fa 设备 备份 证书 https" },
  { path: "/accounts/security/audit", label: "审计日志", keywords: "audit log 日志" },
];

// 其他模块（如阶段 1 的邮件操作）可以注册额外的命令来源：返回命令数组的函数
const sources = [];
export function registerCommandSource(fn) {
  sources.push(fn);
  return () => {
    const i = sources.indexOf(fn);
    if (i !== -1) sources.splice(i, 1);
  };
}

export const commands = computed(() => {
  const list = [
    { id: "compose", group: "常用", label: "写邮件", icon: EditOutlined, shortcut: "c", run: () => openCompose() },
    { id: "sync", group: "常用", label: "同步所有账号", icon: SyncOutlined, keywords: "refresh 刷新", run: syncAll },
    { id: "switcher", group: "常用", label: "切换账号…", icon: AccountCircleOutlined, shortcut: "'", run: () => (store.accountSwitcher = true) },
  ];
  for (const f of folders.value) {
    list.push({ id: `folder-${f.kind}`, group: "跳转", label: f.label, icon: f.icon, keywords: f.kind, run: () => router.push(f.to) });
  }
  for (const p of PAGES) {
    list.push({ id: `page-${p.section}`, group: "跳转", label: p.label, icon: p.icon, run: () => router.push(p.path) });
  }
  for (const lb of labels.value) {
    list.push({
      id: `label-${lb.id}`,
      group: "标签",
      label: lb.name,
      hint: lb.owner?.email,
      icon: LabelOutlined,
      keywords: "label 标签",
      run: () => router.push(lb.to),
    });
  }
  list.push({ id: "account-all", group: "账号", label: "全部账号（统一收件箱）", icon: LayersOutlined, shortcut: "Alt 0", run: () => switchAccount("") });
  for (const a of accounts.value) {
    list.push({
      id: `account-${a.id}`,
      group: "账号",
      label: `切换到 ${a.name}`,
      hint: a.email,
      color: a.color,
      keywords: `${a.email} ${a.group || ""}`,
      run: () => switchAccount(a.id),
    });
  }
  for (const s of SETTINGS) {
    list.push({ id: `settings-${s.path}`, group: "设置", label: `设置：${s.label}`, icon: SettingsOutlined, keywords: s.keywords, run: () => router.push(s.path) });
  }
  list.push(
    { id: "theme-light", group: "外观", label: "外观：浅色", icon: LightModeOutlined, keywords: "theme light", run: () => setThemeMode("light") },
    { id: "theme-dark", group: "外观", label: "外观：深色", icon: DarkModeOutlined, keywords: "theme dark 暗色 夜间", run: () => setThemeMode("dark") },
    { id: "theme-auto", group: "外观", label: "外观：跟随系统", icon: BrightnessAutoOutlined, keywords: "theme auto", run: () => setThemeMode("auto") },
    {
      id: "density",
      group: "外观",
      label: prefs.density === "compact" ? "列表密度：舒适" : "列表密度：紧凑",
      icon: DensityMediumOutlined,
      keywords: "density compact 紧凑",
      run: () => (prefs.density = prefs.density === "compact" ? "comfortable" : "compact"),
    },
    {
      id: "folder-panel",
      group: "外观",
      label: prefs.folderPanel ? "收起文件夹面板" : "展开文件夹面板",
      icon: ViewSidebarOutlined,
      shortcut: "[",
      run: () => (prefs.folderPanel = !prefs.folderPanel),
    },
  );
  if (desktopNotify.supported) {
    list.push({
      id: "notify",
      group: "外观",
      label: desktopNotify.enabled ? "关闭桌面通知" : "开启桌面通知",
      icon: NotificationsActiveOutlined,
      keywords: "notification 通知",
      run: () => setDesktopNotify(!desktopNotify.enabled),
    });
  }
  list.push({ id: "shortcuts", group: "帮助", label: "键盘快捷键", icon: KeyboardOutlined, shortcut: "?", run: () => (store.shortcutHelp = true) });
  if (store.session?.auth_enabled) {
    list.push({ id: "logout", group: "帮助", label: "退出登录", icon: LogOutOutlined, run: logout });
  }
  for (const source of sources) list.push(...source());
  return list;
});
