<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { NButton, NButtonGroup, NCheckbox, NDropdown, NIcon, NRadioButton, NRadioGroup, NSpin } from "naive-ui";
import {
  ArrowDropDownOutlined,
  ArrowUpwardOutlined,
  BlockOutlined,
  ChevronLeftOutlined,
  ChevronRightOutlined,
  CloseOutlined,
  DeleteOutlined,
  DraftsOutlined,
  EditOutlined,
  MarkEmailUnreadOutlined,
  MoreVertOutlined,
  RefreshOutlined,
  SnoozeOutlined,
} from "@vicons/material";
import IconBtn from "../components/IconBtn.vue";
import MailRow from "../components/MailRow.vue";
import RemoteSearch from "../components/RemoteSearch.vue";
import TimePicker from "../components/TimePicker.vue";
import { get, post } from "../api";
import { navAccount, openCompose, store } from "../store";
import { confirm, run, toast } from "../ui";
import { isMobile, makeRefresher, refreshNav, useCleanup } from "../composables/common";
import { usePageShortcuts } from "../composables/shortcuts";
import { onRealtime } from "../realtime";
import { snoozeMessages, syncAccount, syncAll } from "../actions";

const props = defineProps({
  // 列表的筛选条件（地址栏参数）：分栏阅读时取自详情页的 back 参数
  listQuery: { type: Object, required: true },
  // 分栏阅读时右侧打开的邮件
  activeId: { type: Number, default: null },
  // 紧凑的两行布局（分栏阅读、手机端）
  compact: { type: Boolean, default: false },
});

const router = useRouter();

const data = ref(null);
const loading = ref(false);
const error = ref("");
const selected = reactive(new Set());
const focusIndex = ref(-1);
const listEl = ref(null);
const newMailCount = ref(0);
// 已滑动删除、还在「可撤销」期内的邮件（界面上先隐藏，几秒后才真正删除）
const swipeHidden = reactive(new Set());

const DELETE_CONFIRM = "确定删除选中的邮件吗？邮件会移到 Gmail 的「已删除邮件」，30 天后由 Gmail 自动清除。";

// ---- 当前视图（地址栏参数） ----
const query = computed(() => props.listQuery);
const kind = computed(() => data.value?.kind || String(query.value.kind || "inbox"));
const page = computed(() => data.value?.page || 1);
const hasQuery = computed(() => !!(query.value.q || "").trim());
// 第一页且没有搜索时新邮件出现在最上面，可以直接刷新；否则显示「N 封新邮件」提示
const canAutoRefresh = computed(() => page.value === 1 && !hasQuery.value);

const items = computed(() => (data.value?.items || []).filter((m) => !swipeHidden.has(m.id)));
const showAccountChip = computed(() => !data.value?.account_id && (store.nav?.accounts.length || 0) > 1);

// 列表地址（详情页「返回」用）：总是带上文件夹和账号，后端据此计算「第 N 封，共 M 封」
function listPath(overrides = {}) {
  const q = { ...query.value, ...overrides };
  if (!q.kind) q.kind = kind.value;
  if (q.account_id === undefined) q.account_id = data.value?.account_id ?? "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(q)) {
    if (value === undefined || value === null) continue;
    params.append(key, String(value));
  }
  return `/messages/?${params}`;
}

function goView(overrides) {
  const q = { ...query.value, ...overrides };
  for (const key of Object.keys(q)) if (q[key] === undefined) delete q[key];
  router.push({ path: "/messages/", query: q });
}

// ---- 加载 ----
let controller = null;
async function load({ silent = false } = {}) {
  controller?.abort();
  controller = new AbortController();
  if (!silent) loading.value = true;
  try {
    const result = await get("/api/messages", query.value, { signal: controller.signal });
    data.value = result;
    error.value = "";
    store.view.kind = result.kind;
    store.view.accountId = result.account_id;
    store.view.unreadOnly = result.unread_only;
    // 刷新后保留仍在列表中的勾选
    const ids = new Set(result.items.map((m) => m.id));
    for (const id of [...selected]) if (!ids.has(id)) selected.delete(id);
    if (focusIndex.value >= result.items.length) focusIndex.value = result.items.length - 1;
    updateTitle();
  } catch (err) {
    if (err.name === "AbortError") return;
    if (!silent) error.value = err.message;
  } finally {
    if (!silent) loading.value = false;
  }
}

const silentRefresh = makeRefresher(() => load({ silent: true }));

function updateTitle() {
  const d = data.value;
  if (!d || props.activeId) return;   // 分栏阅读时标题由详情页设置
  const unread = d.kind === "inbox" ? store.nav?.unread_inbox : 0;
  const acc = navAccount(d.account_id);
  document.title = `${d.kind_label}${unread ? ` (${unread})` : ""}${acc ? ` - ${acc.email}` : ""} - Roundcube`;
}
watch(() => store.nav, updateTitle);

watch(
  () => JSON.stringify(props.listQuery),
  async () => {
    selected.clear();
    focusIndex.value = -1;
    newMailCount.value = 0;
    await load();
    restoreScroll();
  },
  { immediate: true },
);

// 分栏阅读时详情页里改了邮件（删除、星标、已读等）：刷新列表
watch(() => store.listVersion, () => silentRefresh());

// ---- 打开邮件；记住滚动位置，返回时恢复（手机端打开邮件会离开列表） ----
function scrollKey() {
  return `gm:scroll:${JSON.stringify(props.listQuery)}`;
}
function scrollTarget() {
  return listEl.value;
}
function saveScroll() {
  try {
    sessionStorage.setItem(scrollKey(), String(scrollTarget()?.scrollTop || 0));
  } catch { /* ignore */ }
}
async function restoreScroll() {
  let top = 0;
  try {
    top = Number(sessionStorage.getItem(scrollKey()) || 0);
    sessionStorage.removeItem(scrollKey());
  } catch { /* ignore */ }
  await nextTick();
  const el = scrollTarget();
  if (el) el.scrollTop = top;
}

function openMessage(item, ev) {
  const location = { name: "message", params: { id: item.id }, query: { back: listPath() } };
  if (ev && (ev.ctrlKey || ev.metaKey || ev.button === 1)) {
    window.open(router.resolve(location).href, "_blank");
    return;
  }
  saveScroll();
  router.push(location);
}

// ---- 选择 ----
const selectionMode = computed(() => selected.size > 0);
const allChecked = computed(() => items.value.length > 0 && items.value.every((m) => selected.has(m.id)));
const someChecked = computed(() => selected.size > 0 && !allChecked.value);

function toggleSelect(id) {
  if (selected.has(id)) selected.delete(id);
  else selected.add(id);
}
function selectBy(mode) {
  selected.clear();
  for (const m of items.value) {
    const unread = !m.is_read || m.thread_unread > 0;
    if (mode === "all" || (mode === "unread" && unread) || (mode === "read" && !unread)) selected.add(m.id);
  }
}
const selectOptions = [
  { key: "all", label: "全选" },
  { key: "none", label: "取消选择" },
  { key: "read", label: "已读" },
  { key: "unread", label: "未读" },
];

// ---- 操作 ----
async function act(action, ids) {
  if (!ids.length) return;
  if (action === "delete" && !(await confirm(DELETE_CONFIRM, { danger: true, positiveText: "删除" }))) return;
  const result = await run(post("/api/messages/actions", { action, ids }));
  if (!result) return;
  if (action === "delete") toast(ids.length > 1 ? `已删除 ${ids.length} 封邮件` : "已删除邮件");
  for (const id of ids) selected.delete(id);
  await load({ silent: true });
  refreshNav();
}

// 批量屏蔽：屏蔽选中邮件的发件人，并隐藏这些发件人已有的邮件
async function blockSelected() {
  const ids = [...selected];
  if (!ids.length) return;
  const ok = await confirm(
    `永久屏蔽选中 ${ids.length} 封邮件的发件人？\n后续同步将自动忽略这些发件人，并隐藏对应账号中已有的同发件人邮件。`,
    { danger: true, positiveText: "屏蔽" },
  );
  if (!ok) return;
  const result = await run(post("/api/senders/block", { ids }));
  if (!result) return;
  const n = result.senders.length;
  if (!n) toast("没有可屏蔽的发件人");
  else toast(`已屏蔽 ${n === 1 ? result.senders[0] : `${n} 个发件人`}${result.hidden ? `，隐藏了 ${result.hidden} 封邮件` : ""}`);
  selected.clear();
  await load({ silent: true });
  refreshNav();
}

async function toggleStar(item) {
  const value = !item.is_starred;
  item.is_starred = value;   // 先在界面上切换，失败时恢复
  const result = await run(post(`/api/messages/${item.id}/star`, { starred: value }));
  if (!result) item.is_starred = !value;
  else if (kind.value === "starred" && !value) silentRefresh();
}

// ---- 延后（稍后提醒） ----
// 在 Gmail 服务器上搜索（当前搜索词）
const remoteOpen = ref(false);

const snoozeOpen = ref(false);
function afterSnooze() {
  selected.clear();
  silentRefresh();
  refreshNav();
}
function snoozeSelected(until) {
  snoozeMessages([...selected], until, { onChange: afterSnooze });
}
function unsnoozeSelected() {
  snoozeMessages([...selected], null, { onChange: afterSnooze });
}

function refresh() {
  const accountId = data.value?.account_id;
  if (accountId) syncAccount(accountId);
  else syncAll();
}

const moreOptions = computed(() => {
  const d = data.value;
  const options = [
    { key: "mark-all-read", label: `全部标记为已读${d?.unread_total ? `（${d.unread_total}）` : ""}`, disabled: !d?.unread_total },
    { key: "conversation", label: d?.conversation ? "关闭会话视图" : "开启会话视图" },
  ];
  if (d?.account_id) options.push({ key: "sync-all", label: "同步所有账号" });
  options.push({ type: "divider", key: "d1" });
  options.push({
    key: "page-size",
    label: "每页显示",
    children: (d?.page_size_options || [25, 50, 100]).map((n) => ({
      key: `size-${n}`,
      label: `${n} 封${n === d?.page_size ? " ✓" : ""}`,
    })),
  });
  if (query.value.q || d?.account_id || d?.unread_only) {
    options.push({ type: "divider", key: "d2" }, { key: "clear", label: "清除搜索与筛选" });
  }
  return options;
});

async function onMore(key) {
  const d = data.value;
  if (key === "mark-all-read") {
    const ok = await confirm(
      `确定将当前视图下的全部 ${d.unread_total} 封未读邮件标记为已读吗？\n（同时会同步更新 Gmail 服务器的已读状态）`,
    );
    if (!ok) return;
    const result = await run(post("/api/messages/mark-all-read", { kind: d.kind, account_id: d.account_id, q: d.q }));
    if (result) {
      toast(`已将 ${result.affected} 封邮件标记为已读`);
      load({ silent: true });
      refreshNav();
    }
  } else if (key === "conversation") {
    if (await run(post("/api/messages/conversation-view", { on: !d.conversation }))) load();
  } else if (key === "sync-all") {
    syncAll();
  } else if (key.startsWith("size-")) {
    goView({ page_size: key.slice(5), page: undefined, after: undefined, before: undefined });
  } else if (key === "clear") {
    router.push({ path: "/messages/", query: { kind: d.kind, account_id: "" } });
  }
}

function toggleUnreadOnly() {
  goView({ unread: data.value?.unread_only ? undefined : "1", page: undefined, after: undefined, before: undefined });
}

// 较新 / 较旧：带上当前页首 / 尾邮件的游标，服务端不需要 OFFSET；回到第一页时不带游标
function goNewer() {
  const d = data.value;
  if (!d || d.page <= 1) return;
  goView({ page: d.page - 1, before: d.page > 2 ? d.prev_cursor : undefined, after: undefined });
}
function goOlder() {
  const d = data.value;
  if (!d || d.page >= d.pages) return;
  goView({ page: d.page + 1, after: d.next_cursor, before: undefined });
}

function openLabel(item, lb) {
  router.push({ path: "/messages/", query: { kind: `label-${lb.id}`, account_id: item.account_id } });
}

// ---- 触屏滑动删除：先隐藏并提示「撤销」，5 秒后（或离开页面时）才真正删除 ----
const SWIPE_UNDO_MS = 5000;
let swipeTimer = null;

async function commitSwipeDeletes() {
  clearTimeout(swipeTimer);
  swipeTimer = null;
  const ids = [...swipeHidden];
  if (!ids.length) return;
  try {
    await post("/api/messages/actions", { action: "delete", ids }, { keepalive: true });
    ids.forEach((id) => swipeHidden.delete(id));
    silentRefresh();
    refreshNav();
  } catch {
    ids.forEach((id) => swipeHidden.delete(id));
    toast("删除失败");
  }
}

function undoSwipeDeletes() {
  clearTimeout(swipeTimer);
  swipeTimer = null;
  swipeHidden.clear();
}

function onSwipeDelete(item) {
  swipeHidden.add(item.id);
  clearTimeout(swipeTimer);
  swipeTimer = setTimeout(commitSwipeDeletes, SWIPE_UNDO_MS);
  toast(`已删除 ${swipeHidden.size} 封邮件`, { actionText: "撤销", onAction: undoSwipeDeletes, duration: SWIPE_UNDO_MS });
}

window.addEventListener("pagehide", commitSwipeDeletes);
onBeforeUnmount(() => {
  window.removeEventListener("pagehide", commitSwipeDeletes);
  commitSwipeDeletes();
  controller?.abort();
});

// ---- 实时推送 ----
function eventMatchesView(ev) {
  const d = data.value;
  if (!d) return false;
  if (d.account_id && ev.account_id !== d.account_id) return false;
  if (d.kind === "inbox") return ev.inbox > 0;
  if (d.kind === "sent") return ev.sent > 0;
  if (d.kind === "junk") return ev.junk > 0;
  if (d.kind === "starred" || d.kind.startsWith("label-")) return false;   // 新邮件不会自带星标 / 标签
  return ev.inbox > 0 || ev.sent > 0;   // 「所有邮件」（不含垃圾邮件）
}

useCleanup(
  onRealtime("new_mail", (ev) => {
    if (!eventMatchesView(ev)) return;
    if (canAutoRefresh.value) silentRefresh();
    else newMailCount.value += data.value.kind === "junk" ? ev.junk || 0 : (ev.inbox || 0) + (ev.sent || 0);
  }),
  // 服务器上的已读 / 删除变化：不会把列表往下挤，直接原地刷新
  onRealtime("mail_changed", (ev) => {
    if (!data.value?.account_id || ev.account_id === data.value.account_id) silentRefresh();
  }),
  onRealtime("sync_done", (ev) => {
    if (data.value?.any_busy && (!data.value.account_id || ev.account_id === data.value.account_id)) silentRefresh();
  }),
  // 断线重连 / 从后台切回：期间可能错过了事件，补刷新一次
  onRealtime("open", (ev) => {
    if (!ev.first && canAutoRefresh.value) silentRefresh();
    else if (ev.first && data.value?.any_busy) silentRefresh();
  }),
  onRealtime("resume", () => canAutoRefresh.value && silentRefresh()),
);

// 没有实时连接时的兜底：同步进行中每 5 秒刷新一次列表
let busyTimer = null;
watch(
  () => data.value?.any_busy && store.realtime !== "connected",
  (poll) => {
    clearInterval(busyTimer);
    if (poll) busyTimer = setInterval(silentRefresh, 5000);
  },
);
onBeforeUnmount(() => clearInterval(busyTimer));

function showNewMail() {
  newMailCount.value = 0;
  if (canAutoRefresh.value) load({ silent: true });
  else router.push({ path: "/messages/", query: { kind: data.value.kind, account_id: data.value.account_id ?? "" } });
}

// ---- 键盘快捷键 ----
const focused = computed(() => items.value[focusIndex.value] || null);
function moveFocus(delta) {
  if (!items.value.length) return;
  focusIndex.value = Math.max(0, Math.min(items.value.length - 1, focusIndex.value + delta));
  nextTick(() => listEl.value?.querySelectorAll(".gm-row")[focusIndex.value]?.scrollIntoView({ block: "nearest" }));
}
function targetIds() {
  if (selected.size) return [...selected];
  return focused.value ? [focused.value.id] : [];
}
usePageShortcuts({
  j: () => moveFocus(1),
  k: () => moveFocus(-1),
  o: () => focused.value && openMessage(focused.value),
  Enter: () => (focused.value ? openMessage(focused.value) : false),
  x: () => focused.value && toggleSelect(focused.value.id),
  s: () => focused.value && toggleStar(focused.value),
  "#": () => act("delete", targetIds()),
  I: () => act("read", targetIds()),
  U: () => act("unread", targetIds()),
  Escape: () => selected.clear(),
  b: () => {
    const ids = targetIds();
    if (!ids.length || kind.value === "snoozed") return false;
    if (!selected.size) selected.add(ids[0]);
    snoozeOpen.value = true;
  },
});

const emptyText = computed(() => {
  const d = data.value;
  if (!d) return null;
  if (d.unread_only) return { title: "没有未读邮件", hint: "所有邮件都已读", link: "查看全部邮件", action: toggleUnreadOnly };
  if (d.q) return { title: "没有与搜索条件相符的邮件", link: "清除搜索", action: () => goView({ q: undefined, page: undefined }) };
  if (!d.has_accounts) return { title: "还没有添加邮箱账号", hint: "先到设置添加 Gmail 账号", link: "前往设置", action: () => router.push("/accounts/") };
  if (d.current_label) return { title: `没有带「${d.current_label.name}」标签的邮件` };
  if (d.kind === "snoozed") return { title: "没有延后的邮件", hint: "在邮件上点「延后」，邮件会在指定时间回到收件箱最上面" };
  if (d.kind === "starred") return { title: "没有已加星标的邮件", hint: "点邮件旁边的星形图标即可加星标，方便以后查找" };
  if (d.kind === "inbox") return { title: "收件箱里没有邮件", hint: "点工具栏的刷新按钮拉取最新邮件" };
  return { title: "这里没有邮件" };
});
</script>

<template>
  <div class="gm-panel gm-list-panel" :class="{ 'is-compact': compact }">
    <div class="gm-toolbar">
      <div class="gm-toolbar-start">
        <div class="gm-select">
          <NCheckbox
            :checked="allChecked"
            :indeterminate="someChecked"
            :disabled="!items.length"
            aria-label="选择当前页全部邮件"
            @update:checked="(v) => selectBy(v ? 'all' : 'none')"
          />
          <NDropdown trigger="click" :options="selectOptions" @select="selectBy">
            <IconBtn :icon="ArrowDropDownOutlined" title="选择方式" size="tiny" />
          </NDropdown>
        </div>

        <template v-if="selectionMode">
          <span class="gm-sel-count">已选 {{ selected.size }} 封</span>
          <NButtonGroup size="small">
            <NButton @click="act('read', [...selected])">
              <template #icon><NIcon :component="DraftsOutlined" /></template>
              <template v-if="!compact">标为已读</template>
            </NButton>
            <NButton @click="act('unread', [...selected])">
              <template #icon><NIcon :component="MarkEmailUnreadOutlined" /></template>
              <template v-if="!compact">标为未读</template>
            </NButton>
            <NButton v-if="kind === 'snoozed'" @click="unsnoozeSelected">
              <template #icon><NIcon :component="SnoozeOutlined" /></template>
              <template v-if="!compact">取消延后</template>
            </NButton>
            <NButton v-else @click="snoozeOpen = true">
              <template #icon><NIcon :component="SnoozeOutlined" /></template>
              <template v-if="!compact">延后</template>
            </NButton>
            <NButton @click="blockSelected">
              <template #icon><NIcon :component="BlockOutlined" /></template>
              <template v-if="!compact">屏蔽发件人</template>
            </NButton>
            <NButton type="error" ghost @click="act('delete', [...selected])">
              <template #icon><NIcon :component="DeleteOutlined" /></template>
              <template v-if="!compact">删除</template>
            </NButton>
          </NButtonGroup>
          <IconBtn :icon="CloseOutlined" title="取消选择" size="small" @click="selected.clear()" />
          <TimePicker v-model:show="snoozeOpen" title="延后到…" confirm-text="延后" @pick="snoozeSelected" />
        </template>
        <template v-else>
          <span v-if="!compact" class="gm-toolbar-title">{{ data?.kind_label }}</span>
          <NRadioGroup :value="data?.unread_only ? 'unread' : 'all'" size="small" @update:value="toggleUnreadOnly">
            <NRadioButton value="all">全部</NRadioButton>
            <NRadioButton value="unread">未读</NRadioButton>
          </NRadioGroup>
          <IconBtn :icon="RefreshOutlined" title="刷新（同步）" size="small" @click="refresh" />
          <NDropdown trigger="click" :options="moreOptions" @select="onMore">
            <IconBtn :icon="MoreVertOutlined" title="更多" size="small" />
          </NDropdown>
          <span v-if="data?.any_busy" class="gm-busy" title="正在同步"><NSpin :size="14" />同步中</span>
        </template>
      </div>

      <div class="gm-toolbar-end">
        <span v-if="data?.total" class="gm-pager-info">
          {{ data.first_index }}-{{ data.last_index }} / {{ data.total }}
        </span>
        <NButtonGroup size="small">
          <NButton :disabled="!data || data.page <= 1" title="较新" @click="goNewer">
            <template #icon><NIcon :component="ChevronLeftOutlined" /></template>
          </NButton>
          <NButton :disabled="!data || data.page >= data.pages" title="较旧" @click="goOlder">
            <template #icon><NIcon :component="ChevronRightOutlined" /></template>
          </NButton>
        </NButtonGroup>
      </div>
    </div>

    <div v-if="data?.q" class="gm-banner gm-remote-banner">
      <span>本地搜索只包含已同步的邮件。</span>
      <a href="#" @click.prevent="remoteOpen = true">在 Gmail 服务器上搜索「{{ data.q }}」</a>
      <span>（包括未同步、已归档的邮件）</span>
    </div>
    <RemoteSearch v-model:show="remoteOpen" :q="data?.q || ''" :account-id="data?.account_id || null" />
    <div v-if="data?.current_label" class="gm-banner">
      只显示收件箱、已发送和垃圾邮件中带有「{{ data.current_label.name }}」标签的邮件；在 Gmail 中已归档的邮件不在这里。
    </div>
    <div v-if="data?.kind === 'junk' && items.length" class="gm-banner">
      垃圾邮件文件夹中的邮件来自 Gmail 的垃圾邮件判断，确认无误的邮件可以打开后点「这不是垃圾邮件」。
    </div>

    <div ref="listEl" class="gm-list gm-panel-body" role="list">
      <NSpin v-if="loading && !data" class="gm-list-loading" />
      <div v-else-if="error" class="gm-empty">
        <div class="gm-empty-title">加载失败</div>
        <div>{{ error }}</div>
        <NButton style="margin-top: 16px" @click="load()">重试</NButton>
      </div>
      <template v-else>
        <MailRow
          v-for="(m, i) in items"
          :key="m.id"
          :item="m"
          :selected="selected.has(m.id)"
          :active="m.id === activeId"
          :focused="i === focusIndex"
          :selection-mode="selectionMode"
          :compact="compact"
          :show-folder="kind === 'all'"
          :account="showAccountChip ? navAccount(m.account_id) : null"
          @open="(ev) => openMessage(m, ev)"
          @toggle-select="toggleSelect(m.id)"
          @star="toggleStar(m)"
          @action="(a) => act(a, [m.id])"
          @swipe-delete="onSwipeDelete(m)"
          @label="(lb) => openLabel(m, lb)"
        />
        <div v-if="data && !items.length && emptyText" class="gm-empty">
          <div class="gm-empty-title">{{ emptyText.title }}</div>
          <div v-if="emptyText.hint">{{ emptyText.hint }}</div>
          <a v-if="emptyText.link" href="#" @click.prevent="emptyText.action">{{ emptyText.link }}</a>
        </div>
      </template>
    </div>

    <button v-if="newMailCount" type="button" class="gm-newmail-banner" @click="showNewMail">
      <NIcon :component="ArrowUpwardOutlined" /> {{ newMailCount }} 封新邮件
    </button>

    <NButton v-if="isMobile" type="primary" circle size="large" class="gm-fab" title="写邮件" @click="openCompose()">
      <template #icon><NIcon :component="EditOutlined" /></template>
    </NButton>
  </div>
</template>

<style scoped>
.gm-list-panel { position: relative; }
.gm-select { display: flex; align-items: center; }
.gm-sel-count { font-size: 13px; color: var(--gm-text-2); }
.gm-busy { display: inline-flex; align-items: center; gap: 6px; color: var(--gm-text-3); font-size: 12px; }
.gm-list-loading { display: flex; justify-content: center; padding: 64px 0; }
.gm-newmail-banner {
    position: absolute;
    top: 64px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 5;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border: 0;
    border-radius: 16px;
    background: var(--gm-primary);
    color: #fff;
    font-size: 13px;
    box-shadow: var(--gm-shadow-2);
    cursor: pointer;
}
.gm-fab {
    position: fixed;
    right: 20px;
    bottom: 24px;
    z-index: 10;
    width: 56px !important;
    height: 56px !important;
    box-shadow: var(--gm-shadow-2);
}
</style>
