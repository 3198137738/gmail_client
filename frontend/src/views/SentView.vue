<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { NSpin } from "naive-ui";
import SimpleRow from "../components/SimpleRow.vue";
import { get, post } from "../api";
import { navAccount, store } from "../store";
import { confirm, run } from "../ui";

const router = useRouter();
const data = ref(null);
const multiAccount = computed(() => (store.nav?.accounts.length || 0) > 1);

async function load() {
  data.value = await run(get("/api/sent"));
}
onMounted(load);

// Gmail「已发送」中的邮件打开邮件详情；本客户端的发件记录（发送失败等）打开发件记录详情
function open(item) {
  if (item.source === "gmail") router.push({ name: "message", params: { id: item.id } });
  else router.push({ name: "sentDetail", params: { id: item.id } });
}

async function remove(item) {
  const gmail = item.source === "gmail";
  const text = gmail
    ? "确定删除这封邮件吗？Gmail「已发送」中的邮件也会移到已删除邮件。"
    : "确定删除这条发件记录吗？";
  if (!(await confirm(text, { danger: true, positiveText: "删除" }))) return;
  const url = gmail ? `/api/messages/${item.id}/delete` : `/api/sent/${item.id}/delete`;
  if (await run(post(url), "已删除")) load();
}
</script>

<template>
  <div class="gm-panel">
    <div class="gm-toolbar">
      <div class="gm-toolbar-start"><span class="gm-toolbar-title">发件记录</span></div>
      <div class="gm-toolbar-end">
        <span v-if="data?.items.length" class="gm-pager-info">共 {{ data.items.length }} 封</span>
      </div>
    </div>
    <div v-if="data && !data.sync_sent_enabled" class="gm-banner">
      未开启 Gmail「已发送」同步，这里只显示通过本客户端发出的邮件。在 <code>.env</code> 中设置
      <code>SYNC_SENT_ENABLED=true</code> 并重启后，将与 Gmail「已发送」保持同步。
    </div>
    <div class="gm-page-list" role="list">
      <NSpin v-if="!data" class="gm-page-loading" />
      <template v-else>
        <SimpleRow
          v-for="s in data.items"
          :key="`${s.source}-${s.id}`"
          :name="s.to_name"
          :sender="`收件人：${s.to_short}`"
          :sender-title="s.to"
          :subject="s.subject"
          :snippet="s.snippet"
          :date="s.time"
          @open="open(s)"
          @delete="remove(s)"
        >
          <template #chips>
            <span v-if="s.status === 'partial'" class="gm-chip is-failed" :title="s.error || ''">部分收件人被拒收</span>
            <span v-else-if="s.status !== 'sent'" class="gm-chip is-failed" :title="s.error || ''">发送失败</span>
            <span
              v-if="multiAccount && navAccount(s.account_id)"
              class="gm-chip is-account"
              :style="{ '--label': navAccount(s.account_id).color }"
              :title="navAccount(s.account_id).email"
            >{{ navAccount(s.account_id).name }}</span>
          </template>
        </SimpleRow>
        <div v-if="!data.items.length" class="gm-empty">
          <div class="gm-empty-title">还没有发件记录</div>
          <div>通过本客户端发出的邮件会显示在这里</div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.gm-page-list { flex: 1; overflow-y: auto; min-height: 0; }
.gm-page-loading { display: flex; justify-content: center; padding: 64px 0; }
</style>
