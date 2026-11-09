// 导航数据：文件夹、标签、账号（分组 / 服务商分区）、功能页。
// 文件夹面板、手机端面板、账号切换器、命令面板都从这里取，保证各处一致。
import { computed } from "vue";
import {
  AttachFileOutlined,
  CleaningServicesOutlined,
  DeleteOutlined,
  DraftsOutlined,
  InboxOutlined,
  MailOutlined,
  OutboxOutlined,
  ReportOutlined,
  ScheduleSendOutlined,
  SendOutlined,
  SnoozeOutlined,
  StarBorderOutlined,
  UnsubscribeOutlined,
} from "@vicons/material";
import { router } from "../router";
import { store } from "../store";
import { prefs, rememberAccount } from "../prefs";

export const PROVIDERS = [
  { key: "gmail", name: "Gmail", color: "#d93025" },
  { key: "outlook", name: "Outlook", color: "#0078d4" },
];

// 邮件文件夹（kind）。junk / trash / blocked 只在服务器开启对应同步时显示
const FOLDERS = [
  { kind: "inbox", label: "收件箱", icon: InboxOutlined },
  { kind: "starred", label: "已加星标", icon: StarBorderOutlined },
  { kind: "snoozed", label: "已延后", icon: SnoozeOutlined },
  { kind: "sent", label: "已发送", icon: SendOutlined },
  { kind: "junk", label: "垃圾邮件", icon: ReportOutlined, when: "sync_junk_enabled" },
  { kind: "trash", label: "已删除", icon: DeleteOutlined, when: "sync_trash_enabled" },
  { kind: "blocked", label: "屏蔽的邮件", icon: UnsubscribeOutlined, when: "sync_trash_enabled" },
  { kind: "all", label: "所有邮件", icon: MailOutlined },
];

// 功能页（rail：显示在图标栏上）
export const PAGES = [
  { path: "/compose/drafts", section: "drafts", label: "草稿", icon: DraftsOutlined, count: "drafts", rail: true },
  { path: "/compose/scheduled", section: "scheduled", label: "定时发送", icon: ScheduleSendOutlined, count: "scheduled", rail: true },
  { path: "/files", section: "files", label: "附件", icon: AttachFileOutlined, rail: true },
  { path: "/cleanup", section: "cleanup", label: "批量清理", icon: CleaningServicesOutlined, rail: true },
  { path: "/compose/sent", section: "sent_log", label: "发件记录", icon: OutboxOutlined },
];

const ACCOUNT_KINDS = new Set(["inbox", "sent", "junk", "trash", "blocked", "all", "starred", "snoozed"]);

export const nav = computed(() => store.nav);
export const accounts = computed(() => store.nav?.accounts || []);

// 当前查看的账号（null 表示全部账号）
export const currentAccount = computed(() => {
  const id = store.view.accountId ?? store.nav?.preferred_account_id ?? null;
  return accounts.value.find((a) => a.id === id) || null;
});

export function listUrl(kind, accountId = currentAccount.value?.id) {
  const unread = store.view.unreadOnly && router.currentRoute.value.name === "list" ? "&unread=1" : "";
  return `/messages/?kind=${kind}&account_id=${accountId ?? ""}${unread}`;
}

export const folders = computed(() => {
  const config = store.session?.config || {};
  const acc = currentAccount.value;
  const n = store.nav;
  const counts = {
    inbox: acc ? acc.unread_inbox : n?.unread_inbox || 0,
    junk: acc ? acc.unread_junk : n?.unread_junk || 0,
    snoozed: n?.snoozed || 0,
  };
  return FOLDERS.filter((f) => !f.when || config[f.when]).map((f) => ({
    ...f,
    count: counts[f.kind] || 0,
    to: listUrl(f.kind, acc?.id),
  }));
});

export const labels = computed(() => {
  const acc = currentAccount.value;
  const all = store.nav?.labels || [];
  return (acc ? all.filter((lb) => lb.account_id === acc.id) : all).map((lb) => {
    const owner = accounts.value.find((a) => a.id === lb.account_id);
    return {
      ...lb,
      kind: `label-${lb.id}`,
      owner,
      to: `/messages/?kind=label-${lb.id}&account_id=${lb.account_id}`,
    };
  });
});

export function pageCount(page) {
  return page.count ? store.nav?.[page.count] || 0 : 0;
}

function groupMembers(members) {
  const groups = new Map();
  const ungrouped = [];
  for (const a of members) {
    if (a.group) {
      if (!groups.has(a.group)) groups.set(a.group, []);
      groups.get(a.group).push(a);
    } else {
      ungrouped.push(a);
    }
  }
  return {
    groups: [...groups]
      .sort(([x], [y]) => x.localeCompare(y, "zh-CN"))
      .map(([name, list]) => ({ name, accounts: list, unread: list.reduce((n, a) => n + a.unread_inbox, 0) })),
    ungrouped,
  };
}

// 账号分区：同时有 Gmail 和 Outlook 时按服务商分区，区内按自定义分组
export const accountSections = computed(() => {
  const list = accounts.value;
  const present = PROVIDERS.filter((p) => list.some((a) => (a.provider || "gmail") === p.key));
  if (present.length <= 1) return [{ provider: null, accounts: list, unread: 0, ...groupMembers(list) }];
  return present.map((p) => {
    const members = list.filter((a) => (a.provider || "gmail") === p.key);
    return {
      provider: p,
      accounts: members,
      unread: members.reduce((n, a) => n + a.unread_inbox, 0),
      ...groupMembers(members),
    };
  });
});

// 侧栏顺序的账号列表（Alt+1…9 按这个顺序）
export const orderedAccounts = computed(() =>
  accountSections.value.flatMap((s) => [...s.groups.flatMap((g) => g.accounts), ...s.ungrouped]),
);

// 切换账号：保留当前文件夹（标签属于某个账号，切换后回到收件箱）；不在邮件页时进入收件箱
export function switchAccount(id) {
  if (id) rememberAccount(id);
  const route = router.currentRoute.value;
  const onMail = route.name === "list" || route.name === "message";
  const kind = onMail && ACCOUNT_KINDS.has(store.view.kind) ? store.view.kind : "inbox";
  router.push(listUrl(kind, id || ""));
}

export function isGroupCollapsed(key) {
  return prefs.collapsedGroups.includes(key);
}

export function toggleGroup(key) {
  prefs.collapsedGroups = isGroupCollapsed(key)
    ? prefs.collapsedGroups.filter((k) => k !== key)
    : [...prefs.collapsedGroups, key];
}

export function formatCount(n) {
  return n > 999 ? "999+" : String(n);
}
