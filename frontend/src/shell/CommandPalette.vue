<script setup>
// 命令面板（Ctrl+K / ⌘K）：输入关键字查找并执行命令；↑↓ 选择、回车执行、Esc 关闭。
// 不输入时按分组列出常用命令。
import { computed, nextTick, ref, watch } from "vue";
import { NIcon, NInput, NModal } from "naive-ui";
import { SearchOutlined } from "@vicons/material";
import { bestScore } from "../ui/fuzzy";
import { store } from "../store";
import { isMobile } from "../composables/common";
import { commands } from "./commands";

const query = ref("");
const active = ref(0);
const inputRef = ref(null);
const listRef = ref(null);

watch(
  () => store.palette,
  async (open) => {
    if (!open) return;
    query.value = "";
    active.value = 0;
    await nextTick();
    inputRef.value?.focus();
  },
  { immediate: true },
);

// 不搜索时只显示这些分组，避免一次列出几十个账号和标签
const IDLE_GROUPS = ["常用", "跳转", "外观", "帮助"];

const results = computed(() => {
  const q = query.value.trim();
  if (!q) return commands.value.filter((c) => IDLE_GROUPS.includes(c.group));
  return commands.value
    .map((c) => ({ c, score: bestScore([c.label, c.hint || "", c.keywords || "", c.group], q) }))
    .filter((x) => x.score >= 0)
    .sort((x, y) => y.score - x.score)
    .slice(0, 50)
    .map(({ c }) => c);
});

// 显示用：在分组变化处插入标题
const rows = computed(() => {
  const out = [];
  let group = null;
  results.value.forEach((c, index) => {
    if (!query.value.trim() && c.group !== group) {
      group = c.group;
      out.push({ heading: group, key: `h-${group}` });
    }
    out.push({ command: c, index, key: c.id });
  });
  return out;
});

watch(query, () => {
  active.value = 0;
});

function close() {
  store.palette = false;
}

function execute(command) {
  if (!command) return;
  close();
  command.run();
}

function move(step) {
  const n = results.value.length;
  if (!n) return;
  active.value = (active.value + step + n) % n;
  nextTick(() => listRef.value?.querySelector(".is-active")?.scrollIntoView({ block: "nearest" }));
}

function onKeydown(ev) {
  if (ev.key === "ArrowDown") {
    ev.preventDefault();
    move(1);
  } else if (ev.key === "ArrowUp") {
    ev.preventDefault();
    move(-1);
  } else if (ev.key === "Enter") {
    ev.preventDefault();
    execute(results.value[active.value]);
  }
}
</script>

<template>
  <NModal :show="store.palette" :auto-focus="false" @update:show="(v) => !v && close()">
    <div class="gm-pal" :class="{ 'is-mobile': isMobile }" role="dialog" aria-label="命令面板">
      <div class="gm-pal-search">
        <NInput
          ref="inputRef"
          v-model:value="query"
          size="large"
          placeholder="输入命令、文件夹、账号或设置…"
          :input-props="{ autocomplete: 'off', 'aria-controls': 'gm-pal-list' }"
          @keydown="onKeydown"
        >
          <template #prefix><NIcon :component="SearchOutlined" /></template>
        </NInput>
        <button v-if="isMobile" type="button" class="gm-pal-cancel" @click="close">取消</button>
      </div>
      <ul id="gm-pal-list" ref="listRef" class="gm-pal-list" role="listbox">
        <template v-for="row in rows" :key="row.key">
          <li v-if="row.heading" class="gm-pal-heading" role="presentation">{{ row.heading }}</li>
          <li
            v-else
            class="gm-pal-item"
            :class="{ 'is-active': row.index === active }"
            role="option"
            :aria-selected="row.index === active"
            @click="execute(row.command)"
            @mousemove="active = row.index"
          >
            <span v-if="row.command.color" class="gm-pal-dot" :style="{ background: row.command.color }" />
            <NIcon v-else-if="row.command.icon" :component="row.command.icon" :size="18" class="gm-pal-icon" />
            <span v-else class="gm-pal-icon" />
            <span class="gm-pal-label">{{ row.command.label }}</span>
            <span v-if="row.command.hint" class="gm-pal-hint">{{ row.command.hint }}</span>
            <kbd v-if="row.command.shortcut" class="gm-pal-kbd">{{ row.command.shortcut }}</kbd>
          </li>
        </template>
        <li v-if="!results.length" class="gm-pal-empty">没有匹配的命令</li>
      </ul>
    </div>
  </NModal>
</template>

<style scoped>
.gm-pal {
    display: flex;
    flex-direction: column;
    width: min(620px, calc(100vw - 32px));
    max-height: min(560px, 76vh);
    margin: 12vh auto auto;
    border-radius: var(--r-lg);
    background: var(--gm-popover);
    box-shadow: var(--gm-shadow-3);
    overflow: hidden;
}
.gm-pal.is-mobile { width: 100vw; height: 100dvh; max-height: none; margin: 0; border-radius: 0; }
.gm-pal-search { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-3); border-bottom: 1px solid var(--gm-divider); }
.gm-pal-cancel { flex: none; border: 0; background: none; color: var(--gm-primary); font: inherit; padding: 8px; white-space: nowrap; cursor: pointer; }
.gm-pal-list { list-style: none; margin: 0; padding: var(--sp-2); overflow-y: auto; flex: 1; }
.gm-pal-heading { padding: var(--sp-3) 10px var(--sp-1); font-size: var(--fs-xs); font-weight: 600; color: var(--gm-text-3); }
.gm-pal-item {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    height: 40px;
    padding: 0 10px;
    border-radius: var(--r-md);
    cursor: pointer;
}
.gm-pal-item.is-active { background: var(--gm-hover); }
.gm-pal-icon { width: 18px; color: var(--gm-text-3); flex: none; }
.gm-pal-dot { width: 10px; height: 10px; margin: 0 4px; border-radius: 3px; flex: none; }
.gm-pal-label { flex: none; max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-pal-hint { flex: 1; min-width: 0; color: var(--gm-text-3); font-size: var(--fs-sm); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-pal-kbd {
    margin-left: auto;
    padding: 0 6px;
    border: 1px solid var(--gm-border);
    border-radius: 4px;
    font: 11px/18px ui-monospace, Consolas, monospace;
    color: var(--gm-text-3);
}
.gm-pal-empty { padding: var(--sp-6); text-align: center; color: var(--gm-text-3); }
</style>
