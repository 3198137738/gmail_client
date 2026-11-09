<script setup>
// 登录后的整体框架（三栏）：图标栏 │ 文件夹 / 账号面板（可拖宽、可收起）│ 搜索栏 + 内容。
// 窄窗口（<1280）文件夹面板改为浮层；手机端为 顶部搜索 + 内容 + 底部标签栏。
// 写邮件窗口、命令面板、账号切换器、批量删除进度、快捷键帮助浮在上面。
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { NIcon } from "naive-ui";
import { ViewSidebarOutlined } from "@vicons/material";
import IconRail from "./IconRail.vue";
import FolderPanel from "./FolderPanel.vue";
import BottomTabBar from "./BottomTabBar.vue";
import MobileSheets from "./MobileSheets.vue";
import AccountSwitcher from "./AccountSwitcher.vue";
import SearchBox from "../components/SearchBox.vue";
import PurgeProgress from "../components/PurgeProgress.vue";
import GmResizer from "../ui/GmResizer.vue";
import { store } from "../store";
import { prefs } from "../prefs";
import { isMobile, isNarrow, refreshNav } from "../composables/common";
import { useGlobalShortcuts } from "../composables/shortcuts";
import { onRealtime, startRealtime } from "../realtime";
import { startDesktopNotify } from "../desktopNotify";
import { orderedAccounts, switchAccount } from "./navModel";

// 不常用的浮层按需加载，减小首屏体积
const ComposeWindow = defineAsyncComponent(() => import("../components/ComposeWindow.vue"));
const CommandPalette = defineAsyncComponent(() => import("./CommandPalette.vue"));
const ShortcutHelp = defineAsyncComponent(() => import("../components/ShortcutHelp.vue"));

const route = useRoute();
const reading = computed(() => route.name === "message");

// 非邮件列表 / 详情页面：导航中不高亮任何文件夹
watch(
  () => route.name,
  (name) => {
    if (name !== "list" && name !== "message") store.view.kind = null;
    overlayOpen.value = false;
  },
);

// 文件夹面板：宽窗口时停靠（prefs.folderPanel 控制显示），窄窗口时是可开关的浮层
const overlayOpen = ref(false);
const panelDocked = computed(() => !isMobile.value && !isNarrow.value && prefs.folderPanel);
const panelOverlay = computed(() => !isMobile.value && isNarrow.value && overlayOpen.value);

function togglePanel() {
  if (isMobile.value) store.mobileSheet = store.mobileSheet === "folders" ? null : "folders";
  else if (isNarrow.value) overlayOpen.value = !overlayOpen.value;
  else prefs.folderPanel = !prefs.folderPanel;
}

// 浮层或命令面板等第一次打开后才加载对应组件
const paletteLoaded = ref(false);
const helpLoaded = ref(false);
watch(() => store.palette, (v) => v && (paletteLoaded.value = true));
watch(() => store.shortcutHelp, (v) => v && (helpLoaded.value = true));

// 手机端阅读或写邮件时隐藏底部标签栏和顶部搜索
const hideMobileChrome = computed(() => isMobile.value && (reading.value || !!store.compose));

const disposers = [];
onMounted(() => {
  refreshNav();
  startRealtime();
  startDesktopNotify();
  // 任何邮箱变化都要更新导航里的未读数
  for (const type of ["new_mail", "mail_changed", "sync_done", "resume"]) {
    disposers.push(onRealtime(type, refreshNav));
  }
  disposers.push(onRealtime("open", (e) => !e.first && refreshNav()));
});
onBeforeUnmount(() => disposers.forEach((d) => d()));

useGlobalShortcuts({
  togglePanel,
  switchAccountAt(index) {
    if (index < 0) return switchAccount("");
    const account = orderedAccounts.value[index];
    if (account) switchAccount(account.id);
  },
});
</script>

<template>
  <div class="gm-app" :class="{ 'is-mobile': isMobile }">
    <IconRail v-if="!isMobile" class="gm-app-rail" />

    <template v-if="panelDocked">
      <aside class="gm-app-panel" :style="{ width: `${prefs.folderPanelWidth}px` }">
        <FolderPanel @collapse="togglePanel" />
      </aside>
      <GmResizer v-model="prefs.folderPanelWidth" :min="200" :max="340" :default-value="248" label="调整文件夹面板宽度" />
    </template>
    <Transition name="gm-fade">
      <div v-if="panelOverlay" class="gm-app-backdrop" @click="overlayOpen = false" />
    </Transition>
    <Transition name="gm-slide">
      <aside v-if="panelOverlay" class="gm-app-panel is-overlay">
        <FolderPanel @collapse="overlayOpen = false" @navigate="overlayOpen = false" />
      </aside>
    </Transition>

    <main class="gm-app-main">
      <header v-if="!hideMobileChrome" class="gm-app-top">
        <button
          v-if="!panelDocked && !isMobile"
          type="button"
          class="gm-app-toggle"
          title="展开文件夹面板（[）"
          aria-label="展开文件夹面板"
          @click="togglePanel"
        >
          <NIcon :component="ViewSidebarOutlined" :size="22" />
        </button>
        <SearchBox class="gm-app-search" />
      </header>
      <div class="gm-app-content">
        <RouterView />
      </div>
    </main>

    <BottomTabBar v-if="isMobile && !hideMobileChrome" class="gm-app-tabbar" />
    <MobileSheets v-if="isMobile" />

    <AccountSwitcher />
    <CommandPalette v-if="paletteLoaded" />
    <ShortcutHelp v-if="helpLoaded" />
    <ComposeWindow v-if="store.compose" :key="store.compose.key" :params="store.compose.params" />
    <PurgeProgress />
  </div>
</template>

<style scoped>
.gm-app {
    position: fixed;
    inset: 0;
    display: flex;
    padding: var(--pane-gap) var(--pane-gap) var(--pane-gap) 0;
    background: var(--gm-canvas);
}
.gm-app.is-mobile { flex-direction: column; padding: 0; background: var(--gm-surface); }

.gm-app-panel {
    flex: none;
    min-width: 0;
    border-radius: var(--r-lg);
    background: var(--gm-surface);
    box-shadow: var(--gm-shadow-1);
    overflow: hidden;
}
.gm-app-panel.is-overlay {
    position: fixed;
    top: var(--pane-gap);
    bottom: var(--pane-gap);
    left: calc(var(--rail-w) + var(--pane-gap));
    z-index: 1500;
    width: 280px;
    box-shadow: var(--gm-shadow-3);
}
.gm-app-backdrop { position: fixed; inset: 0; z-index: 1400; background: rgba(0, 0, 0, .18); }

.gm-app-main { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; }
.gm-app-top {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    height: 48px;
    padding: 0 var(--sp-1) var(--pane-gap);
    flex: none;
}
.gm-app.is-mobile .gm-app-top {
    height: 56px;
    padding: var(--sp-2) var(--sp-3);
    border-bottom: 1px solid var(--gm-divider);
}
.gm-app-search { flex: 0 1 560px; }
.gm-app.is-mobile .gm-app-search { flex: 1 1 auto; }
.gm-app-toggle {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    border: 0;
    border-radius: var(--r-md);
    background: none;
    color: var(--gm-text-2);
    cursor: pointer;
    flex: none;
}
.gm-app-toggle:hover { background: var(--gm-hover); color: var(--gm-text); }

.gm-app-content {
    position: relative;
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    border-radius: var(--r-lg);
    background: var(--gm-surface);
    box-shadow: var(--gm-shadow-1);
    overflow: hidden;
}
.gm-app.is-mobile .gm-app-content { border-radius: 0; box-shadow: none; }
.gm-app-content > :deep(*) { flex: 1 1 auto; min-height: 0; }

.gm-fade-enter-active, .gm-fade-leave-active { transition: opacity var(--dur) var(--ease); }
.gm-fade-enter-from, .gm-fade-leave-to { opacity: 0; }
.gm-slide-enter-active, .gm-slide-leave-active { transition: transform var(--dur) var(--ease), opacity var(--dur) var(--ease); }
.gm-slide-enter-from, .gm-slide-leave-to { transform: translateX(-16px); opacity: 0; }
</style>
