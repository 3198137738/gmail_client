<script setup>
// 账号切换器（' 键、图标栏头像、面板标题）：搜索名称 / 邮箱 / 分组，↑↓ 选择、回车切换。
// 不搜索时：全部账号 → 最近使用 → 按服务商 / 分组列出全部账号。手机端全屏显示。
import { computed, nextTick, ref, watch } from "vue";
import { NIcon, NInput, NModal } from "naive-ui";
import { LayersOutlined, SearchOutlined } from "@vicons/material";
import GmAvatar from "../ui/GmAvatar.vue";
import GmCountBadge from "../ui/GmCountBadge.vue";
import { bestScore } from "../ui/fuzzy";
import { store } from "../store";
import { prefs } from "../prefs";
import { isMobile } from "../composables/common";
import { accountSections, accounts, currentAccount, nav, switchAccount } from "./navModel";

const query = ref("");
const active = ref(0);
const inputRef = ref(null);
const listRef = ref(null);

watch(
  () => store.accountSwitcher,
  async (open) => {
    if (!open) return;
    query.value = "";
    active.value = 0;
    await nextTick();
    inputRef.value?.focus();
  },
);

// 扁平的条目列表（heading 只用于显示，不可选中）
const entries = computed(() => {
  const q = query.value.trim();
  if (q) {
    return accounts.value
      .map((a) => ({ a, score: bestScore([a.name, a.email, a.group || ""], q) }))
      .filter((x) => x.score >= 0)
      .sort((x, y) => y.score - x.score)
      .map(({ a }) => ({ type: "account", account: a, key: `s-${a.id}` }));
  }
  const out = [{ type: "all", key: "all" }];
  const recent = prefs.recentAccounts.map((id) => accounts.value.find((a) => a.id === id)).filter(Boolean);
  if (recent.length && accounts.value.length > 6) {
    out.push({ type: "heading", label: "最近使用", key: "h-recent" });
    out.push(...recent.map((a) => ({ type: "account", account: a, key: `r-${a.id}` })));
  }
  for (const sec of accountSections.value) {
    if (sec.provider) out.push({ type: "heading", label: sec.provider.name, color: sec.provider.color, key: `h-${sec.provider.key}` });
    else if (out.length > 1) out.push({ type: "heading", label: "全部", key: "h-all" });
    for (const g of sec.groups) {
      out.push(...g.accounts.map((a) => ({ type: "account", account: a, group: g.name, key: `g-${a.id}` })));
    }
    out.push(...sec.ungrouped.map((a) => ({ type: "account", account: a, key: `u-${a.id}` })));
  }
  return out;
});

const selectable = computed(() => entries.value.map((e, i) => (e.type === "heading" ? -1 : i)).filter((i) => i >= 0));

watch(query, () => {
  active.value = selectable.value[0] ?? 0;
});

function close() {
  store.accountSwitcher = false;
}

function choose(entry) {
  if (!entry || entry.type === "heading") return;
  close();
  switchAccount(entry.type === "all" ? "" : entry.account.id);
}

function move(step) {
  const list = selectable.value;
  if (!list.length) return;
  const pos = list.indexOf(active.value);
  active.value = list[(pos + step + list.length) % list.length];
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
    choose(entries.value[active.value]);
  }
}

function isCurrent(entry) {
  if (entry.type === "all") return !currentAccount.value;
  return currentAccount.value?.id === entry.account?.id;
}
</script>

<template>
  <NModal
    :show="store.accountSwitcher"
    :auto-focus="false"
    transform-origin="center"
    @update:show="(v) => !v && close()"
  >
    <div class="gm-sw-card" :class="{ 'is-mobile': isMobile }" role="dialog" aria-label="切换账号">
      <div class="gm-sw-search">
        <NInput
          ref="inputRef"
          v-model:value="query"
          size="large"
          placeholder="搜索账号（名称、邮箱、分组）"
          clearable
          :input-props="{ 'aria-controls': 'gm-sw-list', autocomplete: 'off' }"
          @keydown="onKeydown"
        >
          <template #prefix><NIcon :component="SearchOutlined" /></template>
        </NInput>
        <button v-if="isMobile" type="button" class="gm-sw-cancel" @click="close">取消</button>
      </div>
      <ul id="gm-sw-list" ref="listRef" class="gm-sw-list" role="listbox">
        <template v-for="(e, i) in entries" :key="e.key">
          <li v-if="e.type === 'heading'" class="gm-sw-heading" role="presentation">
            <span v-if="e.color" class="gm-sw-heading-bar" :style="{ background: e.color }" />{{ e.label }}
          </li>
          <li
            v-else
            class="gm-sw-item"
            :class="{ 'is-active': i === active, 'is-current': isCurrent(e) }"
            role="option"
            :aria-selected="i === active"
            @click="choose(e)"
            @mousemove="active = i"
          >
            <template v-if="e.type === 'all'">
              <span class="gm-sw-all"><NIcon :component="LayersOutlined" :size="18" /></span>
              <span class="gm-sw-main">
                <span class="gm-sw-name">全部账号</span>
                <span class="gm-sw-email">统一收件箱 · {{ accounts.length }} 个账号</span>
              </span>
              <GmCountBadge :count="nav?.unread_inbox || 0" />
              <kbd v-if="!isMobile" class="gm-sw-kbd">Alt 0</kbd>
            </template>
            <template v-else>
              <GmAvatar :name="e.account.name" :color="e.account.color" :size="30" />
              <span class="gm-sw-main">
                <span class="gm-sw-name">{{ e.account.name }}</span>
                <span class="gm-sw-email">{{ e.account.email }}</span>
              </span>
              <span v-if="e.group || (query && e.account.group)" class="gm-sw-group">{{ e.group || e.account.group }}</span>
              <GmCountBadge :count="e.account.unread_inbox" />
            </template>
          </li>
        </template>
        <li v-if="!entries.length" class="gm-sw-empty">没有匹配的账号</li>
      </ul>
      <div v-if="!isMobile" class="gm-sw-foot">
        <span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>
        <span><kbd>Enter</kbd> 切换</span>
        <span><kbd>Alt</kbd>+<kbd>1…9</kbd> 直接切换</span>
        <span><kbd>Esc</kbd> 关闭</span>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.gm-sw-card {
    display: flex;
    flex-direction: column;
    width: min(560px, calc(100vw - 32px));
    max-height: min(640px, 76vh);
    margin: 12vh auto auto;
    border-radius: var(--r-lg);
    background: var(--gm-popover);
    box-shadow: var(--gm-shadow-3);
    overflow: hidden;
}
.gm-sw-card.is-mobile {
    width: 100vw;
    height: 100dvh;
    max-height: none;
    margin: 0;
    border-radius: 0;
}
.gm-sw-search { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-3); border-bottom: 1px solid var(--gm-divider); }
.gm-sw-cancel { flex: none; border: 0; background: none; color: var(--gm-primary); font: inherit; padding: 8px; white-space: nowrap; cursor: pointer; }
.gm-sw-list { list-style: none; margin: 0; padding: var(--sp-2); overflow-y: auto; flex: 1; }
.gm-sw-heading {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: var(--sp-3) 10px var(--sp-1);
    font-size: var(--fs-xs);
    font-weight: 600;
    color: var(--gm-text-3);
}
.gm-sw-heading-bar { width: 3px; height: 12px; border-radius: 2px; }
.gm-sw-item {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding: 8px 10px;
    border-radius: var(--r-md);
    cursor: pointer;
}
.gm-sw-item.is-active { background: var(--gm-hover); }
.gm-sw-item.is-current .gm-sw-name { color: var(--gm-primary); }
.gm-sw-all {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    border-radius: 50%;
    background: var(--gm-primary-soft);
    color: var(--gm-primary);
    flex: none;
}
.gm-sw-main { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.gm-sw-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-sw-email { font-size: var(--fs-xs); color: var(--gm-text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-sw-group {
    flex: none;
    padding: 0 8px;
    border-radius: var(--r-pill);
    background: var(--gm-surface-2);
    color: var(--gm-text-3);
    font-size: var(--fs-xs);
    line-height: 20px;
}
.gm-sw-kbd, .gm-sw-foot kbd {
    padding: 0 5px;
    border: 1px solid var(--gm-border);
    border-radius: 4px;
    font: 11px/18px ui-monospace, Consolas, monospace;
    color: var(--gm-text-3);
}
.gm-sw-empty { padding: var(--sp-6); text-align: center; color: var(--gm-text-3); }
.gm-sw-foot {
    display: flex;
    gap: var(--sp-4);
    padding: var(--sp-2) var(--sp-4);
    border-top: 1px solid var(--gm-divider);
    font-size: var(--fs-xs);
    color: var(--gm-text-3);
}
.gm-sw-foot kbd { margin-right: 2px; }
</style>
