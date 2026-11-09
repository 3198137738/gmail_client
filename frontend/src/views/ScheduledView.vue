<script setup>
// 定时发送：已安排的邮件、发送时间与状态；可立即发送（失败时重试）或取消并转为草稿继续编辑（附件保留）。
import { computed, onBeforeUnmount, onMounted } from "vue";
import { NTag } from "naive-ui";
import { EditOutlined, ScheduleSendOutlined, SendOutlined } from "@vicons/material";
import GmPage from "../ui/GmPage.vue";
import GmListRow from "../ui/GmListRow.vue";
import GmAccountChip from "../ui/GmAccountChip.vue";
import { get, post } from "../api";
import { openCompose } from "../store";
import { confirm, run, toast } from "../ui";
import { refreshNav } from "../composables/common";
import { useResource } from "../composables/useResource";
import { futureDate } from "../format";

const res = useResource(async () => (await get("/api/scheduled")).items);
const items = computed(() => res.data.value || []);

// 到点后后台任务会把邮件发出并从列表中移除：定时刷新
let timer = null;
onMounted(() => (timer = setInterval(() => res.reload({ silent: true }), 30000)));
onBeforeUnmount(() => clearInterval(timer));

function actionsFor(it) {
  if (it.status === "sending") return [];
  return [
    { key: "send", label: it.status === "failed" ? "重新发送" : "立即发送", icon: SendOutlined },
    { key: "edit", label: "取消定时并编辑", icon: EditOutlined },
  ];
}

async function onAction(it, key) {
  if (key === "send") {
    if (await run(post(`/api/scheduled/${it.id}/send-now`))) {
      toast("正在发送…");
      setTimeout(() => {
        res.reload({ silent: true });
        refreshNav();
      }, 3000);
    }
    return;
  }
  if (!(await confirm("取消定时发送并转为草稿继续编辑？", { positiveText: "取消定时" }))) return;
  const result = await run(post(`/api/scheduled/${it.id}/cancel`));
  if (!result) return;
  refreshNav();
  res.reload({ silent: true });
  openCompose({ draft: result.draft_id });
}
</script>

<template>
  <GmPage
    title="定时发送"
    :count="items.length ? `${items.length} 封` : ''"
    :loading="res.loading.value"
    :ready="!!res.data.value"
    :error="res.error.value"
    :empty="!!res.data.value && !items.length"
    empty-title="没有定时发送的邮件"
    empty-hint="写邮件时点「发送」旁的箭头，选择「定时发送」，邮件会在指定时间自动发出"
    :empty-icon="ScheduleSendOutlined"
    @retry="res.reload()"
  >
    <div role="list">
      <GmListRow
        v-for="it in items"
        :key="it.id"
        :avatar="it.to_name"
        :title="`收件人：${it.to_short}`"
        :title-attr="it.to"
        :subject="it.subject"
        :snippet="it.snippet"
        :actions="actionsFor(it)"
        @open="onAction(it, 'edit')"
        @action="(k) => onAction(it, k)"
      >
        <template #chips><GmAccountChip :account-id="it.account_id" /></template>
        <template #extra>
          <NTag v-if="it.status === 'failed'" size="small" type="error" :bordered="false">发送失败</NTag>
          <NTag v-else-if="it.status === 'sending'" size="small" type="info" :bordered="false">正在发送</NTag>
          <span class="gm-sched-when">将于 {{ futureDate(it.send_at) }} 发送</span>
          <span v-if="it.attachments.length" class="gm-sched-att">· {{ it.attachments.length }} 个附件</span>
          <span v-if="it.error" class="gm-sched-error">{{ it.error }}</span>
        </template>
      </GmListRow>
    </div>
  </GmPage>
</template>

<style scoped>
.gm-sched-when { color: var(--gm-primary); font-weight: 500; }
.gm-sched-att { color: var(--gm-text-3); }
.gm-sched-error { width: 100%; color: var(--gm-error); }
</style>
