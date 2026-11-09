<script setup>
// 一个写邮件窗口（可以同时开几个，由 ComposeDock 排列）：电脑端在右下角，可最小化为标签、可全屏；手机端全屏。
// 表单 / 草稿与附件 / 发送 分别在 useComposeForm、useDraft、useSend。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { NAlert, NButton, NButtonGroup, NCheckbox, NDropdown, NIcon, NPopover, NProgress, NSelect, NSpin } from "naive-ui";
import {
  ArrowDropDownOutlined,
  AttachFileOutlined,
  CloseFullscreenOutlined,
  CloseOutlined,
  DeleteOutlined,
  MinimizeOutlined,
  MoreVertOutlined,
  OpenInFullOutlined,
  RefreshOutlined,
  TextFormatOutlined,
  TextSnippetOutlined,
  UploadFileOutlined,
  WarningAmberOutlined,
} from "@vicons/material";
import AddressInput from "../components/AddressInput.vue";
import RichEditor from "../components/RichEditor.vue";
import TimePicker from "../components/TimePicker.vue";
import GmIconButton from "../ui/GmIconButton.vue";
import FileIcon from "../ui/FileIcon.vue";
import { get, post } from "../api";
import { closeCompose, focusCompose, store } from "../store";
import { confirm, prompt, toast } from "../ui";
import { refreshNav } from "../composables/common";
import { fileSize } from "../format";
import { useComposeForm } from "./useComposeForm";
import { useDraft } from "./useDraft";
import { useSend } from "./useSend";

const props = defineProps({
  entry: { type: Object, required: true },
  mobile: { type: Boolean, default: false },
});

const router = useRouter();
const MAX_TOTAL = 25 * 1024 * 1024;

const editor = ref(null);
const toInput = ref(null);
const ccInput = ref(null);
const bccInput = ref(null);
const fileInput = ref(null);
const error = ref("");
const showFormat = ref(false);
const dragging = ref(0);

const f = useComposeForm(props.entry.params, { editor, onChange: () => draft.markDirty() });
const { form, prefill } = f;
const draft = useDraft({
  entry: props.entry,
  form,
  prefill,
  bodyFields: f.bodyFields,
  addressFields: f.addressFields,
  canSave: () => sender.phase.value === "ready",
});
const sender = useSend({
  entry: props.entry,
  form,
  prefill,
  draft,
  addressFields: f.addressFields,
  bodyFields: f.bodyFields,
  inputs: [toInput, ccInput, bccInput],
  error,
});

const state = computed(() => props.entry.state);
const fullScreen = computed(() => props.mobile || state.value === "max");
const totalSize = computed(() => draft.attachments.value.reduce((n, a) => n + (a.size || 0), 0));

watch(f.title, (t) => (props.entry.title = t), { immediate: true });

onMounted(async () => {
  await f.load();
  draft.restoreAttachments(prefill.value.attachments);
  await nextTick();
  if (!prefill.value.is_reply || prefill.value.is_forward) toInput.value?.focus();
});

// ---- 附件（拖放） ----
function onFilePicked(ev) {
  draft.addFiles(ev.target.files);
  ev.target.value = "";
}
const hasFiles = (ev) => Array.from(ev.dataTransfer?.types || []).includes("Files");
function onDragEnter(ev) {
  if (!hasFiles(ev)) return;
  ev.preventDefault();
  dragging.value += 1;
}
function onDragLeave() {
  dragging.value = Math.max(0, dragging.value - 1);
}
function onDrop(ev) {
  dragging.value = 0;
  if (!hasFiles(ev) || ev.defaultPrevented) return;   // 图片拖进正文时由编辑器插入
  ev.preventDefault();
  draft.addFiles(ev.dataTransfer.files);
}

// ---- 模板（快捷回复） ----
const templates = ref([]);
async function loadTemplates() {
  try {
    templates.value = (await get("/api/templates")).items;
  } catch {
    /* 菜单里仍可保存、管理 */
  }
}
const templateMenu = computed(() => {
  const items = templates.value.map((t) => ({ key: `t-${t.id}`, label: t.name }));
  if (!items.length) items.push({ key: "none", label: "还没有模板", disabled: true });
  return [
    { type: "group", key: "g", label: "插入模板", children: items },
    { type: "divider", key: "d" },
    { key: "save", label: "将当前正文保存为模板…" },
    { key: "manage", label: "管理模板" },
  ];
});

function htmlToText(html) {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div.innerText;
}

function onTemplateMenu(key) {
  if (key === "save") return saveAsTemplate();
  if (key === "manage") return router.push("/settings/templates");
  const t = templates.value.find((x) => `t-${x.id}` === key);
  if (!t) return;
  if (form.plain) form.plainText = `${form.plainText}${form.plainText ? "\n" : ""}${htmlToText(t.body_html)}`;
  else editor.value?.insertHtml(t.body_html);
  draft.markDirty();
}

async function saveAsTemplate() {
  const body = form.plain
    ? form.plainText.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/\n/g, "<br>")
    : editor.value?.getInnerHtml() || "";
  if (!body.trim()) {
    toast("正文是空的，先写点内容再保存为模板", { tone: "error" });
    return;
  }
  const name = await prompt({
    title: "保存为模板",
    label: "模板名称",
    value: form.subject.trim().slice(0, 60),
    positiveText: "保存",
    validate: (v) => (v.trim() ? "" : "请填写模板名称"),
  });
  if (!name) return;
  try {
    await post("/api/templates", { name, body_html: body });
    toast("已保存为模板");
  } catch (err) {
    toast(err.message || "保存失败", { tone: "error" });
  }
}

// 纯文本模式：正文切换为普通文本框，只发送纯文本
function togglePlain(value) {
  if (value) {
    form.plainText = (editor.value?.getText() || "").replace(/\n{3,}/g, "\n\n");
    showFormat.value = false;
  }
  form.plain = value;
  if (!value) nextTick(() => editor.value?.setText(form.plainText));
  draft.markDirty();
}

function onKeydown(ev) {
  if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
    ev.preventDefault();
    sender.send();
  }
}

// ---- 窗口 ----
async function close() {
  const ok = await draft.save({ keepalive: true });
  if (!ok && !(await confirm("草稿保存失败，仍要关闭吗？未保存的内容会丢失。", { danger: true, positiveText: "关闭" }))) return;
  draft.clearUnsaved();
  closeCompose(props.entry.key);
}

async function discard() {
  const has = draft.isDirty() || form.draftId || draft.attachments.value.length;
  if (has && !(await confirm("舍弃这封草稿？已填写的内容和附件将被删除。", { danger: true, positiveText: "舍弃" }))) return;
  draft.stop();
  draft.attachments.value.forEach((a) => a.abort?.());
  draft.clearUnsaved();
  const id = form.draftId;
  closeCompose(props.entry.key);
  if (id) {
    try {
      await post(`/api/drafts/${id}/delete`, {}, { keepalive: true });
    } catch {
      /* 留在草稿列表里，用户可以手动删除 */
    }
    refreshNav();
  }
}

function toggleMin() {
  if (state.value === "min") focusCompose(props.entry.key);
  else props.entry.state = "min";
}
function toggleMax() {
  if (state.value === "max") props.entry.state = "normal";
  else {
    focusCompose(props.entry.key);
    props.entry.state = "max";
  }
}

// 页面被关闭时尽量把最后的修改存进草稿（keepalive 请求在页面卸载后仍会发出）
function onPageHide() {
  if (draft.isUnsaved()) draft.save({ keepalive: true });
}
window.addEventListener("pagehide", onPageHide);
onBeforeUnmount(() => {
  window.removeEventListener("pagehide", onPageHide);
  draft.stop();
  sender.dispose();
});

defineExpose({ close });
</script>

<template>
  <section
    v-show="!entry.pending"
    class="gm-cw"
    :class="{ 'is-min': state === 'min' && !mobile, 'is-max': fullScreen, 'is-mobile': mobile, 'is-active': store.composeActive === entry.key }"
    role="dialog"
    :aria-label="f.title.value"
    @keydown="onKeydown"
    @focusin="store.composeActive = entry.key"
    @dragenter="onDragEnter"
    @dragover="(e) => hasFiles(e) && e.preventDefault()"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <header class="gm-cw-head" @click="state === 'min' && toggleMin()">
      <span class="gm-cw-title">{{ f.title.value }}</span>
      <span v-if="state === 'min' && entry.busy" class="gm-cw-dot" title="有尚未保存的修改" />
      <span class="gm-cw-head-actions" @click.stop>
        <template v-if="!mobile">
          <GmIconButton :icon="MinimizeOutlined" :title="state === 'min' ? '展开' : '最小化'" :size="30" :icon-size="18" @click="toggleMin" />
          <GmIconButton
            :icon="state === 'max' ? CloseFullscreenOutlined : OpenInFullOutlined"
            :title="state === 'max' ? '退出全屏' : '全屏'"
            :size="30"
            :icon-size="18"
            @click="toggleMax"
          />
        </template>
        <GmIconButton :icon="CloseOutlined" title="保存草稿并关闭" :size="30" :icon-size="18" @click="close" />
      </span>
    </header>

    <div v-show="state !== 'min' || mobile" class="gm-cw-body">
      <NSpin v-if="f.loading.value" class="gm-cw-loading" />
      <div v-else-if="f.loadError.value" class="gm-cw-empty">{{ f.loadError.value }}</div>
      <div v-else-if="!f.accounts.value.length" class="gm-cw-empty">
        还没有可用账号，请先到 <RouterLink to="/settings/accounts" @click="closeCompose(entry.key)">设置</RouterLink> 添加并启用。
      </div>
      <template v-else>
        <NAlert v-if="error" type="error" class="gm-cw-error" closable @close="error = ''">{{ error }}</NAlert>

        <div class="gm-cw-field">
          <label class="gm-cw-label">发件人</label>
          <NSelect
            v-model:value="form.accountId"
            :options="f.accountOptions.value"
            size="small"
            :bordered="false"
            :consistent-menu-width="false"
            class="gm-cw-from"
            @update:value="f.onAccountChange"
          />
        </div>
        <div v-if="f.currentAccount.value && !f.currentAccount.value.display_name" class="gm-cw-hint">
          <NIcon :component="WarningAmberOutlined" :size="16" />
          <span>未设置发件人姓名，收件人只会看到邮箱地址。</span>
          <NButton
            v-if="f.suggestedName.value"
            text
            type="primary"
            size="small"
            :loading="f.savingName.value"
            @click="f.useSuggestedName"
          >使用「{{ f.suggestedName.value }}」</NButton>
          <RouterLink v-else to="/settings/accounts" @click="close">去设置</RouterLink>
        </div>
        <div class="gm-cw-field">
          <label class="gm-cw-label">收件人</label>
          <AddressInput ref="toInput" v-model:value="form.to" :contacts="f.contacts.value" label="收件人" @change="draft.markDirty" />
          <span class="gm-cw-extras">
            <button v-if="!f.showCc.value" type="button" class="gm-cw-link" @click="f.showCc.value = true; nextTick(() => ccInput?.focus())">抄送</button>
            <button v-if="!f.showBcc.value" type="button" class="gm-cw-link" @click="f.showBcc.value = true; nextTick(() => bccInput?.focus())">密送</button>
          </span>
        </div>
        <div v-if="f.showCc.value" class="gm-cw-field">
          <label class="gm-cw-label">抄送</label>
          <AddressInput ref="ccInput" v-model:value="form.cc" :contacts="f.contacts.value" label="抄送" @change="draft.markDirty" />
        </div>
        <div v-if="f.showBcc.value" class="gm-cw-field">
          <label class="gm-cw-label">密送</label>
          <AddressInput ref="bccInput" v-model:value="form.bcc" :contacts="f.contacts.value" label="密送" @change="draft.markDirty" />
        </div>
        <div class="gm-cw-field">
          <input v-model="form.subject" class="gm-cw-subject" type="text" placeholder="主题" aria-label="主题" @input="draft.markDirty" />
        </div>

        <div class="gm-cw-content">
          <RichEditor
            v-if="!form.plain"
            ref="editor"
            :initial-html="prefill.body_html || ''"
            :initial-text="prefill.body_text || ''"
            :show-format-bar="showFormat"
            :focus-start="!!prefill.is_reply && !prefill.is_forward"
            :collapsible-quote="!fullScreen"
            placeholder="正文（可以直接粘贴或拖入图片）"
            @change="draft.markDirty"
          />
          <textarea v-else v-model="form.plainText" class="gm-cw-plain" aria-label="正文（纯文本）" @input="draft.markDirty" />

          <ul v-if="(prefill.forward_attachments || []).length || draft.attachments.value.length" class="gm-cw-atts">
            <li
              v-for="name in prefill.forward_attachments || []"
              :key="'fwd-' + name"
              class="gm-cw-att is-fixed"
              title="转发的原邮件附件，将随邮件一起发送"
            >
              <NIcon :component="AttachFileOutlined" :size="16" />
              <span class="gm-cw-att-name">{{ name }}</span>
              <span class="gm-cw-att-meta">转发附件</span>
            </li>
            <li v-for="a in draft.attachments.value" :key="a.key" class="gm-cw-att" :class="`is-${a.state}`" :title="a.error || a.name">
              <FileIcon :mime="a.file?.type || ''" :size="16" />
              <span class="gm-cw-att-name">{{ a.name }}</span>
              <span v-if="a.state === 'uploading'" class="gm-cw-att-progress">
                <NProgress type="line" :percentage="Math.round(a.progress * 100)" :show-indicator="false" :height="4" />
              </span>
              <span v-else-if="a.state === 'error'" class="gm-cw-att-meta is-error">{{ a.error }}</span>
              <span v-else class="gm-cw-att-meta">{{ fileSize(a.size) }}</span>
              <button v-if="a.state === 'error'" type="button" class="gm-cw-att-btn" :aria-label="`重新上传 ${a.name}`" @click="draft.retryAttachment(a)">
                <NIcon :component="RefreshOutlined" :size="14" />
              </button>
              <button type="button" class="gm-cw-att-btn" :aria-label="`移除附件 ${a.name}`" @click="draft.removeAttachment(a)">
                <NIcon :component="CloseOutlined" :size="14" />
              </button>
            </li>
          </ul>
          <div v-if="totalSize > MAX_TOTAL" class="gm-cw-warning">
            <NIcon :component="WarningAmberOutlined" :size="16" /> 附件总大小超过 25 MB，邮件服务器可能拒收。
          </div>
        </div>

        <footer class="gm-cw-foot">
          <NButtonGroup class="gm-cw-send">
            <NButton type="primary" :disabled="sender.phase.value !== 'ready'" title="发送（Ctrl+Enter）" @click="sender.send">发送</NButton>
            <NDropdown trigger="click" placement="top-start" :options="[{ key: 'schedule', label: '定时发送…' }]" @select="sender.openSchedule">
              <NButton type="primary" :disabled="sender.phase.value !== 'ready'" title="定时发送" class="gm-cw-send-more">
                <template #icon><NIcon :component="ArrowDropDownOutlined" /></template>
              </NButton>
            </NDropdown>
          </NButtonGroup>
          <GmIconButton :icon="TextFormatOutlined" title="格式工具栏" :disabled="form.plain" :active="showFormat" @click="showFormat = !showFormat" />
          <GmIconButton :icon="AttachFileOutlined" title="添加附件（也可以直接拖放文件）" @click="fileInput.click()" />
          <input ref="fileInput" type="file" multiple hidden @change="onFilePicked" />
          <NDropdown
            trigger="click"
            placement="top-start"
            :options="templateMenu"
            scrollable
            style="max-height: 360px"
            @update:show="(v) => v && loadTemplates()"
            @select="onTemplateMenu"
          >
            <GmIconButton :icon="TextSnippetOutlined" title="模板（快捷回复）" />
          </NDropdown>
          <NPopover trigger="click" placement="top-start">
            <template #trigger>
              <GmIconButton :icon="MoreVertOutlined" title="更多选项" />
            </template>
            <div class="gm-cw-more">
              <NCheckbox :checked="form.plain" @update:checked="togglePlain">纯文本模式</NCheckbox>
            </div>
          </NPopover>
          <span class="gm-cw-spacer" />
          <span class="gm-cw-status" aria-live="polite">{{ draft.status.value }}</span>
          <GmIconButton :icon="DeleteOutlined" title="舍弃草稿" danger @click="discard" />
        </footer>
        <TimePicker
          v-model:show="sender.scheduleOpen.value"
          title="定时发送"
          confirm-text="安排发送"
          @pick="sender.scheduleAt"
        />

        <div v-if="dragging" class="gm-cw-drop">
          <NIcon :component="UploadFileOutlined" :size="40" />
          <span>拖放到此处添加附件（图片拖进正文可直接插入）</span>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.gm-cw {
    display: flex;
    flex-direction: column;
    width: 560px;
    height: min(640px, calc(100vh - 80px));
    background: var(--gm-surface);
    border: 1px solid var(--gm-border);
    border-bottom: 0;
    border-radius: var(--r-lg) var(--r-lg) 0 0;
    box-shadow: var(--gm-shadow-3);
    overflow: hidden;
    pointer-events: auto;
}
.gm-cw.is-min { width: 260px; height: auto; }
.gm-cw.is-max {
    position: fixed;
    left: 50%;
    top: 50%;
    z-index: 1100;
    transform: translate(-50%, -50%);
    width: min(1100px, calc(100vw - 96px));
    height: calc(100vh - 96px);
    border-bottom: 1px solid var(--gm-border);
    border-radius: var(--r-lg);
}
.gm-cw.is-mobile {
    position: fixed;
    inset: 0;
    transform: none;
    width: 100%;
    height: 100%;
    border: 0;
    border-radius: 0;
}

.gm-cw-head {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    height: 44px;
    padding: 0 var(--sp-2) 0 var(--sp-4);
    background: var(--gm-surface-2);
    border-bottom: 1px solid var(--gm-divider);
    flex: none;
}
.gm-cw.is-active:not(.is-min) .gm-cw-head { background: color-mix(in srgb, var(--gm-primary) 8%, var(--gm-surface-2)); }
.gm-cw.is-min .gm-cw-head { cursor: pointer; border-bottom: 0; }
.gm-cw-title { flex: 1; min-width: 0; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-cw-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--gm-warning); flex: none; }
.gm-cw-head-actions { display: flex; gap: 2px; }

.gm-cw-body { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 var(--sp-4); }
.gm-cw-loading { margin: auto; }
.gm-cw-empty { padding: var(--sp-7) 0; color: var(--gm-text-3); text-align: center; }
.gm-cw-error { margin-top: var(--sp-2); }

.gm-cw-field { display: flex; align-items: center; gap: var(--sp-2); min-height: 42px; border-bottom: 1px solid var(--gm-divider); }
.gm-cw-label { flex: none; min-width: 42px; color: var(--gm-text-3); }
.gm-cw-from { flex: 1; }
.gm-cw-hint {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 6px;
    margin: 6px 0;
    padding: 6px 10px;
    border-radius: var(--r-md);
    background: var(--gm-warning-soft);
    font-size: var(--fs-xs);
    color: var(--gm-text-2);
}
.gm-cw-hint > .n-icon { color: var(--gm-warning); }
.gm-cw-extras { display: flex; gap: var(--sp-2); flex: none; }
.gm-cw-link { border: 0; background: none; color: var(--gm-text-3); cursor: pointer; padding: 4px; font: inherit; }
.gm-cw-link:hover { color: var(--gm-primary); }
.gm-cw-subject { flex: 1; border: 0; outline: 0; padding: 8px 0; font: inherit; color: var(--gm-text); background: transparent; }
.gm-cw-subject::placeholder { color: var(--gm-text-4); }

.gm-cw-content { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow-y: auto; }
.gm-cw-plain {
    flex: 1;
    min-height: 160px;
    padding: 10px 0;
    border: 0;
    outline: 0;
    resize: none;
    font: 14px/1.6 var(--gm-font);
    color: var(--gm-text);
    background: transparent;
}
.gm-cw-atts { display: flex; flex-direction: column; gap: 6px; margin: 0; padding: var(--sp-2) 0; list-style: none; }
.gm-cw-att {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    max-width: 460px;
    padding: 6px 8px;
    border: 1px solid var(--gm-border);
    border-radius: var(--r-md);
    background: var(--gm-surface-2);
    font-size: var(--fs-sm);
}
.gm-cw-att.is-error { border-color: var(--gm-error); background: var(--gm-error-soft); }
.gm-cw-att-name { flex: 1; min-width: 0; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-cw-att-meta { color: var(--gm-text-3); white-space: nowrap; }
.gm-cw-att-meta.is-error { color: var(--gm-error); max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
.gm-cw-att-progress { width: 90px; }
.gm-cw-att-btn { display: flex; padding: 3px; border: 0; border-radius: 50%; background: none; color: var(--gm-text-3); cursor: pointer; }
.gm-cw-att-btn:hover { background: var(--gm-hover); color: var(--gm-error); }
.gm-cw-warning { display: flex; align-items: center; gap: 6px; padding: 4px 0 8px; color: var(--gm-warning); font-size: var(--fs-sm); }

.gm-cw-foot { display: flex; align-items: center; gap: 2px; padding: 10px 0; border-top: 1px solid var(--gm-divider); flex: none; }
.gm-cw-send { margin-right: var(--sp-2); }
.gm-cw-send-more { padding: 0 6px !important; }
.gm-cw-spacer { flex: 1; }
.gm-cw-status { margin-right: 4px; color: var(--gm-text-3); font-size: var(--fs-xs); white-space: nowrap; }
.gm-cw-more { display: flex; flex-direction: column; gap: 6px; min-width: 160px; }

.gm-cw-drop {
    position: absolute;
    inset: 0;
    z-index: 5;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--sp-2);
    border: 2px dashed var(--gm-primary);
    background: var(--gm-primary-soft);
    color: var(--gm-primary);
    font-weight: 600;
    text-align: center;
    pointer-events: none;
}
</style>
