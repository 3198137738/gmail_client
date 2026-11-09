<script setup>
// 发件记录详情（本客户端发出、不在已同步「已发送」中的邮件）：与阅读区共用邮件卡片。
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NAlert } from "naive-ui";
import { ArrowBackOutlined, DeleteOutlined, PrintOutlined } from "@vicons/material";
import ThreadMessage from "./ThreadMessage.vue";
import GmIconButton from "../ui/GmIconButton.vue";
import GmEmptyState from "../ui/GmEmptyState.vue";
import { get, post } from "../api";
import { confirm, run } from "../ui";
import { isMobile } from "../composables/common";
import { APP_NAME } from "../brand";

const route = useRoute();
const router = useRouter();
const record = ref(null);
const error = ref("");
const showImages = ref(false);

async function load() {
  error.value = "";
  showImages.value = false;
  try {
    record.value = await get(`/api/sent/${route.params.id}`);
    document.title = `${record.value.subject || "(无主题)"} - ${APP_NAME}`;
  } catch (err) {
    record.value = null;
    error.value = err.message || "加载失败";
  }
}
watch(() => route.params.id, (id) => id && load(), { immediate: true });

// 转成邮件卡片需要的字段
const message = computed(() => {
  const r = record.value;
  if (!r) return null;
  const me = r.account_name || r.account_email || "";
  return {
    id: r.id,
    from_name: me,
    from_email: r.account_email,
    from_full: r.account_email,
    to_short: r.to_short,
    cc_short: r.cc_short,
    to_full: r.to_full,
    cc_full: r.cc_full,
    bcc_full: r.bcc_full,
    date: r.time,
    subject: r.subject,
    attachments: [],
    remote_images: r.remote_images,
    phishing: [],
    otp_code: null,
  };
});

function back() {
  router.push("/compose/sent");
}

function printPage() {
  window.print();
}

async function remove() {
  if (!(await confirm("确定删除这条发件记录吗？", { danger: true, positiveText: "删除" }))) return;
  if (await run(post(`/api/sent/${record.value.id}/delete`), "已删除发件记录")) back();
}
</script>

<template>
  <section class="gm-sr">
    <header class="gm-sr-bar no-print">
      <GmIconButton :icon="ArrowBackOutlined" title="返回发件记录" @click="back" />
      <GmIconButton v-if="record" :icon="DeleteOutlined" title="删除记录" danger @click="remove" />
      <GmIconButton v-if="record" class="gm-sr-end" :icon="PrintOutlined" title="打印" @click="printPage" />
    </header>
    <div class="gm-sr-scroll">
      <GmEmptyState v-if="error" tone="error" title="发件记录加载失败" :hint="error" action-text="重试" @action="load" />
      <div v-else-if="!record" class="gm-sr-loading" aria-hidden="true"><span /><span /></div>
      <div v-else class="gm-sr-thread">
        <h1 class="gm-sr-subject">{{ record.subject || "(无主题)" }}</h1>
        <div class="gm-sr-tags">
          <span v-if="record.status === 'sent' || record.status === 'partial'" class="gm-sr-tag">已发送</span>
          <span v-else class="gm-sr-tag is-failed">发送失败</span>
        </div>
        <NAlert v-if="record.error && record.status === 'partial'" type="warning" class="gm-sr-alert">{{ record.error }}</NAlert>
        <NAlert v-else-if="record.error && record.status !== 'sent'" type="error" class="gm-sr-alert">发送失败：{{ record.error }}</NAlert>
        <ThreadMessage
          :message="message"
          :body-src="`/compose/sent/${record.id}/body${showImages ? '?images=1' : ''}`"
          :show-images="showImages"
          :actions="false"
          :compact-date="isMobile"
          @show-images="showImages = true"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.gm-sr { display: flex; flex-direction: column; height: 100%; min-height: 0; background: var(--gm-surface); }
.gm-sr-bar { display: flex; align-items: center; gap: var(--sp-1); min-height: 52px; padding: var(--sp-2) var(--sp-3); border-bottom: 1px solid var(--gm-divider); flex: none; }
.gm-sr-end { margin-left: auto; }
.gm-sr-scroll { flex: 1; min-height: 0; overflow-y: auto; }
.gm-sr-thread { max-width: 960px; padding: var(--sp-5) var(--sp-6) var(--sp-7); }
.gm-sr-subject { margin: 0; font-size: var(--fs-xl); font-weight: 600; line-height: 1.35; word-break: break-word; }
.gm-sr-tags { margin: var(--sp-2) 0 var(--sp-4); }
.gm-sr-tag { padding: 0 8px; border: 1px solid var(--gm-border); border-radius: var(--r-sm); font-size: var(--fs-xs); line-height: 20px; color: var(--gm-text-2); }
.gm-sr-tag.is-failed { border-color: var(--gm-error-soft); background: var(--gm-error-soft); color: var(--gm-error); }
.gm-sr-alert { margin-bottom: var(--sp-3); }
.gm-sr-loading { display: flex; flex-direction: column; gap: 12px; padding: var(--sp-6); }
.gm-sr-loading span { height: 14px; width: 50%; border-radius: 6px; background: var(--gm-surface-2); }
@media (max-width: 768px) {
    .gm-sr-thread { padding: var(--sp-3) var(--sp-3) var(--sp-6); }
}
</style>
