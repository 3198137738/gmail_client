<script setup>
// 登录后的整体框架（Naive UI 布局）：顶栏 + 可折叠侧栏 + 内容区；写邮件窗口和快捷键帮助浮在上面。
import { onBeforeUnmount, onMounted, watch } from "vue";
import { useRoute } from "vue-router";
import { NDrawer, NDrawerContent, NLayout, NLayoutContent, NLayoutHeader, NLayoutSider } from "naive-ui";
import AppHeader from "./AppHeader.vue";
import AppSidebar from "./AppSidebar.vue";
import ComposeWindow from "./ComposeWindow.vue";
import ShortcutHelp from "./ShortcutHelp.vue";
import { store } from "../store";
import { isMobile, refreshNav } from "../composables/common";
import { useGlobalShortcuts } from "../composables/shortcuts";
import { onRealtime, startRealtime } from "../realtime";
import { startDesktopNotify } from "../desktopNotify";

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
  startDesktopNotify();
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
  <NLayout class="gm-shell" position="absolute">
    <NLayoutHeader bordered class="gm-header">
      <AppHeader />
    </NLayoutHeader>
    <NLayout has-sider position="absolute" class="gm-body">
      <NLayoutSider
        v-if="!isMobile"
        bordered
        collapse-mode="width"
        :collapsed-width="64"
        :width="232"
        :collapsed="store.navCollapsed"
        :native-scrollbar="false"
      >
        <AppSidebar :collapsed="store.navCollapsed" />
      </NLayoutSider>
      <NLayoutContent class="gm-content" content-style="height: 100%; display: flex; flex-direction: column;">
        <RouterView />
      </NLayoutContent>
    </NLayout>
    <NDrawer v-if="isMobile" v-model:show="store.drawerOpen" placement="left" :width="280">
      <NDrawerContent :native-scrollbar="false" body-content-style="padding: 0">
        <AppSidebar mobile />
      </NDrawerContent>
    </NDrawer>
    <ComposeWindow v-if="store.compose" :key="store.compose.key" :params="store.compose.params" />
    <ShortcutHelp />
  </NLayout>
</template>

<style scoped>
.gm-header { height: var(--gm-header-h); }
.gm-body { top: var(--gm-header-h) !important; }
.gm-content :deep(.n-layout-scroll-container) { overflow: hidden; }
.gm-content :deep(.n-layout-scroll-container > *) { flex: 1 1 auto; min-height: 0; }
</style>
