<script setup>
// 写邮件窗口（Gmail 式）：电脑端在右下角悬浮，可最小化 / 全屏；手机端全屏。
// - 停止输入 2 秒后自动保存草稿（不含附件），关闭窗口时立即保存；发送成功后服务端删除草稿。
// - 撤销发送：点发送后先隐藏窗口并提示「正在发送… 撤销」，几秒后才真正发送；撤销则恢复窗口。
import { computed, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  NAlert,
  NButton,
  NButtonGroup,
  NCheckbox,
  NDropdown,
  NIcon,
  NInput,
  NPopover,
  NRadio,
  NRadioGroup,
  NSelect,
  NSpin,
} from "naive-ui";
import {
  ArrowDropDownOutlined,
  AttachFileOutlined,
  CloseFullscreenOutlined,
  CloseOutlined,
  DeleteOutlined,
  LinkOutlined,
  TextSnippetOutlined,
  MinimizeOutlined,
  MoreVertOutlined,
  OpenInFullOutlined,
  TextFormatOutlined,
  UploadFileOutlined,
  WarningAmberOutlined,
} from "@vicons/material";
import AddressInput from "./AddressInput.vue";
import RichEditor from "./RichEditor.vue";
import IconBtn from "./IconBtn.vue";
import { api, get, post } from "../api";
import { formatAddress, isValidEmail, parseAddressList } from "../address";
import { closeCompose, store } from "../store";
import { confirm, toast, ui } from "../ui";
import { isMobile, refreshNav } from "../composables/common";
import { fileSize, futureDate } from "../format";
import TimePicker from "./TimePicker.vue";

const props = defineProps({
  params: { type: Object, default: () => ({}) },
});

const router = useRouter();
const MAX_TOTAL = 25 * 1024 * 1024;

const loading = ref(true);
const loadError = ref("");
const accounts = ref([]);
const contacts = ref([]);
const prefill = ref({});
const winState = ref("normal");   // normal / min / max
const phase = ref("ready");       // ready / pending（撤销等待期）/ sending
const error = ref("");
const draftStatus = ref("");
const showFormat = ref(false);
const showCc = ref(false);
const showBcc = ref(false);
const dragging = ref(0);

const form = reactive({
  accountId: null,
  to: [],
  cc: [],
  bcc: [],
  subject: "",
  plain: false,
  plainText: "",
  useSsl: "on",
  draftId: null,
});
const files = ref([]);

const editor = ref(null);
const toInput = ref(null);
const ccInput = ref(null);
const bccInput = ref(null);
const fileInput = ref(null);

const defaultTitle = computed(() => {
  const p = prefill.value;
  if (p.is_forward) return "转发";
  if (p.reply_all) return "回复全部";
  if (p.is_reply) return "回复";
  return "新邮件";
});
const title = computed(() => form.subject.trim() || defaultTitle.value);
const accountOptions = computed(() =>
  accounts.value.map((a) => ({ value: a.id, label: a.display_name ? `${a.display_name} <${a.email}>` : a.email })),
);
const totalSize = computed(() => files.value.reduce((n, f) => n + f.size, 0));

// ---- 初始化 ----
onMounted(async () => {
  try {
    const query = {};
    for (const [key, value] of Object.entries(props.params)) if (value) query[key] = value;
    const data = await get("/api/compose", query);
    accounts.value = data.accounts;
    contacts.value = data.contacts;
    prefill.value = data.prefill || {};
    const p = prefill.value;
    const ids = data.accounts.map((a) => a.id);
    form.accountId = ids.includes(p.account_id) ? p.account_id : ids[0] ?? null;
    form.to = parseAddressList(p.to);
    form.cc = parseAddressList(p.cc);
    form.bcc = parseAddressList(p.bcc);
    form.subject = p.subject || "";
    form.draftId = p.draft_id || null;
    showCc.value = form.cc.length > 0;
    showBcc.value = form.bcc.length > 0;
  } catch (err) {
    loadError.value = err.message;
  } finally {
    loading.value = false;
  }
  await nextTick();
  // 草稿里已经带着签名，不再重复添加
  if (!prefill.value.draft_id) applySignature(form.accountId, { initial: true });
  if (!prefill.value.is_reply || prefill.value.is_forward) toInput.value?.focus();
});

// ---- 签名 ----
// 签名放在 class="gm-signature" 的块里：新邮件在正文末尾，回复 / 转发在引用原文上方（与 Gmail 一致）；
// 切换发件账号时替换为新账号的签名，新账号没有签名就去掉。
function applySignature(accountId, { initial = false } = {}) {
  const el = editor.value?.editorEl();
  if (!el) return;
  const html = accounts.value.find((a) => a.id === accountId)?.signature_html || "";
  let block = el.querySelector(".gm-signature");
  if (!html) {
    if (block) {
      block.remove();
      editor.value.changed();
    }
    return;
  }
  if (!block) {
    block = document.createElement("div");
    block.className = "gm-signature";
    const quote = el.querySelector(".gmail_quote");
    if (quote) {
      el.insertBefore(block, quote);
    } else {
      // 新邮件：签名上方留出空行供输入
      if (!el.textContent.trim()) el.innerHTML = "<div><br></div><div><br></div>";
      el.appendChild(block);
    }
  }
  block.innerHTML = `<div>-- </div>${html}`;
  if (!initial) editor.value.changed();
}

function onAccountChange(accountId) {
  if (!form.plain) applySignature(accountId);
  markDirty();
}

// ---- 修改与草稿自动保存 ----
let dirty = false;      // 填写过内容（舍弃时需要确认）
let unsaved = false;    // 有尚未保存到草稿的修改
let saveTimer = null;
let savingPromise = null;

function markDirty() {
  dirty = true;
  unsaved = true;
  store.composeBusy = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveDraft(), 2000);
}

function bodyFields() {
  if (form.plain) return { body_text: form.plainText, body_html: "" };
  return { body_text: editor.value?.getText() || "", body_html: editor.value?.getHtml() || "" };
}

function addressFields() {
  return {
    to: form.to.map(formatAddress).join(", "),
    cc: form.cc.map(formatAddress).join(", "),
    bcc: form.bcc.map(formatAddress).join(", "),
  };
}

function saveDraft(keepalive = false) {
  clearTimeout(saveTimer);
  if (!unsaved || phase.value !== "ready" || !form.accountId) return savingPromise || Promise.resolve(true);
  // 上一次保存还没回来时排在它后面，保证用的是最新的草稿 id
  const previous = savingPromise || Promise.resolve(true);
  const mine = previous.then(async () => {
    unsaved = false;
    draftStatus.value = "正在保存…";
    const p = prefill.value;
    try {
      const result = await post(
        "/api/drafts",
        {
          draft_id: form.draftId,
          account_id: form.accountId,
          subject: form.subject,
          in_reply_to: p.in_reply_to || null,
          references: p.references || null,
          forward_message_id: p.forward_message_id || null,
          ...addressFields(),
          ...bodyFields(),
        },
        { keepalive },
      );
      form.draftId = result.id || null;
      draftStatus.value = result.id ? `草稿已保存 ${result.saved_at}` : "";
      if (!unsaved) store.composeBusy = false;
      refreshNav();
      return true;
    } catch {
      unsaved = true;
      draftStatus.value = "草稿保存失败";
      return false;
    }
  });
  savingPromise = mine;
  mine.then(() => {
    if (savingPromise === mine) savingPromise = null;
  });
  return mine;
}

// ---- 附件 ----
function addFiles(list) {
  if (!list || !list.length) return;
  files.value = files.value.concat(Array.from(list));
  markDirty();
}
function removeFile(i) {
  files.value.splice(i, 1);
  markDirty();
}
function onFilePicked(ev) {
  addFiles(ev.target.files);
  ev.target.value = "";
}
function hasFiles(ev) {
  return Array.from(ev.dataTransfer?.types || []).includes("Files");
}
function onDragEnter(ev) {
  if (!hasFiles(ev)) return;
  ev.preventDefault();
  dragging.value += 1;
}
function onDragLeave() {
  dragging.value = Math.max(0, dragging.value - 1);
}
function onDrop(ev) {
  if (!hasFiles(ev)) return;
  ev.preventDefault();
  dragging.value = 0;
  addFiles(ev.dataTransfer.files);
}

// ---- 插入链接 ----
function promptLink() {
  if (form.plain) return;
  const url = ref("https://");
  ui.dialog.create({
    title: "插入链接",
    showIcon: false,
    content: () =>
      h(NInput, {
        value: url.value,
        "onUpdate:value": (v) => (url.value = v),
        placeholder: "https://",
        autofocus: true,
      }),
    positiveText: "插入",
    negativeText: "取消",
    onPositiveClick: () => {
      let value = url.value.trim();
      if (!value || value === "https://") return;
      if (!/^(https?:|mailto:)/i.test(value)) value = `https://${value}`;
      editor.value.insertLink(value);
    },
  });
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
  if (key === "manage") return router.push("/accounts/templates");
  const t = templates.value.find((x) => `t-${x.id}` === key);
  if (!t) return;
  if (form.plain) form.plainText = `${form.plainText}${form.plainText ? "\n" : ""}${htmlToText(t.body_html)}`;
  else editor.value?.insertHtml(t.body_html);
  markDirty();
}

function saveAsTemplate() {
  const body = form.plain
    ? form.plainText.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/\n/g, "<br>")
    : editor.value?.getInnerHtml() || "";
  if (!body.trim()) {
    ui.message.warning("正文是空的，先写点内容再保存为模板");
    return;
  }
  const name = ref(form.subject.trim().slice(0, 60));
  ui.dialog.create({
    title: "保存为模板",
    showIcon: false,
    content: () =>
      h(NInput, { value: name.value, "onUpdate:value": (v) => (name.value = v), placeholder: "模板名称", autofocus: true }),
    positiveText: "保存",
    negativeText: "取消",
    onPositiveClick: async () => {
      if (!name.value.trim()) {
        ui.message.warning("请填写模板名称");
        return false;
      }
      try {
        await post("/api/templates", { name: name.value, body_html: body });
        ui.message.success("已保存为模板");
      } catch (err) {
        ui.message.error(err.message);
        return false;
      }
    },
  });
}

// 纯文本模式：正文切换为普通文本框，只发送纯文本
function togglePlain(value) {
  if (value) {
    form.plainText = (editor.value?.getText() || "").replace(/\n{3,}/g, "\n\n");
    showFormat.value = false;
  }
  form.plain = value;
  if (!value) nextTick(() => editor.value?.setText(form.plainText));
  markDirty();
}

// ---- 发送（含撤销发送） ----
const undoSeconds = computed(() => store.session?.config?.undo_send_seconds || 0);
let pendingTimer = null;
let closeToast = null;

function validate() {
  [toInput, ccInput, bccInput].forEach((r) => r.value?.commit());
  if (!form.to.length && !form.cc.length && !form.bcc.length) {
    error.value = "没有填写收件人";
    toInput.value?.focus();
    return false;
  }
  if ([...form.to, ...form.cc, ...form.bcc].some((a) => !isValidEmail(a.email))) {
    error.value = "邮箱地址无效";
    return false;
  }
  return true;
}

function send() {
  if (phase.value !== "ready" || !validate()) return;
  error.value = "";
  clearTimeout(saveTimer);
  if (undoSeconds.value > 0) {
    phase.value = "pending";
    store.composePending = true;
    pendingTimer = setTimeout(commitSend, undoSeconds.value * 1000);
    closeToast = toast(`正在发送…（${undoSeconds.value} 秒内可撤销）`, {
      actionText: "撤销",
      onAction: undoSend,
      duration: undoSeconds.value * 1000,
    });
    return;
  }
  commitSend();
}

function undoSend() {
  if (phase.value !== "pending") return;
  clearTimeout(pendingTimer);
  phase.value = "ready";
  store.composePending = false;
  winState.value = "normal";
  if (unsaved) saveDraft();
}

// 发送 / 定时发送的表单内容（含附件）
function buildFormData() {
  const p = prefill.value;
  const data = new FormData();
  const fields = {
    account_id: form.accountId,
    subject: form.subject,
    use_ssl: form.useSsl,
    in_reply_to: p.in_reply_to || "",
    references: p.references || "",
    forward_message_id: p.forward_message_id || "",
    draft_id: form.draftId || "",
    ...addressFields(),
    ...bodyFields(),
  };
  for (const [key, value] of Object.entries(fields)) data.append(key, value ?? "");
  for (const f of files.value) data.append("attachments", f, f.name);
  return data;
}

// ---- 定时发送 ----
const scheduleOpen = ref(false);
const sendMenu = [{ key: "schedule", label: "定时发送…" }];

function openSchedule() {
  if (phase.value !== "ready" || !validate()) return;
  error.value = "";
  scheduleOpen.value = true;
}

async function scheduleAt(ms) {
  clearTimeout(saveTimer);
  phase.value = "sending";
  store.composePending = true;
  await (savingPromise || Promise.resolve());
  const data = buildFormData();
  data.append("send_at_ms", String(ms));
  try {
    await api("/api/compose/schedule", { method: "POST", form: data });
    unsaved = false;
    closeCompose();
    refreshNav();
    toast(`已安排在 ${futureDate(new Date(ms))} 发送`, {
      linkText: "查看",
      onLink: () => router.push("/compose/scheduled"),
      duration: 6000,
    });
  } catch (err) {
    phase.value = "ready";
    store.composePending = false;
    error.value = err.message;
  }
}

async function commitSend() {
  clearTimeout(pendingTimer);
  closeToast?.();
  phase.value = "sending";
  store.composePending = true;
  // 等进行中的草稿保存结束（拿到草稿 id），服务端发送成功后才能把它删掉
  await (savingPromise || Promise.resolve());
  const data = buildFormData();
  const sendingToast = toast("正在发送…", { duration: 0 });
  try {
    const result = await api("/api/compose/send", { method: "POST", form: data });
    sendingToast();
    unsaved = false;
    closeCompose();
    refreshNav();
    toast(result.status === "partial" ? "邮件已发送，但部分收件人被拒收" : "邮件已发送", {
      linkText: "查看邮件",
      onLink: () => router.push(`/compose/sent/${result.sent_id}`),
      duration: 6000,
    });
  } catch (err) {
    sendingToast();
    phase.value = "ready";
    store.composePending = false;
    winState.value = "normal";
    error.value = err.message;
  }
}

function onKeydown(ev) {
  if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
    ev.preventDefault();
    send();
  }
}

// ---- 关闭 / 舍弃 ----
// 关闭（×）：与 Gmail 一致，先保存草稿再关闭；舍弃（垃圾桶）：确认后删除草稿
async function close() {
  const ok = await saveDraft(true);
  if (!ok && !(await confirm("草稿保存失败，仍要关闭吗？未保存的内容会丢失。", { danger: true, positiveText: "关闭" }))) return;
  unsaved = false;
  closeCompose();
}

async function discard() {
  if ((dirty || form.draftId) && !(await confirm("舍弃这封草稿？已填写的内容将被删除。", { danger: true, positiveText: "舍弃" }))) {
    return;
  }
  clearTimeout(saveTimer);
  unsaved = false;
  const id = form.draftId;
  closeCompose();
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
  winState.value = winState.value === "min" ? "normal" : "min";
}
function toggleMax() {
  winState.value = winState.value === "max" ? "normal" : "max";
}

// 再次点「写邮件」时恢复显示已打开的窗口
watch(
  () => store.composeFocus,
  () => {
    if (phase.value === "ready") winState.value = "normal";
    if (store.composeBusy) ui.message?.info("请先发送或关闭正在编写的邮件");
  },
);

// 页面被关闭时尽量把最后的修改存进草稿（keepalive 请求在页面卸载后仍会发出）
function onPageHide() {
  if (unsaved) saveDraft(true);
}
function onBeforeUnload(ev) {
  if ((unsaved || phase.value !== "ready") && phase.value !== "sending") {
    ev.preventDefault();
    ev.returnValue = "";
  }
}
window.addEventListener("pagehide", onPageHide);
window.addEventListener("beforeunload", onBeforeUnload);
onBeforeUnmount(() => {
  window.removeEventListener("pagehide", onPageHide);
  window.removeEventListener("beforeunload", onBeforeUnload);
  clearTimeout(saveTimer);
  // 等待发送期间窗口被关闭（如退出登录）：不再发送
  clearTimeout(pendingTimer);
});

const hidden = computed(() => phase.value !== "ready");
const fullScreen = computed(() => isMobile.value || winState.value === "max");
</script>

<template>
  <div v-if="winState === 'max' && !isMobile && !hidden" class="gm-compose-backdrop" @click="toggleMax" />
  <section
    v-show="!hidden"
    class="gm-compose"
    :class="{ 'is-min': winState === 'min' && !isMobile, 'is-max': fullScreen, 'is-mobile': isMobile }"
    role="dialog"
    :aria-label="title"
    @keydown="onKeydown"
    @dragenter="onDragEnter"
    @dragover="(e) => hasFiles(e) && e.preventDefault()"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <header class="gm-compose-head" @click="winState === 'min' && toggleMin()">
      <span class="gm-compose-title">{{ title }}</span>
      <span class="gm-compose-head-actions" @click.stop>
        <template v-if="!isMobile">
          <IconBtn :icon="MinimizeOutlined" title="最小化" size="small" :icon-size="18" @click="toggleMin" />
          <IconBtn
            :icon="winState === 'max' ? CloseFullscreenOutlined : OpenInFullOutlined"
            :title="winState === 'max' ? '退出全屏' : '全屏'"
            size="small"
            :icon-size="18"
            @click="toggleMax"
          />
        </template>
        <IconBtn :icon="CloseOutlined" title="保存并关闭" size="small" :icon-size="18" @click="close" />
      </span>
    </header>

    <div v-show="winState !== 'min' || isMobile" class="gm-compose-body">
      <NSpin v-if="loading" class="gm-compose-loading" />
      <div v-else-if="loadError" class="gm-compose-empty">{{ loadError }}</div>
      <div v-else-if="!accounts.length" class="gm-compose-empty">
        还没有可用账号，请先到 <RouterLink to="/accounts/" @click="closeCompose">设置</RouterLink> 添加并启用。
      </div>
      <template v-else>
        <NAlert v-if="error" type="error" class="gm-compose-error" closable @close="error = ''">{{ error }}</NAlert>

        <div class="gm-field">
          <label class="gm-field-label">发件人</label>
          <NSelect
            v-model:value="form.accountId"
            :options="accountOptions"
            size="small"
            :bordered="false"
            class="gm-from"
            @update:value="onAccountChange"
          />
        </div>
        <div class="gm-field">
          <label class="gm-field-label">收件人</label>
          <AddressInput ref="toInput" v-model:value="form.to" :contacts="contacts" label="收件人" @change="markDirty" />
          <span class="gm-field-extras">
            <button v-if="!showCc" type="button" class="gm-link-btn" @click="showCc = true; nextTick(() => ccInput?.focus())">抄送</button>
            <button v-if="!showBcc" type="button" class="gm-link-btn" @click="showBcc = true; nextTick(() => bccInput?.focus())">密送</button>
          </span>
        </div>
        <div v-if="showCc" class="gm-field">
          <label class="gm-field-label">抄送</label>
          <AddressInput ref="ccInput" v-model:value="form.cc" :contacts="contacts" label="抄送" @change="markDirty" />
        </div>
        <div v-if="showBcc" class="gm-field">
          <label class="gm-field-label">密送</label>
          <AddressInput ref="bccInput" v-model:value="form.bcc" :contacts="contacts" label="密送" @change="markDirty" />
        </div>
        <div class="gm-field">
          <input v-model="form.subject" class="gm-subject-input" type="text" placeholder="主题" aria-label="主题" @input="markDirty" />
        </div>

        <div class="gm-compose-content">
          <RichEditor
            v-if="!form.plain"
            ref="editor"
            :initial-html="prefill.body_html || ''"
            :initial-text="prefill.body_text || ''"
            :show-format-bar="showFormat"
            :focus-start="!!prefill.is_reply && !prefill.is_forward"
            @change="markDirty"
          />
          <textarea v-else v-model="form.plainText" class="gm-plain" aria-label="正文（纯文本）" @input="markDirty" />

          <div v-if="(prefill.forward_attachments || []).length || files.length" class="gm-attachments">
            <span
              v-for="name in prefill.forward_attachments || []"
              :key="'fwd-' + name"
              class="gm-attachment is-fixed"
              title="转发的原邮件附件，将随邮件一起发送"
            >
              <NIcon :component="AttachFileOutlined" :size="16" />
              <span class="gm-attachment-name">{{ name }}</span>
              <span class="gm-attachment-meta">转发附件</span>
            </span>
            <span v-for="(f, i) in files" :key="i" class="gm-attachment" :title="f.name">
              <NIcon :component="AttachFileOutlined" :size="16" />
              <span class="gm-attachment-name">{{ f.name }}</span>
              <span class="gm-attachment-meta">{{ fileSize(f.size) }}</span>
              <button type="button" class="gm-attachment-remove" :aria-label="`移除附件 ${f.name}`" @click="removeFile(i)">
                <NIcon :component="CloseOutlined" :size="14" />
              </button>
            </span>
          </div>
          <div v-if="totalSize > MAX_TOTAL" class="gm-size-warning">
            <NIcon :component="WarningAmberOutlined" :size="16" /> 附件总大小超过 25 MB，Gmail 可能拒收。
          </div>
        </div>

        <footer class="gm-compose-foot">
          <NButtonGroup class="gm-send-btn">
            <NButton type="primary" :disabled="phase !== 'ready'" title="发送（Ctrl+Enter）" @click="send">发送</NButton>
            <NDropdown trigger="click" placement="top-start" :options="sendMenu" @select="openSchedule">
              <NButton type="primary" :disabled="phase !== 'ready'" title="更多发送选项" class="gm-send-more">
                <template #icon><NIcon :component="ArrowDropDownOutlined" /></template>
              </NButton>
            </NDropdown>
          </NButtonGroup>
          <IconBtn
            :icon="TextFormatOutlined"
            title="格式选项"
            :disabled="form.plain"
            :color="showFormat ? 'var(--gm-primary)' : ''"
            @click="showFormat = !showFormat"
          />
          <IconBtn :icon="AttachFileOutlined" title="添加附件（也可以直接拖放文件）" @click="fileInput.click()" />
          <input ref="fileInput" type="file" multiple hidden @change="onFilePicked" />
          <IconBtn :icon="LinkOutlined" title="插入链接" :disabled="form.plain" @click="promptLink" />
          <NDropdown
            trigger="click"
            placement="top-start"
            :options="templateMenu"
            scrollable
            style="max-height: 360px"
            @update:show="(v) => v && loadTemplates()"
            @select="onTemplateMenu"
          >
            <IconBtn :icon="TextSnippetOutlined" title="模板（快捷回复）" />
          </NDropdown>
          <NPopover trigger="click" placement="top-start">
            <template #trigger>
              <IconBtn :icon="MoreVertOutlined" title="更多选项" />
            </template>
            <div class="gm-more-menu">
              <NCheckbox :checked="form.plain" @update:checked="togglePlain">纯文本模式</NCheckbox>
              <div class="gm-more-label">SMTP 连接方式</div>
              <NRadioGroup v-model:value="form.useSsl">
                <div><NRadio value="on">SSL 465（推荐）</NRadio></div>
                <div><NRadio value="off">STARTTLS 587</NRadio></div>
              </NRadioGroup>
            </div>
          </NPopover>
          <span class="gm-foot-spacer" />
          <span class="gm-draft-status" aria-live="polite">{{ draftStatus }}</span>
          <IconBtn :icon="DeleteOutlined" title="舍弃草稿" @click="discard" />
        </footer>
        <TimePicker v-model:show="scheduleOpen" title="定时发送" confirm-text="安排发送" @pick="scheduleAt" />

        <div v-if="dragging" class="gm-drop-overlay">
          <NIcon :component="UploadFileOutlined" :size="40" />
          <span>将文件拖放到此处</span>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.gm-compose {
    position: fixed;
    right: 24px;
    bottom: 0;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    width: 560px;
    height: min(620px, calc(100vh - 80px));
    background: var(--gm-surface);
    border: 1px solid var(--gm-border);
    border-bottom: 0;
    border-radius: var(--gm-radius) var(--gm-radius) 0 0;
    box-shadow: var(--gm-shadow-3);
    overflow: hidden;
}
.gm-compose.is-min { width: 300px; height: auto; }
.gm-compose.is-max {
    right: 50%;
    bottom: 50%;
    transform: translate(50%, 50%);
    width: min(1100px, calc(100vw - 96px));
    height: calc(100vh - 96px);
    border-bottom: 1px solid var(--gm-border);
    border-radius: var(--gm-radius);
}
.gm-compose.is-mobile {
    inset: 0;
    transform: none;
    width: 100%;
    height: 100%;
    border: 0;
    border-radius: 0;
}
.gm-compose-backdrop { position: fixed; inset: 0; z-index: 999; background: rgba(0, 0, 0, .45); }

.gm-compose-head {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 44px;
    padding: 0 8px 0 16px;
    background: var(--gm-surface-2);
    border-bottom: 1px solid var(--gm-divider);
    flex: none;
}
.gm-compose.is-min .gm-compose-head { cursor: pointer; border-bottom: 0; }
.gm-compose-title { flex: 1; min-width: 0; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-compose-head-actions { display: flex; gap: 2px; }

.gm-compose-body { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0 16px; }
.gm-compose-loading { margin: auto; }
.gm-compose-empty { padding: 32px 0; color: var(--gm-text-3); text-align: center; }
.gm-compose-error { margin-top: 8px; }

.gm-field { display: flex; align-items: center; gap: 8px; min-height: 42px; border-bottom: 1px solid var(--gm-divider); }
.gm-field-label { color: var(--gm-text-3); flex: none; min-width: 42px; }
.gm-from { flex: 1; }
.gm-field-extras { display: flex; gap: 8px; flex: none; }
.gm-link-btn { border: 0; background: none; color: var(--gm-text-3); cursor: pointer; padding: 4px; font: inherit; }
.gm-link-btn:hover { color: var(--gm-primary); }
.gm-subject-input { flex: 1; border: 0; outline: 0; font: inherit; color: var(--gm-text); padding: 8px 0; background: transparent; }
.gm-subject-input::placeholder { color: var(--gm-text-4); }

.gm-compose-content { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow-y: auto; }
.gm-plain {
    flex: 1;
    min-height: 160px;
    border: 0;
    outline: 0;
    resize: none;
    padding: 8px 0;
    font: 14px/1.6 var(--gm-font);
    color: var(--gm-text);
    background: transparent;
}
.gm-attachments { display: flex; flex-direction: column; gap: 6px; padding: 8px 0; }
.gm-attachment {
    display: flex;
    align-items: center;
    gap: 8px;
    max-width: 440px;
    padding: 6px 8px;
    border: 1px solid var(--gm-border);
    border-radius: var(--gm-radius);
    background: var(--gm-surface-2);
    font-size: 13px;
}
.gm-attachment-name { flex: 1; min-width: 0; color: var(--gm-primary); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-attachment-meta { color: var(--gm-text-3); }
.gm-attachment-remove { display: flex; border: 0; background: none; padding: 2px; cursor: pointer; color: var(--gm-text-3); border-radius: 50%; }
.gm-attachment-remove:hover { background: var(--gm-hover); color: var(--gm-error); }
.gm-size-warning { display: flex; align-items: center; gap: 6px; color: var(--gm-warning); font-size: 13px; padding: 4px 0 8px; }

.gm-compose-foot { display: flex; align-items: center; gap: 2px; padding: 10px 0; border-top: 1px solid var(--gm-divider); flex: none; }
.gm-send-btn { margin-right: 8px; }
.gm-send-more { padding: 0 6px !important; }
.gm-foot-spacer { flex: 1; }
.gm-draft-status { color: var(--gm-text-3); font-size: 12px; margin-right: 4px; white-space: nowrap; }
.gm-more-menu { display: flex; flex-direction: column; gap: 6px; min-width: 180px; }
.gm-more-label { font-size: 12px; color: var(--gm-text-3); margin-top: 6px; }

.gm-drop-overlay {
    position: absolute;
    inset: 0;
    z-index: 5;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    background: var(--gm-primary-soft);
    backdrop-filter: blur(2px);
    border: 2px dashed var(--gm-primary);
    color: var(--gm-primary);
    font-size: 16px;
    font-weight: 600;
    pointer-events: none;
}
</style>
