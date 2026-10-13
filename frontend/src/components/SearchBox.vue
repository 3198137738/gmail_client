<script setup>
// 顶栏搜索框：点击时下拉显示最近的搜索记录（输入文字时按包含关系过滤），
// ↑↓ 选择、回车搜索、Esc 关闭；每条记录可单独删除，也可全部清空。
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NIcon, NInput } from "naive-ui";
import { CloseOutlined, HistoryOutlined, SearchOutlined } from "@vicons/material";
import { store } from "../store";
import { clearSearchHistory, forgetSearch, rememberSearch, searchHistory } from "../searchHistory";

const route = useRoute();
const router = useRouter();

const search = ref("");
const open = ref(false);
const active = ref(-1);
const inputRef = ref(null);

watch(
  () => route.fullPath,
  () => {
    search.value = route.name === "list" ? String(route.query.q || "") : "";
  },
  { immediate: true },
);

const suggestions = computed(() => {
  const q = search.value.trim().toLowerCase();
  const items = searchHistory.value.filter((x) => !q || (x.toLowerCase().includes(q) && x.toLowerCase() !== q));
  return items.slice(0, 8);
});
const showDropdown = computed(() => open.value && suggestions.value.length > 0);

watch(suggestions, () => {
  active.value = -1;
});

function submit(text = search.value) {
  const q = text.trim();
  search.value = q;
  open.value = false;
  inputRef.value?.blur();
  rememberSearch(q);
  // 在列表页搜索时保留当前文件夹、账号、每页封数和「仅看未读」
  const base = route.name === "list" ? route.query : { kind: store.view.kind || "inbox" };
  const query = { ...base, q, page: undefined, after: undefined, before: undefined };
  if (!q) delete query.q;
  router.push({ path: "/messages/", query });
}

function onKeydown(ev) {
  if (ev.key === "Escape") {
    open.value = false;
    ev.target.blur();
    return;
  }
  if (!showDropdown.value) {
    if (ev.key === "ArrowDown") open.value = true;
    return;
  }
  const n = suggestions.value.length;
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    ev.preventDefault();
    active.value = (active.value + (ev.key === "ArrowDown" ? 1 : -1) + n + 1) % (n + 1);
    if (active.value === n) active.value = -1;   // 回到输入框本身
  } else if (ev.key === "Enter" && active.value >= 0) {
    ev.preventDefault();
    submit(suggestions.value[active.value]);
  }
}

// 下拉列表上的 mousedown 已阻止默认行为，点记录时输入框不会失焦，这里只处理真正离开搜索框
function onBlur() {
  open.value = false;
}

function remove(item) {
  forgetSearch(item);
}
</script>

<template>
  <form class="gm-search" role="search" @submit.prevent="submit()">
    <NInput
      ref="inputRef"
      v-model:value="search"
      placeholder="搜索邮件（发件人、主题、正文）"
      clearable
      :input-props="{ 'aria-label': '搜索邮件', autocomplete: 'off', 'data-search': '' }"
      @focus="open = true"
      @blur="onBlur"
      @input="open = true"
      @clear="search = ''; submit('')"
      @keydown="onKeydown"
    >
      <template #prefix><NIcon :component="SearchOutlined" /></template>
    </NInput>

    <div v-if="showDropdown" class="gm-search-history" role="listbox" @mousedown.prevent>
      <div class="gm-search-history-head">
        <span>最近搜索</span>
        <button type="button" class="gm-search-history-clear" @click="clearSearchHistory">清空</button>
      </div>
      <div
        v-for="(item, i) in suggestions"
        :key="item"
        class="gm-search-history-item"
        :class="{ 'is-active': i === active }"
        role="option"
        :aria-selected="i === active"
        @click="submit(item)"
        @mouseenter="active = i"
      >
        <NIcon :component="HistoryOutlined" :size="18" class="gm-search-history-icon" />
        <span class="gm-search-history-text">{{ item }}</span>
        <button
          type="button"
          class="gm-search-history-remove"
          :aria-label="`删除搜索记录 ${item}`"
          title="删除这条记录"
          @click.stop="remove(item)"
        >
          <NIcon :component="CloseOutlined" :size="16" />
        </button>
      </div>
    </div>
  </form>
</template>

<style scoped>
.gm-search { position: relative; flex: 0 1 480px; min-width: 0; }
.gm-search-history {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    right: 0;
    z-index: 2000;
    padding: 4px 0;
    background: var(--gm-popover);
    border: 1px solid var(--gm-border);
    border-radius: var(--gm-radius);
    box-shadow: var(--gm-shadow-2);
}
.gm-search-history-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 12px 6px;
    font-size: 12px;
    color: var(--gm-text-3);
}
.gm-search-history-clear {
    border: 0;
    background: none;
    padding: 0;
    font: inherit;
    color: var(--gm-primary);
    cursor: pointer;
}
.gm-search-history-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 8px 6px 12px;
    cursor: pointer;
}
.gm-search-history-item.is-active { background: var(--gm-hover); }
.gm-search-history-icon { color: var(--gm-text-3); flex: none; }
.gm-search-history-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-search-history-remove {
    display: flex;
    border: 0;
    background: none;
    padding: 4px;
    border-radius: 50%;
    color: var(--gm-text-3);
    cursor: pointer;
    visibility: hidden;
}
.gm-search-history-item.is-active .gm-search-history-remove { visibility: visible; }
.gm-search-history-remove:hover { background: var(--gm-hover); color: var(--gm-error); }

@media (max-width: 768px) {
    .gm-search { flex: 1 1 auto; }
    .gm-search-history-remove { visibility: visible; }
}
</style>
