<script setup>
// 签名：每个账号一份，写邮件时自动添加（新邮件在末尾，回复 / 转发在引用原文上方）；切换发件账号时自动替换。
import { computed, ref, watch } from "vue";
import { NButton, NSelect } from "naive-ui";
import RichEditor from "../../components/RichEditor.vue";
import GmEmptyState from "../../ui/GmEmptyState.vue";
import { get, post } from "../../api";
import { run } from "../../ui";
import { useResource } from "../../composables/useResource";

const res = useResource(() => get("/api/accounts"));
const accounts = computed(() => res.data.value?.accounts || []);
const accountId = ref(null);
const editor = ref(null);
const editorKey = ref(0);
const saving = ref(false);

watch(accounts, (list) => {
  if (!accountId.value && list.length) accountId.value = list[0].id;
});
watch(accountId, () => (editorKey.value += 1));

const current = computed(() => accounts.value.find((a) => a.id === accountId.value) || null);
const options = computed(() =>
  accounts.value.map((a) => ({ value: a.id, label: `${a.display_name ? `${a.display_name} <${a.email}>` : a.email}${a.signature_html ? "" : "（未设置）"}` })),
);

async function save() {
  const a = current.value;
  if (!a) return;
  saving.value = true;
  const html = editor.value?.getInnerHtml() || "";
  const result = await run(post(`/api/accounts/${a.id}/update`, { signature_html: html }), html ? "签名已保存" : "已清除签名");
  saving.value = false;
  if (result) a.signature_html = html;
}

async function clear() {
  editor.value?.setText("");
  await save();
}
</script>

<template>
  <div class="gm-settings">
    <GmEmptyState v-if="res.error.value" tone="error" title="账号加载失败" :hint="res.error.value" action-text="重试" @action="res.reload()" />
    <GmEmptyState v-else-if="res.data.value && !accounts.length" title="还没有邮箱账号" hint="添加账号后才能设置签名" />
    <section v-else-if="current" class="gm-settings-row">
      <div class="gm-settings-label">
        签名
        <div class="gm-settings-hint">写邮件时自动添加：新邮件在正文末尾，回复 / 转发在引用原文上方。切换发件账号时自动换成对应账号的签名。</div>
      </div>
      <div class="gm-settings-content">
        <NSelect v-model:value="accountId" :options="options" class="gm-sg-account" filterable />
        <div class="gm-sg-editor">
          <RichEditor :key="editorKey" ref="editor" :initial-html="current.signature_html" show-format-bar placeholder="例如：姓名 / 职位 / 电话" />
        </div>
        <div class="gm-settings-actions">
          <NButton type="primary" :loading="saving" @click="save">保存签名</NButton>
          <NButton v-if="current.signature_html" quaternary @click="clear">清除签名</NButton>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.gm-sg-account { max-width: 420px; margin-bottom: var(--sp-3); }
.gm-sg-editor {
    display: flex;
    flex-direction: column;
    min-height: 220px;
    padding: 0 var(--sp-3);
    border: 1px solid var(--gm-border);
    border-radius: var(--r-md);
    background: var(--gm-surface);
}
</style>
