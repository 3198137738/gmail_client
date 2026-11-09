<script setup>
// 图标按钮：方形圆角、悬停底色；title 同时作为无障碍名称和悬停提示。active 表示开关已打开。
import { NIcon } from "naive-ui";

defineProps({
  icon: { type: [Object, Function], required: true },
  title: { type: String, required: true },
  size: { type: Number, default: 36 },
  iconSize: { type: Number, default: 20 },
  active: { type: Boolean, default: false },
  danger: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  color: { type: String, default: "" },
});
defineEmits(["click"]);
</script>

<template>
  <button
    type="button"
    class="gm-ib"
    :class="{ 'is-active': active, 'is-danger': danger }"
    :style="{ width: `${size}px`, height: `${size}px`, color: color || undefined }"
    :title="title"
    :aria-label="title"
    :aria-pressed="active || undefined"
    :disabled="disabled"
    @click="$emit('click', $event)"
  >
    <NIcon :component="icon" :size="iconSize" />
  </button>
</template>

<style scoped>
.gm-ib {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: none;
    padding: 0;
    border: 0;
    border-radius: var(--r-md);
    background: none;
    color: var(--gm-text-2);
    cursor: pointer;
    transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}
.gm-ib:hover:not(:disabled) { background: var(--gm-hover); color: var(--gm-text); }
.gm-ib.is-active { background: var(--gm-selected); color: var(--gm-primary); }
.gm-ib.is-danger:hover:not(:disabled) { background: var(--gm-error-soft); color: var(--gm-error); }
.gm-ib:disabled { color: var(--gm-text-4); cursor: default; }
.gm-ib:focus-visible { outline: 2px solid var(--gm-primary); outline-offset: -2px; }
</style>
