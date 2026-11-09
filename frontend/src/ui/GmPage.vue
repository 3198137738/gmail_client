<script setup>
// 功能页的外框：标题栏（标题、计数、操作）、可选的工具栏和说明、内容区（加载中 / 加载失败可重试 / 空状态 / 内容）、底栏。
import GmEmptyState from "./GmEmptyState.vue";

defineProps({
  title: { type: String, required: true },
  count: { type: String, default: "" },
  loading: { type: Boolean, default: false },
  error: { type: String, default: "" },
  // 已加载且没有内容
  empty: { type: Boolean, default: false },
  emptyTitle: { type: String, default: "这里还没有内容" },
  emptyHint: { type: String, default: "" },
  emptyIcon: { type: [Object, Function], default: null },
  // 已经有数据（重新加载时不显示骨架）
  ready: { type: Boolean, default: false },
});
defineEmits(["retry"]);
</script>

<template>
  <section class="gm-page" :aria-label="title">
    <header class="gm-page-head">
      <h2 class="gm-page-title">{{ title }}</h2>
      <span v-if="count" class="gm-page-count">{{ count }}</span>
      <div class="gm-page-actions"><slot name="actions" /></div>
    </header>
    <div v-if="$slots.toolbar" class="gm-page-toolbar"><slot name="toolbar" /></div>
    <div class="gm-page-body">
      <slot name="banner" />
      <div v-if="loading && !ready" class="gm-page-skeleton" aria-hidden="true">
        <div v-for="i in 6" :key="i" class="gm-page-skel-row">
          <span class="gm-page-skel-avatar" />
          <span class="gm-page-skel-lines"><span /><span /></span>
        </div>
      </div>
      <GmEmptyState v-else-if="error" tone="error" :title="`${title}加载失败`" :hint="error" action-text="重试" @action="$emit('retry')" />
      <GmEmptyState v-else-if="empty" :title="emptyTitle" :hint="emptyHint" :icon="emptyIcon">
        <slot name="empty" />
      </GmEmptyState>
      <slot v-else />
    </div>
    <footer v-if="$slots.footer" class="gm-page-foot"><slot name="footer" /></footer>
  </section>
</template>

<style scoped>
.gm-page { display: flex; flex-direction: column; height: 100%; min-height: 0; background: var(--gm-surface); }
.gm-page-head {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    min-height: 56px;
    padding: var(--sp-2) var(--sp-3) var(--sp-2) var(--sp-5);
    border-bottom: 1px solid var(--gm-divider);
    flex: none;
}
.gm-page-title { margin: 0; font-size: var(--fs-lg); font-weight: 600; color: var(--gm-text); white-space: nowrap; }
.gm-page-count { font-size: var(--fs-xs); color: var(--gm-text-3); white-space: nowrap; }
.gm-page-actions { display: flex; align-items: center; gap: var(--sp-1); margin-left: auto; }
.gm-page-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--sp-2);
    padding: var(--sp-2) var(--sp-5);
    border-bottom: 1px solid var(--gm-divider);
    flex: none;
}
.gm-page-body { flex: 1; min-height: 0; overflow-y: auto; }
.gm-page-foot { display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-2) var(--sp-4); border-top: 1px solid var(--gm-divider); flex: none; }
.gm-page-skel-row { display: flex; gap: var(--sp-3); padding: 14px var(--sp-5); border-bottom: 1px solid var(--gm-divider); }
.gm-page-skel-avatar { width: 36px; height: 36px; border-radius: 50%; background: var(--gm-surface-2); flex: none; }
.gm-page-skel-lines { display: flex; flex-direction: column; gap: 8px; flex: 1; padding-top: 4px; }
.gm-page-skel-lines span { height: 10px; border-radius: 5px; background: var(--gm-surface-2); animation: gm-page-skel 1.2s ease-in-out infinite; }
.gm-page-skel-lines span:first-child { width: 35%; }
.gm-page-skel-lines span:last-child { width: 70%; }
@keyframes gm-page-skel { 50% { opacity: .5; } }
@media (max-width: 768px) {
    .gm-page-head { padding-left: var(--sp-4); }
    .gm-page-toolbar { padding: var(--sp-2) var(--sp-3); }
}
</style>
