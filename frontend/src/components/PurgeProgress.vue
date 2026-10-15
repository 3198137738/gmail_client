<script setup>
// 右下角的批量永久删除进度卡片：逐个地址显示进度，结束后显示结果，关闭前一直保留（刷新页面也在）。
import { onBeforeUnmount, onMounted, reactive, watch } from "vue";
import { NIcon, NProgress, NSpin } from "naive-ui";
import { CheckCircleOutlined, CloseOutlined, ErrorOutlineOutlined, ExpandLessOutlined, ExpandMoreOutlined } from "@vicons/material";
import IconBtn from "./IconBtn.vue";
import { store } from "../store";
import { onRealtime } from "../realtime";
import { dismissPurgeJob, loadPurgeJobs, purgeJobs, upsertPurgeJob } from "../purgeJobs";

// 实时推送不可用时，有进行中的任务就定时查询
const POLL_MS = 3000;

const expanded = reactive(new Set());

function toggle(id) {
  if (expanded.has(id)) expanded.delete(id);
  else expanded.add(id);
}

function percent(job) {
  return job.total ? Math.round((job.done / job.total) * 100) : 100;
}

function summary(job) {
  if (!job.finished) return job.current ? `正在处理 ${job.current}` : "等待开始…";
  const text = `已完成，共永久删除 ${job.purged} 封`;
  return job.failed ? `${text}；${job.failed} 个地址失败` : text;
}

let timer = null;
function stopPolling() {
  clearInterval(timer);
  timer = null;
}
watch(
  () => store.realtime !== "connected" && purgeJobs.list.some((j) => !j.finished),
  (poll) => {
    if (poll && !timer) timer = setInterval(loadPurgeJobs, POLL_MS);
    else if (!poll) stopPolling();
  },
);

const disposers = [];
onMounted(() => {
  loadPurgeJobs();
  disposers.push(onRealtime("purge_progress", (ev) => upsertPurgeJob(ev.job)));
  disposers.push(onRealtime("open", (e) => !e.first && loadPurgeJobs()));
});
onBeforeUnmount(() => {
  disposers.forEach((d) => d());
  stopPolling();
});
</script>

<template>
  <div v-if="purgeJobs.list.length" class="gm-purge">
    <div v-for="job in purgeJobs.list" :key="job.id" class="gm-purge-job" role="status" aria-live="polite">
      <div class="gm-purge-head">
        <NSpin v-if="!job.finished" :size="14" />
        <NIcon
          v-else
          :component="job.failed ? ErrorOutlineOutlined : CheckCircleOutlined"
          :class="job.failed ? 'gm-purge-warn' : 'gm-purge-ok'"
          :size="18"
        />
        <span class="gm-purge-title">永久删除屏蔽地址的邮件（{{ job.done }}/{{ job.total }}）</span>
        <IconBtn
          :icon="expanded.has(job.id) ? ExpandLessOutlined : ExpandMoreOutlined"
          :title="expanded.has(job.id) ? '收起详情' : '展开详情'"
          size="tiny"
          @click="toggle(job.id)"
        />
        <IconBtn v-if="job.finished" :icon="CloseOutlined" title="关闭" size="tiny" @click="dismissPurgeJob(job.id)" />
      </div>
      <NProgress
        type="line"
        :percentage="percent(job)"
        :status="job.finished && job.failed ? 'warning' : job.finished ? 'success' : 'default'"
        :show-indicator="false"
        :height="4"
      />
      <div class="gm-purge-summary">{{ summary(job) }}</div>
      <ul v-if="expanded.has(job.id)" class="gm-purge-items">
        <li v-for="(item, i) in job.items" :key="i" :class="`is-${item.status}`">
          <span class="gm-purge-sender" :title="item.account">{{ item.sender }}</span>
          <span class="gm-purge-state">
            <template v-if="item.status === 'pending'">等待中</template>
            <template v-else-if="item.status === 'running'">进行中…</template>
            <template v-else>{{ item.message }}</template>
          </span>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.gm-purge {
    position: fixed;
    right: 24px;
    bottom: 24px;
    z-index: 900;   /* 写邮件窗口（1000）在上面 */
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: min(380px, calc(100vw - 32px));
}
.gm-purge-job {
    padding: 8px 8px 10px 14px;
    border-radius: var(--gm-radius);
    background: var(--gm-surface);
    box-shadow: var(--gm-shadow-3);
    font-size: 13px;
}
.gm-purge-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.gm-purge-title { flex: 1; font-weight: 500; }
.gm-purge-ok { color: var(--gm-primary); }
.gm-purge-warn { color: #f0a020; }
.gm-purge-summary { margin-top: 6px; padding-right: 6px; color: var(--gm-text-2); word-break: break-all; }
.gm-purge-items {
    list-style: none;
    margin: 8px 0 0;
    padding: 0 6px 0 0;
    max-height: 240px;
    overflow-y: auto;
    border-top: 1px solid var(--gm-divider);
}
.gm-purge-items li { display: flex; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--gm-divider); }
.gm-purge-items li:last-child { border-bottom: 0; }
.gm-purge-sender { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-purge-state { flex-shrink: 0; max-width: 55%; color: var(--gm-text-3); text-align: right; }
.is-running .gm-purge-state { color: var(--gm-primary); }
.is-failed .gm-purge-state { color: #d03050; }
@media (max-width: 768px) {
    .gm-purge { right: 8px; left: 8px; bottom: 8px; width: auto; }
}
</style>
