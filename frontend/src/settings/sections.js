// 设置分区：导航分组、名称、图标、组件。路由（/settings/:section）和设置导航都从这里生成。
import {
  BackupOutlined,
  BlockOutlined,
  DrawOutlined,
  FilterAltOutlined,
  FolderOutlined,
  KeyboardOutlined,
  ManageAccountsOutlined,
  NotificationsOutlined,
  PaletteOutlined,
  ReceiptLongOutlined,
  ShieldOutlined,
  TextSnippetOutlined,
} from "@vicons/material";

export const SECTION_GROUPS = [
  {
    label: "邮箱",
    items: [
      { key: "accounts", label: "账号", icon: ManageAccountsOutlined, hint: "添加、授权、停用、删除邮箱账号", component: () => import("./sections/AccountsSection.vue") },
      { key: "signatures", label: "签名", icon: DrawOutlined, hint: "每个账号写邮件时自动添加的签名", component: () => import("./sections/SignaturesSection.vue") },
      { key: "groups", label: "分组", icon: FolderOutlined, hint: "侧栏里按分组折叠显示账号", component: () => import("./sections/GroupsSection.vue") },
    ],
  },
  {
    label: "邮件处理",
    items: [
      { key: "rules", label: "自动规则", icon: FilterAltOutlined, hint: "新邮件自动标记、加标签或删除", component: () => import("./sections/RulesSection.vue") },
      { key: "templates", label: "模板", icon: TextSnippetOutlined, hint: "写邮件时一键插入的常用内容", component: () => import("./sections/TemplatesSection.vue") },
      { key: "senders", label: "屏蔽与信任", icon: BlockOutlined, hint: "屏蔽的发件人、信任的发件人", component: () => import("./sections/SendersSection.vue") },
    ],
  },
  {
    label: "个性化",
    items: [
      { key: "appearance", label: "外观与阅读", icon: PaletteOutlined, hint: "主题、密度、阅读窗格、滑动手势", component: () => import("./sections/AppearanceSection.vue") },
      { key: "notifications", label: "通知", icon: NotificationsOutlined, hint: "桌面通知、Telegram 推送", component: () => import("./sections/NotificationsSection.vue") },
      { key: "shortcuts", label: "快捷键", icon: KeyboardOutlined, hint: "键盘快捷键一览", component: () => import("./sections/ShortcutsSection.vue") },
    ],
  },
  {
    label: "安全与维护",
    items: [
      { key: "security", label: "安全", icon: ShieldOutlined, hint: "两步验证、登录设备、IP 黑名单", component: () => import("./sections/SecuritySection.vue") },
      { key: "backup", label: "备份与证书", icon: BackupOutlined, hint: "自动备份、HTTPS 证书检测与续订", component: () => import("./sections/BackupSection.vue") },
      { key: "audit", label: "审计日志", icon: ReceiptLongOutlined, hint: "登录、发信、删除等操作记录", component: () => import("./sections/AuditSection.vue") },
    ],
  },
];

export const SECTIONS = SECTION_GROUPS.flatMap((g) => g.items);

export function sectionOf(key) {
  return SECTIONS.find((s) => s.key === key) || null;
}
