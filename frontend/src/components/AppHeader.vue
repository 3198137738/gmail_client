<script setup>
import { computed, h } from "vue";
import { NAvatar, NBadge, NDropdown, NIcon, NTooltip } from "naive-ui";
import {
  BrightnessAutoOutlined,
  DarkModeOutlined,
  LightModeOutlined,
  LogOutOutlined,
  MenuOutlined,
  SettingsOutlined,
  SyncOutlined,
} from "@vicons/material";
import IconBtn from "./IconBtn.vue";
import SearchBox from "./SearchBox.vue";
import { navAccount, setNavCollapsed, setThemeMode, store } from "../store";
import { isMobile } from "../composables/common";
import { syncAll, logout } from "../actions";
import { initial } from "../format";

// 电脑端折叠 / 展开侧栏，手机端打开导航抽屉
function toggleMenu() {
  if (isMobile.value) store.drawerOpen = true;
  else setNavCollapsed(!store.navCollapsed);
}

const me = computed(() => {
  const nav = store.nav;
  if (!nav) return null;
  return navAccount(nav.preferred_account_id) || nav.accounts[0] || null;
});

const authEnabled = computed(() => store.session?.auth_enabled);
const realtimeEnabled = computed(() => store.session?.config?.realtime_enabled);
const realtimeType = computed(
  () => ({ connected: "success", connecting: "warning", offline: "error" })[store.realtime] || "default",
);

const THEME_ICONS = { light: LightModeOutlined, dark: DarkModeOutlined, auto: BrightnessAutoOutlined };
const themeOptions = [
  { key: "light", label: "浅色", icon: () => h(NIcon, { component: LightModeOutlined }) },
  { key: "dark", label: "深色", icon: () => h(NIcon, { component: DarkModeOutlined }) },
  { key: "auto", label: "跟随系统", icon: () => h(NIcon, { component: BrightnessAutoOutlined }) },
];
</script>

<template>
  <div class="gm-header-inner">
    <IconBtn :icon="MenuOutlined" :title="isMobile ? '主菜单' : '折叠 / 展开侧栏'" @click="toggleMenu" />
    <RouterLink v-if="!isMobile" class="gm-brand" to="/messages/" title="收件箱">
      <img src="/img/logo.svg" alt="" width="28" height="28" />
      <span>Roundcube</span>
    </RouterLink>

    <SearchBox />

    <div class="gm-header-end">
      <NTooltip v-if="realtimeEnabled && !isMobile">
        <template #trigger>
          <NBadge dot :type="realtimeType" :processing="store.realtime === 'connecting'" class="gm-rt">
            <span class="gm-rt-label">实时</span>
          </NBadge>
        </template>
        {{ store.realtimeTitle || "实时推送" }}
      </NTooltip>
      <template v-if="!isMobile">
        <IconBtn :icon="SyncOutlined" title="同步所有账号" @click="syncAll" />
        <NDropdown trigger="click" :options="themeOptions" :value="store.themeMode" @select="setThemeMode">
          <IconBtn :icon="THEME_ICONS[store.themeMode]" title="外观" />
        </NDropdown>
        <RouterLink to="/accounts/" custom v-slot="{ navigate }">
          <IconBtn :icon="SettingsOutlined" title="设置" @click="navigate" />
        </RouterLink>
        <IconBtn v-if="authEnabled" :icon="LogOutOutlined" title="退出登录" @click="logout" />
      </template>
      <RouterLink class="gm-me" to="/accounts/" :title="me ? me.email : '添加账号'">
        <NAvatar round :size="30" :style="{ background: me ? me.color : undefined }">{{ initial(me ? me.name : "我") }}</NAvatar>
        <span v-if="me && !isMobile" class="gm-me-email">{{ me.email }}</span>
      </RouterLink>
    </div>
  </div>
</template>

<style scoped>
.gm-header-inner {
    height: 100%;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 16px 0 15px;   /* 菜单按钮与折叠侧栏的图标在同一条中线上 */
}
.gm-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 160px;
    flex: none;
    color: var(--gm-text);
    font-size: 18px;
    font-weight: 600;
}
.gm-brand:hover { color: var(--gm-text); }
.gm-header-end { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.gm-rt { margin-right: 8px; }
.gm-rt-label { font-size: 12px; color: var(--gm-text-3); padding-right: 4px; }
.gm-me { display: flex; align-items: center; gap: 8px; margin-left: 8px; color: var(--gm-text-2); }
.gm-me:hover { color: var(--gm-text); }
.gm-me-email { font-size: 13px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

@media (max-width: 768px) {
    .gm-header-inner { padding: 0 8px; gap: 8px; }
}
</style>
