<script setup>
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NIcon, NInput } from "naive-ui";
import {
  CloseOutlined,
  LogOutOutlined,
  MenuOutlined,
  SearchOutlined,
  SettingsOutlined,
  SyncOutlined,
} from "@vicons/material";
import IconBtn from "./IconBtn.vue";
import { navAccount, setNavCollapsed, store } from "../store";
import { isMobile } from "../composables/common";
import { syncAll, logout } from "../actions";
import { initial } from "../format";

const route = useRoute();
const router = useRouter();

const search = ref("");
watch(
  () => route.fullPath,
  () => {
    search.value = route.name === "list" ? String(route.query.q || "") : "";
  },
  { immediate: true },
);

function submitSearch() {
  const q = search.value.trim();
  // 在列表页搜索时保留当前文件夹、账号、每页封数和「仅看未读」
  const base = route.name === "list" ? route.query : { kind: store.view.kind || "inbox" };
  const query = { ...base, q, page: undefined, after: undefined, before: undefined };
  if (!q) delete query.q;
  router.push({ path: "/messages/", query });
}

function clearSearch() {
  search.value = "";
  submitSearch();
}

function toggleMenu() {
  if (isMobile.value) store.drawerOpen = !store.drawerOpen;
  else setNavCollapsed(!store.navCollapsed);
}

const me = computed(() => {
  const nav = store.nav;
  if (!nav) return null;
  return navAccount(nav.preferred_account_id) || nav.accounts[0] || null;
});

const authEnabled = computed(() => store.session?.auth_enabled);
const realtimeEnabled = computed(() => store.session?.config?.realtime_enabled);
</script>

<template>
  <header class="gm-header">
    <div class="gm-header-start">
      <IconBtn :icon="MenuOutlined" title="主菜单" size="large" :icon-size="24" @click="toggleMenu" />
      <RouterLink v-if="!isMobile" class="gm-brand" to="/messages/" title="收件箱">
        <img src="/img/logo.svg" alt="" width="40" height="30" />
        <span>Roundcube</span>
      </RouterLink>
    </div>

    <form class="gm-search" role="search" @submit.prevent="submitSearch">
      <NInput
        v-model:value="search"
        round
        size="large"
        placeholder="搜索邮件"
        class="gm-search-input"
        :input-props="{ 'aria-label': '搜索邮件', autocomplete: 'off', 'data-search': '' }"
        @keydown.esc="($event.target).blur()"
      >
        <template #prefix>
          <NIcon :component="SearchOutlined" :size="22" class="gm-search-icon" @click="submitSearch" />
        </template>
        <template #suffix>
          <NIcon
            v-if="search"
            :component="CloseOutlined"
            :size="20"
            class="gm-search-icon"
            title="清除搜索"
            @click="clearSearch"
          />
        </template>
      </NInput>
    </form>

    <div class="gm-header-end">
      <span
        v-if="realtimeEnabled && !isMobile"
        class="gm-rt"
        :data-state="store.realtime"
        :title="store.realtimeTitle"
        role="status"
      >
        <span class="gm-rt-dot" />
      </span>
      <template v-if="!isMobile">
        <IconBtn :icon="SyncOutlined" title="同步所有账号" size="large" :icon-size="22" @click="syncAll" />
        <RouterLink to="/accounts/" custom v-slot="{ navigate }">
          <IconBtn :icon="SettingsOutlined" title="设置" size="large" :icon-size="22" @click="navigate" />
        </RouterLink>
        <IconBtn v-if="authEnabled" :icon="LogOutOutlined" title="退出登录" size="large" :icon-size="22" @click="logout" />
      </template>
      <RouterLink class="gm-avatar-btn" to="/accounts/" :title="me ? `Roundcube 账号\n${me.email}` : '添加账号'">
        <span class="gm-avatar" :style="{ background: me ? me.color : '#5f6368' }">{{ initial(me ? me.name : "我") }}</span>
      </RouterLink>
    </div>
  </header>
</template>

<style scoped>
.gm-header {
    height: var(--gm-header-h);
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px 8px 8px;
    flex: none;
}
.gm-header-start { display: flex; align-items: center; gap: 4px; width: calc(var(--gm-nav-w) - 16px); flex: none; }
.gm-brand {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 8px;
    color: var(--gm-text-2);
    font: 400 22px/1 var(--gm-font);
    text-decoration: none !important;
}
.gm-search { flex: 1 1 auto; max-width: 720px; min-width: 0; }
.gm-search-input {
    --n-color: var(--gm-search) !important;
    --n-color-focus: #fff !important;
    --n-border: 1px solid transparent !important;
    --n-border-hover: 1px solid transparent !important;
    --n-height: 48px !important;
}
.gm-search-input:focus-within { box-shadow: var(--gm-shadow-1); border-radius: 24px; }
.gm-search-icon { cursor: pointer; color: var(--gm-text-2); margin: 0 4px; }
.gm-header-end { display: flex; align-items: center; gap: 2px; margin-left: auto; }
.gm-avatar-btn { display: flex; padding: 4px; margin-left: 4px; border-radius: 50%; }
.gm-avatar-btn:hover { background: var(--gm-hover); }

.gm-rt { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; }
.gm-rt-dot { width: 8px; height: 8px; border-radius: 50%; background: #9aa0a6; }
.gm-rt[data-state="connected"] .gm-rt-dot { background: #1e8e3e; }
.gm-rt[data-state="connecting"] .gm-rt-dot { background: #f9ab00; animation: gm-pulse 1.2s infinite; }
.gm-rt[data-state="offline"] .gm-rt-dot { background: #d93025; }
@keyframes gm-pulse { 50% { opacity: .35; } }

@media (max-width: 768px) {
    .gm-header { padding: 8px; gap: 4px; }
    .gm-header-start { width: auto; }
}
</style>
