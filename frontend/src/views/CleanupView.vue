<script setup>
// 批量清理：跨账号按发件人汇总邮件数量，选中发件人（或只按时间）后先预览再一次删除，同步到邮箱服务器。
// 星标和已延后的邮件始终保留。
import { computed, reactive, ref, watch } from "vue";
import { NButton, NCheckbox, NIcon, NInput, NSelect } from "naive-ui";
import { CleaningServicesOutlined, SearchOutlined } from "@vicons/material";
import GmPage from "../ui/GmPage.vue";
import GmAvatar from "../ui/GmAvatar.vue";
import { post } from "../api";
import { store } from "../store";
import { confirm, run, toast } from "../ui";
import { refreshNav } from "../composables/common";
import { useResource } from "../composables/useResource";
import { listDate } from "../format";

const SCOPES = [
  { value: "inbox", label: "收件箱" },
  { value: "all", label: "所有邮件" },
  { value: "junk", label: "垃圾邮件" },
];
const AGES = [
  { value: 0, label: "不限时间" },
  { value: 30, label: "早于 1 个月" },
  { value: 90, label: "早于 3 个月" },
  { value: 180, label: "早于 6 个月" },
  { value: 365, label: "早于 1 年" },
  { value: 730, label: "早于 2 年" },
];

const filters = reactive({ scope: "inbox", account_id: 0, older_than_days: 0, only_read: false });
const res = useResource(() => post("/api/cleanup/senders", { ...filters, senders: [] }), { immediate: false });
const data = res.data;
const deleting = ref(false);
const selected = ref(new Set());
const keyword = ref("");

const accountOptions = computed(() => [
  { value: 0, label: "全部账号" },
  ...(store.nav?.accounts || []).map((a) => ({ value: a.id, label: a.email })),
]);

const visibleSenders = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  const list = data.value?.senders || [];
  return q ? list.filter((s) => s.email.includes(q) || s.name.toLowerCase().includes(q)) : list;
});
const selectedCount = computed(() =>
  (data.value?.senders || []).filter((s) => selected.value.has(s.email)).reduce((sum, s) => sum + s.count, 0),
);
// 不选发件人时：只按时间清理（或清空垃圾邮件）
const canDeleteAll = computed(() => filters.older_than_days > 0 || filters.scope === "junk");
const allVisibleChecked = computed(() =>
  visibleSenders.value.length > 0 && visibleSenders.value.every((s) => selected.value.has(s.email)),
);

async function load() {
  const result = await res.reload();
  if (!result) return;
  const present = new Set(result.senders.map((s) => s.email));
  selected.value = new Set([...selected.value].filter((e) => present.has(e)));
}
watch(filters, load, { immediate: true });

function toggle(email) {
  const next = new Set(selected.value);
  next.has(email) ? next.delete(email) : next.add(email);
  selected.value = next;
}

function toggleAll(checked) {
  const next = new Set(selected.value);
  for (const s of visibleSenders.value) checked ? next.add(s.email) : next.delete(s.email);
  selected.value = next;
}

async function clean() {
  const body = { ...filters, senders: [...selected.value] };
  const preview = await run(post("/api/cleanup/preview", body));
  if (!preview) return;
  if (!preview.count) {
    toast("没有符合条件的邮件");
    return;
  }
  const samples = preview.samples.map((m) => `· ${m.from}：${m.subject || "(无主题)"}`).join("\n");
  const ok = await confirm(
    `将删除 ${preview.count} 封邮件（邮箱服务器上同时移到已删除邮件），星标和已延后的邮件会保留。\n\n${samples}${preview.count > preview.samples.length ? "\n……" : ""}`,
    { title: "批量删除邮件", positiveText: `删除 ${preview.count} 封`, danger: true },
  );
  if (!ok) return;
  deleting.value = true;
  const result = await run(post("/api/cleanup/delete", body));
  deleting.value = false;
  if (!result) return;
  toast(`已删除 ${result.deleted} 封邮件`);
  selected.value = new Set();
  store.listVersion += 1;
  refreshNav();
  load();
}
</script>

<template>
  <GmPage
    title="批量清理"
    :count="data ? `${data.total} 封邮件` : ''"
    :loading="res.loading.value"
    :ready="!!data"
    :error="res.error.value"
    :empty="!!data && !visibleSenders.length"
    empty-title="没有符合条件的邮件"
    :empty-hint="keyword ? '换个关键字试试' : '调整上方的范围和时间条件'"
    :empty-icon="CleaningServicesOutlined"
    @retry="load"
  >
    <template #toolbar>
      <div class="gm-cl-seg" role="radiogroup" aria-label="范围">
        <button
          v-for="s in SCOPES"
          :key="s.value"
          type="button"
          role="radio"
          :aria-checked="filters.scope === s.value"
          :class="{ 'is-on': filters.scope === s.value }"
          @click="filters.scope = s.value"
        >{{ s.label }}</button>
      </div>
      <NSelect v-if="accountOptions.length > 2" v-model:value="filters.account_id" :options="accountOptions" size="small" class="gm-cl-account" />
      <NSelect v-model:value="filters.older_than_days" :options="AGES" size="small" class="gm-cl-age" />
      <NCheckbox v-model:checked="filters.only_read">只清理已读</NCheckbox>
    </template>

    <template #banner>
      <div v-if="data" class="gm-cl-head">
        <NCheckbox
          :checked="allVisibleChecked"
          :indeterminate="!allVisibleChecked && visibleSenders.some((s) => selected.has(s.email))"
          :disabled="!visibleSenders.length"
          aria-label="全选发件人"
          @update:checked="toggleAll"
        />
        <span class="gm-cl-summary">按发件人的邮件数排序（星标、已延后的邮件不计入）</span>
        <NInput v-model:value="keyword" size="small" placeholder="筛选发件人" clearable class="gm-cl-filter">
          <template #prefix><NIcon :component="SearchOutlined" /></template>
        </NInput>
      </div>
    </template>

    <div role="list">
      <div
        v-for="s in visibleSenders"
        :key="s.email"
        class="gm-cl-sender"
        :class="{ 'is-selected': selected.has(s.email) }"
        role="checkbox"
        :aria-checked="selected.has(s.email)"
        tabindex="0"
        @click="toggle(s.email)"
        @keydown.space.prevent="toggle(s.email)"
      >
        <NCheckbox :checked="selected.has(s.email)" :focusable="false" />
        <GmAvatar :name="s.name || s.email" :size="32" />
        <span class="gm-cl-main">
          <span class="gm-cl-name">{{ s.name || s.email }}</span>
          <span v-if="s.name" class="gm-cl-email">{{ s.email }}</span>
        </span>
        <span class="gm-cl-latest">最近 {{ listDate(s.latest) }}</span>
        <span class="gm-cl-count"><b>{{ s.count }}</b> 封<template v-if="s.unread">，{{ s.unread }} 封未读</template></span>
      </div>
    </div>

    <template #footer>
      <span v-if="selected.size" class="gm-cl-hint is-strong">已选 {{ selected.size }} 个发件人，共 {{ selectedCount }} 封</span>
      <span v-else-if="canDeleteAll" class="gm-cl-hint">未选发件人：将清理所有符合上方条件的邮件</span>
      <span v-else class="gm-cl-hint">选择要清理的发件人，或设置「早于」时间</span>
      <NButton type="error" class="gm-cl-go" :disabled="!selected.size && !canDeleteAll" :loading="deleting" @click="clean">预览并删除</NButton>
    </template>
  </GmPage>
</template>

<style scoped>
.gm-cl-seg { display: inline-flex; padding: 2px; border-radius: var(--r-md); background: var(--gm-surface-2); }
.gm-cl-seg button {
    height: 28px;
    padding: 0 12px;
    border: 0;
    border-radius: var(--r-sm);
    background: none;
    color: var(--gm-text-3);
    font: inherit;
    font-size: var(--fs-sm);
    cursor: pointer;
}
.gm-cl-seg button.is-on { background: var(--gm-surface); color: var(--gm-text); font-weight: 600; box-shadow: var(--gm-shadow-1); }
.gm-cl-account { width: 220px; }
.gm-cl-age { width: 130px; }
.gm-cl-head {
    position: sticky;
    top: 0;
    z-index: 1;
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding: var(--sp-2) var(--sp-5);
    border-bottom: 1px solid var(--gm-divider);
    background: var(--gm-surface);
}
.gm-cl-summary { flex: 1; min-width: 0; font-size: var(--fs-xs); color: var(--gm-text-3); }
.gm-cl-filter { width: 200px; }
.gm-cl-sender {
    display: grid;
    grid-template-columns: auto 32px 1fr auto 150px;
    align-items: center;
    gap: var(--sp-3);
    padding: var(--sp-2) var(--sp-5);
    border-bottom: 1px solid var(--gm-divider);
    cursor: pointer;
    outline: none;
}
.gm-cl-sender:hover { background: var(--gm-hover); }
.gm-cl-sender:focus-visible { box-shadow: inset 3px 0 0 var(--gm-primary); }
.gm-cl-sender.is-selected { background: var(--gm-selected); }
.gm-cl-main { display: flex; align-items: baseline; gap: var(--sp-2); min-width: 0; }
.gm-cl-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-cl-email { font-size: var(--fs-xs); color: var(--gm-text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-cl-latest { font-size: var(--fs-xs); color: var(--gm-text-3); white-space: nowrap; }
.gm-cl-count { text-align: right; white-space: nowrap; color: var(--gm-text-2); }
.gm-cl-hint { color: var(--gm-text-3); }
.gm-cl-hint.is-strong { color: var(--gm-text); font-weight: 600; }
.gm-cl-go { margin-left: auto; }
@media (max-width: 768px) {
    .gm-cl-account, .gm-cl-age { width: calc(50% - 4px); }
    .gm-cl-filter { width: 120px; }
    .gm-cl-sender { grid-template-columns: auto 32px 1fr auto; padding: var(--sp-2) var(--sp-3); }
    .gm-cl-latest { display: none; }
    .gm-cl-main { flex-direction: column; gap: 0; }
}
</style>
