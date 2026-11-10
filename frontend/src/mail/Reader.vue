<script setup>
// 阅读区：工具栏（操作来自 mail/actions.js）、主题行（标签、文件夹、打印）、会话中的各封邮件、回复按钮。
// 删除 / 延后 / 恢复等让邮件离开当前列表的操作完成后，自动打开下一封（设置为「回到列表」时返回）。
// 手机端：顶部返回 + 上下封，底部操作栏。
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { NButton, NDropdown, NIcon } from "naive-ui";
import {
  ArrowBackOutlined,
  ChevronLeftOutlined,
  ChevronRightOutlined,
  CloseOutlined,
  ExpandLessOutlined,
  ExpandMoreOutlined,
  ForwardOutlined,
  MoreHorizOutlined,
  OpenInNewOutlined,
  PrintOutlined,
  ReplyAllOutlined,
  ReplyOutlined,
  ShieldOutlined,
} from "@vicons/material";
import ThreadMessage from "./ThreadMessage.vue";
import LabelPicker from "./LabelPicker.vue";
import GmIconButton from "../ui/GmIconButton.vue";
import GmEmptyState from "../ui/GmEmptyState.vue";
import { get } from "../api";
import { openCompose, store } from "../store";
import { prefs } from "../prefs";
import { refreshNav, useCleanup } from "../composables/common";
import { onRealtime } from "../realtime";
import { usePageShortcuts } from "../composables/shortcuts";
import { registerCommandSource } from "../shell/commands";
import { APP_NAME } from "../brand";
import { actionIcon, actionLabel, actionTitle, actionsFor, runAction } from "./actions";
import { hiddenIds, markRemoved } from "./pendingQueue";
import { cachedMessage, forgetMessage } from "./prefetch";

const props = defineProps({
  id: { type: Number, required: true },
  back: { type: String, default: "/messages/" },
  // 电脑端分栏：「返回」显示为关闭
  split: { type: Boolean, default: false },
  mobile: { type: Boolean, default: false },
});

const router = useRouter();
const FOLDER_NAMES = { inbox: "收件箱", sent: "已发送", junk: "垃圾邮件", trash: "已删除" };

const msg = ref(null);
const error = ref("");
const loading = ref(false);
const expanded = reactive({});
const showImages = ref(false);
const labelsOpen = ref(false);

const threaded = computed(() => (msg.value?.thread.length || 0) > 1);
const lastMsg = computed(() => msg.value?.thread[msg.value.thread.length - 1]);
const allExpanded = computed(() => msg.value?.thread.every((m) => expanded[m.id]));

function show(data) {
  msg.value = data;
  for (const key of Object.keys(expanded)) delete expanded[key];
  for (const m of data.thread) expanded[m.id] = m.expanded;
}

async function load() {
  loading.value = true;
  error.value = "";
  showImages.value = false;
  labelsOpen.value = false;
  // 预先加载过的先显示出来，再请求一次（标为已读、取最新状态）
  const cached = cachedMessage(props.id);
  if (cached) show(cached);
  forgetMessage(props.id);
  try {
    const data = await get(`/api/messages/${props.id}`, { back: props.back });
    if (data.id !== props.id && data.thread.every((m) => m.id !== props.id)) return;   // 已经切到别的邮件
    const keep = cached && msg.value === cached ? { ...expanded } : null;
    show(data);
    if (keep) Object.assign(expanded, keep);   // 用户在显示缓存时展开 / 收起过的保持不变
    store.view.kind = data.folder_kind;
    const backAccount = new URLSearchParams(data.back_url.split("?")[1] || "").get("account_id");
    store.view.accountId = backAccount ? Number(backAccount) : null;
    document.title = `${data.subject || "(无主题)"} - ${APP_NAME}`;
    // 打开邮件会把它标为已读：更新未读数和列表
    refreshNav();
    store.listVersion += 1;
  } catch (err) {
    msg.value = null;
    error.value = err.message || "加载失败";
  } finally {
    loading.value = false;
  }
}

watch(() => props.id, (id) => id && load(), { immediate: true });

// 当前邮件被删除（可撤销的删除先隐藏）：离开
watch(
  () => msg.value && hiddenIds.has(msg.value.id),
  (gone) => gone && afterRemoved(),
);

function goBack() {
  router.push(props.back);
}

function openSibling(id) {
  // 新邮件还在加载时 position 仍是上一封的，快速连按 j / k 会跳错：等加载完
  if (!id || msg.value?.id !== props.id) return;
  router.push({ name: "message", params: { id }, query: { back: props.back } });
}

function afterRemoved() {
  const pos = msg.value?.position;
  const next = prefs.afterAction === "next" ? pos?.older_id || pos?.newer_id : null;
  if (next) openSibling(next);
  else goBack();
}

// 正文到达后换一个地址让 iframe 重新加载（新邮件头部先行时正文稍后才有）
const bodyVersion = ref(0);
function bodyUrl(m) {
  const query = [];
  if (showImages.value) query.push("images=1");
  if (bodyVersion.value) query.push(`v=${bodyVersion.value}`);
  return `/messages/${m.id}/body${query.length ? `?${query.join("&")}` : ""}`;
}

async function refreshPendingBody() {
  try {
    const data = await get(`/api/messages/${props.id}`, { back: props.back, peek: 1 });
    if (!msg.value || data.id !== msg.value.id) return;
    const keep = { ...expanded };
    show(data);
    Object.assign(expanded, keep);
    bodyVersion.value += 1;
  } catch {
    // 下次事件再试
  }
}

useCleanup(
  onRealtime("mail_changed", (ev) => {
    const m = msg.value;
    if (m && ev.account_id === m.account_id && m.thread.some((t) => t.body_pending)) refreshPendingBody();
  }),
);

// ---- 操作 ----
const ctx = computed(() => {
  const m = msg.value;
  if (!m) return null;
  return {
    ids: [m.id],
    items: [m],
    kind: m.folder_kind,
    source: "reader",
    message: m,
    hooks: {
      removed(ids) {
        markRemoved(ids);
        afterRemoved();
      },
      pickLabels() {
        labelsOpen.value = true;
      },
    },
  };
});

const TOOLBAR_SKIP = new Set(["reply", "replyAll", "forward", "print", "openBody"]);
const toolbarActions = computed(() => (ctx.value ? actionsFor("reader", ctx.value).filter((a) => !TOOLBAR_SKIP.has(a.id)) : []));

// 手机端底部操作栏：回复、删除、延后、星标 + 更多
const MOBILE_MAIN = ["archive", "delete", "snooze", "star"];
const mobileMain = computed(() =>
  MOBILE_MAIN.map((id) => toolbarActions.value.find((a) => a.id === id)).filter(Boolean).slice(0, 3),
);
const mobileMore = computed(() => {
  if (!ctx.value) return [];
  const main = new Set(mobileMain.value.map((a) => a.id));
  const rest = actionsFor("reader", ctx.value).filter((a) => !main.has(a.id) && a.id !== "reply");
  return rest.map((a) => ({ key: a.id, label: actionLabel(a, ctx.value) }));
});

function run(id) {
  if (ctx.value) runAction(id, ctx.value);
}

function reply(m, mode) {
  if (mode === "forward") openCompose({ forward: m.id });
  else openCompose({ reply_to: m.id, mode: mode === "all" ? "all" : undefined });
}

function onMore(m, key) {
  if (key === "print") window.print();
  else if (key === "open") window.open(`/messages/${m.id}/body?images=1`, "_blank", "noopener");
  else reply(m, key);
}

function toggleAll() {
  const open = !allExpanded.value;
  for (const m of msg.value.thread) expanded[m.id] = open;
}

function viewBlocked() {
  router.push({ path: "/settings/senders", query: { account_id: msg.value.account_id, q: msg.value.sender_email } });
}

function onLabels(labels) {
  msg.value.thread_labels = labels;
}

const keyAction = (id) => () => (msg.value ? run(id) : false);
usePageShortcuts({
  u: goBack,
  Escape: () => (props.split ? goBack() : false),
  k: () => openSibling(msg.value?.position.newer_id),
  j: () => openSibling(msg.value?.position.older_id),
  r: () => lastMsg.value && reply(lastMsg.value),
  a: () => lastMsg.value && reply(lastMsg.value, "all"),
  f: () => lastMsg.value && reply(lastMsg.value, "forward"),
  s: keyAction("star"),
  e: keyAction("archive"),
  "!": keyAction("spam"),
  "#": keyAction("delete"),
  U: keyAction("unread"),
  b: keyAction("snooze"),
  l: keyAction("labels"),
});

let unregister = null;
onMounted(() => {
  unregister = registerCommandSource(() => {
    const c = ctx.value;
    if (!c) return [];
    return actionsFor("palette", { ...c, source: "palette" }).map((a) => ({
      id: `reader-${a.id}`,
      group: "邮件",
      label: actionLabel(a, c),
      hint: msg.value.subject || "(无主题)",
      icon: actionIcon(a, c),
      shortcut: a.shortcut,
      run: () => runAction(a.id, c),
    }));
  });
});
onBeforeUnmount(() => unregister?.());
</script>

<template>
  <section class="gm-rd" :class="{ 'is-mobile': mobile }" aria-label="阅读邮件">
    <header class="gm-rd-bar no-print">
      <GmIconButton
        :icon="split ? CloseOutlined : ArrowBackOutlined"
        :title="split ? '关闭（Esc）' : '返回列表（u）'"
        @click="goBack"
      />
      <div v-if="msg && !mobile" class="gm-rd-actions" role="toolbar" aria-label="邮件操作">
        <template v-for="a in toolbarActions" :key="a.id">
          <LabelPicker v-if="a.id === 'labels'" v-model:show="labelsOpen" :message="msg" @applied="onLabels">
            <GmIconButton :icon="actionIcon(a, ctx)" :title="actionTitle(a, ctx)" :active="labelsOpen" @click="labelsOpen = !labelsOpen" />
          </LabelPicker>
          <GmIconButton
            v-else
            :icon="actionIcon(a, ctx)"
            :title="actionTitle(a, ctx)"
            :danger="a.danger"
            :active="a.active?.(ctx)"
            :color="a.id === 'star' && a.active?.(ctx) ? 'var(--gm-star)' : ''"
            @click="run(a.id)"
          />
        </template>
        <GmIconButton
          v-if="msg.sender_blocked && msg.folder_kind !== 'sent'"
          :icon="ShieldOutlined"
          title="已屏蔽该发件人，查看屏蔽列表"
          @click="viewBlocked"
        />
      </div>
      <div v-if="msg" class="gm-rd-nav">
        <span v-if="!mobile" class="gm-rd-pos">{{ msg.position.position }} / {{ msg.position.total }}</span>
        <GmIconButton :icon="ChevronLeftOutlined" title="较新（k）" :disabled="!msg.position.newer_id" @click="openSibling(msg.position.newer_id)" />
        <GmIconButton :icon="ChevronRightOutlined" title="较旧（j）" :disabled="!msg.position.older_id" @click="openSibling(msg.position.older_id)" />
      </div>
    </header>

    <div class="gm-rd-scroll">
      <div v-if="loading && !msg" class="gm-rd-skeleton" aria-hidden="true">
        <span class="is-title" /><span /><span /><span class="is-body" />
      </div>
      <GmEmptyState v-else-if="error" tone="error" title="邮件加载失败" :hint="error" action-text="重试" @action="load">
        <NButton quaternary class="gm-rd-back" @click="goBack">返回列表</NButton>
      </GmEmptyState>

      <!-- 按邮件 id 整体重建：浏览器「翻译网页」会替换文字节点，原地改文字时译文不会更新 -->
      <div v-else-if="msg" :key="msg.id" class="gm-rd-thread">
        <div class="gm-rd-subject-row">
          <h1 class="gm-rd-subject">
            {{ msg.subject || "(无主题)" }}
            <span v-if="threaded" class="gm-rd-count" title="会话中的邮件数">{{ msg.thread.length }}</span>
          </h1>
          <div class="gm-rd-subject-actions no-print">
            <GmIconButton
              v-if="threaded"
              :icon="allExpanded ? ExpandLessOutlined : ExpandMoreOutlined"
              :title="allExpanded ? '全部收起' : '全部展开'"
              :size="32"
              :icon-size="18"
              @click="toggleAll"
            />
            <GmIconButton :icon="PrintOutlined" title="打印" :size="32" :icon-size="18" @click="onMore(msg, 'print')" />
            <GmIconButton :icon="OpenInNewOutlined" title="在新窗口中打开正文" :size="32" :icon-size="18" @click="onMore(msg, 'open')" />
          </div>
        </div>
        <div class="gm-rd-tags">
          <RouterLink
            class="gm-rd-tag"
            :class="{ 'is-junk': msg.folder_kind === 'junk' }"
            :to="`/messages/?kind=${msg.folder_kind}&account_id=${msg.account_id}`"
          >{{ FOLDER_NAMES[msg.folder_kind] || msg.folder_kind }}</RouterLink>
          <RouterLink
            v-for="lb in msg.thread_labels"
            :key="lb.id"
            class="gm-rd-tag is-label"
            :to="`/messages/?kind=label-${lb.id}&account_id=${msg.account_id}`"
            :title="lb.name"
          >{{ lb.name }}</RouterLink>
          <span v-if="msg.thread[0]?.account_email" class="gm-rd-account">{{ msg.thread[0].account_email }}</span>
        </div>

        <ThreadMessage
          v-for="m in msg.thread"
          :key="m.id"
          :message="m"
          :expanded="!threaded || !!expanded[m.id]"
          :collapsible="threaded"
          :body-src="bodyUrl(m)"
          :show-images="showImages"
          :folder-label="threaded && m.folder_kind !== msg.folder_kind ? FOLDER_NAMES[m.folder_kind] || m.folder_kind : ''"
          :compact-date="mobile"
          @toggle="expanded[m.id] = !expanded[m.id]"
          @reply="reply(m)"
          @more="(key) => onMore(m, key)"
          @show-images="showImages = true"
        />

        <div v-if="!mobile" class="gm-rd-reply no-print">
          <NButton @click="reply(lastMsg)">
            <template #icon><NIcon :component="ReplyOutlined" /></template>回复
          </NButton>
          <NButton @click="reply(lastMsg, 'all')">
            <template #icon><NIcon :component="ReplyAllOutlined" /></template>回复全部
          </NButton>
          <NButton @click="reply(lastMsg, 'forward')">
            <template #icon><NIcon :component="ForwardOutlined" /></template>转发
          </NButton>
        </div>
      </div>
    </div>

    <footer v-if="mobile && msg" class="gm-rd-dock no-print">
      <button type="button" class="gm-rd-dock-btn" @click="reply(lastMsg)">
        <NIcon :component="ReplyOutlined" :size="22" /><span>回复</span>
      </button>
      <button
        v-for="a in mobileMain"
        :key="a.id"
        type="button"
        class="gm-rd-dock-btn"
        :class="{ 'is-on': a.active?.(ctx) }"
        @click="run(a.id)"
      >
        <NIcon :component="actionIcon(a, ctx)" :size="22" /><span>{{ actionLabel(a, ctx).replace("…", "") }}</span>
      </button>
      <LabelPicker v-model:show="labelsOpen" :message="msg" @applied="onLabels">
        <NDropdown trigger="click" placement="top-end" :options="mobileMore" @select="run">
          <button type="button" class="gm-rd-dock-btn"><NIcon :component="MoreHorizOutlined" :size="22" /><span>更多</span></button>
        </NDropdown>
      </LabelPicker>
    </footer>
  </section>
</template>

<style scoped>
.gm-rd { display: flex; flex-direction: column; height: 100%; min-height: 0; background: var(--gm-surface); }
.gm-rd-bar {
    display: flex;
    align-items: center;
    gap: var(--sp-1);
    min-height: 52px;
    padding: var(--sp-2) var(--sp-3);
    border-bottom: 1px solid var(--gm-divider);
    flex: none;
}
.gm-rd-actions { display: flex; align-items: center; gap: 2px; min-width: 0; padding-left: var(--sp-2); margin-left: var(--sp-1); border-left: 1px solid var(--gm-divider); }
.gm-rd-nav { display: flex; align-items: center; gap: 2px; margin-left: auto; }
.gm-rd-pos { margin-right: var(--sp-1); font-size: var(--fs-xs); color: var(--gm-text-3); font-variant-numeric: tabular-nums; }

.gm-rd-scroll { flex: 1; min-height: 0; overflow-y: auto; }
.gm-rd-thread { max-width: 960px; padding: var(--sp-5) var(--sp-6) var(--sp-7); }
.gm-rd-subject-row { display: flex; align-items: flex-start; gap: var(--sp-2); }
.gm-rd-subject { flex: 1; margin: 0; font-size: var(--fs-xl); font-weight: 600; line-height: 1.35; color: var(--gm-text); word-break: break-word; }
.gm-rd-count {
    display: inline-block;
    margin-left: 6px;
    padding: 0 7px;
    border-radius: var(--r-pill);
    background: var(--gm-surface-2);
    color: var(--gm-text-3);
    font-size: var(--fs-xs);
    font-weight: 500;
    line-height: 20px;
    vertical-align: 3px;
}
.gm-rd-subject-actions { display: flex; flex: none; }
.gm-rd-tags { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: var(--sp-2) 0 var(--sp-4); }
.gm-rd-tag {
    padding: 0 8px;
    border: 1px solid var(--gm-border);
    border-radius: var(--r-sm);
    color: var(--gm-text-2);
    font-size: var(--fs-xs);
    line-height: 20px;
}
.gm-rd-tag:hover { border-color: var(--gm-primary); color: var(--gm-primary); }
.gm-rd-tag.is-junk { border-color: var(--gm-error-soft); background: var(--gm-error-soft); color: var(--gm-error); }
.gm-rd-account { margin-left: auto; font-size: var(--fs-xs); color: var(--gm-text-3); }
.gm-rd-reply { display: flex; flex-wrap: wrap; gap: var(--sp-3); padding-top: var(--sp-2); }
.gm-rd-back { margin-top: var(--sp-2); }

.gm-rd-skeleton { display: flex; flex-direction: column; gap: 12px; max-width: 960px; padding: var(--sp-6); }
.gm-rd-skeleton span { height: 12px; width: 60%; border-radius: 6px; background: var(--gm-surface-2); animation: gm-skel 1.2s ease-in-out infinite; }
.gm-rd-skeleton span.is-title { height: 22px; width: 50%; }
.gm-rd-skeleton span.is-body { height: 220px; width: 100%; border-radius: var(--r-lg); }
@keyframes gm-skel { 50% { opacity: .5; } }

/* 手机端 */
.gm-rd.is-mobile .gm-rd-bar { min-height: 52px; padding: var(--sp-1) var(--sp-2); }
.gm-rd.is-mobile .gm-rd-thread { padding: var(--sp-3) var(--sp-3) var(--sp-6); }
.gm-rd.is-mobile .gm-rd-subject { font-size: var(--fs-lg); }
.gm-rd.is-mobile .gm-rd-account { display: none; }
.gm-rd-dock {
    display: flex;
    height: calc(var(--tabbar-h) + env(safe-area-inset-bottom));
    padding-bottom: env(safe-area-inset-bottom);
    border-top: 1px solid var(--gm-divider);
    background: var(--gm-surface);
    flex: none;
}
.gm-rd-dock > :deep(*) { flex: 1; display: flex; }
.gm-rd-dock-btn {
    display: flex;
    flex: 1;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    border: 0;
    background: none;
    color: var(--gm-text-2);
    font: 500 11px var(--gm-font);
    cursor: pointer;
}
.gm-rd-dock-btn.is-on { color: var(--gm-primary); }

@media print {
    .gm-rd-thread { padding: 0; }
    .gm-rd-scroll { overflow: visible; }
}
</style>
