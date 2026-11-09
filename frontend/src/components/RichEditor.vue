<script setup>
// 富文本编辑器（contenteditable）：写邮件正文、签名、模板共用。
// - 工具栏：撤销 / 重做、字号、粗斜体下划线删除线、文字颜色 / 高亮、对齐、列表、缩进、引用、链接、分割线、表格、清除格式
// - 选中文字时浮出小工具栏（粗体、斜体、下划线、链接）
// - 粘贴：清洗格式（去掉 Office / 网页样式）；Ctrl+Shift+V 粘贴为纯文本；粘贴 / 拖入的图片压缩后内嵌到正文
// - 回复时引用的原文可以折叠（collapsibleQuote）
// 不用 ProseMirror 类编辑器：回复引用和签名里是任意的 HTML（表格、内联样式），它们会被改写。
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { NDropdown, NIcon } from "naive-ui";
import {
  FormatAlignCenterOutlined,
  FormatAlignLeftOutlined,
  FormatAlignRightOutlined,
  FormatBoldOutlined,
  FormatClearOutlined,
  FormatColorFillOutlined,
  FormatColorTextOutlined,
  FormatIndentDecreaseOutlined,
  FormatIndentIncreaseOutlined,
  FormatItalicOutlined,
  FormatListBulletedOutlined,
  FormatListNumberedOutlined,
  FormatQuoteOutlined,
  FormatSizeOutlined,
  FormatUnderlinedOutlined,
  HorizontalRuleOutlined,
  ImageOutlined,
  LinkOutlined,
  MoreHorizOutlined,
  RedoOutlined,
  StrikethroughSOutlined,
  TableChartOutlined,
  UndoOutlined,
} from "@vicons/material";
import { prompt, toast } from "../ui";
import { escapeText, sanitizePastedHtml } from "../editor/sanitize";
import { imageToDataUrl, isImageFile } from "../editor/images";

const props = defineProps({
  // 初始内容：服务端生成或已在服务端清洗过的 HTML（回复引用、草稿）
  initialHtml: { type: String, default: "" },
  initialText: { type: String, default: "" },
  showFormatBar: { type: Boolean, default: false },
  // 回复时光标放在引用内容上方
  focusStart: { type: Boolean, default: false },
  // 引用的原文默认折叠（写邮件窗口不是全屏时）
  collapsibleQuote: { type: Boolean, default: false },
  placeholder: { type: String, default: "" },
});
const emit = defineEmits(["change"]);

const editor = ref(null);
const wrap = ref(null);
const imageInput = ref(null);
const empty = ref(true);
const states = ref({});
const color = ref("#d93025");
const highlight = ref("#fff59d");
const hasQuote = ref(false);
const quoteHidden = ref(props.collapsibleQuote);
const bubble = ref(null);   // 选中文字时的浮动工具栏位置 { x, y }

const MARKS = [
  { cmd: "bold", icon: FormatBoldOutlined, title: "粗体（Ctrl+B）" },
  { cmd: "italic", icon: FormatItalicOutlined, title: "斜体（Ctrl+I）" },
  { cmd: "underline", icon: FormatUnderlinedOutlined, title: "下划线（Ctrl+U）" },
  { cmd: "strikeThrough", icon: StrikethroughSOutlined, title: "删除线" },
];
const BLOCKS = [
  { cmd: "insertOrderedList", icon: FormatListNumberedOutlined, title: "编号列表（Ctrl+Shift+7）" },
  { cmd: "insertUnorderedList", icon: FormatListBulletedOutlined, title: "项目符号列表（Ctrl+Shift+8）" },
  { cmd: "outdent", icon: FormatIndentDecreaseOutlined, title: "减少缩进" },
  { cmd: "indent", icon: FormatIndentIncreaseOutlined, title: "增加缩进" },
  { cmd: "formatBlock", value: "blockquote", icon: FormatQuoteOutlined, title: "引用" },
];
const SIZES = [
  { key: "2", label: "小" },
  { key: "3", label: "正常" },
  { key: "5", label: "大" },
  { key: "6", label: "特大" },
];
const ALIGNS = [
  { key: "justifyLeft", label: "左对齐", icon: FormatAlignLeftOutlined },
  { key: "justifyCenter", label: "居中", icon: FormatAlignCenterOutlined },
  { key: "justifyRight", label: "右对齐", icon: FormatAlignRightOutlined },
];
const alignIcon = computed(() => (ALIGNS.find((a) => states.value[a.key]) || ALIGNS[0]).icon);
const MORE = [
  { key: "image", label: "插入图片" },
  { key: "table", label: "插入表格" },
  { key: "hr", label: "分割线" },
];

function isEmpty() {
  const el = editor.value;
  return !el || (!el.textContent.trim() && !el.querySelector("img, table, hr"));
}

function onInput() {
  empty.value = isEmpty();
  hasQuote.value = !!editor.value?.querySelector(".gmail_quote");
  emit("change");
}

// 编辑器里最近一次的光标 / 选区：点菜单、弹出对话框后编辑器会失去焦点，插入内容时回到这里
let savedRange = null;

function restoreSelection() {
  editor.value.focus();
  if (!savedRange) return;
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(savedRange);
}

function updateStates() {
  const el = editor.value;
  const sel = window.getSelection();
  const inside = sel && sel.rangeCount && el && el.contains(sel.anchorNode);
  if (!inside) {
    if (document.activeElement !== el) bubble.value = null;
    return;
  }
  savedRange = sel.getRangeAt(0).cloneRange();
  const next = {};
  for (const cmd of ["bold", "italic", "underline", "strikeThrough", "insertOrderedList", "insertUnorderedList", "justifyLeft", "justifyCenter", "justifyRight"]) {
    try {
      next[cmd] = document.queryCommandState(cmd);
    } catch {
      next[cmd] = false;
    }
  }
  states.value = next;
  // 选中了文字：在选区上方浮出小工具栏
  if (!sel.isCollapsed && sel.toString().trim() && wrap.value) {
    const rect = savedRange.getBoundingClientRect();
    const box = wrap.value.getBoundingClientRect();
    bubble.value = { x: Math.max(4, rect.left - box.left + rect.width / 2 - 80), y: Math.max(0, rect.top - box.top - 42) };
  } else {
    bubble.value = null;
  }
}

function exec(cmd, value) {
  restoreSelection();
  document.execCommand(cmd, false, value ?? null);
  onInput();
  updateStates();
}

// 在光标处插入一段 HTML（模板、签名、粘贴）；没有光标时插到开头
function insertHtml(html) {
  restoreSelection();
  document.execCommand("insertHTML", false, html);
  onInput();
}

function insertLink(url) {
  restoreSelection();
  const selection = window.getSelection();
  if (selection && selection.toString() && editor.value.contains(selection.anchorNode)) {
    exec("createLink", url);
  } else {
    const safe = url.replace(/"/g, "&quot;");
    const label = url.replace(/&/g, "&amp;").replace(/</g, "&lt;");
    exec("insertHTML", `<a href="${safe}">${label}</a>`);
  }
}

async function requestLink() {
  const range = savedRange;
  const value = await prompt({
    title: "插入链接",
    value: "https://",
    placeholder: "https://",
    positiveText: "插入",
    validate: (v) => (!v.trim() || v.trim() === "https://" ? "请填写链接地址" : ""),
  });
  if (!value) return;
  savedRange = range;
  let url = value.trim();
  if (!/^(https?:|mailto:)/i.test(url)) url = `https://${url}`;
  insertLink(url);
}

function insertTable() {
  const cell = '<td style="border: 1px solid #ccc; padding: 4px 8px; min-width: 60px;"><br></td>';
  const row = `<tr>${cell.repeat(3)}</tr>`;
  insertHtml(`<table style="border-collapse: collapse;"><tbody>${row.repeat(2)}</tbody></table><div><br></div>`);
}

async function insertImages(images) {
  for (const file of images) {
    try {
      const url = await imageToDataUrl(file);
      insertHtml(`<img src="${url}" alt="${escapeText(file.name || "图片")}" style="max-width: 100%;">`);
    } catch {
      toast(`无法插入图片 ${file.name || ""}`, { tone: "error" });
    }
  }
}

function onMore(key) {
  if (key === "image") imageInput.value?.click();
  else if (key === "table") insertTable();
  else if (key === "hr") exec("insertHorizontalRule");
}

function onImagePicked(ev) {
  const images = [...ev.target.files].filter(isImageFile);
  ev.target.value = "";
  insertImages(images);
}

// ---- 粘贴 / 拖入 ----
let plainNext = false;

function onKeydown(ev) {
  const mod = ev.ctrlKey || ev.metaKey;
  if (mod && ev.shiftKey && ev.key.toLowerCase() === "v") plainNext = true;
  else if (mod && !ev.shiftKey && ev.key.toLowerCase() === "k") {
    ev.preventDefault();
    ev.stopPropagation();
    requestLink();
  } else if (mod && ev.shiftKey && (ev.key === "&" || ev.code === "Digit7")) {
    ev.preventDefault();
    exec("insertOrderedList");
  } else if (mod && ev.shiftKey && (ev.key === "*" || ev.code === "Digit8")) {
    ev.preventDefault();
    exec("insertUnorderedList");
  }
}

function onPaste(ev) {
  const data = ev.clipboardData;
  if (!data) return;
  const images = [...(data.files || [])].filter(isImageFile);
  if (images.length) {
    ev.preventDefault();
    insertImages(images);
    return;
  }
  const html = data.getData("text/html");
  const text = data.getData("text/plain");
  if (plainNext || !html) {
    plainNext = false;
    if (!text) return;
    ev.preventDefault();
    insertHtml(escapeText(text));
    return;
  }
  ev.preventDefault();
  insertHtml(sanitizePastedHtml(html));
}

function onDrop(ev) {
  const files = [...(ev.dataTransfer?.files || [])];
  // 全是图片时插入正文；有其他文件时交给写邮件窗口作为附件
  if (!files.length || !files.every(isImageFile)) return;
  ev.preventDefault();
  ev.stopPropagation();
  const range = document.caretRangeFromPoint?.(ev.clientX, ev.clientY);
  if (range) savedRange = range;
  insertImages(files);
}

onMounted(() => {
  if (props.initialHtml) editor.value.innerHTML = props.initialHtml;
  else if (props.initialText) editor.value.innerText = props.initialText;
  empty.value = isEmpty();
  hasQuote.value = !!editor.value.querySelector(".gmail_quote");
  if (props.focusStart) {
    editor.value.focus();
    const range = document.createRange();
    range.setStart(editor.value, 0);
    range.collapse(true);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }
  document.addEventListener("selectionchange", updateStates);
});
onBeforeUnmount(() => document.removeEventListener("selectionchange", updateStates));

defineExpose({
  focus: () => editor.value?.focus(),
  insertLink,
  insertHtml,
  insertImages,
  requestLink,
  isEmpty,
  // 当前内容（不加外层 div），保存为模板用
  getInnerHtml: () => (isEmpty() ? "" : editor.value.innerHTML),
  // 编辑区 DOM：切换发件账号时替换其中的签名块
  editorEl: () => editor.value,
  // 直接改了编辑区 DOM 后调用，更新占位提示并通知修改
  changed: onInput,
  getHtml: () => (isEmpty() ? "" : `<div dir="ltr">${editor.value.innerHTML}</div>`),
  getText: () => editor.value?.innerText || "",
  setText(text) {
    editor.value.innerText = text;
    onInput();
  },
});
</script>

<template>
  <div ref="wrap" class="gm-editor-wrap">
    <div v-show="showFormatBar" class="gm-format-bar" role="toolbar" aria-label="格式" @mousedown.prevent>
      <button type="button" class="gm-fmt-btn" title="撤销（Ctrl+Z）" @click="exec('undo')"><NIcon :component="UndoOutlined" :size="18" /></button>
      <button type="button" class="gm-fmt-btn" title="重做（Ctrl+Y）" @click="exec('redo')"><NIcon :component="RedoOutlined" :size="18" /></button>
      <span class="gm-fmt-sep" />
      <NDropdown trigger="click" :options="SIZES" @select="(k) => exec('fontSize', k)">
        <button type="button" class="gm-fmt-btn" title="字号"><NIcon :component="FormatSizeOutlined" :size="18" /></button>
      </NDropdown>
      <button
        v-for="b in MARKS"
        :key="b.cmd"
        type="button"
        class="gm-fmt-btn"
        :class="{ 'is-active': states[b.cmd] }"
        :title="b.title"
        :aria-pressed="!!states[b.cmd]"
        @click="exec(b.cmd)"
      >
        <NIcon :component="b.icon" :size="18" />
      </button>
      <label class="gm-fmt-btn gm-fmt-color" title="文字颜色">
        <NIcon :component="FormatColorTextOutlined" :size="18" :color="color" />
        <input v-model="color" type="color" aria-label="文字颜色" @input="exec('foreColor', color)" />
      </label>
      <label class="gm-fmt-btn gm-fmt-color" title="高亮">
        <NIcon :component="FormatColorFillOutlined" :size="18" :color="highlight" />
        <input v-model="highlight" type="color" aria-label="高亮颜色" @input="exec('hiliteColor', highlight)" />
      </label>
      <span class="gm-fmt-sep" />
      <NDropdown trigger="click" :options="ALIGNS.map((a) => ({ key: a.key, label: a.label }))" @select="(k) => exec(k)">
        <button type="button" class="gm-fmt-btn" title="对齐"><NIcon :component="alignIcon" :size="18" /></button>
      </NDropdown>
      <button
        v-for="b in BLOCKS"
        :key="b.cmd + (b.value || '')"
        type="button"
        class="gm-fmt-btn"
        :class="{ 'is-active': states[b.cmd] }"
        :title="b.title"
        @click="exec(b.cmd, b.value)"
      >
        <NIcon :component="b.icon" :size="18" />
      </button>
      <span class="gm-fmt-sep" />
      <button type="button" class="gm-fmt-btn" title="插入链接（Ctrl+K）" @click="requestLink"><NIcon :component="LinkOutlined" :size="18" /></button>
      <button type="button" class="gm-fmt-btn" title="插入图片" @click="imageInput?.click()"><NIcon :component="ImageOutlined" :size="18" /></button>
      <NDropdown trigger="click" :options="MORE" @select="onMore">
        <button type="button" class="gm-fmt-btn" title="更多：表格、分割线"><NIcon :component="MoreHorizOutlined" :size="18" /></button>
      </NDropdown>
      <button type="button" class="gm-fmt-btn" title="清除格式" @click="exec('removeFormat')"><NIcon :component="FormatClearOutlined" :size="18" /></button>
      <input ref="imageInput" type="file" accept="image/*" multiple hidden @change="onImagePicked" />
    </div>

    <div
      ref="editor"
      class="gm-editor"
      :class="{ 'is-empty': empty, 'is-quote-hidden': hasQuote && quoteHidden }"
      :data-placeholder="placeholder"
      contenteditable="true"
      role="textbox"
      aria-multiline="true"
      aria-label="正文"
      @input="onInput"
      @keydown="onKeydown"
      @paste="onPaste"
      @drop="onDrop"
    />
    <button
      v-if="hasQuote && collapsibleQuote"
      type="button"
      class="gm-quote-toggle"
      :title="quoteHidden ? '显示引用的原文' : '隐藏引用的原文'"
      @click="quoteHidden = !quoteHidden"
    >
      <NIcon :component="MoreHorizOutlined" :size="18" />
      <span>{{ quoteHidden ? "显示引用内容" : "隐藏引用内容" }}</span>
    </button>

    <div v-if="bubble" class="gm-bubble" :style="{ left: `${bubble.x}px`, top: `${bubble.y}px` }" @mousedown.prevent>
      <button
        v-for="b in MARKS.slice(0, 3)"
        :key="b.cmd"
        type="button"
        class="gm-fmt-btn"
        :class="{ 'is-active': states[b.cmd] }"
        :title="b.title"
        @click="exec(b.cmd)"
      >
        <NIcon :component="b.icon" :size="18" />
      </button>
      <button type="button" class="gm-fmt-btn" title="链接（Ctrl+K）" @click="requestLink"><NIcon :component="LinkOutlined" :size="18" /></button>
      <button type="button" class="gm-fmt-btn" title="清除格式" @click="exec('removeFormat')"><NIcon :component="FormatClearOutlined" :size="18" /></button>
    </div>
  </div>
</template>

<style scoped>
.gm-editor-wrap { position: relative; display: flex; flex-direction: column; flex: 1; min-height: 0; }
.gm-editor {
    flex: 1;
    min-height: 160px;
    padding: 10px 0;
    outline: 0;
    overflow-y: auto;
    font: 14px/1.6 Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
    color: var(--gm-text);
    word-break: break-word;
}
.gm-editor.is-empty::before { content: attr(data-placeholder); color: var(--gm-text-4); pointer-events: none; position: absolute; }
.gm-editor :deep(blockquote) { margin: 0 0 0 .8ex; border-left: 2px solid var(--gm-border); padding-left: 1ex; }
.gm-editor :deep(img) { max-width: 100%; height: auto; }
.gm-editor :deep(table td) { border: 1px solid var(--gm-border); }
.gm-editor.is-quote-hidden :deep(.gmail_quote) { display: none; }
.gm-quote-toggle {
    display: inline-flex;
    align-items: center;
    align-self: flex-start;
    gap: 4px;
    margin: 4px 0;
    padding: 0 10px;
    height: 24px;
    border: 1px solid var(--gm-border);
    border-radius: var(--r-pill);
    background: var(--gm-surface-2);
    color: var(--gm-text-3);
    font: inherit;
    font-size: var(--fs-xs);
    cursor: pointer;
}
.gm-quote-toggle:hover { color: var(--gm-primary); border-color: var(--gm-primary); }
.gm-format-bar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 1px;
    padding: 3px;
    margin: 6px 0 2px;
    border-radius: var(--r-md);
    border: 1px solid var(--gm-border);
    background: var(--gm-surface-2);
}
.gm-fmt-btn {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    border: 0;
    border-radius: var(--r-sm);
    background: none;
    color: var(--gm-text-2);
    cursor: pointer;
}
.gm-fmt-btn:hover { background: var(--gm-hover); color: var(--gm-text); }
.gm-fmt-btn.is-active { background: var(--gm-primary-soft); color: var(--gm-primary); }
.gm-fmt-color input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
.gm-fmt-sep { width: 1px; height: 18px; background: var(--gm-border); margin: 0 3px; }
.gm-bubble {
    position: absolute;
    z-index: 3;
    display: flex;
    gap: 1px;
    padding: 3px;
    border-radius: var(--r-md);
    background: var(--gm-popover);
    box-shadow: var(--gm-shadow-2);
}
</style>
