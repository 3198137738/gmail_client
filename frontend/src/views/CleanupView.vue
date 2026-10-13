<script setup>
// 批量清理：跨账号按发件人汇总邮件数量，选中发件人（或只按时间）后先预览再一次删除，同步到 Gmail
import { computed, reactive, ref, watch } from "vue";
import { NButton, NCheckbox, NIcon, NInput, NRadioButton, NRadioGroup, NSelect, NSpin } from "naive-ui";
import { SearchOutlined } from "@vicons/material";
import { post } from "../api";
import { store } from "../store";
import { confirm, run, toast } from "../ui";
import { refreshNav } from "../composables/common";
import { avatarColor, initial, listDate } from "../format";

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
const data = ref(null);
const loading = ref(false);
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

function body() {
  return { ...filters, senders: [...selected.value] };
}

async function load() {
  loading.value = true;
  const result = await run(post("/api/cleanup/senders", { ...filters, senders: [] }));
  loading.value = false;
  if (!result) return;
  data.value = result;
  const present = new Set(result.senders.map((s) => s.email));
  selected.value = new Set([...selected.value].filter((e) => present.has(e)));
}

watch(filters, load, { immediate: true });

function toggle(email, checked) {
  const next = new Set(selected.value);
  checked ? next.add(email) : next.delete(email);
  selected.value = next;
}

function toggleAll(checked) {
  const next = new Set(selected.value);
  for (const s of visibleSenders.value) checked ? next.add(s.email) : next.delete(s.email);
  selected.value = next;
}

async function clean() {
  const preview = await run(post("/api/cleanup/preview", body()));
  if (!preview) return;
  if (!preview.count) {
    toast("没有符合条件的邮件");
    return;
  }
  const samples = preview.samples.map((m) => `· ${m.from}：${m.subject || "(无主题)"}`).join("\n");
  const ok = await confirm(
    `将删除 ${preview.count} 封邮件（Gmail 上同时移到已删除邮件），星标和已延后的邮件会保留。\n\n${samples}${preview.count > preview.samples.length ? "\n……" : ""}`,
    { title: "批量删除邮件", positiveText: `删除 ${preview.count} 封`, danger: true },
  );
  if (!ok) return;
  deleting.value = true;
  const result = await run(post("/api/cleanup/delete", body()));
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
  <div class="gm-panel">
    <div class="gm-toolbar">
      <div class="gm-toolbar-start">
        <span class="gm-toolbar-title">批量清理</span>
        <NRadioGroup v-model:value="filters.scope" size="small">
          <NRadioButton v-for="s in SCOPES" :key="s.value" :value="s.value">{{ s.label }}</NRadioButton>
        </NRadioGroup>
        <NSelect
          v-if="accountOptions.length > 2"
          v-model:value="filters.account_id"
          :options="accountOptions"
          size="small"
          class="gm-cleanup-account"
        />
        <NSelect v-model:value="filters.older_than_days" :options="AGES" size="small" class="gm-cleanup-age" />
        <NCheckbox v-model:checked="filters.only_read">只清理已读</NCheckbox>
      </div>
    </div>

    <div class="gm-panel-body">
      <div class="gm-cleanup-head">
        <NCheckbox
          :checked="allVisibleChecked"
          :indeterminate="!allVisibleChecked && visibleSenders.some((s) => selected.has(s.email))"
          :disabled="!visibleSenders.length"
          @update:checked="toggleAll"
        />
        <span class="gm-cleanup-summary">
          <template v-if="data">共 {{ data.total }} 封邮件，按发件人数量排序（星标、已延后的邮件不计入）</template>
        </span>
        <NInput v-model:value="keyword" size="small" placeholder="筛选发件人" clearable class="gm-cleanup-filter">
          <template #prefix><NIcon :component="SearchOutlined" /></template>
        </NInput>
      </div>
      <NSpin v-if="loading && !data" class="gm-cleanup-loading" />
      <template v-else-if="data">
        <div
          v-for="s in visibleSenders"
          :key="s.email"
          class="gm-sender"
          :class="{ 'is-selected': selected.has(s.email) }"
          role="checkbox"
          :aria-checked="selected.has(s.email)"
          tabindex="0"
          @click="toggle(s.email, !selected.has(s.email))"
          @keydown.space.prevent="toggle(s.email, !selected.has(s.email))"
        >
          <NCheckbox :checked="selected.has(s.email)" :focusable="false" />
          <span class="gm-sender-avatar" :style="{ background: avatarColor(s.name || s.email) }">{{ initial(s.name || s.email) }}</span>
          <span class="gm-sender-main">
            <span class="gm-sender-name">{{ s.name || s.email }}</span>
            <span v-if="s.name" class="gm-sender-email">{{ s.email }}</span>
          </span>
          <span class="gm-sender-latest">最近 {{ listDate(s.latest) }}</span>
          <span class="gm-sender-count">
            <b>{{ s.count }}</b> 封<template v-if="s.unread">，{{ s.unread }} 封未读</template>
          </span>
        </div>
        <div v-if="!visibleSenders.length" class="gm-empty">
          <div class="gm-empty-title">没有符合条件的邮件</div>
        </div>
      </template>
    </div>

    <div class="gm-cleanup-bar">
      <span v-if="selected.size">已选 {{ selected.size }} 个发件人，共 {{ selectedCount }} 封</span>
      <span v-else-if="canDeleteAll">未选发件人：将清理所有符合上方条件的邮件</span>
      <span v-else class="gm-cleanup-hint">选择要清理的发件人，或设置「早于」时间</span>
      <NButton
        type="error"
        :disabled="!selected.size && !canDeleteAll"
        :loading="deleting"
        @click="clean"
      >预览并删除</NButton>
    </div>
  </div>
</template>

<style scoped>
.gm-cleanup-account { width: 220px; }
.gm-cleanup-age { width: 130px; }
.gm-cleanup-loading { display: flex; justify-content: center; padding: 64px 0; }
.gm-cleanup-head {
    position: sticky;
    top: 0;
    z-index: 1;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--gm-divider);
    background: var(--gm-surface);
}
.gm-cleanup-summary { flex: 1; min-width: 0; font-size: 12px; color: var(--gm-text-3); }
.gm-cleanup-filter { width: 200px; }
.gm-sender {
    display: grid;
    grid-template-columns: auto 32px 1fr auto 140px;
    align-items: center;
    gap: 12px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--gm-divider);
    cursor: pointer;
}
.gm-sender:hover { background: var(--gm-hover); }
.gm-sender.is-selected { background: var(--gm-selected); }
.gm-sender-avatar {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    color: #fff;
    font-weight: 600;
}
.gm-sender-main { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.gm-sender-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-sender-email { font-size: 12px; color: var(--gm-text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-sender-latest { font-size: 12px; color: var(--gm-text-3); white-space: nowrap; }
.gm-sender-count { text-align: right; white-space: nowrap; color: var(--gm-text-2); }
.gm-cleanup-bar {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 16px;
    padding: 10px 16px;
    border-top: 1px solid var(--gm-divider);
}
.gm-cleanup-hint { color: var(--gm-text-3); }
@media (max-width: 768px) {
    .gm-cleanup-account, .gm-cleanup-age { width: 100%; }
    .gm-cleanup-filter { width: 120px; }
    .gm-sender { grid-template-columns: auto 32px 1fr auto; }
    .gm-sender-latest { display: none; }
    .gm-sender-main { flex-direction: column; gap: 0; }
}
</style>
