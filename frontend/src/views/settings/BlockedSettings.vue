<script setup>
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NInput, NSelect, NSpin } from "naive-ui";
import SettingsHeader from "../../components/SettingsHeader.vue";
import { get, post } from "../../api";
import { store } from "../../store";
import { confirm, run, ui } from "../../ui";
import { dateTime } from "../../format";

const route = useRoute();
const router = useRouter();

const data = ref(null);
const accountId = ref(null);
const q = ref("");
const purging = ref(null);

const accountOptions = computed(() => [
  { value: null, label: "全部账号" },
  ...(store.nav?.accounts || []).map((a) => ({ value: a.id, label: a.name })),
]);

async function load() {
  const result = await run(get("/api/senders", { account_id: route.query.account_id, q: route.query.q }));
  if (result) data.value = result;
}

watch(
  () => route.query,
  (query) => {
    accountId.value = query.account_id ? Number(query.account_id) : null;
    q.value = String(query.q || "");
    load();
  },
  { immediate: true },
);

function search() {
  const query = {};
  if (accountId.value) query.account_id = accountId.value;
  if (q.value.trim()) query.q = q.value.trim();
  router.push({ path: "/messages/blocked-senders", query });
}

async function purge(row) {
  const ok = await confirm(
    `确定永久删除 ${row.sender_email} 在 Gmail 中的全部邮件吗？包括所有邮件、垃圾邮件和已删除邮件，删除后无法恢复；屏蔽会继续保留。`,
    { danger: true, positiveText: "永久删除" },
  );
  if (!ok) return;
  purging.value = row.id;
  const result = await run(post(`/api/senders/blocked/${row.id}/purge`));
  purging.value = null;
  if (result) ui.message[result.ok ? "success" : "error"](result.message, { duration: 6000 });
}

async function unblock(row) {
  if (!(await confirm(`确定解除对 ${row.sender_email} 的屏蔽吗？`))) return;
  if (await run(post(`/api/senders/blocked/${row.id}/remove`), "已解除屏蔽，正在补回屏蔽期间的邮件")) load();
}

async function untrust(row) {
  if (!(await confirm(`确定取消信任 ${row.sender_email} 吗？`))) return;
  if (await run(post(`/api/senders/trusted/${row.id}/remove`), "已取消信任")) load();
}
</script>

<template>
  <div class="gm-card gm-settings-page">
    <SettingsHeader />
    <div class="gm-settings">
      <form class="gm-settings-filter" @submit.prevent="search">
        <NSelect v-model:value="accountId" :options="accountOptions" class="gm-filter-account" @update:value="search" />
        <NInput v-model:value="q" placeholder="按发件人地址搜索" clearable class="gm-filter-q" @clear="q = ''; search()" />
        <NButton attr-type="submit" secondary>搜索</NButton>
      </form>

      <NSpin v-if="!data" class="gm-settings-loading" />
      <template v-else>
        <section class="gm-settings-row">
          <div class="gm-settings-label">
            屏蔽的地址：
            <div class="gm-settings-hint">
              同步时会忽略这些发件人的邮件，但邮件仍留在 Gmail 里；「永久删除」会把该发件人在 Gmail 中的全部邮件（含垃圾邮件、已删除邮件）彻底清除，屏蔽继续保留。解除屏蔽后，会自动补回屏蔽期间被忽略的邮件。
            </div>
          </div>
          <div class="gm-settings-content">
            <div v-for="row in data.blocked" :key="row.id" class="gm-addr">
              <span class="gm-addr-email">{{ row.sender_email }}</span>
              <span class="gm-addr-meta">{{ row.account_name }} · {{ dateTime(row.created_at) }}</span>
              <span class="gm-addr-actions">
                <NButton
                  text
                  type="error"
                  :loading="purging === row.id"
                  title="彻底删除该发件人在 Gmail 中的全部邮件（不解除屏蔽）"
                  @click="purge(row)"
                >永久删除</NButton>
                <NButton text type="primary" @click="unblock(row)">解除屏蔽</NButton>
              </span>
            </div>
            <div v-if="!data.blocked.length" class="gm-settings-empty">您目前没有屏蔽任何地址。</div>
          </div>
        </section>

        <section class="gm-settings-row">
          <div class="gm-settings-label">
            信任的发件人：
            <div class="gm-settings-hint">在垃圾邮件中点「这不是垃圾邮件」后加入；其邮件被 Gmail 判为垃圾邮件时会自动移回收件箱。</div>
          </div>
          <div class="gm-settings-content">
            <div v-for="row in data.trusted" :key="row.id" class="gm-addr">
              <span class="gm-addr-email">{{ row.sender_email }}</span>
              <span class="gm-addr-meta">{{ row.account_name }} · {{ dateTime(row.created_at) }}</span>
              <span class="gm-addr-actions">
                <NButton text type="primary" @click="untrust(row)">取消信任</NButton>
              </span>
            </div>
            <div v-if="!data.trusted.length" class="gm-settings-empty">当前没有信任的发件人。</div>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.gm-settings-page { overflow-y: auto; }
.gm-settings-loading { display: flex; justify-content: center; padding: 48px 0; }
.gm-settings-filter { display: flex; flex-wrap: wrap; gap: 8px; padding: 16px 0 4px; max-width: 640px; }
.gm-filter-account { width: 200px; }
.gm-filter-q { flex: 1; min-width: 180px; }
.gm-addr {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 16px;
    padding: 10px 0;
    border-bottom: 1px solid var(--gm-divider);
}
.gm-addr:last-of-type { border-bottom: 0; }
.gm-addr-email { font-weight: 500; word-break: break-all; }
.gm-addr-meta { color: var(--gm-text-3); font-size: 12px; }
.gm-addr-actions { display: flex; gap: 16px; margin-left: auto; }
@media (max-width: 768px) {
    .gm-settings-page { border-radius: 0; }
    .gm-filter-account { width: 100%; }
}
</style>
