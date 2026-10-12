<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { NSpin } from "naive-ui";
import SimpleRow from "../components/SimpleRow.vue";
import { get, post } from "../api";
import { navAccount, openCompose, store } from "../store";
import { confirm, run } from "../ui";
import { refreshNav } from "../composables/common";

const drafts = ref(null);
const multiAccount = computed(() => (store.nav?.accounts.length || 0) > 1);

async function load() {
  const data = await run(get("/api/drafts"));
  if (data) drafts.value = data.items;
}

async function remove(d) {
  if (!(await confirm("舍弃这封草稿？", { danger: true, positiveText: "舍弃" }))) return;
  if (await run(post(`/api/drafts/${d.id}/delete`))) {
    drafts.value = drafts.value.filter((x) => x.id !== d.id);
    refreshNav();
  }
}

// 草稿列表随写邮件窗口的自动保存变化
onMounted(load);
watch(() => store.nav?.drafts, load);
</script>

<template>
  <div class="gm-card gm-page">
    <div class="gm-toolbar">
      <div class="gm-toolbar-start"><span class="gm-toolbar-title">草稿</span></div>
      <div class="gm-toolbar-end">
        <span v-if="drafts?.length" class="gm-pager-info">共 {{ drafts.length }} 封</span>
      </div>
    </div>
    <div class="gm-banner">写邮件时会自动保存草稿（不含附件），发送后自动删除。</div>
    <div class="gm-page-list" role="list">
      <NSpin v-if="!drafts" class="gm-page-loading" />
      <template v-else>
        <SimpleRow
          v-for="d in drafts"
          :key="d.id"
          :name="d.to_name"
          :sender-title="d.to"
          :subject="d.subject"
          :snippet="d.snippet"
          :date="d.updated_at"
          delete-title="舍弃草稿"
          @open="openCompose({ draft: d.id })"
          @delete="remove(d)"
        >
          <template #sender>
            <span class="gm-draft-label">草稿</span> {{ d.to_short }}
          </template>
          <template v-if="multiAccount && navAccount(d.account_id)" #chips>
            <span class="gm-chip is-account" :style="{ '--label': navAccount(d.account_id).color }" :title="navAccount(d.account_id).email">
              {{ navAccount(d.account_id).name }}
            </span>
          </template>
        </SimpleRow>
        <div v-if="!drafts.length" class="gm-empty">
          <div class="gm-empty-title">没有草稿</div>
          <div>写邮件时内容会自动保存到这里</div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.gm-page { display: flex; flex-direction: column; overflow: hidden; }
.gm-page-list { flex: 1; overflow-y: auto; min-height: 0; border-top: 1px solid var(--gm-divider); }
.gm-page-loading { display: flex; justify-content: center; padding: 64px 0; }
.gm-draft-label { color: var(--gm-error); }
@media (max-width: 768px) {
    .gm-page { border-radius: 0; overflow: visible; }
    .gm-page-list { overflow: visible; }
}
</style>
