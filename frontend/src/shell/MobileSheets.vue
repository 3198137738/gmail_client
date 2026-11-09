<script setup>
// 手机端的底部面板：文件夹（文件夹 / 标签 / 账号 / 工具）和「我的」（工具页、设置、外观、通知、退出）。
import { computed, h } from "vue";
import { useRouter } from "vue-router";
import { NDrawer, NDrawerContent, NIcon } from "naive-ui";
import {
  BrightnessAutoOutlined,
  ChevronRightOutlined,
  DarkModeOutlined,
  KeyboardOutlined,
  LightModeOutlined,
  LogOutOutlined,
  NotificationsActiveOutlined,
  NotificationsOffOutlined,
  SettingsOutlined,
  SyncOutlined,
} from "@vicons/material";
import FolderPanel from "./FolderPanel.vue";
import GmCountBadge from "../ui/GmCountBadge.vue";
import { setThemeMode, store } from "../store";
import { prefs } from "../prefs";
import { logout, syncAll } from "../actions";
import { desktopNotify, setDesktopNotify } from "../desktopNotify";
import { PAGES, pageCount } from "./navModel";

const router = useRouter();

const show = computed({
  get: () => !!store.mobileSheet,
  set: (v) => {
    if (!v) store.mobileSheet = null;
  },
});

function close() {
  store.mobileSheet = null;
}

function go(path) {
  close();
  router.push(path);
}

const THEMES = [
  { mode: "light", label: "浅色", icon: LightModeOutlined },
  { mode: "dark", label: "深色", icon: DarkModeOutlined },
  { mode: "auto", label: "跟随系统", icon: BrightnessAutoOutlined },
];

const icon = (c) => () => h(NIcon, { component: c, size: 22 });
const actions = computed(() => {
  const list = [
    { key: "settings", label: "设置", icon: icon(SettingsOutlined), run: () => go("/accounts/") },
    { key: "sync", label: "同步所有账号", icon: icon(SyncOutlined), run: () => (close(), syncAll()) },
  ];
  if (desktopNotify.supported) {
    list.push({
      key: "notify",
      label: desktopNotify.enabled ? "关闭桌面通知" : "开启桌面通知",
      icon: icon(desktopNotify.enabled ? NotificationsActiveOutlined : NotificationsOffOutlined),
      run: () => setDesktopNotify(!desktopNotify.enabled),
    });
  }
  list.push({ key: "shortcuts", label: "键盘快捷键", icon: icon(KeyboardOutlined), run: () => (close(), (store.shortcutHelp = true)) });
  if (store.session?.auth_enabled) {
    list.push({ key: "logout", label: "退出登录", icon: icon(LogOutOutlined), danger: true, run: () => (close(), logout()) });
  }
  return list;
});
</script>

<template>
  <NDrawer v-model:show="show" placement="bottom" height="85%" :auto-focus="false" class="gm-sheet">
    <NDrawerContent :native-scrollbar="false" body-content-style="padding: 0" :body-style="{ borderRadius: 'var(--r-lg) var(--r-lg) 0 0' }">
      <div class="gm-sheet-grip" aria-hidden="true" />
      <FolderPanel v-if="store.mobileSheet === 'folders'" mobile @navigate="close" />
      <div v-else-if="store.mobileSheet === 'me'" class="gm-me">
        <div class="gm-me-heading">工具</div>
        <button v-for="p in PAGES" :key="p.path" type="button" class="gm-me-item" @click="go(p.path)">
          <NIcon :component="p.icon" :size="22" />
          <span class="gm-me-text">{{ p.label }}</span>
          <GmCountBadge :count="pageCount(p)" />
          <NIcon :component="ChevronRightOutlined" :size="20" class="gm-me-chevron" />
        </button>

        <div class="gm-me-heading">外观</div>
        <div class="gm-me-themes" role="radiogroup" aria-label="外观">
          <button
            v-for="t in THEMES"
            :key="t.mode"
            type="button"
            role="radio"
            :aria-checked="prefs.theme === t.mode"
            class="gm-me-theme"
            :class="{ 'is-active': prefs.theme === t.mode }"
            @click="setThemeMode(t.mode)"
          >
            <NIcon :component="t.icon" :size="20" />
            {{ t.label }}
          </button>
        </div>

        <div class="gm-me-heading">更多</div>
        <button v-for="a in actions" :key="a.key" type="button" class="gm-me-item" :class="{ 'is-danger': a.danger }" @click="a.run()">
          <component :is="a.icon" />
          <span class="gm-me-text">{{ a.label }}</span>
        </button>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.gm-sheet-grip { width: 40px; height: 4px; margin: 8px auto 0; border-radius: var(--r-pill); background: var(--gm-border); }
.gm-me { padding: 0 var(--sp-3) var(--sp-6); }
.gm-me-heading { padding: var(--sp-4) var(--sp-2) var(--sp-1); font-size: var(--fs-xs); font-weight: 600; color: var(--gm-text-3); }
.gm-me-item {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    width: 100%;
    height: 48px;
    padding: 0 var(--sp-2);
    border: 0;
    border-radius: var(--r-md);
    background: none;
    color: var(--gm-text);
    font: inherit;
    font-size: var(--fs-lg);
    text-align: left;
    cursor: pointer;
}
.gm-me-item:active { background: var(--gm-hover); }
.gm-me-item.is-danger { color: var(--gm-error); }
.gm-me-text { flex: 1; }
.gm-me-chevron { color: var(--gm-text-4); }
.gm-me-themes { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--sp-2); }
.gm-me-theme {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    padding: var(--sp-3) 0;
    border: 1px solid var(--gm-border);
    border-radius: var(--r-md);
    background: none;
    color: var(--gm-text-2);
    font: inherit;
    cursor: pointer;
}
.gm-me-theme.is-active { border-color: var(--gm-primary); background: var(--gm-primary-soft); color: var(--gm-primary); }
</style>
