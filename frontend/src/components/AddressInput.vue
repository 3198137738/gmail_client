<script setup>
// 收件人输入框（Gmail 地址块）：回车 / 逗号 / 分号 / 失去焦点时把输入的文字变成地址块，
// 按最近联系人自动补全（↑↓ 选择，回车 / Tab 确认），退格删除最后一个，无效地址标红。
import { computed, ref } from "vue";
import { NTag } from "naive-ui";
import { isValidEmail, parseAddress, splitAddresses } from "../address";
import { avatarColor, initial } from "../format";

const props = defineProps({
  // 地址数组：[{ name, email }]
  value: { type: Array, required: true },
  contacts: { type: Array, default: () => [] },
  label: { type: String, default: "" },
});
const emit = defineEmits(["update:value", "change"]);

const input = ref(null);
const text = ref("");
const active = ref(0);
const focused = ref(false);

const suggestions = computed(() => {
  const q = text.value.trim().toLowerCase();
  if (!q || !focused.value) return [];
  const taken = new Set(props.value.map((a) => a.email.toLowerCase()));
  return props.contacts
    .filter((c) => !taken.has(c.email) && (c.email.includes(q) || (c.name && c.name.toLowerCase().includes(q))))
    .slice(0, 8);
});

function update(list) {
  emit("update:value", list);
  emit("change");
}

function add(list, addr) {
  if (!addr.email) return;
  if (list.some((a) => a.email.toLowerCase() === addr.email.toLowerCase())) return;
  list.push(addr);
}

function commit() {
  const raw = text.value;
  text.value = "";
  if (!raw.trim()) return;
  const list = [...props.value];
  for (const token of splitAddresses(raw.replace(/[，；]/g, ","))) add(list, parseAddress(token));
  update(list);
}

function remove(index) {
  const list = [...props.value];
  list.splice(index, 1);
  update(list);
}

function pick(i = active.value) {
  const c = suggestions.value[i];
  if (!c) return false;
  text.value = "";
  const list = [...props.value];
  add(list, { name: c.name, email: c.email });
  update(list);
  input.value?.focus();
  return true;
}

function onInput() {
  emit("change");
  active.value = 0;
  if (/[,;，；]/.test(text.value)) commit();
}

function onKeydown(ev) {
  if (suggestions.value.length) {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      const n = suggestions.value.length;
      active.value = (active.value + (ev.key === "ArrowDown" ? 1 : -1) + n) % n;
      return;
    }
    if ((ev.key === "Enter" || ev.key === "Tab") && !ev.ctrlKey && !ev.metaKey && pick()) {
      ev.preventDefault();
      return;
    }
    if (ev.key === "Escape") {
      ev.stopPropagation();
      text.value = "";
      return;
    }
  }
  if ((ev.key === "Enter" && !ev.ctrlKey && !ev.metaKey) || (ev.key === "Tab" && text.value.trim())) {
    if (text.value.trim()) {
      ev.preventDefault();
      commit();
    } else if (ev.key === "Enter") {
      ev.preventDefault();
    }
  } else if (ev.key === "Backspace" && !text.value && props.value.length) {
    remove(props.value.length - 1);
  }
}

function onBlur() {
  focused.value = false;
  commit();
}

defineExpose({
  focus: () => input.value?.focus(),
  // 发送前把输入框里还没变成地址块的文字也加上
  commit,
});
</script>

<template>
  <div class="gm-chips" @mousedown.self.prevent="input?.focus()">
    <NTag
      v-for="(a, i) in value"
      :key="a.email"
      round
      closable
      size="small"
      :type="isValidEmail(a.email) ? 'default' : 'error'"
      :title="isValidEmail(a.email) ? a.email : `无效的邮箱地址：${a.email}`"
      class="gm-addr-chip"
      @close="remove(i)"
    >
      <template #avatar>
        <span class="gm-avatar gm-chip-avatar" :style="{ background: avatarColor(a.name || a.email) }">{{ initial(a.name || a.email) }}</span>
      </template>
      {{ a.name || a.email }}
    </NTag>
    <input
      ref="input"
      v-model="text"
      class="gm-chip-input"
      type="text"
      autocomplete="off"
      spellcheck="false"
      :aria-label="label"
      @input="onInput"
      @keydown="onKeydown"
      @focus="focused = true"
      @blur="onBlur"
    />
    <div v-if="suggestions.length" class="gm-suggest" role="listbox">
      <div
        v-for="(c, i) in suggestions"
        :key="c.email"
        class="gm-suggest-item"
        :class="{ 'is-active': i === active }"
        role="option"
        @mousedown.prevent="pick(i)"
      >
        <span class="gm-avatar gm-suggest-avatar" :style="{ background: avatarColor(c.name || c.email) }">{{ initial(c.name || c.email) }}</span>
        <span class="gm-suggest-text">
          <span class="gm-suggest-name">{{ c.name || c.email }}</span>
          <span v-if="c.name" class="gm-suggest-email">{{ c.email }}</span>
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gm-chips {
    position: relative;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
    flex: 1;
    min-width: 0;
    min-height: 32px;
    padding: 2px 0;
    cursor: text;
}
.gm-addr-chip { max-width: 100%; }
.gm-chip-avatar { width: 18px; height: 18px; font-size: 11px; }
.gm-chip-input {
    flex: 1;
    min-width: 120px;
    border: 0;
    outline: 0;
    padding: 4px 0;
    font: inherit;
    background: transparent;
}
.gm-suggest {
    position: absolute;
    top: 100%;
    left: 0;
    z-index: 20;
    width: min(440px, 100%);
    padding: 8px 0;
    background: #fff;
    border-radius: 8px;
    box-shadow: var(--gm-shadow-3);
}
.gm-suggest-item { display: flex; align-items: center; gap: 12px; padding: 6px 16px; cursor: pointer; }
.gm-suggest-item.is-active, .gm-suggest-item:hover { background: var(--gm-hover); }
.gm-suggest-avatar { width: 32px; height: 32px; }
.gm-suggest-text { display: flex; flex-direction: column; min-width: 0; line-height: 1.35; }
.gm-suggest-name { font-size: 14px; color: var(--gm-text); }
.gm-suggest-email { font-size: 12px; color: var(--gm-text-3); }
</style>
