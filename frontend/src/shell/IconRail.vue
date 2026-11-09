<script setup>
// 电脑端最左侧的图标栏：写邮件、收件箱、账号切换、功能页；底部：实时状态、命令面板、设置、个人菜单。
// 主题、桌面通知、同步、退出只在个人菜单里出现（不再分散在多处）。
import { computed, h } from "vue";
import { useRoute } from "vue-router";
import { NBadge, NDropdown, NIcon, NTooltip } from "naive-ui";
import {
  BrightnessAutoOutlined,
  DarkModeOutlined,
  EditOutlined,
  InboxOutlined,
  KeyboardOutlined,
  LayersOutlined,
  LightModeOutlined,
  LogOutOutlined,
  NotificationsActiveOutlined,
  NotificationsOffOutlined,
  SearchOutlined,
  SettingsOutlined,
  SyncOutlined,
} from "@vicons/material";
import GmAvatar from "../ui/GmAvatar.vue";
import GmCountBadge from "../ui/GmCountBadge.vue";
import { openCompose, setThemeMode, store } from "../store";
import { prefs } from "../prefs";
import { logout, syncAll } from "../actions";
import { desktopNotify, setDesktopNotify } from "../desktopNotify";
import { APP_NAME } from "../brand";
import { PAGES, currentAccount, listUrl, nav, pageCount } from "./navModel";

const route = useRoute();
const onMail = computed(() => route.name === "list" || route.name === "message");
const railPages = PAGES.filter((p) => p.rail);
const inboxUrl = computed(() => listUrl("inbox"));
const unread = computed(() => (currentAccount.value ? currentAccount.value.unread_inbox : nav.value?.unread_inbox || 0));

const realtimeEnabled = computed(() => store.session?.config?.realtime_enabled);
const realtimeType = computed(
  () => ({ connected: "success", connecting: "warning", offline: "error" })[store.realtime] || "default",
);

const icon = (component) => () => h(NIcon, { component });
const THEMES = [
  { key: "theme-light", mode: "light", label: "浅色", icon: LightModeOutlined },
  { key: "theme-dark", mode: "dark", label: "深色", icon: DarkModeOutlined },
  { key: "theme-auto", mode: "auto", label: "跟随系统", icon: BrightnessAutoOutlined },
];

const meOptions = computed(() => {
  const theme = THEMES.find((t) => t.mode === prefs.theme) || THEMES[2];
  const opts = [
    {
      key: "theme",
      label: `外观：${theme.label}`,
      icon: icon(theme.icon),
      children: THEMES.map((t) => ({ key: t.key, label: t.label, icon: icon(t.icon) })),
    },
  ];
  if (desktopNotify.supported) {
    opts.push({
      key: "notify",
      label: desktopNotify.enabled ? "桌面通知：已开启" : "桌面通知：已关闭",
      icon: icon(desktopNotify.enabled ? NotificationsActiveOutlined : NotificationsOffOutlined),
    });
  }
  opts.push(
    { key: "sync", label: "同步所有账号", icon: icon(SyncOutlined) },
    { key: "shortcuts", label: "键盘快捷键", icon: icon(KeyboardOutlined) },
  );
  if (store.session?.auth_enabled) {
    opts.push({ type: "divider", key: "d1" }, { key: "logout", label: "退出登录", icon: icon(LogOutOutlined) });
  }
  return opts;
});

function onMe(key) {
  const theme = THEMES.find((t) => t.key === key);
  if (theme) setThemeMode(theme.mode);
  else if (key === "notify") setDesktopNotify(!desktopNotify.enabled);
  else if (key === "sync") syncAll();
  else if (key === "shortcuts") store.shortcutHelp = true;
  else if (key === "logout") logout();
}
</script>

<template>
  <nav class="gm-rail" aria-label="主导航">
    <RouterLink :to="inboxUrl" class="gm-rail-logo" :title="APP_NAME">
      <img src="/img/logo.svg" alt="" width="30" height="30" />
    </RouterLink>

    <NTooltip placement="right" :delay="300">
      <template #trigger>
        <button type="button" class="gm-rail-compose" aria-label="写邮件（c）" @click="openCompose()">
          <NIcon :component="EditOutlined" :size="22" />
        </button>
      </template>
      写邮件 <kbd>c</kbd>
    </NTooltip>

    <div class="gm-rail-group">
      <NTooltip placement="right" :delay="300">
        <template #trigger>
          <RouterLink :to="inboxUrl" class="gm-rail-btn" :class="{ 'is-active': onMail }" aria-label="邮件">
            <NIcon :component="InboxOutlined" :size="22" />
            <GmCountBadge class="gm-rail-badge" :count="unread" tone="solid" />
          </RouterLink>
        </template>
        邮件<template v-if="unread">（{{ unread }} 封未读）</template>
      </NTooltip>

      <NTooltip placement="right" :delay="300">
        <template #trigger>
          <button type="button" class="gm-rail-btn" aria-label="切换账号" @click="store.accountSwitcher = true">
            <GmAvatar v-if="currentAccount" :name="currentAccount.name" :color="currentAccount.color" :size="28" />
            <span v-else class="gm-rail-all"><NIcon :component="LayersOutlined" :size="18" /></span>
          </button>
        </template>
        {{ currentAccount ? currentAccount.email : "全部账号" }} · 切换账号 <kbd>'</kbd>
      </NTooltip>

      <span class="gm-rail-sep" />

      <NTooltip v-for="p in railPages" :key="p.path" placement="right" :delay="300">
        <template #trigger>
          <RouterLink :to="p.path" class="gm-rail-btn" :class="{ 'is-active': route.meta.section === p.section }" :aria-label="p.label">
            <NIcon :component="p.icon" :size="22" />
            <GmCountBadge class="gm-rail-badge" :count="pageCount(p)" tone="solid" />
          </RouterLink>
        </template>
        {{ p.label }}
      </NTooltip>
    </div>

    <div class="gm-rail-group is-bottom">
      <NTooltip v-if="realtimeEnabled" placement="right" :delay="200">
        <template #trigger>
          <span class="gm-rail-rt" tabindex="0" aria-label="实时推送状态">
            <NBadge dot :type="realtimeType" :processing="store.realtime === 'connecting'" />
          </span>
        </template>
        {{ store.realtimeTitle || "实时推送" }}
      </NTooltip>

      <NTooltip placement="right" :delay="300">
        <template #trigger>
          <button type="button" class="gm-rail-btn" aria-label="命令面板" @click="store.palette = true">
            <NIcon :component="SearchOutlined" :size="22" />
          </button>
        </template>
        命令面板 <kbd>Ctrl K</kbd>
      </NTooltip>

      <NTooltip placement="right" :delay="300">
        <template #trigger>
          <RouterLink to="/accounts/" class="gm-rail-btn" :class="{ 'is-active': route.meta.section === 'settings' }" aria-label="设置">
            <NIcon :component="SettingsOutlined" :size="22" />
          </RouterLink>
        </template>
        设置
      </NTooltip>

      <NDropdown trigger="click" placement="right-end" :options="meOptions" @select="onMe">
        <button type="button" class="gm-rail-btn" aria-label="个人菜单：外观、通知、同步、退出">
          <NIcon :component="prefs.theme === 'dark' ? DarkModeOutlined : prefs.theme === 'light' ? LightModeOutlined : BrightnessAutoOutlined" :size="22" />
        </button>
      </NDropdown>
    </div>
  </nav>
</template>

<style scoped>
.gm-rail {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--sp-2);
    width: var(--rail-w);
    height: 100%;
    padding: var(--sp-3) 0;
    flex: none;
}
.gm-rail-logo { display: flex; padding: 4px; border-radius: var(--r-md); }
.gm-rail-compose {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    margin: var(--sp-1) 0 var(--sp-2);
    border: 0;
    border-radius: var(--r-md);
    background: var(--gm-primary);
    color: #fff;
    box-shadow: 0 4px 12px color-mix(in srgb, var(--gm-primary) 35%, transparent);
    cursor: pointer;
    transition: transform var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease);
}
.gm-rail-compose:hover { background: var(--gm-primary-hover); transform: translateY(-1px); }
.gm-rail-group { display: flex; flex-direction: column; align-items: center; gap: var(--sp-1); }
.gm-rail-group.is-bottom { margin-top: auto; }
.gm-rail-sep { width: 24px; height: 1px; margin: var(--sp-1) 0; background: var(--gm-border); }
.gm-rail-btn {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    border: 0;
    border-radius: var(--r-md);
    background: none;
    color: var(--gm-text-3);
    cursor: pointer;
    transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}
.gm-rail-btn:hover { background: var(--gm-hover); color: var(--gm-text); }
.gm-rail-btn.is-active { background: var(--gm-selected); color: var(--gm-primary); }
.gm-rail-btn:focus-visible, .gm-rail-compose:focus-visible { outline: 2px solid var(--gm-primary); outline-offset: 2px; }
.gm-rail-badge { position: absolute; top: 2px; right: 0; }
.gm-rail-all {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--gm-primary-soft);
    color: var(--gm-primary);
}
.gm-rail-rt { display: inline-flex; padding: 8px; }
kbd {
    margin-left: 4px;
    padding: 0 4px;
    border: 1px solid rgba(255, 255, 255, .3);
    border-radius: 3px;
    font: 11px/16px ui-monospace, Consolas, monospace;
}
</style>
