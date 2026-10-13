<script setup>
import { computed, onMounted, ref } from "vue";
import { NButton, NCheckbox, NForm, NFormItem, NInput, NModal, NSelect, NSpin, NSwitch, NTag } from "naive-ui";
import SettingsHeader from "../../components/SettingsHeader.vue";
import { get, post } from "../../api";
import { store } from "../../store";
import { confirm, run, ui } from "../../ui";
import { refreshNav } from "../../composables/common";

const FIELD_NAMES = { from: "发件人", to: "收件人（含抄送）", subject: "主题", any: "发件人 / 收件人 / 主题" };
const fieldOptions = Object.entries(FIELD_NAMES).map(([value, label]) => ({ value, label }));

const items = ref(null);
const editing = ref(null);
const saving = ref(false);

const accountOptions = computed(() => [
  { value: null, label: "所有账号" },
  ...(store.nav?.accounts || []).map((a) => ({ value: a.id, label: a.email })),
]);

async function load() {
  const data = await run(get("/api/rules"));
  if (data) items.value = data.items;
}
onMounted(load);

function actionsText(r) {
  const parts = [];
  if (r.mark_read) parts.push("标为已读");
  if (r.star) parts.push("加星标");
  if (r.label_name) parts.push(`加标签「${r.label_name}」`);
  if (r.delete) parts.push("删除");
  return parts.join("、");
}

function edit(r) {
  editing.value = r
    ? { ...r }
    : { id: null, name: "", account_id: null, field: "from", contains: "", mark_read: false, star: false, label_name: "", delete: false, enabled: true };
}

function bodyOf(r) {
  const { name, account_id, field, contains, mark_read, star, label_name, delete: del, enabled } = r;
  return { name, account_id, field, contains, mark_read, star, label_name, delete: del, enabled };
}

async function save() {
  saving.value = true;
  const r = editing.value;
  const url = r.id ? `/api/rules/${r.id}/update` : "/api/rules";
  const result = await run(post(url, bodyOf(r)), "已保存，之后收到的新邮件会自动执行");
  saving.value = false;
  if (result) {
    editing.value = null;
    load();
  }
}

async function toggle(r, enabled) {
  if (await run(post(`/api/rules/${r.id}/update`, { ...bodyOf(r), enabled }))) r.enabled = enabled;
}

async function remove(r) {
  if (!(await confirm(`确定删除规则「${r.name || r.contains}」吗？`, { danger: true, positiveText: "删除" }))) return;
  if (await run(post(`/api/rules/${r.id}/delete`), "已删除")) load();
}

async function applyExisting(r) {
  const text = r.delete
    ? `对已有邮件执行「${r.name || r.contains}」？\n收件箱和垃圾邮件中所有匹配的邮件都会被删除（移到 Gmail 的已删除邮件）。`
    : `对已有邮件执行「${r.name || r.contains}」？收件箱和垃圾邮件中所有匹配的邮件都会执行这些动作。`;
  if (!(await confirm(text, { danger: r.delete, positiveText: "执行" }))) return;
  const result = await run(post(`/api/rules/${r.id}/apply`));
  if (result) {
    ui.message.success(`已对 ${result.affected} 封邮件执行`);
    load();
    refreshNav();
    store.listVersion += 1;
  }
}
</script>

<template>
  <div class="gm-panel gm-settings-page">
    <SettingsHeader />
    <div class="gm-settings">
      <section class="gm-settings-row">
        <div class="gm-settings-label">
          自动规则：
          <div class="gm-settings-hint">
            收到新邮件时，按规则自动标为已读、加星标、加标签或删除，同时同步到 Gmail。只对之后收到的新邮件生效；已有的邮件可以点「对已有邮件执行」。
          </div>
        </div>
        <div class="gm-settings-content">
          <NSpin v-if="!items" />
          <template v-else>
            <div v-for="r in items" :key="r.id" class="gm-rule" :class="{ 'is-off': !r.enabled }">
              <NSwitch :value="r.enabled" size="small" @update:value="(v) => toggle(r, v)" />
              <div class="gm-rule-main">
                <div class="gm-rule-name">{{ r.name || `${FIELD_NAMES[r.field]}包含「${r.contains}」` }}</div>
                <div class="gm-rule-desc">
                  当{{ FIELD_NAMES[r.field] }}包含「{{ r.contains }}」时：{{ actionsText(r) }}
                </div>
                <div class="gm-rule-meta">
                  <NTag size="small" :bordered="false">{{ r.account_email || "所有账号" }}</NTag>
                  <span>已命中 {{ r.hits }} 封</span>
                </div>
              </div>
              <div class="gm-rule-actions">
                <NButton text type="primary" @click="applyExisting(r)">对已有邮件执行</NButton>
                <NButton text type="primary" @click="edit(r)">编辑</NButton>
                <NButton text type="error" @click="remove(r)">删除</NButton>
              </div>
            </div>
            <div v-if="!items.length" class="gm-settings-empty">还没有规则。</div>
            <NButton type="primary" style="margin-top: 12px" @click="edit(null)">新建规则</NButton>
          </template>
        </div>
      </section>
    </div>

    <NModal
      :show="!!editing"
      preset="card"
      :title="editing?.id ? '编辑规则' : '新建规则'"
      :bordered="false"
      style="width: min(520px, calc(100vw - 32px))"
      @update:show="(v) => !v && (editing = null)"
    >
      <NForm v-if="editing" label-placement="left" label-width="80">
        <NFormItem label="名称">
          <NInput v-model:value="editing.name" placeholder="可选，便于识别" maxlength="120" />
        </NFormItem>
        <NFormItem label="适用账号">
          <NSelect v-model:value="editing.account_id" :options="accountOptions" />
        </NFormItem>
        <NFormItem label="当">
          <div class="gm-rule-cond">
            <NSelect v-model:value="editing.field" :options="fieldOptions" style="width: 190px" />
            <span>包含</span>
            <NInput v-model:value="editing.contains" placeholder="如 newsletter@ 或 【广告】" />
          </div>
        </NFormItem>
        <NFormItem label="执行">
          <div class="gm-rule-acts">
            <NCheckbox v-model:checked="editing.mark_read">标为已读</NCheckbox>
            <NCheckbox v-model:checked="editing.star">加星标</NCheckbox>
            <div class="gm-rule-label">
              <span>加标签</span>
              <NInput v-model:value="editing.label_name" size="small" placeholder="标签名，不存在时自动创建" />
            </div>
            <NCheckbox v-model:checked="editing.delete">删除（移到 Gmail 的已删除邮件）</NCheckbox>
          </div>
        </NFormItem>
        <NFormItem label="启用">
          <NSwitch v-model:value="editing.enabled" />
        </NFormItem>
      </NForm>
      <template #footer>
        <div class="gm-rule-footer">
          <NButton @click="editing = null">取消</NButton>
          <NButton type="primary" :loading="saving" :disabled="!editing?.contains.trim()" @click="save">保存</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.gm-settings-page { overflow-y: auto; background: var(--gm-bg); }
.gm-rule { display: flex; align-items: flex-start; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--gm-divider); }
.gm-rule.is-off .gm-rule-main { opacity: .55; }
.gm-rule-main { flex: 1; min-width: 0; }
.gm-rule-name { font-weight: 600; }
.gm-rule-desc { color: var(--gm-text-2); font-size: 13px; margin-top: 2px; word-break: break-all; }
.gm-rule-meta { display: flex; align-items: center; gap: 8px; margin-top: 6px; font-size: 12px; color: var(--gm-text-3); }
.gm-rule-actions { display: flex; flex-wrap: wrap; gap: 12px; flex: none; }
.gm-rule-cond { display: flex; align-items: center; gap: 8px; width: 100%; }
.gm-rule-acts { display: flex; flex-direction: column; gap: 8px; width: 100%; }
.gm-rule-label { display: flex; align-items: center; gap: 8px; }
.gm-rule-label span { white-space: nowrap; }
.gm-rule-footer { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 768px) {
    .gm-rule { flex-wrap: wrap; }
    .gm-rule-actions { width: 100%; justify-content: flex-end; }
    .gm-rule-cond { flex-wrap: wrap; }
}
</style>
