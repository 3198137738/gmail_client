<script setup>
// 未读数等计数徽标；0 时不显示。tone=solid 用于图标栏上的角标。
defineProps({
  count: { type: Number, default: 0 },
  tone: { type: String, default: "soft" },
});
</script>

<template>
  <span v-if="count > 0" class="gm-count" :class="`is-${tone}`">{{ count > 999 ? "999+" : count }}</span>
</template>

<style scoped>
.gm-count {
    flex: none;
    min-width: 20px;
    padding: 0 6px;
    border-radius: var(--r-pill);
    font-size: var(--fs-xs);
    font-weight: 600;
    line-height: 18px;
    text-align: center;
    font-variant-numeric: tabular-nums;
}
.gm-count.is-soft { background: var(--gm-primary-soft); color: var(--gm-primary); }
.gm-count.is-solid {
    min-width: 16px;
    padding: 0 4px;
    line-height: 16px;
    font-size: 10px;
    background: var(--gm-error);
    color: #fff;
    box-shadow: 0 0 0 2px var(--gm-surface);
}
</style>
