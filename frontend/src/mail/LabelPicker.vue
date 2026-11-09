<script setup>
// 标签选择（阅读区）：勾选该账号的标签后应用；可搜索。由工具栏「标签」按钮或 l 键打开。
import { computed, ref, watch } from "vue";
import { NButton, NCheckbox, NCheckboxGroup, NInput, NPopover } from "naive-ui";
import { post } from "../api";
import { run } from "../ui";
import { refreshNav } from "../composables/common";

const props = defineProps({
  show: { type: Boolean, default: false },
  message: { type: Object, required: true },
});
const emit = defineEmits(["update:show", "applied"]);

const selection = ref([]);
const filter = ref("");
const saving = ref(false);

watch(
  () => props.show,
  (open) => {
    if (!open) return;
    selection.value = props.message.thread_labels.map((lb) => lb.id);
    filter.value = "";
  },
);

const visible = computed(() => {
  const q = filter.value.trim().toLowerCase();
  return props.message.account_labels.filter((lb) => !q || lb.name.toLowerCase().includes(q));
});

async function apply() {
  saving.value = true;
  const result = await run(post(`/api/messages/${props.message.id}/labels`, { label_ids: selection.value }), "标签已更新");
  saving.value = false;
  if (!result) return;
  emit("applied", result.labels);
  emit("update:show", false);
  refreshNav();
}
</script>

<template>
  <NPopover :show="show" trigger="manual" placement="bottom-start" :show-arrow="false" @clickoutside="emit('update:show', false)">
    <template #trigger><slot /></template>
    <div class="gm-lp" @keydown.esc="emit('update:show', false)">
      <NInput v-if="message.account_labels.length > 8" v-model:value="filter" size="small" placeholder="查找标签" clearable />
      <NCheckboxGroup v-model:value="selection">
        <div class="gm-lp-list">
          <NCheckbox v-for="lb in visible" :key="lb.id" :value="lb.id" :label="lb.name" class="gm-lp-item" />
          <div v-if="!visible.length" class="gm-lp-empty">没有匹配的标签</div>
        </div>
      </NCheckboxGroup>
      <div class="gm-lp-foot">
        <NButton size="small" quaternary @click="emit('update:show', false)">取消</NButton>
        <NButton size="small" type="primary" :loading="saving" @click="apply">应用</NButton>
      </div>
    </div>
  </NPopover>
</template>

<style scoped>
.gm-lp { display: flex; flex-direction: column; gap: var(--sp-2); width: 240px; }
.gm-lp-list { display: flex; flex-direction: column; max-height: 300px; overflow-y: auto; }
.gm-lp-item { width: 100%; padding: 6px 4px; border-radius: var(--r-sm); }
.gm-lp-item:hover { background: var(--gm-hover); }
.gm-lp-empty { padding: var(--sp-3); color: var(--gm-text-3); text-align: center; }
.gm-lp-foot { display: flex; justify-content: flex-end; gap: var(--sp-2); padding-top: var(--sp-2); border-top: 1px solid var(--gm-divider); }
</style>
