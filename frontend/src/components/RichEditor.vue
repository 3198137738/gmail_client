<script setup>
// 写邮件正文编辑器（contenteditable）和格式工具栏：粗体、斜体、颜色、列表、引用等。
import { onBeforeUnmount, onMounted, ref } from "vue";
import { NIcon } from "naive-ui";
import {
  FormatBoldOutlined,
  FormatClearOutlined,
  FormatColorTextOutlined,
  FormatItalicOutlined,
  FormatListBulletedOutlined,
  FormatListNumberedOutlined,
  FormatQuoteOutlined,
  FormatUnderlinedOutlined,
  StrikethroughSOutlined,
} from "@vicons/material";

const props = defineProps({
  // 初始内容：服务端生成或已在服务端清洗过的 HTML（回复引用、草稿）
  initialHtml: { type: String, default: "" },
  initialText: { type: String, default: "" },
  showFormatBar: { type: Boolean, default: false },
  // 回复时光标放在引用内容上方
  focusStart: { type: Boolean, default: false },
});
const emit = defineEmits(["change"]);

const editor = ref(null);
const empty = ref(true);
const states = ref({});
const color = ref("#d93025");

const BUTTONS = [
  { cmd: "bold", icon: FormatBoldOutlined, title: "粗体（Ctrl+B）" },
  { cmd: "italic", icon: FormatItalicOutlined, title: "斜体（Ctrl+I）" },
  { cmd: "underline", icon: FormatUnderlinedOutlined, title: "下划线（Ctrl+U）" },
  { cmd: "strikeThrough", icon: StrikethroughSOutlined, title: "删除线" },
];
const BUTTONS2 = [
  { cmd: "insertOrderedList", icon: FormatListNumberedOutlined, title: "编号列表" },
  { cmd: "insertUnorderedList", icon: FormatListBulletedOutlined, title: "项目符号列表" },
  { cmd: "formatBlock", value: "blockquote", icon: FormatQuoteOutlined, title: "引用" },
];

function isEmpty() {
  const el = editor.value;
  return !el || (!el.textContent.trim() && !el.querySelector("img"));
}

function onInput() {
  empty.value = isEmpty();
  emit("change");
}

function updateStates() {
  if (document.activeElement !== editor.value) return;
  const next = {};
  for (const b of BUTTONS.concat(BUTTONS2)) {
    if (b.cmd === "formatBlock") continue;
    try {
      next[b.cmd] = document.queryCommandState(b.cmd);
    } catch {
      next[b.cmd] = false;
    }
  }
  states.value = next;
}

function exec(cmd, value) {
  editor.value.focus();
  document.execCommand(cmd, false, value ?? null);
  onInput();
  updateStates();
}

function insertLink(url) {
  const selection = window.getSelection();
  if (selection && selection.toString() && editor.value.contains(selection.anchorNode)) {
    exec("createLink", url);
  } else {
    const safe = url.replace(/"/g, "&quot;");
    const label = url.replace(/&/g, "&amp;").replace(/</g, "&lt;");
    exec("insertHTML", `<a href="${safe}">${label}</a>`);
  }
}

onMounted(() => {
  if (props.initialHtml) editor.value.innerHTML = props.initialHtml;
  else if (props.initialText) editor.value.innerText = props.initialText;
  empty.value = isEmpty();
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
  isEmpty,
  getHtml: () => (isEmpty() ? "" : `<div dir="ltr">${editor.value.innerHTML}</div>`),
  getText: () => editor.value?.innerText || "",
  setText(text) {
    editor.value.innerText = text;
    onInput();
  },
});
</script>

<template>
  <div class="gm-editor-wrap">
    <div
      ref="editor"
      class="gm-editor"
      :class="{ 'is-empty': empty }"
      contenteditable="true"
      role="textbox"
      aria-multiline="true"
      aria-label="正文"
      @input="onInput"
    />
    <div v-show="showFormatBar" class="gm-format-bar">
      <button
        v-for="b in BUTTONS"
        :key="b.cmd"
        type="button"
        class="gm-fmt-btn"
        :class="{ 'is-active': states[b.cmd] }"
        :title="b.title"
        @mousedown.prevent
        @click="exec(b.cmd)"
      >
        <NIcon :component="b.icon" :size="20" />
      </button>
      <label class="gm-fmt-btn gm-fmt-color" title="文字颜色" @mousedown.prevent>
        <NIcon :component="FormatColorTextOutlined" :size="20" :color="color" />
        <input v-model="color" type="color" aria-label="文字颜色" @input="exec('foreColor', color)" />
      </label>
      <span class="gm-fmt-sep" />
      <button
        v-for="b in BUTTONS2"
        :key="b.cmd"
        type="button"
        class="gm-fmt-btn"
        :class="{ 'is-active': states[b.cmd] }"
        :title="b.title"
        @mousedown.prevent
        @click="exec(b.cmd, b.value)"
      >
        <NIcon :component="b.icon" :size="20" />
      </button>
      <span class="gm-fmt-sep" />
      <button type="button" class="gm-fmt-btn" title="清除格式" @mousedown.prevent @click="exec('removeFormat')">
        <NIcon :component="FormatClearOutlined" :size="20" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.gm-editor-wrap { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.gm-editor {
    flex: 1;
    min-height: 160px;
    padding: 8px 0;
    outline: 0;
    overflow-y: auto;
    font: 14px/1.5 Arial, sans-serif;
    color: #222;
    word-break: break-word;
}
.gm-editor :deep(blockquote) { margin: 0 0 0 .8ex; border-left: 1px solid #ccc; padding-left: 1ex; }
.gm-format-bar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 2px;
    padding: 4px;
    margin: 4px 0;
    border-radius: 8px;
    box-shadow: var(--gm-shadow-1);
    background: #fff;
}
.gm-fmt-btn {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border: 0;
    border-radius: 4px;
    background: none;
    color: var(--gm-text-2);
    cursor: pointer;
}
.gm-fmt-btn:hover { background: var(--gm-hover); }
.gm-fmt-btn.is-active { background: var(--gm-nav-active); color: #001d35; }
.gm-fmt-color input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
.gm-fmt-sep { width: 1px; height: 20px; background: var(--gm-outline-variant); margin: 0 4px; }
</style>
