<script setup>
// 草稿：写邮件时自动保存（含附件），点开继续编辑；发送后自动删除。
import { computed, watch } from "vue";
import { NIcon } from "naive-ui";
import { AttachFileOutlined, DeleteOutlined, DraftsOutlined } from "@vicons/material";
import GmPage from "../ui/GmPage.vue";
import GmListRow from "../ui/GmListRow.vue";
import GmAccountChip from "../ui/GmAccountChip.vue";
import { get, post } from "../api";
import { openCompose, store } from "../store";
import { confirm, run } from "../ui";
import { refreshNav } from "../composables/common";
import { useResource } from "../composables/useResource";

const res = useResource(async () => (await get("/api/drafts")).items);
const items = computed(() => res.data.value || []);
const ACTIONS = [{ key: "delete", label: "舍弃草稿", icon: DeleteOutlined, danger: true }];

// 写邮件窗口自动保存时草稿数会变化：刷新
watch(() => store.nav?.drafts, () => res.reload({ silent: true }));

async function onAction(d, key) {
  if (key !== "delete") return;
  if (!(await confirm("舍弃这封草稿？草稿里的附件也会删除。", { danger: true, positiveText: "舍弃" }))) return;
  if (await run(post(`/api/drafts/${d.id}/delete`), "已舍弃草稿")) {
    res.data.value = items.value.filter((x) => x.id !== d.id);
    refreshNav();
  }
}
</script>

<template>
  <GmPage
    title="草稿"
    :count="items.length ? `${items.length} 封` : ''"
    :loading="res.loading.value"
    :ready="!!res.data.value"
    :error="res.error.value"
    :empty="!!res.data.value && !items.length"
    empty-title="没有草稿"
    empty-hint="写邮件时内容和附件会自动保存到这里"
    :empty-icon="DraftsOutlined"
    @retry="res.reload()"
  >
    <div role="list">
      <GmListRow
        v-for="d in items"
        :key="d.id"
        :avatar="d.to_name || '草稿'"
        :title-attr="d.to"
        :subject="d.subject"
        :snippet="d.snippet"
        :date="d.updated_at"
        :actions="ACTIONS"
        @open="openCompose({ draft: d.id })"
        @action="(k) => onAction(d, k)"
      >
        <template #title><span class="gm-draft-tag">草稿</span>{{ d.to_short || "（未填收件人）" }}</template>
        <template #chips>
          <span v-if="d.attachment_count" class="gm-draft-att" :title="`${d.attachment_count} 个附件`">
            <NIcon :component="AttachFileOutlined" :size="14" />{{ d.attachment_count }}
          </span>
          <GmAccountChip :account-id="d.account_id" />
        </template>
      </GmListRow>
    </div>
  </GmPage>
</template>

<style scoped>
.gm-draft-tag { margin-right: 6px; color: var(--gm-error); }
.gm-draft-att { display: inline-flex; align-items: center; gap: 2px; font-size: var(--fs-xs); color: var(--gm-text-3); }
</style>
