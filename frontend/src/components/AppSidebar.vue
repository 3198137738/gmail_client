<script setup>
// 左侧导航（Gmail 式）：写邮件按钮、文件夹、标签、账号。
// 文件夹 / 标签 / 账号是三个独立的菜单：可以同时高亮「收件箱」和某个账号。
import { computed, h } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NIcon, NMenu } from "naive-ui";
import {
  AddOutlined,
  BlockOutlined,
  DraftsOutlined,
  EditOutlined,
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
  StarBorderOutlined,
  SyncOutlined,
} from "@vicons/material";
import { openCompose, store } from "../store";
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
    { key: listUrl("sent", accId), kind: "sent", label: "已发送", icon: icon(SendOutlined) },
  ];
  if (store.session?.config?.sync_junk_enabled) {
    items.push({ key: listUrl("junk", accId), kind: "junk", label: itemLabel("垃圾邮件", junk), icon: icon(ReportOutlined), unread: junk });
  }
  items.push(
    { key: listUrl("all", accId), kind: "all", label: "所有邮件", icon: icon(MailOutlined) },
    { key: "/compose/drafts", section: "drafts", label: itemLabel("草稿", n?.drafts || 0), icon: icon(DraftsOutlined) },
    { key: "/compose/sent", section: "sent_log", label: "发件记录", icon: icon(OutboxOutlined) },
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
  ["inbox", "sent", "junk", "all", "starred"].includes(activeKind.value) ? activeKind.value : "inbox",
);

const accountOptions = computed(() => {
  const items = [{ key: "all", label: "全部账号", icon: icon(LayersOutlined) }];
  for (const a of nav.value?.accounts || []) {
    items.push({
      key: `acc-${a.id}`,
      label: () =>
        h("span", { class: "gm-nav-label", title: a.email }, [
          h("span", { class: "gm-nav-text" }, a.name),
          a.unread_inbox ? h("span", { class: "gm-nav-count" }, String(a.unread_inbox)) : null,
        ]),
      icon: icon(LabelFilled, a.color),
    });
  }
  return items;
});

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

const mobileOptions = [
  { key: "settings", label: "设置", icon: icon(SettingsOutlined) },
  { key: "sync", label: "同步所有账号", icon: icon(SyncOutlined) },
];

function onMobile(key) {
  store.drawerOpen = false;
  if (key === "settings") router.push("/accounts/");
  else if (key === "sync") syncAll();
  else if (key === "logout") logout();
}

const mobileMenu = computed(() =>
  store.session?.auth_enabled
    ? [...mobileOptions, { key: "logout", label: "退出登录", icon: icon(LogOutOutlined) }]
    : mobileOptions,
);
</script>

<template>
  <aside class="gm-nav" :class="{ 'is-collapsed': collapsed, 'is-mobile': mobile }" aria-label="导航">
    <div v-if="mobile" class="gm-nav-brand">
      <img src="/img/logo.svg" alt="" width="32" height="24" />
      <span>Roundcube</span>
    </div>
    <div class="gm-nav-compose">
      <NButton class="gm-compose-btn" :circle="collapsed" title="写邮件（c）" @click="compose">
        <template #icon><NIcon :component="EditOutlined" :size="22" /></template>
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
      :indent="26"
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
        :indent="26"
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
      :value="accountValue"
      :collapsed="collapsed"
      :collapsed-width="72"
      :collapsed-icon-size="22"
      :icon-size="20"
      :indent="26"
      @update:value="onAccount"
    />
    <div v-else-if="nav && !collapsed" class="gm-nav-empty">
      还没有账号，点 <RouterLink to="/accounts/">+</RouterLink> 添加
    </div>

    <template v-if="mobile">
      <div class="gm-nav-divider" />
      <NMenu :options="mobileMenu" :value="null" :indent="26" :icon-size="20" @update:value="onMobile" />
    </template>
  </aside>
</template>

<style scoped>
.gm-nav {
    width: var(--gm-nav-w);
    flex: none;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 0 8px 16px 0;
    scrollbar-width: thin;
    transition: width .2s;
}
.gm-nav.is-collapsed { width: var(--gm-nav-w-collapsed); padding-right: 0; }
.gm-nav.is-mobile { width: 100%; padding-top: 8px; }
.gm-nav-brand {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 24px 12px;
    color: var(--gm-text-2);
    font: 400 20px/1 var(--gm-font);
}
.gm-nav-compose { padding: 8px 0 16px 8px; }
.gm-nav.is-collapsed .gm-nav-compose { padding-left: 8px; }
.gm-compose-btn {
    --n-height: 56px !important;
    --n-color: var(--gm-compose) !important;
    --n-color-hover: #b3dcf7 !important;
    --n-color-pressed: #a5d2f0 !important;
    --n-color-focus: var(--gm-compose) !important;
    --n-text-color: #001d35 !important;
    --n-text-color-hover: #001d35 !important;
    --n-text-color-pressed: #001d35 !important;
    --n-text-color-focus: #001d35 !important;
    --n-border: none !important;
    --n-border-hover: none !important;
    --n-border-pressed: none !important;
    --n-border-focus: none !important;
    --n-border-radius: 16px !important;
    --n-font-size: 14px !important;
    --n-padding: 0 24px 0 18px !important;
    --n-icon-margin: 12px !important;
    font-weight: 500;
}
.gm-compose-btn:hover { box-shadow: var(--gm-shadow-2); }
.gm-nav.is-collapsed .gm-compose-btn { --n-width: 56px !important; --n-padding: 0 !important; }

.gm-nav :deep(.n-menu-item-content) { padding-right: 12px !important; }
.gm-nav :deep(.n-menu-item-content::before) { left: 0 !important; right: 0 !important; border-radius: 0 16px 16px 0; }
.gm-nav.is-collapsed :deep(.n-menu-item-content::before) { left: 12px !important; right: 12px !important; border-radius: 16px; }
.gm-nav :deep(.gm-nav-label) { display: flex; align-items: center; gap: 8px; width: 100%; }
.gm-nav :deep(.gm-nav-text) { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.gm-nav :deep(.gm-nav-count) { font-size: 12px; font-weight: 700; }
.gm-nav :deep(.n-menu-item-content--selected .gm-nav-count) { font-weight: 700; }

.gm-nav-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 12px 4px 26px;
    font: 500 16px/1.5 var(--gm-font);
    color: var(--gm-text);
}
.gm-nav-add {
    display: flex;
    padding: 6px;
    border-radius: 50%;
    color: var(--gm-text-2);
}
.gm-nav-add:hover { background: var(--gm-hover); }
.gm-nav-empty { padding: 4px 26px; color: var(--gm-text-3); font-size: 13px; }
.gm-nav-divider { height: 1px; background: var(--gm-divider); margin: 12px 0; }
</style>
