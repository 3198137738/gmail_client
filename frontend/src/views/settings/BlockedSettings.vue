<script setup>
import { computed, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NCheckbox, NInput, NSelect, NSpin } from "naive-ui";
import SettingsHeader from "../../components/SettingsHeader.vue";
import { get, post } from "../../api";
import { store } from "../../store";
import { confirm, run, ui } from "../../ui";
import { dateTime } from "../../format";
import { upsertPurgeJob } from "../../purgeJobs";

const route = useRoute();
const router = useRouter();

const data = ref(null);
const accountId = ref(null);
const q = ref("");
const purging = ref(null);
// 批量添加屏蔽地址
const addAccountId = ref(null);
const addText = ref("");
const adding = ref(false);
// 勾选的屏蔽记录（批量永久删除）
const checked = reactive(new Set());
const batchPurging = ref(false);

const accountOptions = computed(() => [
  { value: null, label: "全部账号" },
  ...(store.nav?.accounts || []).map((a) => ({ value: a.id, label: a.name })),
]);
const addAccountOptions = computed(() => accountOptions.value.slice(1));

async function load() {
  const result = await run(get("/api/senders", { account_id: route.query.account_id, q: route.query.q }));
  if (!result) return;
  data.value = result;
  const ids = new Set(result.blocked.map((r) => r.id));
  for (const id of [...checked]) if (!ids.has(id)) checked.delete(id);
}

watch(
  () => route.query,
  (query) => {
    accountId.value = query.account_id ? Number(query.account_id) : null;
    q.value = String(query.q || "");
    if (accountId.value) addAccountId.value = accountId.value;
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

async function addBlocked() {
  if (!addAccountId.value) return ui.message.warning("请选择账号");
  if (!addText.value.trim()) return;
  adding.value = true;
  const result = await run(post("/api/senders/blocked", { account_id: addAccountId.value, text: addText.value }));
  adding.value = false;
  if (!result) return;
  const parts = [`已屏蔽 ${result.added} 个地址`];
  if (result.existing) parts.push(`${result.existing} 个之前已屏蔽`);
  if (result.job) parts.push("正在把其邮件移到「已删除」");
  ui.message.success(parts.join("，"));
  if (result.job) upsertPurgeJob(result.job);   // 右下角显示进度
  addText.value = "";
  load();
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

const allChecked = computed(() => !!data.value?.blocked.length && data.value.blocked.every((r) => checked.has(r.id)));
function toggleAll(value) {
  checked.clear();
  if (value) for (const r of data.value.blocked) checked.add(r.id);
}
function toggleCheck(id, value) {
  if (value) checked.add(id);
  else checked.delete(id);
}

async function purgeChecked() {
  const ids = [...checked];
  if (!ids.length) return;
  const ok = await confirm(
    `确定永久删除选中 ${ids.length} 个地址在 Gmail 中的全部邮件吗？包括所有邮件、垃圾邮件和已删除邮件，删除后无法恢复；屏蔽会继续保留。`,
    { danger: true, positiveText: "永久删除" },
  );
  if (!ok) return;
  batchPurging.value = true;
  const result = await run(post("/api/senders/blocked/purge", { ids }));
  batchPurging.value = false;
  if (!result) return;
  checked.clear();
  upsertPurgeJob(result.job);   // 右下角显示进度
}

async function unblock(row) {
  if (!(await confirm(`确定解除对 ${row.sender_email} 的屏蔽吗？`))) return;
  if (await run(post(`/api/senders/blocked/${row.id}/remove`), "已解除屏蔽，之后的新邮件会正常收取")) load();
}

async function untrust(row) {
  if (!(await confirm(`确定取消信任 ${row.sender_email} 吗？`))) return;
  if (await run(post(`/api/senders/trusted/${row.id}/remove`), "已取消信任")) load();
}
</script>

<template>
  <div class="gm-panel gm-settings-page">
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
            批量屏蔽：
            <div class="gm-settings-hint">一次可粘贴多个地址，用逗号、分号、空格或换行分隔；会同时隐藏该账号中这些发件人已有的邮件。</div>
          </div>
          <div class="gm-settings-content">
            <form class="gm-add" @submit.prevent="addBlocked">
              <NSelect v-model:value="addAccountId" :options="addAccountOptions" placeholder="选择账号" class="gm-filter-account" />
              <NInput
                v-model:value="addText"
                type="textarea"
                :autosize="{ minRows: 3, maxRows: 10 }"
                :placeholder="'spam@example.com\nads@example.org'"
              />
              <NButton attr-type="submit" type="primary" :loading="adding" :disabled="!addText.trim()">屏蔽</NButton>
            </form>
          </div>
        </section>

        <section class="gm-settings-row">
          <div class="gm-settings-label">
            屏蔽的地址：
            <div class="gm-settings-hint">
              屏蔽时会把该发件人的已有邮件（含垃圾邮件）移到「已删除」，之后收到的新邮件也会自动移到「已删除」，30 天后由 Gmail 自动清除。「永久删除」会把该发件人在 Gmail 中的全部邮件（含「已删除」里的）立即彻底清除，屏蔽继续保留。
            </div>
          </div>
          <div class="gm-settings-content">
            <div v-if="data.blocked.length" class="gm-batch">
              <NCheckbox
                :checked="allChecked"
                :indeterminate="checked.size > 0 && !allChecked"
                @update:checked="toggleAll"
              >{{ checked.size ? `已选 ${checked.size} 个` : "全选" }}</NCheckbox>
              <NButton
                size="small"
                type="error"
                ghost
                :disabled="!checked.size"
                :loading="batchPurging"
                title="彻底删除选中发件人在 Gmail 中的全部邮件（不解除屏蔽）"
                @click="purgeChecked"
              >永久删除选中</NButton>
            </div>
            <div v-for="row in data.blocked" :key="row.id" class="gm-addr">
              <NCheckbox :checked="checked.has(row.id)" @update:checked="(v) => toggleCheck(row.id, v)" />
              <span class="gm-addr-email">{{ row.sender_email }}</span>
              <span class="gm-addr-meta">{{ row.account_name }} · {{ dateTime(row.created_at) }}</span>
              <span class="gm-addr-actions">
                <NButton
                  text
                  type="primary"
                  title="在「屏蔽的邮件」中查看该发件人的邮件"
                  @click="router.push({ path: '/messages/', query: { kind: 'blocked', account_id: row.account_id, q: row.sender_email } })"
                >查看邮件</NButton>
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
.gm-settings-page { overflow-y: auto; background: var(--gm-bg); }
.gm-settings-loading { display: flex; justify-content: center; padding: 48px 0; }
.gm-settings-filter { display: flex; flex-wrap: wrap; gap: 8px; padding: 16px 0 4px; max-width: 640px; }
.gm-filter-account { width: 200px; }
.gm-filter-q { flex: 1; min-width: 180px; }
.gm-add { display: flex; flex-direction: column; gap: 8px; max-width: 520px; }
.gm-add > .n-button { align-self: flex-start; }
.gm-batch { display: flex; align-items: center; gap: 16px; padding-bottom: 8px; border-bottom: 1px solid var(--gm-divider); }
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
