<script setup>
// 空状态 / 错误状态：图标 + 标题 + 说明 + 操作。tone=error 时为加载失败样式（带「重试」）。
import { NButton, NIcon } from "naive-ui";
import { ErrorOutlineOutlined, InboxOutlined } from "@vicons/material";

defineProps({
  title: { type: String, required: true },
  hint: { type: String, default: "" },
  icon: { type: [Object, Function], default: null },
  tone: { type: String, default: "empty" },
  actionText: { type: String, default: "" },
});
defineEmits(["action"]);
</script>

<template>
  <div class="gm-state" :class="`is-${tone}`" :role="tone === 'error' ? 'alert' : undefined">
    <span class="gm-state-icon">
      <NIcon :component="icon || (tone === 'error' ? ErrorOutlineOutlined : InboxOutlined)" :size="28" />
    </span>
    <div class="gm-state-title">{{ title }}</div>
    <div v-if="hint" class="gm-state-hint">{{ hint }}</div>
    <slot />
    <NButton v-if="actionText" class="gm-state-action" :type="tone === 'error' ? 'primary' : 'default'" secondary @click="$emit('action')">
      {{ actionText }}
    </NButton>
  </div>
</template>

<style scoped>
.gm-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: var(--sp-8) var(--sp-6);
    text-align: center;
    color: var(--gm-text-3);
}
.gm-state-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 56px;
    height: 56px;
    margin-bottom: var(--sp-3);
    border-radius: 50%;
    background: var(--gm-surface-2);
    color: var(--gm-text-3);
}
.gm-state.is-error .gm-state-icon { background: var(--gm-error-soft); color: var(--gm-error); }
.gm-state-title { font-size: var(--fs-lg); font-weight: 600; color: var(--gm-text-2); }
.gm-state-hint { max-width: 420px; margin-top: var(--sp-2); line-height: 1.6; }
.gm-state-action { margin-top: var(--sp-4); }
</style>
