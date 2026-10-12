<script setup>
// 圆形图标按钮（Gmail 工具栏样式）。title 同时用作悬停提示和无障碍标签。
import { NButton, NIcon } from "naive-ui";

defineProps({
  icon: { type: [Object, Function], required: true },
  title: { type: String, default: "" },
  size: { type: String, default: "medium" },
  iconSize: { type: Number, default: 20 },
  disabled: { type: Boolean, default: false },
  color: { type: String, default: "" },
  loading: { type: Boolean, default: false },
});
</script>

<template>
  <NButton
    quaternary
    circle
    :size="size"
    :title="title"
    :aria-label="title"
    :disabled="disabled"
    :loading="loading"
    class="gm-icon-btn"
  >
    <template #icon>
      <NIcon :component="icon" :size="iconSize" :color="color || undefined" />
    </template>
  </NButton>
</template>

<style scoped>
.gm-icon-btn { color: var(--gm-text-2); }
</style>
