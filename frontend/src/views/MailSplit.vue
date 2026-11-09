<script setup>
// 邮件列表 + 阅读窗格。列表页（/messages/?...）和详情页（/messages/:id?back=...）共用这个组件，
// 路由切换时组件实例保留，列表不会重新加载、不丢滚动位置和勾选。
// 阅读窗格在右侧（默认）：列表宽度可拖动，未打开邮件时右侧显示提示；
// 关闭阅读窗格 / 窄窗口 / 手机：打开邮件时整页阅读，列表隐藏但保留状态。
import { computed } from "vue";
import { useRoute } from "vue-router";
import { NIcon } from "naive-ui";
import { MarkunreadMailboxOutlined } from "@vicons/material";
import MailList from "../mail/MailList.vue";
import Reader from "../mail/Reader.vue";
import MailPickers from "../mail/MailPickers.vue";
import GmResizer from "../ui/GmResizer.vue";
import { store } from "../store";
import { prefs } from "../prefs";
import { isCramped, isMobile, isTouch } from "../composables/common";

const route = useRoute();
const reading = computed(() => route.name === "message");
const split = computed(() => !isMobile.value && !isCramped.value && prefs.readingPane === "right");

// 打开邮件前所在的列表：优先取 back 参数，没有时（附件中心、通知等进入）回到最近显示的列表
const back = computed(() => {
  const value = String(route.query.back || "");
  if (value.startsWith("/messages/?")) return value;
  return store.lastListPath || "/messages/?kind=inbox";
});

const listQuery = computed(() => {
  if (!reading.value) return route.query;
  return Object.fromEntries(new URLSearchParams(back.value.slice("/messages/?".length)));
});

const layout = computed(() => (split.value || isMobile.value ? "card" : "wide"));
const listVisible = computed(() => split.value || !reading.value);
</script>

<template>
  <div class="gm-split" :class="{ 'is-split': split }">
    <div
      v-show="listVisible"
      class="gm-split-list"
      :style="split ? { width: `${prefs.listWidth}px` } : undefined"
    >
      <MailList
        :list-query="listQuery"
        :active-id="reading ? Number(route.params.id) : null"
        :layout="layout"
        :visible="listVisible"
        :touch="isTouch || isMobile"
      />
    </div>
    <GmResizer
      v-if="split"
      v-model="prefs.listWidth"
      class="gm-split-resizer"
      :min="320"
      :max="680"
      :default-value="420"
      label="调整邮件列表宽度"
    />
    <div v-if="split || reading" class="gm-split-reader">
      <Reader
        v-if="reading"
        :id="Number(route.params.id)"
        :back="back"
        :split="split"
        :mobile="isMobile"
      />
      <div v-else class="gm-split-empty">
        <span class="gm-split-empty-icon"><NIcon :component="MarkunreadMailboxOutlined" :size="32" /></span>
        <div class="gm-split-empty-title">选择一封邮件阅读</div>
        <div class="gm-split-empty-keys">
          <span><kbd>j</kbd> <kbd>k</kbd> 上下移动</span>
          <span><kbd>o</kbd> 打开</span>
          <span><kbd>x</kbd> 选中</span>
          <span><kbd>Ctrl</kbd> <kbd>K</kbd> 命令面板</span>
        </div>
      </div>
    </div>
    <MailPickers />
  </div>
</template>

<style scoped>
.gm-split { display: flex; height: 100%; min-height: 0; }
.gm-split-list { flex: 1 1 auto; min-width: 0; }
.gm-split.is-split .gm-split-list { flex: none; max-width: 60%; }
.gm-split-resizer { background: var(--gm-canvas); }
.gm-split-reader { flex: 1 1 auto; min-width: 0; }
.gm-split.is-split .gm-split-reader { min-width: 420px; }
.gm-split-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    gap: var(--sp-3);
    color: var(--gm-text-3);
    background: var(--gm-surface);
}
.gm-split-empty-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 64px;
    height: 64px;
    border-radius: 50%;
    background: var(--gm-surface-2);
}
.gm-split-empty-title { font-size: var(--fs-lg); font-weight: 600; color: var(--gm-text-2); }
.gm-split-empty-keys { display: flex; flex-wrap: wrap; justify-content: center; gap: var(--sp-2) var(--sp-4); font-size: var(--fs-sm); }
.gm-split-empty-keys kbd {
    padding: 0 6px;
    border: 1px solid var(--gm-border);
    border-bottom-width: 2px;
    border-radius: 4px;
    font: 12px/18px ui-monospace, Consolas, monospace;
    color: var(--gm-text-2);
}
</style>
