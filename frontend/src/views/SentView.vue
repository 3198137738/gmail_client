<script setup>
// 发件记录：通过本客户端发出的邮件（以及同步的「已发送」），发送失败 / 部分收件人被拒收时标出。
import { computed } from "vue";
import { useRouter } from "vue-router";
import { DeleteOutlined, OutboxOutlined } from "@vicons/material";
import GmPage from "../ui/GmPage.vue";
import GmListRow from "../ui/GmListRow.vue";
import GmBanner from "../ui/GmBanner.vue";
import GmAccountChip from "../ui/GmAccountChip.vue";
import { get, post } from "../api";
import { confirm, run } from "../ui";
import { useResource } from "../composables/useResource";
import { providerOf, serviceName } from "../mail/wording";

const router = useRouter();
const res = useResource(() => get("/api/sent"));
const items = computed(() => res.data.value?.items || []);
const ACTIONS = [{ key: "delete", label: "删除", icon: DeleteOutlined, danger: true }];

// 同步来的「已发送」邮件打开邮件详情；本客户端的发件记录（发送失败等）打开发件记录详情
function open(item) {
  if (item.source === "gmail") router.push({ name: "message", params: { id: item.id } });
  else router.push({ name: "sentDetail", params: { id: item.id } });
}

async function onAction(item, key) {
  if (key !== "delete") return;
  const synced = item.source === "gmail";
  const text = synced
    ? `确定删除这封邮件吗？${serviceName(providerOf([item.account_id]))}「已发送」中的邮件也会移到已删除邮件。`
    : "确定删除这条发件记录吗？";
  if (!(await confirm(text, { danger: true, positiveText: "删除" }))) return;
  const url = synced ? `/api/messages/${item.id}/delete` : `/api/sent/${item.id}/delete`;
  if (await run(post(url), "已删除")) res.reload({ silent: true });
}
</script>

<template>
  <GmPage
    title="发件记录"
    :count="items.length ? `${items.length} 封` : ''"
    :loading="res.loading.value"
    :ready="!!res.data.value"
    :error="res.error.value"
    :empty="!!res.data.value && !items.length"
    empty-title="还没有发件记录"
    empty-hint="通过本客户端发出的邮件会显示在这里"
    :empty-icon="OutboxOutlined"
    @retry="res.reload()"
  >
    <template #banner>
      <GmBanner v-if="res.data.value && !res.data.value.sync_sent_enabled">
        未开启「已发送」同步，这里只显示通过本客户端发出的邮件。在 .env 中设置 SYNC_SENT_ENABLED=true 并重启后会与邮箱的「已发送」保持一致。
      </GmBanner>
    </template>
    <div role="list">
      <GmListRow
        v-for="s in items"
        :key="`${s.source}-${s.id}`"
        :avatar="s.to_name"
        :title="`收件人：${s.to_short}`"
        :title-attr="s.to"
        :subject="s.subject"
        :snippet="s.snippet"
        :date="s.time"
        :actions="ACTIONS"
        @open="open(s)"
        @action="(k) => onAction(s, k)"
      >
        <template #chips>
          <span v-if="s.status === 'partial'" class="gm-sent-bad" :title="s.error || ''">部分收件人被拒收</span>
          <span v-else-if="s.status !== 'sent'" class="gm-sent-bad" :title="s.error || ''">发送失败</span>
          <GmAccountChip :account-id="s.account_id" />
        </template>
      </GmListRow>
    </div>
  </GmPage>
</template>

<style scoped>
.gm-sent-bad {
    padding: 0 6px;
    border-radius: var(--r-sm);
    background: var(--gm-error-soft);
    color: var(--gm-error);
    font-size: 11px;
    line-height: 18px;
}
</style>
