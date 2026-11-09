<script setup>
// 邮件列表：工具栏（标题、全部 / 未读、会话视图、刷新、更多、翻页）、选中后的批量操作栏、说明条、
// 邮件行、空状态 / 加载失败、新邮件提示；右键菜单、键盘快捷键、手机下拉刷新。
// 操作统一来自 mail/actions.js（与阅读区、命令面板、滑动手势一致）。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { NCheckbox, NDropdown, NIcon, NSpin } from "naive-ui";
import {
  ArrowDropDownOutlined,
  ArrowUpwardOutlined,
  ChevronLeftOutlined,
  ChevronRightOutlined,
  CloseOutlined,
  ForumOutlined,
  MoreHorizOutlined,
  OpenInNewOutlined,
  RefreshOutlined,
  SearchOffOutlined,
} from "@vicons/material";
import MailRow from "./MailRow.vue";
import BulkLabelPicker from "./BulkLabelPicker.vue";
import RemoteSearch from "../components/RemoteSearch.vue";
import GmIconButton from "../ui/GmIconButton.vue";
import GmEmptyState from "../ui/GmEmptyState.vue";
import GmBanner from "../ui/GmBanner.vue";
import GmContextMenu from "../ui/GmContextMenu.vue";
import { usePullToRefresh } from "../ui/usePullToRefresh";
import { post } from "../api";
import { navAccount, store } from "../store";
import { prefs } from "../prefs";
import { confirm, run, toast } from "../ui";
import { refreshNav } from "../composables/common";
import { usePageShortcuts } from "../composables/shortcuts";
import { syncAccount, syncAll } from "../actions";
import { registerCommandSource } from "../shell/commands";
import { useMailList } from "./useMailList";
import { useSelection } from "./useSelection";
import { actionIcon, actionLabel, actionTitle, actionsFor, runAction } from "./actions";
import { markRemoved } from "./pendingQueue";
import { prefetchMessage } from "./prefetch";
import { providerOf, trashRetention } from "./wording";

const props = defineProps({
  listQuery: { type: Object, required: true },
  activeId: { type: Number, default: null },
  layout: { type: String, default: "card" },
  // 整页阅读时列表隐藏（但保留状态），此时不响应列表快捷键
  visible: { type: Boolean, default: true },
  touch: { type: Boolean, default: false },
});

const router = useRouter();
const scrollEl = ref(null);
const listQuery = computed(() => props.listQuery);
const reading = computed(() => props.activeId !== null);

const list = useMailList(listQuery, { scrollEl, isReading: reading });
const { data, loading, error, items, kind, newMailCount } = list;
const sel = useSelection(items);
const focusIndex = ref(-1);

const showAccountChip = computed(() => !data.value?.account_id && (store.nav?.accounts.length || 0) > 1);
const inTrash = computed(() => kind.value === "trash" || kind.value === "blocked");
const provider = computed(() => providerOf(data.value?.account_id ? [data.value.account_id] : null));

watch(() => JSON.stringify(props.listQuery), () => {
  sel.clear();
  focusIndex.value = -1;
});

// 打开的邮件在列表里立即显示为已读（服务器端由阅读区标记）
watch(
  () => props.activeId,
  (id) => {
    const item = items.value.find((m) => m.id === id);
    if (item) {
      item.is_read = true;
      if ("thread_unread" in item) item.thread_unread = 0;
      focusIndex.value = items.value.indexOf(item);
    }
  },
);

// ---- 操作 ----
function ctxFor(ids, source = "list") {
  const idSet = new Set(ids);
  return {
    ids,
    items: items.value.filter((m) => idSet.has(m.id)),
    kind: kind.value,
    source,
    hooks: {
      removed(removed) {
        markRemoved(removed);
        removed.forEach((id) => sel.selected.delete(id));
        // 正在阅读的邮件离开了列表：阅读区自己处理跳转
      },
      pickLabels() {
        // 没有勾选时（快捷键 / 右键作用于单封）先选中它，批量标签作用于选中的邮件
        if (!sel.count.value) ids.forEach((id) => sel.selected.add(id));
        labelsOpen.value = true;
      },
    },
  };
}

const labelsOpen = ref(false);
function onLabelsApplied() {
  list.silentRefresh();
}

const selectionCtx = computed(() => ctxFor([...sel.selected]));
const bulkActions = computed(() => actionsFor("list", selectionCtx.value));

function act(id, ids) {
  if (!ids.length) return;
  runAction(id, ctxFor(ids));
}

function openMessage(item, ev) {
  const location = { name: "message", params: { id: item.id }, query: { back: list.listPath() } };
  if (ev && (ev.ctrlKey || ev.metaKey || ev.button === 1)) {
    window.open(router.resolve(location).href, "_blank");
    return;
  }
  list.saveScroll();
  router.push(location);
}

function openLabel(item, lb) {
  router.push({ path: "/messages/", query: { kind: `label-${lb.id}`, account_id: item.account_id } });
}

function refresh() {
  const accountId = data.value?.account_id;
  if (accountId) syncAccount(accountId);
  else syncAll();
}

function toggleUnreadOnly(value) {
  list.goView({ unread: value === "unread" ? "1" : undefined, page: undefined, after: undefined, before: undefined });
}

async function toggleConversation() {
  if (await run(post("/api/messages/conversation-view", { on: !data.value?.conversation }))) {
    toast(data.value?.conversation ? "已关闭会话视图" : "已开启会话视图：同一会话的往来邮件合并为一行");
    list.load();
  }
}

async function markAllRead() {
  const d = data.value;
  if (!d?.unread_total) return;
  const ok = await confirm(`确定将当前视图下的全部 ${d.unread_total} 封未读邮件标记为已读吗？\n（同时会同步到邮箱服务器）`);
  if (!ok) return;
  const result = await run(post("/api/messages/mark-all-read", { kind: d.kind, account_id: d.account_id, q: d.q }));
  if (result) {
    toast(`已将 ${result.affected} 封邮件标记为已读`);
    list.load({ silent: true });
    refreshNav();
  }
}

// 清空当前账号（或全部账号）的「已删除」/「屏蔽的邮件」
async function emptyTrash() {
  const d = data.value;
  const scope = d?.account_id ? "当前账号" : "所有账号";
  const ok = await confirm(
    d?.kind === "blocked"
      ? `确定永久删除${scope}「屏蔽的邮件」中的全部邮件吗？邮件会从邮箱服务器中彻底删除，无法恢复；屏蔽继续保留。`
      : `确定清空${scope}的「已删除」吗？其中的邮件会从邮箱服务器中彻底删除，无法恢复。`,
    { danger: true, positiveText: d?.kind === "blocked" ? "全部永久删除" : "清空" },
  );
  if (!ok) return;
  const result = await run(post("/api/messages/empty-trash", { account_id: d?.account_id || null, kind: d?.kind }));
  if (!result) return;
  toast(`已永久删除 ${result.affected} 封邮件`);
  sel.clear();
  list.load({ silent: true });
}

// ---- 更多菜单 ----
const moreOptions = computed(() => {
  const d = data.value;
  const options = [
    { key: "mark-all-read", label: `全部标为已读${d?.unread_total ? `（${d.unread_total}）` : ""}`, disabled: !d?.unread_total },
  ];
  if (d?.account_id) options.push({ key: "sync-all", label: "同步所有账号" });
  options.push(
    { type: "divider", key: "d1" },
    {
      key: "page-size",
      label: "每页显示",
      children: (d?.page_size_options || [25, 50, 100]).map((n) => ({ key: `size-${n}`, label: `${n} 封${n === d?.page_size ? " ✓" : ""}` })),
    },
    {
      key: "density",
      label: "列表密度",
      children: [
        { key: "density-comfortable", label: `舒适${prefs.density === "comfortable" ? " ✓" : ""}` },
        { key: "density-compact", label: `紧凑${prefs.density === "compact" ? " ✓" : ""}` },
      ],
    },
  );
  if (!props.touch) {
    options.push({
      key: "pane",
      label: "阅读窗格",
      children: [
        { key: "pane-right", label: `右侧${prefs.readingPane === "right" ? " ✓" : ""}` },
        { key: "pane-off", label: `关闭（整页阅读）${prefs.readingPane === "off" ? " ✓" : ""}` },
      ],
    });
  }
  if (props.listQuery.q || d?.account_id || d?.unread_only) {
    options.push({ type: "divider", key: "d2" }, { key: "clear", label: "清除搜索与筛选" });
  }
  return options;
});

function onMore(key) {
  if (key === "mark-all-read") markAllRead();
  else if (key === "sync-all") syncAll();
  else if (key.startsWith("size-")) list.goView({ page_size: key.slice(5), page: undefined, after: undefined, before: undefined });
  else if (key.startsWith("density-")) prefs.density = key.slice(8);
  else if (key.startsWith("pane-")) prefs.readingPane = key.slice(5);
  else if (key === "clear") router.push({ path: "/messages/", query: { kind: data.value.kind, account_id: "" } });
}

const selectOptions = [
  { key: "all", label: "全选" },
  { key: "none", label: "取消选择" },
  { key: "unread", label: "未读" },
  { key: "read", label: "已读" },
  { key: "starred", label: "已加星标" },
];

// 批量栏按常用程度排列，放不下的收进「更多」（标签需要弹出选择框，总在前面）
const BULK_ORDER = ["archive", "delete", "read", "unread", "labels", "snooze", "star", "restore", "spam", "block"];
const MAX_BULK_ICONS = 6;
const bulkSorted = computed(() =>
  [...bulkActions.value].sort((a, b) => BULK_ORDER.indexOf(a.id) - BULK_ORDER.indexOf(b.id)),
);
const bulkVisible = computed(() => bulkSorted.value.slice(0, MAX_BULK_ICONS));
const bulkOverflow = computed(() =>
  bulkSorted.value.slice(MAX_BULK_ICONS).map((a) => ({ key: a.id, label: actionLabel(a, selectionCtx.value) })),
);

// ---- 右键菜单 ----
const menu = ref({ show: false, x: 0, y: 0, ids: [] });
const menuCtx = computed(() => ctxFor(menu.value.ids, "context"));
const menuItems = computed(() => {
  const ctx = menuCtx.value;
  if (!ctx.ids.length) return [];
  const out = [];
  if (ctx.ids.length === 1) out.push({ key: "open", label: "打开" });
  for (const a of actionsFor("context", ctx)) {
    if (a.id === "openTab") out.push({ key: a.id, label: actionLabel(a, ctx), icon: OpenInNewOutlined });
    else out.push({ key: a.id, label: actionLabel(a, ctx), icon: actionIcon(a, ctx), danger: a.danger });
  }
  // 回复类放一组、状态类一组、删除最后
  const order = ["open", "openTab", "reply", "replyAll", "forward", "|", "archive", "read", "unread", "star", "snooze", "copyOtp", "|", "restore", "notSpam", "spam", "block", "delete"];
  const sorted = [];
  for (const key of order) {
    if (key === "|") {
      if (sorted.length && !sorted[sorted.length - 1].divider) sorted.push({ divider: true, key: `d${sorted.length}` });
      continue;
    }
    const found = out.find((o) => o.key === key);
    if (found) sorted.push(found);
  }
  if (sorted[sorted.length - 1]?.divider) sorted.pop();
  return sorted;
});

function openMenu(item, ev) {
  const ids = sel.selected.has(item.id) ? [...sel.selected] : [item.id];
  menu.value = { show: true, x: ev.clientX, y: ev.clientY, ids };
}

function onMenu(key) {
  const ids = menu.value.ids;
  if (key === "open") {
    const item = items.value.find((m) => m.id === ids[0]);
    if (item) openMessage(item);
    return;
  }
  runAction(key, ctxFor(ids, "context"));
}

// ---- 键盘 ----
const focused = computed(() => items.value[focusIndex.value] || null);
function moveFocus(delta) {
  if (!items.value.length) return;
  focusIndex.value = Math.max(0, Math.min(items.value.length - 1, focusIndex.value + delta));
  nextTick(() => scrollEl.value?.querySelectorAll(".gm-mr")[focusIndex.value]?.scrollIntoView({ block: "nearest" }));
}
function targetIds() {
  if (sel.count.value) return [...sel.selected];
  return focused.value ? [focused.value.id] : [];
}
function keyAction(id) {
  return () => {
    if (!props.visible) return false;
    const ids = targetIds();
    if (!ids.length) return false;
    runAction(id, ctxFor(ids));
  };
}
const guard = (fn) => () => (props.visible ? fn() : false);
usePageShortcuts({
  j: guard(() => moveFocus(1)),
  k: guard(() => moveFocus(-1)),
  o: guard(() => focused.value && openMessage(focused.value)),
  Enter: guard(() => (focused.value ? openMessage(focused.value) : false)),
  x: guard(() => focused.value && sel.toggle(focused.value.id)),
  Escape: guard(() => (sel.count.value ? sel.clear() : false)),
  s: keyAction("star"),
  e: keyAction("archive"),
  "!": keyAction("spam"),
  "#": keyAction("delete"),
  I: keyAction("read"),
  U: keyAction("unread"),
  b: keyAction("snooze"),
  l: keyAction("labels"),
});

// 键盘移动光标时预先加载这封邮件
watch(focusIndex, (i) => {
  const item = items.value[i];
  if (item && props.visible) prefetchMessage(item.id, list.listPath());
});

// ---- 命令面板：对选中（或光标所在）的邮件执行操作 ----
let unregister = null;
onMounted(() => {
  unregister = registerCommandSource(() => {
    if (!props.visible || reading.value) return [];
    const ids = targetIds();
    const cmds = [];
    if (ids.length) {
      const ctx = ctxFor(ids, "palette");
      const scope = sel.count.value ? `选中的 ${ids.length} 封邮件` : "当前邮件";
      for (const a of actionsFor("palette", ctx)) {
        cmds.push({ id: `mail-${a.id}`, group: "邮件", label: actionLabel(a, ctx), hint: scope, icon: actionIcon(a, ctx), shortcut: a.shortcut, run: () => runAction(a.id, ctx) });
      }
    }
    if (data.value?.unread_total) cmds.push({ id: "mark-all-read", group: "邮件", label: "全部标为已读", hint: data.value.kind_label, run: markAllRead });
    cmds.push(
      { id: "unread-only", group: "邮件", label: data.value?.unread_only ? "显示全部邮件" : "只看未读", run: () => toggleUnreadOnly(data.value?.unread_only ? "all" : "unread") },
      { id: "conversation", group: "邮件", label: data.value?.conversation ? "关闭会话视图" : "开启会话视图", icon: ForumOutlined, run: toggleConversation },
    );
    return cmds;
  });
});
onBeforeUnmount(() => unregister?.());

// ---- 手机下拉刷新 ----
const ptr = usePullToRefresh({
  el: scrollEl,
  enabled: () => props.touch,
  onRefresh: async () => {
    refresh();
    await list.load({ silent: true });
  },
});

// ---- 空状态 ----
const emptyText = computed(() => {
  const d = data.value;
  if (!d) return null;
  if (d.unread_only) return { title: "没有未读邮件", hint: "所有邮件都已读", link: "查看全部邮件", action: () => toggleUnreadOnly("all") };
  if (d.q) return { title: "没有与搜索条件相符的邮件", icon: SearchOffOutlined, link: "清除搜索", action: () => list.goView({ q: undefined, page: undefined }) };
  if (!d.has_accounts) return { title: "还没有添加邮箱账号", hint: "先到设置添加 Gmail 或 Outlook 账号", link: "前往设置", action: () => router.push("/accounts/") };
  if (d.current_label) return { title: `没有带「${d.current_label.name}」标签的邮件` };
  if (d.kind === "snoozed") return { title: "没有延后的邮件", hint: "在邮件上点「延后」，邮件会在指定时间回到收件箱最上面" };
  if (d.kind === "starred") return { title: "没有已加星标的邮件", hint: "点邮件旁边的星形图标即可加星标，方便以后查找" };
  if (d.kind === "inbox") return { title: "收件箱已清空", hint: "新邮件到达时会自动出现在这里" };
  if (d.kind === "trash") return { title: "「已删除」里没有邮件", hint: `删除的邮件会在这里保留一段时间，${trashRetention(provider.value)}` };
  if (d.kind === "blocked") return { title: "没有屏蔽发件人的邮件", hint: "屏蔽发件人后，其邮件会移到「已删除」并显示在这里" };
  return { title: "这里没有邮件" };
});

const remoteOpen = ref(false);
</script>

<template>
  <section class="gm-ml" :class="{ 'is-wide': layout === 'wide' }" aria-label="邮件列表">
    <header class="gm-ml-head">
      <div class="gm-ml-bar">
        <div class="gm-ml-select">
          <NCheckbox
            :checked="sel.allChecked.value"
            :indeterminate="sel.someChecked.value"
            :disabled="!items.length"
            aria-label="选择当前页全部邮件"
            @update:checked="(v) => sel.selectBy(v ? 'all' : 'none')"
          />
          <NDropdown trigger="click" :options="selectOptions" @select="(m) => (m === 'none' ? sel.clear() : sel.selectBy(m))">
            <GmIconButton :icon="ArrowDropDownOutlined" title="按条件选择" :size="24" :icon-size="18" />
          </NDropdown>
        </div>

        <template v-if="sel.active.value">
          <span class="gm-ml-selcount">已选 {{ sel.count.value }}</span>
          <div class="gm-ml-bulk" role="toolbar" aria-label="批量操作">
            <template v-for="a in bulkVisible" :key="a.id">
              <BulkLabelPicker v-if="a.id === 'labels'" v-model:show="labelsOpen" :items="sel.selectedItems.value" @applied="onLabelsApplied">
                <GmIconButton :icon="actionIcon(a, selectionCtx)" :title="actionTitle(a, selectionCtx)" :active="labelsOpen" @click="labelsOpen = !labelsOpen" />
              </BulkLabelPicker>
              <GmIconButton
                v-else
                :icon="actionIcon(a, selectionCtx)"
                :title="actionTitle(a, selectionCtx)"
                :danger="a.danger"
                :active="a.active?.(selectionCtx)"
                @click="runAction(a.id, selectionCtx)"
              />
            </template>
            <NDropdown v-if="bulkOverflow.length" trigger="click" :options="bulkOverflow" @select="(id) => runAction(id, selectionCtx)">
              <GmIconButton :icon="MoreHorizOutlined" title="更多操作" />
            </NDropdown>
          </div>
          <GmIconButton class="gm-ml-end" :icon="CloseOutlined" title="取消选择（Esc）" @click="sel.clear()" />
        </template>
        <template v-else>
          <h2 class="gm-ml-title">
            {{ data?.kind_label || "邮件" }}
            <span v-if="data?.unread_total && kind !== 'sent'" class="gm-ml-unread">{{ data.unread_total }} 封未读</span>
          </h2>
          <span v-if="data?.any_busy" class="gm-ml-busy" title="正在同步"><NSpin :size="12" /></span>
          <GmIconButton class="gm-ml-end" :icon="RefreshOutlined" title="同步并刷新" @click="refresh" />
          <NDropdown trigger="click" placement="bottom-end" :options="moreOptions" @select="onMore">
            <GmIconButton :icon="MoreHorizOutlined" title="更多" />
          </NDropdown>
        </template>
      </div>

      <div class="gm-ml-bar is-sub">
        <div class="gm-ml-seg" role="radiogroup" aria-label="筛选">
          <button
            type="button"
            role="radio"
            :aria-checked="!data?.unread_only"
            :class="{ 'is-on': !data?.unread_only }"
            @click="toggleUnreadOnly('all')"
          >全部</button>
          <button
            type="button"
            role="radio"
            :aria-checked="!!data?.unread_only"
            :class="{ 'is-on': data?.unread_only }"
            @click="toggleUnreadOnly('unread')"
          >未读</button>
        </div>
        <GmIconButton
          :icon="ForumOutlined"
          :title="data?.conversation ? '会话视图：开（点击关闭）' : '会话视图：关（点击开启）'"
          :active="!!data?.conversation"
          :size="30"
          :icon-size="18"
          @click="toggleConversation"
        />
        <span v-if="data?.total" class="gm-ml-range">{{ data.first_index }}–{{ data.last_index }} / {{ data.total }}</span>
        <GmIconButton :icon="ChevronLeftOutlined" title="较新" :size="30" :disabled="!data || data.page <= 1" @click="list.goNewer" />
        <GmIconButton :icon="ChevronRightOutlined" title="较旧" :size="30" :disabled="!data || data.page >= data.pages" @click="list.goOlder" />
      </div>
    </header>

    <div
      ref="scrollEl"
      class="gm-ml-scroll"
      role="listbox"
      aria-multiselectable="true"
      :aria-busy="loading"
      v-on="ptr.handlers"
    >
      <div v-if="ptr.pull.value || ptr.refreshing.value" class="gm-ml-ptr" :style="{ height: `${ptr.pull.value}px` }">
        <NSpin v-if="ptr.refreshing.value" :size="18" />
        <span v-else>{{ ptr.ready.value ? "松开刷新" : "下拉刷新" }}</span>
      </div>

      <GmBanner v-if="data?.q" action-text="在邮箱服务器上搜索" @action="remoteOpen = true">
        本地搜索只包含已同步的邮件，服务器上还可以找到未同步、已归档的邮件。
      </GmBanner>
      <GmBanner v-if="data?.current_label">
        只显示收件箱、已发送和垃圾邮件中带有「{{ data.current_label.name }}」标签的邮件；已归档的邮件不在这里。
      </GmBanner>
      <GmBanner v-if="kind === 'trash' && items.length" tone="warning" action-text="立即清空" @action="emptyTrash">
        「已删除」中的邮件{{ trashRetention(provider) }}；在这里删除会立即永久删除。
      </GmBanner>
      <GmBanner v-if="kind === 'blocked' && items.length" tone="warning" action-text="全部永久删除" @action="emptyTrash">
        这里是屏蔽发件人的邮件，已移到「已删除」，{{ trashRetention(provider) }}；在这里删除会立即永久删除。
      </GmBanner>
      <GmBanner v-if="kind === 'junk' && items.length">
        垃圾邮件来自邮箱服务商的判断，确认无误的邮件可以打开后点「不是垃圾邮件」。
      </GmBanner>

      <div v-if="loading && !data" class="gm-ml-skeleton" aria-hidden="true">
        <div v-for="i in 8" :key="i" class="gm-ml-skel-row">
          <span class="gm-ml-skel-avatar" />
          <span class="gm-ml-skel-lines"><span /><span /><span /></span>
        </div>
      </div>
      <GmEmptyState
        v-else-if="error"
        tone="error"
        title="邮件列表加载失败"
        :hint="error"
        action-text="重试"
        @action="list.load()"
      />
      <template v-else>
        <MailRow
          v-for="(m, i) in items"
          :key="m.id"
          :item="m"
          :kind="kind"
          :layout="layout"
          :selected="sel.selected.has(m.id)"
          :active="m.id === activeId"
          :focused="i === focusIndex && m.id !== activeId"
          :selection-mode="sel.active.value"
          :show-folder="kind === 'all'"
          :account="showAccountChip ? navAccount(m.account_id) : null"
          :touch="touch"
          @open="(ev) => openMessage(m, ev)"
          @toggle="(opts) => sel.toggle(m.id, opts)"
          @action="(id) => act(id, [m.id])"
          @context="(ev) => openMenu(m, ev)"
          @label="(lb) => openLabel(m, lb)"
          @prefetch="prefetchMessage(m.id, list.listPath())"
        />
        <GmEmptyState
          v-if="data && !items.length && emptyText"
          :title="emptyText.title"
          :hint="emptyText.hint"
          :icon="emptyText.icon"
          :action-text="emptyText.link"
          @action="emptyText.action"
        />
      </template>
    </div>

    <Transition name="gm-pill">
      <button v-if="newMailCount" type="button" class="gm-ml-pill" @click="list.showNewMail">
        <NIcon :component="ArrowUpwardOutlined" /> {{ newMailCount }} 封新邮件
      </button>
    </Transition>

    <RemoteSearch v-model:show="remoteOpen" :q="data?.q || ''" :account-id="data?.account_id || null" />
    <GmContextMenu v-model:show="menu.show" :x="menu.x" :y="menu.y" :items="menuItems" @select="onMenu" />
  </section>
</template>

<style scoped>
.gm-ml { position: relative; display: flex; flex-direction: column; height: 100%; min-height: 0; background: var(--gm-surface); }
.gm-ml-head { flex: none; border-bottom: 1px solid var(--gm-divider); }
.gm-ml-bar { display: flex; align-items: center; gap: var(--sp-1); min-height: 48px; padding: var(--sp-2) var(--sp-2) 0 var(--sp-3); }
.gm-ml-bar.is-sub { min-height: 40px; padding-top: 2px; padding-bottom: var(--sp-1); }
.gm-ml-select { display: flex; align-items: center; gap: 0; padding-left: 7px; margin-right: var(--sp-2); }
.gm-ml-title {
    display: flex;
    align-items: baseline;
    gap: var(--sp-2);
    min-width: 0;
    margin: 0;
    font-size: var(--fs-lg);
    font-weight: 600;
    color: var(--gm-text);
    white-space: nowrap;
    overflow: hidden;
}
.gm-ml-unread { font-size: var(--fs-xs); font-weight: 500; color: var(--gm-primary); }
.gm-ml-busy { display: inline-flex; margin-left: var(--sp-1); }
.gm-ml-end { margin-left: auto; }
.gm-ml-selcount { font-weight: 600; color: var(--gm-text); white-space: nowrap; margin-right: var(--sp-1); }
.gm-ml-bulk { display: flex; align-items: center; gap: 2px; min-width: 0; }
.gm-ml-seg {
    display: inline-flex;
    padding: 2px;
    margin-left: 4px;
    border-radius: var(--r-md);
    background: var(--gm-surface-2);
}
.gm-ml-seg button {
    height: 26px;
    padding: 0 12px;
    border: 0;
    border-radius: var(--r-sm);
    background: none;
    color: var(--gm-text-3);
    font: inherit;
    font-size: var(--fs-sm);
    cursor: pointer;
}
.gm-ml-seg button.is-on { background: var(--gm-surface); color: var(--gm-text); font-weight: 600; box-shadow: var(--gm-shadow-1); }
.gm-ml-range { margin-left: auto; font-size: var(--fs-xs); color: var(--gm-text-3); white-space: nowrap; font-variant-numeric: tabular-nums; }

.gm-ml-scroll { position: relative; flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; }
.gm-ml-scroll > :deep(.gm-banner-x:last-of-type) { margin-bottom: var(--sp-2); }
.gm-ml-ptr { display: flex; align-items: flex-end; justify-content: center; padding-bottom: 8px; overflow: hidden; color: var(--gm-text-3); font-size: var(--fs-sm); }

.gm-ml-skel-row { display: flex; gap: var(--sp-3); padding: 14px 16px 14px 20px; border-bottom: 1px solid var(--gm-divider); }
.gm-ml-skel-avatar { width: 36px; height: 36px; border-radius: 50%; background: var(--gm-surface-2); flex: none; }
.gm-ml-skel-lines { display: flex; flex-direction: column; gap: 8px; flex: 1; }
.gm-ml-skel-lines span { height: 10px; border-radius: 5px; background: var(--gm-surface-2); animation: gm-skel 1.2s ease-in-out infinite; }
.gm-ml-skel-lines span:nth-child(1) { width: 40%; }
.gm-ml-skel-lines span:nth-child(2) { width: 75%; }
.gm-ml-skel-lines span:nth-child(3) { width: 60%; }
@keyframes gm-skel { 50% { opacity: .5; } }

.gm-ml-pill {
    position: absolute;
    top: 104px;
    left: 50%;
    z-index: 5;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border: 0;
    border-radius: var(--r-pill);
    background: var(--gm-primary);
    color: #fff;
    font-size: var(--fs-sm);
    box-shadow: var(--gm-shadow-2);
    cursor: pointer;
    transform: translateX(-50%);
}
.gm-pill-enter-active, .gm-pill-leave-active { transition: opacity var(--dur) var(--ease), transform var(--dur) var(--ease); }
.gm-pill-enter-from, .gm-pill-leave-to { opacity: 0; transform: translate(-50%, -8px); }
</style>
