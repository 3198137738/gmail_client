<script setup>
// 附件中心：所有邮件里的附件，按类型 / 文件名筛选（随当前账号范围），可直接下载或打开所在邮件。
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NIcon, NInput } from "naive-ui";
import { AttachFileOutlined, DownloadOutlined, SearchOutlined } from "@vicons/material";
import GmPage from "../ui/GmPage.vue";
import GmPager from "../ui/GmPager.vue";
import GmAccountChip from "../ui/GmAccountChip.vue";
import FileIcon from "../ui/FileIcon.vue";
import { get } from "../api";
import { store } from "../store";
import { useResource } from "../composables/useResource";
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

const name = ref("");
const query = computed(() => ({
  type: String(route.query.type || ""),
  q: String(route.query.q || ""),
  page: Number(route.query.page) || 1,
}));

const res = useResource(
  () =>
    get("/api/files", {
      type: query.value.type || undefined,
      q: query.value.q || undefined,
      page: query.value.page,
      account_id: store.view.accountId || undefined,
    }),
  { immediate: false },
);
const data = res.data;

watch(
  () => route.fullPath,
  () => {
    if (route.name !== "files") return;
    name.value = query.value.q;
    res.reload();
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
  <GmPage
    title="附件"
    :loading="res.loading.value"
    :ready="!!data"
    :error="res.error.value"
    :empty="!!data && !data.items.length"
    empty-title="没有找到附件"
    :empty-hint="query.q || query.type ? '换个筛选条件试试' : '收到的邮件附件会集中显示在这里'"
    :empty-icon="AttachFileOutlined"
    @retry="res.reload()"
  >
    <template #actions>
      <GmPager v-if="data" :page="data.page" :pages="data.pages" :total="data.total" @change="(p) => go({ page: p })" />
    </template>
    <template #toolbar>
      <div class="gm-fv-seg" role="radiogroup" aria-label="文件类型">
        <button
          v-for="t in TYPES"
          :key="t.value"
          type="button"
          role="radio"
          :aria-checked="query.type === t.value"
          :class="{ 'is-on': query.type === t.value }"
          @click="go({ type: t.value, page: undefined })"
        >{{ t.label }}</button>
      </div>
      <form class="gm-fv-search" @submit.prevent="go({ q: name.trim(), page: undefined })">
        <NInput v-model:value="name" size="small" placeholder="按文件名搜索" clearable @clear="go({ q: undefined, page: undefined })">
          <template #prefix><NIcon :component="SearchOutlined" /></template>
        </NInput>
      </form>
    </template>

    <div v-if="data" role="list">
      <div
        v-for="it in data.items"
        :key="it.id"
        class="gm-fv-row"
        role="listitem"
        tabindex="0"
        @click="openMessage(it)"
        @keydown.enter.self="openMessage(it)"
      >
        <span class="gm-fv-thumb">
          <img v-if="attachmentKind(it.mime_type) === 'image'" :src="it.url" alt="" loading="lazy" />
          <FileIcon v-else :mime="it.mime_type" :size="26" />
        </span>
        <div class="gm-fv-main">
          <div class="gm-fv-name" :title="it.name">{{ it.name }}</div>
          <div class="gm-fv-meta">
            <span>{{ fileSize(it.size) }}</span>
            <span>·</span>
            <span class="gm-fv-from">{{ it.folder_kind === "sent" ? "发给 " : "" }}{{ it.from_name }}</span>
            <span>·</span>
            <span class="gm-fv-subject" :title="it.subject">{{ it.subject || "(无主题)" }}</span>
            <GmAccountChip :account-id="it.account_id" />
          </div>
        </div>
        <span class="gm-fv-date">{{ listDate(it.date) }}</span>
        <a class="gm-fv-download" :href="it.download_url" download :title="`下载 ${it.name}`" :aria-label="`下载 ${it.name}`" @click.stop>
          <NIcon :component="DownloadOutlined" :size="20" />
        </a>
      </div>
    </div>
  </GmPage>
</template>

<style scoped>
.gm-fv-seg { display: inline-flex; flex-wrap: wrap; padding: 2px; border-radius: var(--r-md); background: var(--gm-surface-2); }
.gm-fv-seg button {
    height: 28px;
    padding: 0 10px;
    border: 0;
    border-radius: var(--r-sm);
    background: none;
    color: var(--gm-text-3);
    font: inherit;
    font-size: var(--fs-sm);
    cursor: pointer;
}
.gm-fv-seg button.is-on { background: var(--gm-surface); color: var(--gm-text); font-weight: 600; box-shadow: var(--gm-shadow-1); }
.gm-fv-search { width: 220px; }
.gm-fv-row {
    display: grid;
    grid-template-columns: 48px 1fr auto 36px;
    align-items: center;
    gap: var(--sp-3);
    padding: var(--sp-2) var(--sp-5);
    border-bottom: 1px solid var(--gm-divider);
    cursor: pointer;
    outline: none;
}
.gm-fv-row:hover { background: var(--gm-hover); }
.gm-fv-row:focus-visible { box-shadow: inset 3px 0 0 var(--gm-primary); }
.gm-fv-thumb {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 48px;
    height: 48px;
    border: 1px solid var(--gm-border);
    border-radius: var(--r-md);
    background: var(--gm-surface-2);
    overflow: hidden;
}
.gm-fv-thumb img { width: 100%; height: 100%; object-fit: cover; }
.gm-fv-main { min-width: 0; }
.gm-fv-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-fv-meta { display: flex; align-items: center; gap: 6px; min-width: 0; margin-top: 2px; font-size: var(--fs-xs); color: var(--gm-text-3); }
.gm-fv-from { flex: none; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-fv-subject { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-fv-date { font-size: var(--fs-xs); color: var(--gm-text-3); white-space: nowrap; }
.gm-fv-download {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    border-radius: var(--r-md);
    color: var(--gm-text-2);
}
.gm-fv-download:hover { background: var(--gm-hover); color: var(--gm-primary); }
@media (max-width: 768px) {
    .gm-fv-search { width: 100%; }
    .gm-fv-row { grid-template-columns: 44px 1fr 34px; padding: var(--sp-2) var(--sp-3); }
    .gm-fv-date { display: none; }
}
</style>
