<script setup>
// 邮件列表中的一行。
// card（三栏 / 手机）：头像 · 发件人 · 日期 / 主题 / 摘要，标记（账号、标签、验证码、附件）在下方；
// wide（关闭阅读窗格的宽列表）：单行表格式。
// 悬停时头像变为复选框、右侧出现快捷操作；右键打开菜单；触屏左右滑动执行设置中的动作，长按进入多选。
import { computed, ref } from "vue";
import { NCheckbox, NIcon } from "naive-ui";
import { AttachFileOutlined, KeyOutlined, SnoozeOutlined, StarBorderOutlined, StarFilled } from "@vicons/material";
import GmAvatar from "../ui/GmAvatar.vue";
import FileIcon from "../ui/FileIcon.vue";
import { useSwipe } from "../ui/useSwipe";
import { fullDate, futureDate, listDate } from "../format";
import { copyText } from "../ui";
import { prefs } from "../prefs";
import { actionIcon, actionLabel, actionTitle, getAction } from "./actions";

const props = defineProps({
  item: { type: Object, required: true },
  kind: { type: String, default: "inbox" },
  layout: { type: String, default: "card" },   // card / wide
  selected: { type: Boolean, default: false },
  active: { type: Boolean, default: false },
  focused: { type: Boolean, default: false },
  selectionMode: { type: Boolean, default: false },
  showFolder: { type: Boolean, default: false },
  account: { type: Object, default: null },
  touch: { type: Boolean, default: false },
});
const emit = defineEmits(["open", "toggle", "action", "context", "label", "prefetch"]);

const FOLDER_NAMES = { inbox: "收件箱", sent: "已发送", junk: "垃圾邮件", trash: "已删除" };
const HOVER_ACTIONS = ["archive", "snooze", "toggleRead", "delete"];

const unread = computed(() => !props.item.is_read || props.item.thread_unread > 0);
const peer = computed(() => props.item.peer || "(未知)");
const atts = computed(() => props.item.attachments || []);
const ctx = computed(() => ({ ids: [props.item.id], items: [props.item], kind: props.kind, source: "list" }));

const hoverActions = computed(() =>
  HOVER_ACTIONS.map(getAction).filter((a) => a && a.available(ctx.value)),
);

// ---- 滑动 ----
const rowEl = ref(null);
const swipeAction = (dir) => {
  const id = dir === "right" ? prefs.swipeRight : prefs.swipeLeft;
  const action = id && id !== "none" ? getAction(id) : null;
  return action && action.available(ctx.value) ? action : null;
};
const swipe = useSwipe({
  el: rowEl,
  enabled: () => props.touch && !props.selectionMode,
  canSwipe: (dir) => !!swipeAction(dir),
  // 删除、延后等会让邮件离开当前列表，先滑出
  willLeave: (dir) => ["delete", "archive", "spam", "snooze"].includes(swipeAction(dir)?.id) || (swipeAction(dir)?.id === "star" && props.kind === "starred"),
  onCommit: (dir) => {
    const action = swipeAction(dir);
    if (action) emit("action", action.id);
    if (action?.id === "snooze") setTimeout(swipe.reset, 400);   // 取消选择时间时回到原位
  },
  onLongPress: () => emit("toggle", {}),
});
const swipeBg = computed(() => {
  const action = swipe.direction.value ? swipeAction(swipe.direction.value) : null;
  if (!action) return null;
  return {
    icon: actionIcon(action, ctx.value),
    label: actionLabel(action, ctx.value),
    tone: action.id === "delete" ? "danger" : action.id === "star" ? "star" : "primary",
  };
});

function onClick(ev) {
  if (swipe.justGestured()) return;
  if (props.selectionMode || ev.shiftKey) {
    emit("toggle", { range: ev.shiftKey });
    return;
  }
  if (String(window.getSelection?.() || "").trim()) return;   // 正在选择文字
  emit("open", ev);
}

// 悬停 150ms 以上才预先加载（鼠标只是划过时不请求）
let hoverTimer = null;
function onEnter() {
  if (props.touch) return;
  hoverTimer = setTimeout(() => emit("prefetch"), 150);
}
function onLeave() {
  clearTimeout(hoverTimer);
}

function onAuxClick(ev) {
  if (ev.button === 1) emit("open", ev);   // 中键：新标签页打开
}
</script>

<template>
  <div class="gm-mr-wrap">
    <div
      v-if="swipe.dx.value && swipeBg"
      class="gm-mr-swipe"
      :class="[`is-${swipeBg.tone}`, swipe.direction.value === 'left' ? 'is-left' : 'is-right', { 'is-armed': swipe.armed.value }]"
      aria-hidden="true"
    >
      <NIcon :component="swipeBg.icon" :size="22" />
      <span>{{ swipeBg.label }}</span>
    </div>
    <div
      ref="rowEl"
      class="gm-row gm-mr"
      :class="[
        `is-${layout}`,
        {
          'is-unread': unread,
          'is-selected': selected,
          'is-active': active,
          'is-focused': focused,
          'is-selecting': selectionMode,
        },
      ]"
      :style="swipe.style.value"
      role="option"
      :aria-selected="selected || active"
      :aria-label="`${unread ? '未读，' : ''}${peer}：${item.subject || '(无主题)'}`"
      @click="onClick"
      @auxclick="onAuxClick"
      @contextmenu.prevent="emit('context', $event)"
      @mouseenter="onEnter"
      @mouseleave="onLeave"
      v-on="swipe.handlers"
    >
      <span class="gm-mr-lead" @click.stop="emit('toggle', { range: $event.shiftKey })">
        <GmAvatar class="gm-mr-avatar" :name="peer" :size="layout === 'wide' ? 28 : 36" />
        <span class="gm-mr-check">
          <NCheckbox :checked="selected" :aria-label="`选择 ${item.subject || '(无主题)'}`" :focusable="false" />
        </span>
      </span>
      <span class="gm-mr-dot" aria-hidden="true" />

      <div class="gm-mr-top">
        <span class="gm-mr-peer" :title="item.peer_full">
          <template v-if="item.folder_kind === 'sent'">收件人：</template>{{ peer }}
        </span>
        <span v-if="item.thread_count > 1" class="gm-mr-count">{{ item.thread_count }}</span>
        <span
          v-if="account && layout !== 'wide'"
          class="gm-mr-acc"
          :style="{ '--acc': account.color }"
          :title="account.email"
        >{{ account.name }}</span>
      </div>

      <div class="gm-mr-body">
        <span v-if="layout === 'wide'" class="gm-mr-chips">
          <span v-if="account" class="gm-mr-acc" :style="{ '--acc': account.color }" :title="account.email">{{ account.name }}</span>
          <span v-if="showFolder && FOLDER_NAMES[item.folder_kind]" class="gm-mr-chip">{{ FOLDER_NAMES[item.folder_kind] }}</span>
          <button v-for="lb in item.labels" :key="lb.id" type="button" class="gm-mr-chip is-label" :title="lb.name" @click.stop="emit('label', lb)">
            {{ lb.name.split("/").pop() }}
          </button>
        </span>
        <button
          v-if="item.otp_code"
          type="button"
          class="gm-mr-otp"
          :title="`验证码 ${item.otp_code}，点击复制`"
          @click.stop="copyText(item.otp_code, `已复制验证码 ${item.otp_code}`)"
        >
          <NIcon :component="KeyOutlined" :size="13" />{{ item.otp_code }}
        </button>
        <span class="gm-mr-subject">{{ item.subject || "(无主题)" }}</span>
        <span v-if="item.snippet" class="gm-mr-snippet">{{ item.snippet }}</span>
      </div>

      <div v-if="layout !== 'wide' && (item.labels.length || (showFolder && FOLDER_NAMES[item.folder_kind]) || atts.length)" class="gm-mr-tags">
        <span v-if="showFolder && FOLDER_NAMES[item.folder_kind]" class="gm-mr-chip">{{ FOLDER_NAMES[item.folder_kind] }}</span>
        <button v-for="lb in item.labels" :key="lb.id" type="button" class="gm-mr-chip is-label" :title="lb.name" @click.stop="emit('label', lb)">
          {{ lb.name.split("/").pop() }}
        </button>
        <span v-for="(a, i) in atts.slice(0, 2)" :key="i" class="gm-mr-att" :title="a.name">
          <FileIcon :mime="a.mime_type" :size="14" />
          <span class="gm-mr-att-name">{{ a.name }}</span>
        </span>
        <span v-if="atts.length > 2" class="gm-mr-more">+{{ atts.length - 2 }}</span>
      </div>

      <div class="gm-mr-meta">
        <NIcon v-if="item.has_attachments" :component="AttachFileOutlined" :size="15" class="gm-mr-clip" />
        <span v-if="item.snoozed_until" class="gm-mr-date is-snoozed" :title="`延后到 ${fullDate(item.snoozed_until)}`">
          <NIcon :component="SnoozeOutlined" :size="13" />{{ futureDate(item.snoozed_until) }}
        </span>
        <span v-else class="gm-mr-date" :title="fullDate(item.date)">{{ listDate(item.date) }}</span>
      </div>

      <button
        type="button"
        class="gm-mr-star"
        :class="{ 'is-on': item.is_starred }"
        :aria-pressed="item.is_starred"
        :title="item.is_starred ? '取消星标（s）' : '加星标（s）'"
        aria-label="星标"
        @click.stop="emit('action', 'star')"
      >
        <NIcon :component="item.is_starred ? StarFilled : StarBorderOutlined" :size="18" />
      </button>

      <div v-if="!touch && !selectionMode" class="gm-mr-hover" @click.stop>
        <button
          v-for="a in hoverActions"
          :key="a.id"
          type="button"
          class="gm-mr-hover-btn"
          :class="{ 'is-danger': a.danger }"
          :title="actionTitle(a, ctx)"
          :aria-label="actionLabel(a, ctx)"
          @click="emit('action', a.id)"
        >
          <NIcon :component="actionIcon(a, ctx)" :size="18" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gm-mr-wrap { position: relative; }

/* ---- 卡片布局 ---- */
.gm-mr {
    position: relative;
    display: grid;
    grid-template-columns: 36px 1fr auto;
    grid-template-areas:
        "lead top meta"
        "lead body star"
        ". tags tags";
    column-gap: var(--sp-3);
    row-gap: 2px;
    padding: var(--row-pad-y) var(--row-pad-x) var(--row-pad-y) calc(var(--row-pad-x) + 4px);
    border-bottom: 1px solid var(--gm-divider);
    background: var(--gm-surface);
    color: var(--gm-text-2);
    cursor: pointer;
    transition: background-color var(--dur-fast) var(--ease);
}
.gm-mr:hover { background: var(--gm-hover); }
.gm-mr.is-selected { background: var(--gm-primary-soft); }
.gm-mr.is-active { background: var(--gm-selected); }
.gm-mr.is-active::before, .gm-mr.is-focused::before {
    content: "";
    position: absolute;
    left: 0;
    top: 6px;
    bottom: 6px;
    width: 3px;
    border-radius: 0 3px 3px 0;
    background: var(--gm-primary);
}
.gm-mr.is-focused:not(.is-active)::before { background: var(--gm-text-4); }

.gm-mr-lead { grid-area: lead; position: relative; display: flex; align-items: flex-start; justify-content: center; padding-top: 2px; }
.gm-mr-check {
    position: absolute;
    inset: 2px 0 auto;
    display: none;
    align-items: center;
    justify-content: center;
    height: 36px;
    border-radius: 50%;
    background: var(--gm-surface);
}
.gm-mr.is-selecting .gm-mr-check, .gm-mr.is-selected .gm-mr-check { display: flex; }
@media (hover: hover) {
    .gm-mr:hover .gm-mr-check { display: flex; }
}
.gm-mr-dot { position: absolute; left: 6px; top: calc(var(--row-pad-y) + 14px); width: 7px; height: 7px; border-radius: 50%; }
.gm-mr.is-unread .gm-mr-dot { background: var(--gm-primary); }

.gm-mr-top { grid-area: top; display: flex; align-items: center; gap: 6px; min-width: 0; }
.gm-mr-peer { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--gm-text); }
.gm-mr.is-unread .gm-mr-peer, .gm-mr.is-unread .gm-mr-subject { font-weight: 600; }
.gm-mr-count {
    flex: none;
    padding: 0 5px;
    border-radius: var(--r-pill);
    background: var(--gm-surface-2);
    color: var(--gm-text-3);
    font-size: 11px;
    line-height: 16px;
}
.gm-mr-acc {
    flex: none;
    max-width: 110px;
    margin-left: auto;
    padding: 0 6px;
    border-radius: var(--r-sm);
    background: color-mix(in srgb, var(--acc) 12%, transparent);
    color: var(--acc);
    font-size: 11px;
    line-height: 18px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.gm-mr-body { grid-area: body; display: flex; flex-direction: column; min-width: 0; }
.gm-mr-subject { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--gm-text); }
.gm-mr-snippet { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--gm-text-3); font-size: var(--fs-sm); }
.gm-mr-otp {
    display: inline-flex;
    align-items: center;
    align-self: flex-start;
    gap: 3px;
    height: 20px;
    margin: 2px 0;
    padding: 0 6px;
    border: 1px solid var(--gm-primary);
    border-radius: var(--r-sm);
    background: var(--gm-primary-soft);
    color: var(--gm-primary);
    font: 600 12px/1 ui-monospace, Consolas, monospace;
    letter-spacing: .5px;
    cursor: pointer;
}
.gm-mr-otp:hover { background: var(--gm-selected); }

.gm-mr-tags { grid-area: tags; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; padding-top: 4px; min-width: 0; }
.gm-mr-chip {
    max-width: 140px;
    padding: 0 6px;
    border: 1px solid var(--gm-border);
    border-radius: var(--r-sm);
    background: none;
    color: var(--gm-text-2);
    font: inherit;
    font-size: 11px;
    line-height: 18px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.gm-mr-chip.is-label { cursor: pointer; }
.gm-mr-chip.is-label:hover { border-color: var(--gm-primary); color: var(--gm-primary); }
.gm-mr-att {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    max-width: 160px;
    height: 22px;
    padding: 0 8px;
    border: 1px solid var(--gm-border);
    border-radius: var(--r-pill);
    font-size: 12px;
}
.gm-mr-att-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-mr-more { font-size: 12px; color: var(--gm-text-3); }

.gm-mr-meta { grid-area: meta; display: flex; align-items: center; justify-content: flex-end; gap: 4px; }
.gm-mr-date { font-size: var(--fs-xs); color: var(--gm-text-3); white-space: nowrap; }
.gm-mr.is-unread .gm-mr-date { color: var(--gm-primary); font-weight: 600; }
.gm-mr-date.is-snoozed { display: inline-flex; align-items: center; gap: 2px; color: var(--gm-primary); font-weight: 600; }
.gm-mr-clip { color: var(--gm-text-3); }
.gm-mr-star {
    grid-area: star;
    display: flex;
    align-items: center;
    justify-content: center;
    justify-self: end;
    width: 28px;
    height: 28px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: none;
    color: var(--gm-text-4);
    cursor: pointer;
}
.gm-mr-star:hover { color: var(--gm-star); background: var(--gm-hover); }
.gm-mr-star.is-on { color: var(--gm-star); }

/* 悬停操作：盖住日期 */
.gm-mr-hover {
    position: absolute;
    top: 6px;
    right: 10px;
    display: none;
    gap: 2px;
    padding: 2px;
    border-radius: var(--r-md);
    background: var(--gm-popover);
    box-shadow: var(--gm-shadow-1);
}
@media (hover: hover) {
    .gm-mr:hover .gm-mr-hover { display: flex; }
}
.gm-mr-hover-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    padding: 0;
    border: 0;
    border-radius: var(--r-sm);
    background: none;
    color: var(--gm-text-2);
    cursor: pointer;
}
.gm-mr-hover-btn:hover { background: var(--gm-hover); color: var(--gm-text); }
.gm-mr-hover-btn.is-danger:hover { background: var(--gm-error-soft); color: var(--gm-error); }

/* 紧凑密度：两行，小头像，不显示附件 */
[data-density="compact"] .gm-mr { grid-template-columns: 28px 1fr auto; }
[data-density="compact"] .gm-mr :deep(.gm-avatar-x) { width: 28px !important; height: 28px !important; font-size: 12px !important; }
[data-density="compact"] .gm-mr-check { height: 28px; }
[data-density="compact"] .gm-mr-dot { top: calc(var(--row-pad-y) + 10px); }
[data-density="compact"] .gm-mr-body { flex-direction: row; gap: 6px; align-items: baseline; }
[data-density="compact"] .gm-mr-subject { flex: none; max-width: 60%; }
[data-density="compact"] .gm-mr-snippet { flex: 1; min-width: 0; }
[data-density="compact"] .gm-mr-att, [data-density="compact"] .gm-mr-more { display: none; }

/* ---- 宽列表：单行 ---- */
.gm-mr.is-wide {
    grid-template-columns: 28px minmax(140px, 200px) 1fr auto 28px;
    grid-template-areas: "lead top body meta star";
    align-items: center;
    row-gap: 0;
    min-height: 44px;
    padding-top: 6px;
    padding-bottom: 6px;
}
.gm-mr.is-wide .gm-mr-lead { padding-top: 0; align-items: center; }
.gm-mr.is-wide .gm-mr-check { inset: 0; height: 28px; }
.gm-mr.is-wide .gm-mr-dot { top: 50%; margin-top: -3px; }
.gm-mr.is-wide .gm-mr-body { flex-direction: row; align-items: center; gap: 6px; }
.gm-mr.is-wide .gm-mr-chips { display: contents; }
.gm-mr.is-wide .gm-mr-acc { margin-left: 0; }
.gm-mr.is-wide .gm-mr-otp { margin: 0; align-self: center; }
.gm-mr.is-wide .gm-mr-subject { flex: none; max-width: 55%; }
.gm-mr.is-wide .gm-mr-snippet { flex: 1; min-width: 0; font-size: var(--fs-md); }
.gm-mr.is-wide .gm-mr-snippet::before { content: "— "; }
.gm-mr.is-wide .gm-mr-hover { top: 50%; transform: translateY(-50%); right: 44px; }

/* ---- 滑动背景 ---- */
.gm-mr-swipe {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    padding: 0 var(--sp-6);
    background: var(--gm-text-4);
    color: #fff;
    font-weight: 600;
    transition: background var(--dur-fast) var(--ease);
}
.gm-mr-swipe.is-left { flex-direction: row-reverse; }
.gm-mr-swipe.is-armed.is-danger { background: var(--gm-error); }
.gm-mr-swipe.is-armed.is-primary { background: var(--gm-primary); }
.gm-mr-swipe.is-armed.is-star { background: var(--gm-star); }
</style>
