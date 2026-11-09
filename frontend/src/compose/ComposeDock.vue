<script setup>
// 写邮件窗口停靠区：电脑端多个窗口从右到左排在底部（最小化的显示为标签）；全屏的窗口居中，背后加遮罩。
// 手机端：当前窗口全屏，其余最小化为底部的标签。离开页面前有未保存 / 正在发送的窗口时提醒。
import { computed, onBeforeUnmount, watch } from "vue";
import ComposeWindow from "./ComposeWindow.vue";
import { focusCompose, store } from "../store";
import { toast } from "../ui";
import { isMobile } from "../composables/common";

const visible = computed(() => store.composes.filter((c) => !c.pending));
const maxed = computed(() => visible.value.find((c) => c.state === "max"));
// 手机端：一次只全屏显示一个窗口
const mobileActive = computed(() => {
  const open = visible.value.filter((c) => c.state !== "min");
  return open.find((c) => c.key === store.composeActive) || open[open.length - 1] || null;
});

watch(mobileActive, (active) => {
  if (!isMobile.value) return;
  for (const c of visible.value) if (c !== active && c.state !== "min") c.state = "min";
});

watch(
  () => store.composeLimitHit,
  () => toast("最多同时写 3 封邮件，请先发送或关闭其中一封"),
);

function onBeforeUnload(ev) {
  if (store.composes.some((c) => c.busy || c.pending)) {
    ev.preventDefault();
    ev.returnValue = "";
  }
}
window.addEventListener("beforeunload", onBeforeUnload);
onBeforeUnmount(() => window.removeEventListener("beforeunload", onBeforeUnload));
</script>

<template>
  <div class="gm-dock" :class="{ 'is-mobile': isMobile }">
    <div v-if="maxed && !isMobile" class="gm-dock-backdrop" @click="maxed.state = 'normal'" />
    <ComposeWindow
      v-for="c in store.composes"
      :key="c.key"
      :entry="c"
      :mobile="isMobile && mobileActive === c"
      :style="isMobile && mobileActive !== c ? { display: 'none' } : undefined"
    />
    <div v-if="isMobile && visible.some((c) => c.state === 'min')" class="gm-dock-chips">
      <button
        v-for="c in visible.filter((x) => x.state === 'min')"
        :key="c.key"
        type="button"
        class="gm-dock-chip"
        @click="focusCompose(c.key)"
      >
        <span class="gm-dock-chip-title">{{ c.title || "新邮件" }}</span>
        <span v-if="c.busy" class="gm-dock-chip-dot" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.gm-dock {
    position: fixed;
    right: 24px;
    bottom: 0;
    z-index: 1000;
    display: flex;
    flex-direction: row-reverse;
    align-items: flex-end;
    gap: 12px;
    max-width: calc(100vw - 96px);
    pointer-events: none;
}
.gm-dock-backdrop { position: fixed; inset: 0; z-index: 1050; background: rgba(0, 0, 0, .4); pointer-events: auto; }
.gm-dock.is-mobile { right: 0; left: 0; max-width: none; }
.gm-dock-chips {
    position: fixed;
    left: 8px;
    right: 8px;
    bottom: calc(var(--tabbar-h) + env(safe-area-inset-bottom) + 8px);
    display: flex;
    gap: 8px;
    overflow-x: auto;
    pointer-events: auto;
}
.gm-dock-chip {
    display: flex;
    align-items: center;
    gap: 6px;
    max-width: 200px;
    padding: 8px 14px;
    border: 0;
    border-radius: var(--r-pill);
    background: var(--gm-text);
    color: var(--gm-surface);
    font: inherit;
    box-shadow: var(--gm-shadow-2);
}
.gm-dock-chip-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-dock-chip-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--gm-warning); }
</style>
