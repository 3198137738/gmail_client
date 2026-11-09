<script setup>
// 分组：侧栏按分组折叠显示账号。在这里给分组改名、解散分组，或调整每个账号所在的分组。
import { computed, ref } from "vue";
import { NAutoComplete, NButton, NTag } from "naive-ui";
import GmAvatar from "../../ui/GmAvatar.vue";
import GmEmptyState from "../../ui/GmEmptyState.vue";
import { get, post } from "../../api";
import { confirm, prompt, toast } from "../../ui";
import { refreshNav } from "../../composables/common";
import { useResource } from "../../composables/useResource";

const res = useResource(() => get("/api/accounts"));
const accounts = computed(() => res.data.value?.accounts || []);
const groups = computed(() => {
  const map = new Map();
  for (const a of accounts.value) {
    if (!a.group_name) continue;
    if (!map.has(a.group_name)) map.set(a.group_name, []);
    map.get(a.group_name).push(a);
  }
  return [...map].sort(([x], [y]) => x.localeCompare(y, "zh-CN")).map(([name, members]) => ({ name, members }));
});
const groupOptions = computed(() => groups.value.map((g) => ({ label: g.name, value: g.name })));
const busy = ref(false);

async function setGroup(list, name) {
  busy.value = true;
  try {
    for (const a of list) {
      await post(`/api/accounts/${a.id}/update`, { group_name: name });
      a.group_name = name;
    }
    refreshNav();
    return true;
  } catch (err) {
    toast(err.message || "保存失败", { tone: "error" });
    res.reload({ silent: true });
    return false;
  } finally {
    busy.value = false;
  }
}

async function rename(g) {
  const name = await prompt({
    title: "重命名分组",
    label: `「${g.name}」中有 ${g.members.length} 个账号`,
    value: g.name,
    positiveText: "保存",
    validate: (v) => (v.trim() ? "" : "请填写分组名称"),
  });
  if (!name || name.trim() === g.name) return;
  if (await setGroup(g.members, name.trim().slice(0, 60))) toast(`已重命名为「${name.trim()}」`);
}

async function dissolve(g) {
  if (!(await confirm(`解散分组「${g.name}」？其中的 ${g.members.length} 个账号会变为不分组，账号和邮件不受影响。`, { positiveText: "解散" }))) return;
  if (await setGroup(g.members, "")) toast("已解散分组");
}

async function changeOne(a, value) {
  const name = (value || "").trim().slice(0, 60);
  if (name === (a.group_name || "")) return;
  if (await setGroup([a], name)) toast(name ? `已移到「${name}」` : "已移出分组");
}
</script>

<template>
  <div class="gm-settings">
    <GmEmptyState v-if="res.error.value" tone="error" title="账号加载失败" :hint="res.error.value" action-text="重试" @action="res.reload()" />
    <template v-else-if="res.data.value">
      <section class="gm-settings-row">
        <div class="gm-settings-label">
          分组
          <div class="gm-settings-hint">同一分组的账号在侧栏折叠在一起，并显示合计未读数</div>
        </div>
        <div class="gm-settings-content">
          <div v-for="g in groups" :key="g.name" class="gm-gp-row">
            <span class="gm-gp-name">{{ g.name }}</span>
            <NTag size="small" :bordered="false">{{ g.members.length }} 个账号</NTag>
            <span class="gm-gp-spacer" />
            <NButton text type="primary" :disabled="busy" @click="rename(g)">重命名</NButton>
            <NButton text type="error" :disabled="busy" @click="dissolve(g)">解散</NButton>
          </div>
          <div v-if="!groups.length" class="gm-settings-empty">还没有分组。在下方给账号填写分组名称即可创建。</div>
        </div>
      </section>

      <section v-if="accounts.length" class="gm-settings-row">
        <div class="gm-settings-label">
          账号所在的分组
          <div class="gm-settings-hint">输入新名称即新建分组；清空表示不分组</div>
        </div>
        <div class="gm-settings-content">
          <div v-for="a in accounts" :key="a.id" class="gm-gp-acc">
            <GmAvatar :name="a.display_name || a.email" :color="a.color" :size="28" />
            <span class="gm-gp-acc-name" :title="a.email">{{ a.display_name || a.email }}</span>
            <NAutoComplete
              :value="a.group_name"
              :options="groupOptions"
              placeholder="不分组"
              clearable
              size="small"
              class="gm-gp-input"
              @blur="(e) => changeOne(a, e.target.value)"
              @select="(v) => changeOne(a, v)"
              @clear="changeOne(a, '')"
            />
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.gm-gp-row { display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-2) 0; border-bottom: 1px solid var(--gm-divider); }
.gm-gp-row:last-of-type { border-bottom: 0; }
.gm-gp-name { font-weight: 600; }
.gm-gp-spacer { flex: 1; }
.gm-gp-acc { display: flex; align-items: center; gap: var(--sp-3); padding: 6px 0; }
.gm-gp-acc-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-gp-input { width: 200px; }
@media (max-width: 768px) {
    .gm-gp-input { width: 140px; }
}
</style>
