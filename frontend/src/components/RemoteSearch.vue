<script setup>
// 在邮箱服务器（Gmail / Outlook）上搜索的结果：按账号分组。本地已有的邮件在本客户端打开，其余在网页版邮箱打开。
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { NAlert, NEmpty, NIcon, NModal, NSpin, NTag } from "naive-ui";
import { OpenInNewOutlined } from "@vicons/material";
import { get } from "../api";
import { listDate } from "../format";

const props = defineProps({
  show: { type: Boolean, default: false },
  q: { type: String, default: "" },
  accountId: { type: Number, default: null },
});
const emit = defineEmits(["update:show"]);
const router = useRouter();

const loading = ref(false);
const error = ref("");
const data = ref(null);

watch(
  () => props.show,
  async (show) => {
    if (!show) return;
    loading.value = true;
    error.value = "";
    data.value = null;
    try {
      data.value = await get("/api/messages/remote-search", { q: props.q, account_id: props.accountId || undefined });
    } catch (err) {
      error.value = err.message;
    } finally {
      loading.value = false;
    }
  },
);

const groups = computed(() => (data.value?.accounts || []).filter((a) => a.items.length || a.error));
const totalFound = computed(() => (data.value?.accounts || []).reduce((n, a) => n + a.total, 0));

function nameOf(from) {
  const m = /^\s*"?([^"<]*?)"?\s*<[^>]+>\s*$/.exec(from || "");
  return (m && m[1]) || from || "(未知发件人)";
}

function open(item) {
  if (item.local_id) {
    emit("update:show", false);
    router.push({ name: "message", params: { id: item.local_id } });
  } else if (item.gmail_url) {
    window.open(item.gmail_url, "_blank", "noopener");
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="`在邮箱服务器上搜索：${q}`"
    :bordered="false"
    style="width: min(760px, calc(100vw - 32px))"
    @update:show="(v) => emit('update:show', v)"
  >
    <div class="gm-remote">
      <div v-if="loading" class="gm-remote-loading">
        <NSpin size="small" /> 正在搜索邮箱服务器（包括未同步、已归档的邮件）…
      </div>
      <NAlert v-else-if="error" type="error">{{ error }}</NAlert>
      <template v-else-if="data">
        <div class="gm-remote-summary">共找到 {{ totalFound }} 封，每个账号显示最新的 30 封。本地已同步的邮件会在这里直接打开，其余在网页版邮箱（Gmail / Outlook）中打开。</div>
        <NEmpty v-if="!groups.length" description="邮箱服务器中也没有找到相符的邮件" />
        <section v-for="g in groups" :key="g.account_id" class="gm-remote-group">
          <div class="gm-remote-account">
            {{ g.account_email }}
            <span v-if="g.total" class="gm-remote-count">{{ g.total }} 封</span>
          </div>
          <NAlert v-if="g.error" type="warning" :show-icon="false" class="gm-remote-error">搜索失败：{{ g.error }}</NAlert>
          <div
            v-for="(it, i) in g.items"
            :key="i"
            class="gm-remote-item"
            :class="{ 'is-unread': it.unread }"
            role="button"
            tabindex="0"
            @click="open(it)"
            @keydown.enter="open(it)"
          >
            <span class="gm-remote-from" :title="it.from">{{ nameOf(it.from) }}</span>
            <span class="gm-remote-subject">{{ it.subject || "(无主题)" }}</span>
            <NTag v-if="it.local_id" size="small" type="info" :bordered="false">已同步</NTag>
            <NIcon v-else :component="OpenInNewOutlined" :size="14" class="gm-remote-ext" :title="g.provider === 'outlook' ? '在 Outlook 网页版中打开' : '在 Gmail 中打开'" />
            <span class="gm-remote-date">{{ listDate(it.date) }}</span>
          </div>
        </section>
      </template>
    </div>
  </NModal>
</template>

<style scoped>
.gm-remote { max-height: min(70vh, 640px); overflow-y: auto; }
.gm-remote-loading { display: flex; align-items: center; gap: 10px; padding: 24px 0; color: var(--gm-text-3); }
.gm-remote-summary { color: var(--gm-text-3); font-size: 13px; margin-bottom: 12px; }
.gm-remote-group { margin-bottom: 16px; }
.gm-remote-account { font-weight: 600; padding: 6px 0; border-bottom: 1px solid var(--gm-divider); }
.gm-remote-count { margin-left: 8px; font-weight: 400; font-size: 12px; color: var(--gm-text-3); }
.gm-remote-error { margin-top: 8px; }
.gm-remote-item {
    display: grid;
    grid-template-columns: minmax(100px, 160px) 1fr auto auto;
    align-items: center;
    gap: 10px;
    padding: 8px 4px;
    border-bottom: 1px solid var(--gm-divider);
    cursor: pointer;
}
.gm-remote-item:hover { background: var(--gm-hover); }
.gm-remote-item.is-unread .gm-remote-from,
.gm-remote-item.is-unread .gm-remote-subject { font-weight: 600; }
.gm-remote-from, .gm-remote-subject { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-remote-subject { color: var(--gm-text-2); }
.gm-remote-ext { color: var(--gm-text-3); }
.gm-remote-date { font-size: 12px; color: var(--gm-text-3); white-space: nowrap; }
@media (max-width: 768px) {
    .gm-remote-item { grid-template-columns: 1fr auto; }
    .gm-remote-from { grid-column: 1 / -1; }
}
</style>
