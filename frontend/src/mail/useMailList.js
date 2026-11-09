// 邮件列表的数据：按地址栏参数加载、游标翻页、实时推送刷新、「N 封新邮件」提示、滚动位置恢复。
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { get } from "../api";
import { navAccount, store } from "../store";
import { makeRefresher, useCleanup } from "../composables/common";
import { onRealtime } from "../realtime";
import { APP_NAME } from "../brand";
import { hiddenIds, removedIds } from "./pendingQueue";

export function useMailList(listQuery, { scrollEl, isReading }) {
  const router = useRouter();
  const data = ref(null);
  const loading = ref(false);
  const error = ref("");
  const newMailCount = ref(0);

  const query = computed(() => listQuery.value);
  const kind = computed(() => data.value?.kind || String(query.value.kind || "inbox"));
  const page = computed(() => data.value?.page || 1);
  const hasSearch = computed(() => !!String(query.value.q || "").trim());
  // 第一页且没有搜索时新邮件出现在最上面，可以直接刷新；否则显示「N 封新邮件」提示
  const canAutoRefresh = computed(() => page.value === 1 && !hasSearch.value);
  const items = computed(() => (data.value?.items || []).filter((m) => !hiddenIds.has(m.id) && !removedIds.has(m.id)));

  // 列表地址（打开邮件时作为 back 参数）：总是带上文件夹和账号，后端据此计算「第 N 封，共 M 封」
  function listPath(overrides = {}) {
    const q = { ...query.value, ...overrides };
    if (!q.kind) q.kind = kind.value;
    if (q.account_id === undefined) q.account_id = data.value?.account_id ?? "";
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(q)) {
      if (value !== undefined && value !== null) params.append(key, String(value));
    }
    return `/messages/?${params}`;
  }

  function goView(overrides) {
    const q = { ...query.value, ...overrides };
    for (const key of Object.keys(q)) if (q[key] === undefined) delete q[key];
    router.push({ path: "/messages/", query: q });
  }

  let controller = null;
  async function load({ silent = false } = {}) {
    controller?.abort();
    controller = new AbortController();
    if (!silent) loading.value = true;
    try {
      const result = await get("/api/messages", query.value, { signal: controller.signal });
      data.value = result;
      error.value = "";
      removedIds.clear();   // 以服务器的最新结果为准
      store.view.kind = result.kind;
      store.view.accountId = result.account_id;
      store.view.unreadOnly = result.unread_only;
      // 没带 back 参数打开邮件时（附件中心、通知等），回到这个列表
      store.lastListPath = listPath();
      updateTitle();
    } catch (err) {
      if (err.name === "AbortError") return;
      if (!silent) error.value = err.message || "加载失败";
    } finally {
      if (!silent) loading.value = false;
    }
  }

  const silentRefresh = makeRefresher(() => load({ silent: true }));

  function updateTitle() {
    const d = data.value;
    if (!d || isReading.value) return;   // 阅读时标题由阅读区设置
    const unread = d.kind === "inbox" ? store.nav?.unread_inbox : 0;
    const acc = navAccount(d.account_id);
    document.title = `${d.kind_label}${unread ? ` (${unread})` : ""}${acc ? ` - ${acc.email}` : ""} - ${APP_NAME}`;
  }
  watch(() => [store.nav, isReading.value], updateTitle);

  // ---- 滚动位置：切换列表时记住，回来时恢复 ----
  const scrollKey = () => `gm:scroll:${JSON.stringify(query.value)}`;
  function saveScroll() {
    try {
      sessionStorage.setItem(scrollKey(), String(scrollEl.value?.scrollTop || 0));
    } catch {
      /* 忽略 */
    }
  }
  async function restoreScroll() {
    let top = 0;
    try {
      top = Number(sessionStorage.getItem(scrollKey()) || 0);
    } catch {
      /* 忽略 */
    }
    await nextTick();
    if (scrollEl.value) scrollEl.value.scrollTop = top;
  }

  watch(
    () => JSON.stringify(listQuery.value),
    async (next, prev) => {
      if (prev) {
        try {
          sessionStorage.setItem(`gm:scroll:${prev}`, String(scrollEl.value?.scrollTop || 0));
        } catch {
          /* 忽略 */
        }
      }
      newMailCount.value = 0;
      await load();
      restoreScroll();
    },
    { immediate: true },
  );

  // 其他地方改了邮件（阅读区里删除、星标、已读等）：刷新
  watch(() => store.listVersion, () => silentRefresh());

  // ---- 翻页（较新 / 较旧带上游标，服务端不需要 OFFSET；回到第一页时不带游标） ----
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

  // ---- 实时推送 ----
  function eventMatchesView(ev) {
    const d = data.value;
    if (!d) return false;
    if (d.account_id && ev.account_id !== d.account_id) return false;
    if (d.kind === "inbox") return ev.inbox > 0;
    if (d.kind === "sent") return ev.sent > 0;
    if (d.kind === "junk") return ev.junk > 0;
    // 已删除的变化走 mail_changed；新邮件不会自带星标 / 标签
    if (["trash", "blocked", "starred", "snoozed"].includes(d.kind) || d.kind.startsWith("label-")) return false;
    return ev.inbox > 0 || ev.sent > 0;   // 所有邮件（不含垃圾邮件）
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

  // 没有实时连接时的兜底：同步进行中每 5 秒刷新一次
  let busyTimer = null;
  watch(
    () => data.value?.any_busy && store.realtime !== "connected",
    (poll) => {
      clearInterval(busyTimer);
      if (poll) busyTimer = setInterval(silentRefresh, 5000);
    },
  );
  onBeforeUnmount(() => {
    clearInterval(busyTimer);
    controller?.abort();
  });

  function showNewMail() {
    newMailCount.value = 0;
    if (canAutoRefresh.value) load({ silent: true });
    else router.push({ path: "/messages/", query: { kind: data.value.kind, account_id: data.value.account_id ?? "" } });
  }

  return {
    data,
    loading,
    error,
    items,
    kind,
    newMailCount,
    load,
    silentRefresh,
    listPath,
    goView,
    goNewer,
    goOlder,
    showNewMail,
    saveScroll,
  };
}
