<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { NButton, NSpin, NTag } from "naive-ui";
import { get, post } from "../api";
import { navAccount, openCompose, store } from "../store";
import { confirm, run, toast } from "../ui";
import { refreshNav } from "../composables/common";
import { avatarColor, futureDate, initial } from "../format";

const items = ref(null);
const multiAccount = computed(() => (store.nav?.accounts.length || 0) > 1);

async function load() {
  const data = await run(get("/api/scheduled"));
  if (data) items.value = data.items;
}

// 到点后后台任务会把邮件发出并从列表中移除：定时刷新
let timer = null;
onMounted(() => {
  load();
  timer = setInterval(load, 30000);
});
onBeforeUnmount(() => clearInterval(timer));

async function cancel(item) {
  const text = item.attachments.length
    ? "取消定时发送并转为草稿？附件不能保存在草稿里，编辑时需要重新添加。"
    : "取消定时发送并转为草稿？";
  if (!(await confirm(text, { positiveText: "取消发送" }))) return;
  const result = await run(post(`/api/scheduled/${item.id}/cancel`));
  if (!result) return;
  refreshNav();
  await load();
  openCompose({ draft: result.draft_id });
}

async function sendNow(item) {
  if (await run(post(`/api/scheduled/${item.id}/send-now`))) {
    toast("正在发送…");
    setTimeout(() => {
      load();
      refreshNav();
    }, 3000);
  }
}
</script>

<template>
  <div class="gm-panel">
    <div class="gm-toolbar">
      <div class="gm-toolbar-start"><span class="gm-toolbar-title">定时发送</span></div>
      <div class="gm-toolbar-end">
        <span v-if="items?.length" class="gm-pager-info">共 {{ items.length }} 封</span>
      </div>
    </div>
    <div class="gm-banner">写邮件时点「发送」旁边的箭头，选择「定时发送」即可安排在指定时间发出。</div>
    <div class="gm-panel-body" role="list">
      <NSpin v-if="!items" class="gm-page-loading" />
      <template v-else>
        <div v-for="it in items" :key="it.id" class="gm-sched" role="listitem">
          <span class="gm-avatar" :style="{ background: avatarColor(it.to_name) }" aria-hidden="true">{{ initial(it.to_name) }}</span>
          <div class="gm-sched-main">
            <div class="gm-sched-line">
              <span class="gm-sched-to" :title="it.to">收件人：{{ it.to_short }}</span>
              <span
                v-if="multiAccount && navAccount(it.account_id)"
                class="gm-chip is-account"
                :style="{ '--label': navAccount(it.account_id).color }"
              >{{ navAccount(it.account_id).name }}</span>
            </div>
            <div class="gm-sched-subject">
              {{ it.subject || "(无主题)" }}<span v-if="it.snippet" class="gm-sched-snippet"> — {{ it.snippet }}</span>
            </div>
            <div class="gm-sched-meta">
              <NTag v-if="it.status === 'failed'" size="small" type="error" :bordered="false">发送失败</NTag>
              <NTag v-else-if="it.status === 'sending'" size="small" type="info" :bordered="false">正在发送</NTag>
              <span>将于 {{ futureDate(it.send_at) }} 发送</span>
              <span v-if="it.attachments.length">· {{ it.attachments.length }} 个附件</span>
            </div>
            <div v-if="it.error" class="gm-sched-error">{{ it.error }}</div>
          </div>
          <div class="gm-sched-actions">
            <NButton size="small" :disabled="it.status === 'sending'" @click="sendNow(it)">
              {{ it.status === "failed" ? "重试" : "立即发送" }}
            </NButton>
            <NButton size="small" :disabled="it.status === 'sending'" @click="cancel(it)">取消并编辑</NButton>
          </div>
        </div>
        <div v-if="!items.length" class="gm-empty">
          <div class="gm-empty-title">没有定时发送的邮件</div>
          <div>安排好的邮件会显示在这里，到时间自动发出</div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.gm-page-loading { display: flex; justify-content: center; padding: 64px 0; }
.gm-sched {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--gm-divider);
}
.gm-sched-main { flex: 1; min-width: 0; }
.gm-sched-line { display: flex; align-items: center; gap: 8px; }
.gm-sched-to { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-sched-subject { margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-sched-snippet { color: var(--gm-text-3); }
.gm-sched-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 4px; font-size: 12px; color: var(--gm-primary); }
.gm-sched-error { margin-top: 4px; font-size: 12px; color: var(--gm-error); }
.gm-sched-actions { display: flex; gap: 8px; flex: none; }
@media (max-width: 768px) {
    .gm-sched { flex-wrap: wrap; }
    .gm-sched-actions { width: 100%; justify-content: flex-end; }
}
</style>
