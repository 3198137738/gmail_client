<script setup>
// 批量加 / 去标签（列表选中的邮件，可以来自多个账号）：每个标签显示为
// 全部有（勾选）/ 部分有（半选）/ 都没有；点一下在「加上」和「去掉」之间切换，应用时只改动过的标签。
// 多个账号时按账号分组，每封邮件只会加上它所在账号的标签。
import { computed, ref, watch } from "vue";
import { NButton, NCheckbox, NInput, NPopover } from "naive-ui";
import { post } from "../api";
import { store } from "../store";
import { run } from "../ui";
import { refreshNav } from "../composables/common";

const props = defineProps({
  show: { type: Boolean, default: false },
  items: { type: Array, required: true },
});
const emit = defineEmits(["update:show", "applied"]);

const filter = ref("");
const saving = ref(false);
// 标签 id -> true（加上）/ false（去掉）；没改动的不在里面
const changes = ref({});

watch(
  () => props.show,
  (open) => {
    if (!open) return;
    changes.value = {};
    filter.value = "";
  },
);

const accounts = computed(() => [...new Set(props.items.map((m) => m.account_id))]);

const groups = computed(() => {
  const q = filter.value.trim().toLowerCase();
  return accounts.value
    .map((accountId) => {
      const account = store.nav?.accounts.find((a) => a.id === accountId);
      const members = props.items.filter((m) => m.account_id === accountId);
      const labels = (store.nav?.labels || [])
        .filter((lb) => lb.account_id === accountId && (!q || lb.name.toLowerCase().includes(q)))
        .map((lb) => {
          const count = members.filter((m) => (m.labels || []).some((x) => x.id === lb.id)).length;
          return { ...lb, state: count === 0 ? "none" : count === members.length ? "all" : "some" };
        });
      return { account, labels };
    })
    .filter((g) => g.labels.length);
});

function checked(lb) {
  if (lb.id in changes.value) return changes.value[lb.id];
  return lb.state === "all";
}

function indeterminate(lb) {
  return !(lb.id in changes.value) && lb.state === "some";
}

function toggle(lb) {
  const next = { ...changes.value };
  const target = !checked(lb) || indeterminate(lb);
  // 切回原来的状态等于没改动
  if ((target && lb.state === "all") || (!target && lb.state === "none")) delete next[lb.id];
  else next[lb.id] = target;
  changes.value = next;
}

const dirty = computed(() => Object.keys(changes.value).length > 0);

async function apply() {
  const add = Object.entries(changes.value).filter(([, v]) => v).map(([k]) => Number(k));
  const remove = Object.entries(changes.value).filter(([, v]) => !v).map(([k]) => Number(k));
  saving.value = true;
  const result = await run(
    post("/api/messages/labels", { ids: props.items.map((m) => m.id), add, remove }),
    `已更新 ${props.items.length} 封邮件的标签`,
  );
  saving.value = false;
  if (!result) return;
  emit("applied");
  emit("update:show", false);
  refreshNav();
}
</script>

<template>
  <NPopover :show="show" trigger="manual" placement="bottom-start" :show-arrow="false" @clickoutside="emit('update:show', false)">
    <template #trigger><slot /></template>
    <div class="gm-blp" @keydown.esc="emit('update:show', false)">
      <NInput v-model:value="filter" size="small" placeholder="查找标签" clearable />
      <div class="gm-blp-list">
        <section v-for="g in groups" :key="g.account?.id">
          <div v-if="accounts.length > 1" class="gm-blp-account">
            <span class="gm-blp-dot" :style="{ background: g.account?.color }" />{{ g.account?.email }}
          </div>
          <NCheckbox
            v-for="lb in g.labels"
            :key="lb.id"
            class="gm-blp-item"
            :checked="checked(lb)"
            :indeterminate="indeterminate(lb)"
            :label="lb.name"
            @update:checked="toggle(lb)"
          />
        </section>
        <div v-if="!groups.length" class="gm-blp-empty">{{ filter ? "没有匹配的标签" : "选中邮件所在的账号没有标签" }}</div>
      </div>
      <div class="gm-blp-foot">
        <NButton size="small" quaternary @click="emit('update:show', false)">取消</NButton>
        <NButton size="small" type="primary" :disabled="!dirty" :loading="saving" @click="apply">应用</NButton>
      </div>
    </div>
  </NPopover>
</template>

<style scoped>
.gm-blp { display: flex; flex-direction: column; gap: var(--sp-2); width: 260px; }
.gm-blp-list { display: flex; flex-direction: column; max-height: 320px; overflow-y: auto; }
.gm-blp-account { display: flex; align-items: center; gap: 6px; padding: var(--sp-2) 4px 2px; font-size: var(--fs-xs); font-weight: 600; color: var(--gm-text-3); }
.gm-blp-dot { width: 8px; height: 8px; border-radius: 2px; }
.gm-blp-item { width: 100%; padding: 6px 4px; border-radius: var(--r-sm); }
.gm-blp-item:hover { background: var(--gm-hover); }
.gm-blp-empty { padding: var(--sp-3); color: var(--gm-text-3); text-align: center; }
.gm-blp-foot { display: flex; justify-content: flex-end; gap: var(--sp-2); padding-top: var(--sp-2); border-top: 1px solid var(--gm-divider); }
</style>
