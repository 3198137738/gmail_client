<script setup>
// 附件中心：所有邮件里的附件，按类型 / 文件名 / 账号筛选，可直接下载或打开所在邮件
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NButtonGroup, NIcon, NInput, NRadioButton, NRadioGroup, NSpin } from "naive-ui";
import {
  ChevronLeftOutlined,
  ChevronRightOutlined,
  DescriptionOutlined,
  DownloadOutlined,
  FolderZipOutlined,
  InsertDriveFileOutlined,
  PictureAsPdfOutlined,
  SearchOutlined,
  TableChartOutlined,
} from "@vicons/material";
import { get } from "../api";
import { navAccount, store } from "../store";
import { run } from "../ui";
import { attachmentKind, fileSize, listDate } from "../format";

const route = useRoute();
const router = useRouter();

const TYPES = [
  { value: "", label: "全部" },
  { value: "image", label: "图片" },
  { value: "pdf", label: "PDF" },
  { value: "doc", label: "文档" },
  { value: "sheet", label: "表格" },
  { value: "zip", label: "压缩包" },
  { value: "other", label: "其他" },
];
const ICONS = {
  pdf: [PictureAsPdfOutlined, "#d93025"],
  sheet: [TableChartOutlined, "#188038"],
  zip: [FolderZipOutlined, "#5f6368"],
  doc: [DescriptionOutlined, "#1a73e8"],
};

const data = ref(null);
const loading = ref(false);
const name = ref("");

const query = computed(() => ({
  type: String(route.query.type || ""),
  q: String(route.query.q || ""),
  page: Number(route.query.page) || 1,
}));
const multiAccount = computed(() => (store.nav?.accounts.length || 0) > 1);

async function load() {
  loading.value = true;
  const result = await run(get("/api/files", {
    type: query.value.type || undefined,
    q: query.value.q || undefined,
    page: query.value.page,
    account_id: store.view.accountId || undefined,
  }));
  loading.value = false;
  if (result) data.value = result;
}

watch(
  () => route.fullPath,
  () => {
    if (route.name !== "files") return;
    name.value = query.value.q;
    load();
  },
  { immediate: true },
);

function go(changes) {
  const next = { ...route.query, ...changes };
  for (const key of Object.keys(next)) if (!next[key] || next[key] === 1) delete next[key];
  router.push({ path: "/files", query: next });
}

function openMessage(item) {
  router.push({ name: "message", params: { id: item.message_id } });
}
</script>

<template>
  <div class="gm-panel">
    <div class="gm-toolbar">
      <div class="gm-toolbar-start">
        <span class="gm-toolbar-title">附件</span>
        <NRadioGroup :value="query.type" size="small" @update:value="(v) => go({ type: v, page: undefined })">
          <NRadioButton v-for="t in TYPES" :key="t.value" :value="t.value">{{ t.label }}</NRadioButton>
        </NRadioGroup>
        <form class="gm-files-search" @submit.prevent="go({ q: name.trim(), page: undefined })">
          <NInput v-model:value="name" size="small" placeholder="按文件名搜索" clearable @clear="go({ q: undefined, page: undefined })">
            <template #prefix><NIcon :component="SearchOutlined" /></template>
          </NInput>
        </form>
      </div>
      <div class="gm-toolbar-end">
        <span v-if="data?.total" class="gm-pager-info">共 {{ data.total }} 个</span>
        <NButtonGroup size="small">
          <NButton :disabled="!data || data.page <= 1" title="上一页" @click="go({ page: data.page - 1 })">
            <template #icon><NIcon :component="ChevronLeftOutlined" /></template>
          </NButton>
          <NButton :disabled="!data || data.page >= data.pages" title="下一页" @click="go({ page: data.page + 1 })">
            <template #icon><NIcon :component="ChevronRightOutlined" /></template>
          </NButton>
        </NButtonGroup>
      </div>
    </div>

    <div class="gm-panel-body">
      <NSpin v-if="loading && !data" class="gm-files-loading" />
      <template v-else-if="data">
        <div v-for="it in data.items" :key="it.id" class="gm-file" role="button" tabindex="0" @click="openMessage(it)" @keydown.enter="openMessage(it)">
          <span class="gm-file-thumb">
            <img v-if="attachmentKind(it.mime_type) === 'image'" :src="it.url" alt="" loading="lazy" />
            <NIcon
              v-else
              :component="(ICONS[attachmentKind(it.mime_type)] || [InsertDriveFileOutlined])[0]"
              :color="(ICONS[attachmentKind(it.mime_type)] || [null, '#5f6368'])[1]"
              :size="28"
            />
          </span>
          <div class="gm-file-main">
            <div class="gm-file-name" :title="it.name">{{ it.name }}</div>
            <div class="gm-file-meta">
              <span>{{ fileSize(it.size) }}</span>
              <span>·</span>
              <span class="gm-file-from">{{ it.folder_kind === "sent" ? "发给 " : "" }}{{ it.from_name }}</span>
              <span>·</span>
              <span class="gm-file-subject" :title="it.subject">{{ it.subject || "(无主题)" }}</span>
              <span
                v-if="multiAccount && navAccount(it.account_id)"
                class="gm-chip is-account"
                :style="{ '--label': navAccount(it.account_id).color }"
              >{{ navAccount(it.account_id).name }}</span>
            </div>
          </div>
          <span class="gm-file-date">{{ listDate(it.date) }}</span>
          <a class="gm-file-download" :href="it.download_url" download title="下载" @click.stop>
            <NIcon :component="DownloadOutlined" :size="20" />
          </a>
        </div>
        <div v-if="!data.items.length" class="gm-empty">
          <div class="gm-empty-title">没有找到附件</div>
          <div v-if="query.q || query.type">换个筛选条件试试</div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.gm-files-search { width: 220px; }
.gm-files-loading { display: flex; justify-content: center; padding: 64px 0; }
.gm-file {
    display: grid;
    grid-template-columns: 48px 1fr auto 36px;
    align-items: center;
    gap: 12px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--gm-divider);
    cursor: pointer;
}
.gm-file:hover { background: var(--gm-hover); }
.gm-file-thumb {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 48px;
    height: 48px;
    border-radius: var(--gm-radius);
    border: 1px solid var(--gm-border);
    background: var(--gm-surface-2);
    overflow: hidden;
}
.gm-file-thumb img { width: 100%; height: 100%; object-fit: cover; }
.gm-file-main { min-width: 0; }
.gm-file-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-file-meta { display: flex; align-items: center; gap: 6px; margin-top: 2px; font-size: 12px; color: var(--gm-text-3); min-width: 0; }
.gm-file-from { flex: none; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-file-subject { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-file-date { font-size: 12px; color: var(--gm-text-3); white-space: nowrap; }
.gm-file-download { display: flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 50%; color: var(--gm-text-2); }
.gm-file-download:hover { background: var(--gm-hover); color: var(--gm-primary); }
@media (max-width: 768px) {
    .gm-files-search { width: 100%; }
    .gm-file { grid-template-columns: 44px 1fr 32px; }
    .gm-file-date { display: none; }
}
</style>
