<script setup>
// 翻页（按页码）：「第 2 / 5 页 · 共 230 个」+ 上一页 / 下一页。邮件列表用游标翻页，样式与这里一致。
import { ChevronLeftOutlined, ChevronRightOutlined } from "@vicons/material";
import GmIconButton from "./GmIconButton.vue";

defineProps({
  page: { type: Number, default: 1 },
  pages: { type: Number, default: 1 },
  total: { type: Number, default: null },
  unit: { type: String, default: "个" },
});
defineEmits(["change"]);
</script>

<template>
  <div class="gm-pager" role="navigation" aria-label="翻页">
    <span class="gm-pager-text">
      <template v-if="pages > 1">第 {{ page }} / {{ pages }} 页</template>
      <template v-if="total !== null"><template v-if="pages > 1"> · </template>共 {{ total }} {{ unit }}</template>
    </span>
    <template v-if="pages > 1">
      <GmIconButton :icon="ChevronLeftOutlined" title="上一页" :size="30" :disabled="page <= 1" @click="$emit('change', page - 1)" />
      <GmIconButton :icon="ChevronRightOutlined" title="下一页" :size="30" :disabled="page >= pages" @click="$emit('change', page + 1)" />
    </template>
  </div>
</template>

<style scoped>
.gm-pager { display: flex; align-items: center; gap: 2px; }
.gm-pager-text { margin-right: var(--sp-1); font-size: var(--fs-xs); color: var(--gm-text-3); white-space: nowrap; font-variant-numeric: tabular-nums; }
</style>
