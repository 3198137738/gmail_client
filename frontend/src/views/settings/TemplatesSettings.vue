<script setup>
import { onMounted, ref } from "vue";
import { NButton, NInput, NModal, NSpin } from "naive-ui";
import SettingsHeader from "../../components/SettingsHeader.vue";
import RichEditor from "../../components/RichEditor.vue";
import { get, post } from "../../api";
import { confirm, run } from "../../ui";
import { dateTime } from "../../format";

const items = ref(null);
// 正在编辑的模板：{ id（新建时为 null）, name, body_html, key }
const editing = ref(null);
const editor = ref(null);
const saving = ref(false);
let editSeq = 0;

async function load() {
  const data = await run(get("/api/templates"));
  if (data) items.value = data.items;
}
onMounted(load);

function edit(t) {
  editSeq += 1;
  editing.value = t
    ? { id: t.id, name: t.name, body_html: t.body_html, key: editSeq }
    : { id: null, name: "", body_html: "", key: editSeq };
}

async function save() {
  const body = { name: editing.value.name, body_html: editor.value?.getInnerHtml() || "" };
  saving.value = true;
  const url = editing.value.id ? `/api/templates/${editing.value.id}/update` : "/api/templates";
  const result = await run(post(url, body), "已保存");
  saving.value = false;
  if (result) {
    editing.value = null;
    load();
  }
}

async function remove(t) {
  if (!(await confirm(`确定删除模板「${t.name}」吗？`, { danger: true, positiveText: "删除" }))) return;
  if (await run(post(`/api/templates/${t.id}/delete`), "已删除")) load();
}

function preview(html) {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div.innerText.replace(/\s+/g, " ").trim();
}
</script>

<template>
  <div class="gm-panel gm-settings-page">
    <SettingsHeader />
    <div class="gm-settings">
      <section class="gm-settings-row">
        <div class="gm-settings-label">
          邮件模板：
          <div class="gm-settings-hint">保存常用的回复内容，写邮件时点工具栏的「模板」按钮一键插入；也可以在写邮件时把当前正文保存为模板。</div>
        </div>
        <div class="gm-settings-content">
          <NSpin v-if="!items" />
          <template v-else>
            <div v-for="t in items" :key="t.id" class="gm-tpl">
              <div class="gm-tpl-main">
                <div class="gm-tpl-name">{{ t.name }}</div>
                <div class="gm-tpl-preview">{{ preview(t.body_html) || "（空）" }}</div>
                <div class="gm-tpl-meta">更新于 {{ dateTime(t.updated_at) }}</div>
              </div>
              <div class="gm-tpl-actions">
                <NButton text type="primary" @click="edit(t)">编辑</NButton>
                <NButton text type="error" @click="remove(t)">删除</NButton>
              </div>
            </div>
            <div v-if="!items.length" class="gm-settings-empty">还没有模板。</div>
            <NButton type="primary" style="margin-top: 12px" @click="edit(null)">新建模板</NButton>
          </template>
        </div>
      </section>
    </div>

    <NModal
      :show="!!editing"
      preset="card"
      :title="editing?.id ? '编辑模板' : '新建模板'"
      :bordered="false"
      style="width: min(640px, calc(100vw - 32px))"
      @update:show="(v) => !v && (editing = null)"
    >
      <template v-if="editing">
        <NInput v-model:value="editing.name" placeholder="模板名称" maxlength="120" />
        <div class="gm-tpl-editor">
          <RichEditor :key="editing.key" ref="editor" :initial-html="editing.body_html" show-format-bar />
        </div>
      </template>
      <template #footer>
        <div class="gm-tpl-footer">
          <NButton @click="editing = null">取消</NButton>
          <NButton type="primary" :loading="saving" :disabled="!editing?.name.trim()" @click="save">保存</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.gm-settings-page { overflow-y: auto; background: var(--gm-bg); }
.gm-tpl { display: flex; align-items: flex-start; gap: 16px; padding: 10px 0; border-bottom: 1px solid var(--gm-divider); }
.gm-tpl-main { flex: 1; min-width: 0; }
.gm-tpl-name { font-weight: 600; }
.gm-tpl-preview { color: var(--gm-text-2); font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }
.gm-tpl-meta { color: var(--gm-text-3); font-size: 12px; margin-top: 2px; }
.gm-tpl-actions { display: flex; gap: 16px; flex: none; }
.gm-tpl-editor {
    display: flex;
    flex-direction: column;
    min-height: 220px;
    margin-top: 12px;
    padding: 0 12px;
    border: 1px solid var(--gm-border);
    border-radius: var(--gm-radius);
}
.gm-tpl-footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
