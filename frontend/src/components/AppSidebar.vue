<script setup>
// 左侧导航（Gmail 式）：写邮件按钮、文件夹、标签、账号。
// 文件夹 / 标签 / 账号是三个独立的菜单：可以同时高亮「收件箱」和某个账号。
import { computed, h, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NIcon, NMenu } from "naive-ui";
import {
  AddOutlined,
  AttachFileOutlined,
  BrightnessAutoOutlined,
  DarkModeOutlined,
  LightModeOutlined,
  BlockOutlined,
  DraftsOutlined,
  ScheduleSendOutlined,
  EditOutlined,
  FolderOutlined,
  InboxOutlined,
  LabelFilled,
  LabelOutlined,
  LayersOutlined,
  LogOutOutlined,
  MailOutlined,
  OutboxOutlined,
  ReportOutlined,
  SendOutlined,
  SettingsOutlined,
  SnoozeOutlined,
  StarBorderOutlined,
  SyncOutlined,
} from "@vicons/material";
import { openCompose, setThemeMode, store } from "../store";
import { logout, syncAll } from "../actions";

const props = defineProps({
  collapsed: { type: Boolean, default: false },
  mobile: { type: Boolean, default: false },
});

const route = useRoute();
const router = useRouter();

const nav = computed(() => store.nav);
const currentAccount = computed(() => {
  const id = store.view.accountId ?? nav.value?.preferred_account_id ?? null;
  return nav.value?.accounts.find((a) => a.id === id) || null;
});
const section = computed(() => route.meta.section || null);
const activeKind = computed(() => (section.value ? null : store.view.kind));
const unreadParam = computed(() => (store.view.unreadOnly && route.name === "list" ? "&unread=1" : ""));

function listUrl(kind, accountId) {
  return `/messages/?kind=${kind}&account_id=${accountId ?? ""}${unreadParam.value}`;
}

const icon = (component, color) => () => h(NIcon, { component, color });

function itemLabel(text, count) {
  if (!count) return text;
  return () =>
    h("span", { class: "gm-nav-label" }, [
      h("span", { class: "gm-nav-text" }, text),
      h("span", { class: "gm-nav-count" }, count > 999 ? "999+" : String(count)),
    ]);
}

const folderOptions = computed(() => {
  const n = nav.value;
  const acc = currentAccount.value;
  const accId = acc?.id;
  const inbox = acc ? acc.unread_inbox : n?.unread_inbox || 0;
  const junk = acc ? acc.unread_junk : n?.unread_junk || 0;
  const items = [
    { key: listUrl("inbox", accId), kind: "inbox", label: itemLabel("收件箱", inbox), icon: icon(InboxOutlined), unread: inbox },
    { key: listUrl("starred", accId), kind: "starred", label: "已加星标", icon: icon(StarBorderOutlined) },
    { key: listUrl("snoozed", accId), kind: "snoozed", label: itemLabel("已延后", n?.snoozed || 0), icon: icon(SnoozeOutlined) },
    { key: listUrl("sent", accId), kind: "sent", label: "已发送", icon: icon(SendOutlined) },
  ];
  if (store.session?.config?.sync_junk_enabled) {
    items.push({ key: listUrl("junk", accId), kind: "junk", label: itemLabel("垃圾邮件", junk), icon: icon(ReportOutlined), unread: junk });
  }
  items.push(
    { key: listUrl("all", accId), kind: "all", label: "所有邮件", icon: icon(MailOutlined) },
    { key: "/compose/drafts", section: "drafts", label: itemLabel("草稿", n?.drafts || 0), icon: icon(DraftsOutlined) },
    { key: "/compose/scheduled", section: "scheduled", label: itemLabel("定时发送", n?.scheduled || 0), icon: icon(ScheduleSendOutlined) },
    { key: "/compose/sent", section: "sent_log", label: "发件记录", icon: icon(OutboxOutlined) },
    { key: "/files", section: "files", label: "附件", icon: icon(AttachFileOutlined) },
    { key: "/messages/blocked-senders", section: "blocked", label: "屏蔽的发件人", icon: icon(BlockOutlined) },
  );
  return items;
});

const folderValue = computed(() => {
  const item = folderOptions.value.find((o) =>
    o.section ? o.section === section.value : o.kind === activeKind.value,
  );
  return item?.key ?? null;
});

const labels = computed(() => {
  const all = nav.value?.labels || [];
  const acc = currentAccount.value;
  return acc ? all.filter((lb) => lb.account_id === acc.id) : all;
});

const labelOptions = computed(() => {
  const multi = (nav.value?.accounts.length || 0) > 1 && !currentAccount.value;
  return labels.value.map((lb) => {
    const owner = nav.value.accounts.find((a) => a.id === lb.account_id);
    return {
      key: `/messages/?kind=label-${lb.id}&account_id=${lb.account_id}`,
      kind: `label-${lb.id}`,
      label: () => h("span", { title: multi && owner ? `${lb.name}（${owner.email}）` : lb.name }, lb.short),
      icon: icon(activeKind.value === `label-${lb.id}` ? LabelFilled : LabelOutlined),
    };
  });
});

const labelValue = computed(() => labelOptions.value.find((o) => o.kind === activeKind.value)?.key ?? null);

// 切换账号时保留当前文件夹（标签视图属于某个账号，切换后回到收件箱）
const accountKind = computed(() =>
  ["inbox", "sent", "junk", "all", "starred", "snoozed"].includes(activeKind.value) ? activeKind.value : "inbox",
);

function accountItem(a) {
  return {
    key: `acc-${a.id}`,
    label: () =>
      h("span", { class: "gm-nav-label", title: a.email }, [
        h("span", { class: "gm-nav-text" }, a.name),
        a.unread_inbox ? h("span", { class: "gm-nav-count" }, String(a.unread_inbox)) : null,
      ]),
    icon: icon(LabelFilled, a.color),
  };
}

// 设置了分组的账号放进可折叠的分组里（显示组内未读合计），没有分组的排在后面
const accountOptions = computed(() => {
  const items = [{ key: "all", label: "全部账号", icon: icon(LayersOutlined) }];
  const groups = new Map();
  const ungrouped = [];
  for (const a of nav.value?.accounts || []) {
    if (a.group) {
      if (!groups.has(a.group)) groups.set(a.group, []);
      groups.get(a.group).push(a);
    } else {
      ungrouped.push(a);
    }
  }
  for (const [name, members] of [...groups].sort(([x], [y]) => x.localeCompare(y, "zh-CN"))) {
    const unread = members.reduce((n, a) => n + a.unread_inbox, 0);
    items.push({
      key: `grp-${name}`,
      label: () =>
        h("span", { class: "gm-nav-label", title: `${name}（${members.length} 个账号）` }, [
          h("span", { class: "gm-nav-text" }, name),
          unread ? h("span", { class: "gm-nav-count" }, String(unread)) : null,
        ]),
      icon: icon(FolderOutlined),
      children: members.map(accountItem),
    });
  }
  items.push(...ungrouped.map(accountItem));
  return items;
});
const groupKeys = computed(() => accountOptions.value.filter((o) => o.children).map((o) => o.key));

// 分组默认展开；用户折叠过的分组记在 localStorage
const COLLAPSED_KEY = "gm:collapsed-groups";
function storedCollapsed() {
  try {
    const keys = JSON.parse(localStorage.getItem(COLLAPSED_KEY) || "[]");
    return new Set(Array.isArray(keys) ? keys : []);
  } catch {
    return new Set();
  }
}
const collapsedGroups = ref(storedCollapsed());
const expandedGroups = computed(() => groupKeys.value.filter((k) => !collapsedGroups.value.has(k)));
function onGroupsExpanded(keys) {
  collapsedGroups.value = new Set(groupKeys.value.filter((k) => !keys.includes(k)));
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsedGroups.value]));
  } catch {
    /* 隐私模式等情况下忽略 */
  }
}

const accountValue = computed(() => {
  if (!activeKind.value) return null;
  return currentAccount.value ? `acc-${currentAccount.value.id}` : "all";
});

function go(url) {
  if (props.mobile) store.drawerOpen = false;
  router.push(url);
}

function onFolder(key) {
  go(key);
}

function onAccount(key) {
  const id = key === "all" ? "" : key.slice(4);
  go(`/messages/?kind=${accountKind.value}&account_id=${id}${unreadParam.value}`);
}

function compose() {
  if (props.mobile) store.drawerOpen = false;
  openCompose();
}

const THEMES = [
  { mode: "light", label: "浅色", icon: LightModeOutlined },
  { mode: "dark", label: "深色", icon: DarkModeOutlined },
  { mode: "auto", label: "跟随系统", icon: BrightnessAutoOutlined },
];

const mobileOptions = computed(() => [
  { key: "settings", label: "设置", icon: icon(SettingsOutlined) },
  { key: "sync", label: "同步所有账号", icon: icon(SyncOutlined) },
  {
    key: "theme",
    label: `外观：${THEMES.find((t) => t.mode === store.themeMode).label}`,
    icon: icon(THEMES.find((t) => t.mode === store.themeMode).icon),
    children: THEMES.map((t) => ({ key: `theme-${t.mode}`, label: t.label, icon: icon(t.icon) })),
  },
]);

function onMobile(key) {
  if (!key.startsWith("theme-")) store.drawerOpen = false;
  if (key.startsWith("theme-")) {
    setThemeMode(key.slice(6));
    return;
  }
  if (key === "settings") router.push("/accounts/");
  else if (key === "sync") syncAll();
  else if (key === "logout") logout();
}

const mobileMenu = computed(() =>
  store.session?.auth_enabled
    ? [...mobileOptions.value, { key: "logout", label: "退出登录", icon: icon(LogOutOutlined) }]
    : mobileOptions.value,
);
</script>

<template>
  <aside class="gm-nav" :class="{ 'is-collapsed': collapsed, 'is-mobile': mobile }" aria-label="导航">
    <div v-if="mobile" class="gm-nav-brand">
      <img src="/img/logo.svg" alt="" width="28" height="28" />
      <span>Roundcube</span>
    </div>
    <div class="gm-nav-compose">
      <NButton type="primary" :block="!collapsed" :circle="collapsed" title="写邮件（c）" @click="compose">
        <template #icon><NIcon :component="EditOutlined" /></template>
        <template v-if="!collapsed">写邮件</template>
      </NButton>
    </div>

    <NMenu
      :options="folderOptions"
      :value="folderValue"
      :collapsed="collapsed"
      :collapsed-width="72"
      :collapsed-icon-size="22"
      :icon-size="20"
      :indent="20"
      @update:value="onFolder"
    />

    <template v-if="labelOptions.length">
      <div v-if="!collapsed" class="gm-nav-heading"><span>标签</span></div>
      <NMenu
        :options="labelOptions"
        :value="labelValue"
        :collapsed="collapsed"
        :collapsed-width="72"
        :collapsed-icon-size="22"
        :icon-size="20"
        :indent="20"
        @update:value="onFolder"
      />
    </template>

    <div v-if="!collapsed" class="gm-nav-heading">
      <span>账号</span>
      <RouterLink class="gm-nav-add" to="/accounts/" title="添加或管理账号" aria-label="添加或管理账号" @click="mobile && (store.drawerOpen = false)">
        <NIcon :component="AddOutlined" :size="20" />
      </RouterLink>
    </div>
    <NMenu
      v-if="nav && nav.accounts.length"
      :options="accountOptions"
      :expanded-keys="expandedGroups"
      @update:expanded-keys="onGroupsExpanded"
      :value="accountValue"
      :collapsed="collapsed"
      :collapsed-width="72"
      :collapsed-icon-size="22"
      :icon-size="20"
      :indent="20"
      @update:value="onAccount"
    />
    <div v-else-if="nav && !collapsed" class="gm-nav-empty">
      还没有账号，点 <RouterLink to="/accounts/">+</RouterLink> 添加
    </div>

    <template v-if="mobile">
      <div class="gm-nav-divider" />
      <NMenu :options="mobileMenu" :value="null" :indent="20" :icon-size="20" @update:value="onMobile" />
    </template>
  </aside>
</template>

<style scoped>
.gm-nav { padding-bottom: 16px; }
.gm-nav-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 16px 20px 8px;
    font-size: 17px;
    font-weight: 600;
}
.gm-nav-compose { padding: 12px 12px 6px; }
.gm-nav.is-collapsed .gm-nav-compose { padding: 16px 0 8px; display: flex; justify-content: center; }
/* 折叠时图标居中：(64 - 22) / 2；Naive 菜单默认算出的左边距偏大，图标会偏右 */
.gm-nav.is-collapsed :deep(.n-menu-item-content) { padding-left: 21px !important; padding-right: 21px !important; }
.gm-nav :deep(.gm-nav-label) { display: flex; align-items: center; gap: 8px; width: 100%; }
.gm-nav :deep(.gm-nav-text) { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.gm-nav :deep(.gm-nav-count) {
    min-width: 20px;
    padding: 0 6px;
    border-radius: 10px;
    background: var(--gm-primary-soft);
    color: var(--gm-primary);
    font-size: 12px;
    font-weight: 600;
    line-height: 18px;
    text-align: center;
}
.gm-nav-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 12px 2px 20px;
    font-size: 12px;
    color: var(--gm-text-3);
}
.gm-nav-add { display: flex; padding: 2px; border-radius: var(--gm-radius); color: var(--gm-text-3); }
.gm-nav-add:hover { background: var(--gm-hover); color: var(--gm-primary); }
.gm-nav-empty { padding: 4px 24px; color: var(--gm-text-3); font-size: 13px; }
.gm-nav-divider { height: 1px; background: var(--gm-divider); margin: 12px 0; }
</style>
