<script setup>
// 登录后的整体框架：顶栏 + 左侧导航 + 右侧内容；写邮件窗口和快捷键帮助浮在上面。
import { onBeforeUnmount, onMounted, watch } from "vue";
import { useRoute } from "vue-router";
import { NDrawer, NDrawerContent } from "naive-ui";
import AppHeader from "./AppHeader.vue";
import AppSidebar from "./AppSidebar.vue";
import ComposeWindow from "./ComposeWindow.vue";
import ShortcutHelp from "./ShortcutHelp.vue";
import { store } from "../store";
import { isMobile, refreshNav } from "../composables/common";
import { useGlobalShortcuts } from "../composables/shortcuts";
import { onRealtime, startRealtime } from "../realtime";

const route = useRoute();

// 非邮件列表 / 详情页面：导航中不高亮任何文件夹
watch(
  () => route.name,
  (name) => {
    if (name !== "list" && name !== "message") store.view.kind = null;
  },
);

const disposers = [];
onMounted(() => {
  refreshNav();
  startRealtime();
  // 任何邮箱变化都要更新左侧的未读数
  for (const type of ["new_mail", "mail_changed", "sync_done", "resume"]) {
    disposers.push(onRealtime(type, refreshNav));
  }
  disposers.push(onRealtime("open", (e) => !e.first && refreshNav()));
});
onBeforeUnmount(() => disposers.forEach((d) => d()));

useGlobalShortcuts();
</script>

<template>
  <div class="gm-shell">
    <AppHeader />
    <div class="gm-body">
      <AppSidebar v-if="!isMobile" :collapsed="store.navCollapsed" />
      <NDrawer v-else v-model:show="store.drawerOpen" placement="left" :width="300">
        <NDrawerContent :native-scrollbar="false" body-content-style="padding: 0">
          <AppSidebar mobile />
        </NDrawerContent>
      </NDrawer>
      <main class="gm-main">
        <RouterView />
      </main>
    </div>
    <ComposeWindow v-if="store.compose" :key="store.compose.key" :params="store.compose.params" />
    <ShortcutHelp />
  </div>
</template>

<style scoped>
.gm-shell { display: flex; flex-direction: column; height: 100%; }
.gm-body { display: flex; flex: 1 1 auto; min-height: 0; }
.gm-main {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    flex-direction: column;
    padding: 0 16px 16px 0;
    overflow: hidden;
}
.gm-main > :deep(*) { flex: 1 1 auto; min-height: 0; }

@media (max-width: 768px) {
    .gm-main { padding: 0; overflow: visible; }
    .gm-shell { height: auto; min-height: 100%; }
}
</style>
