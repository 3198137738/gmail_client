<script setup>
// 列表顶部的说明条：一句说明 + 可选的操作链接。tone：info / warning
defineProps({
  tone: { type: String, default: "info" },
  actionText: { type: String, default: "" },
});
defineEmits(["action"]);
</script>

<template>
  <div class="gm-banner-x" :class="`is-${tone}`">
    <span class="gm-banner-text"><slot /></span>
    <button v-if="actionText" type="button" class="gm-banner-action" @click="$emit('action')">{{ actionText }}</button>
  </div>
</template>

<style scoped>
.gm-banner-x {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 4px var(--sp-2);
    margin: var(--sp-2) var(--sp-3) 0;
    padding: var(--sp-2) var(--sp-3);
    border-radius: var(--r-md);
    background: var(--gm-surface-2);
    color: var(--gm-text-2);
    font-size: var(--fs-sm);
    line-height: 1.5;
}
.gm-banner-x.is-warning { background: var(--gm-warning-soft); }
.gm-banner-text { flex: 1 1 240px; }
.gm-banner-action {
    padding: 0;
    border: 0;
    background: none;
    color: var(--gm-primary);
    font: inherit;
    font-weight: 600;
    cursor: pointer;
}
.gm-banner-action:hover { text-decoration: underline; }
</style>
