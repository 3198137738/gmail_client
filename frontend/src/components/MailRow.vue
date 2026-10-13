<script setup>
// 邮件列表中的一行。宽屏是表格式的一行：复选框、未读标记、头像、发件人、标签、主题 — 摘要、日期、星标，
// 悬停时显示删除 / 标为已读等按钮；紧凑模式（分栏阅读、手机端）是两行的卡片，点头像选中。
// 触屏上左右滑动删除。
import { computed, ref } from "vue";
import { NCheckbox, NIcon } from "naive-ui";
import {
  AttachFileOutlined,
  DeleteOutlined,
  DescriptionOutlined,
  DraftsOutlined,
  FolderZipOutlined,
  ImageOutlined,
  MarkEmailUnreadOutlined,
  PictureAsPdfOutlined,
  SnoozeOutlined,
  StarFilled,
  StarBorderOutlined,
  TableChartOutlined,
} from "@vicons/material";
import IconBtn from "./IconBtn.vue";
import { attachmentKind, avatarColor, fullDate, futureDate, initial, listDate } from "../format";

const props = defineProps({
  item: { type: Object, required: true },
  selected: { type: Boolean, default: false },
  // 分栏阅读时右侧正在显示的邮件
  active: { type: Boolean, default: false },
  // 紧凑的两行布局（分栏阅读、手机端）
  compact: { type: Boolean, default: false },
  focused: { type: Boolean, default: false },
  selectionMode: { type: Boolean, default: false },
  showFolder: { type: Boolean, default: false },
  account: { type: Object, default: null },
});
const emit = defineEmits(["open", "toggle-select", "star", "action", "swipe-delete", "label"]);

const FOLDER_NAMES = { inbox: "收件箱", sent: "已发送", junk: "垃圾邮件" };
const ATT_ICONS = {
  pdf: [PictureAsPdfOutlined, "#d93025"],
  image: [ImageOutlined, "#d93025"],
  sheet: [TableChartOutlined, "#188038"],
  zip: [FolderZipOutlined, "#5f6368"],
  doc: [DescriptionOutlined, "#1a73e8"],
};

const unread = computed(() => !props.item.is_read || props.item.thread_unread > 0);
const peer = computed(() => props.item.peer || "(未知)");
const atts = computed(() => props.item.attachments || []);

function onClick(ev) {
  if (Date.now() - lastSwipeAt < 400) return;   // 滑动结束后浏览器可能补发一次 click
  if (props.selectionMode) {
    emit("toggle-select");
    return;
  }
  const selected = String(window.getSelection?.() || "").trim();
  if (selected) return;   // 正在选择文字
  emit("open", ev);
}

// ---- 触屏左右滑动删除（与 Gmail App 一致） ----
const rowEl = ref(null);
const dx = ref(0);
const swiping = ref(false);
const leaving = ref(false);
let startX = 0;
let startY = 0;
let startT = 0;
let tracking = false;
let lastSwipeAt = 0;

function onTouchStart(ev) {
  if (props.selectionMode || ev.touches.length !== 1 || ev.target.closest("button, input, label")) {
    tracking = false;
    return;
  }
  const t = ev.touches[0];
  startX = t.clientX;
  startY = t.clientY;
  startT = Date.now();
  tracking = true;
  swiping.value = false;
  dx.value = 0;
}

function onTouchMove(ev) {
  if (!tracking) return;
  const t = ev.touches[0];
  const mx = t.clientX - startX;
  const my = t.clientY - startY;
  if (!swiping.value) {
    if (Math.abs(mx) < 10 && Math.abs(my) < 10) return;
    // 先判断方向：竖向为主则交给页面滚动
    if (Math.abs(my) >= Math.abs(mx)) {
      tracking = false;
      return;
    }
    swiping.value = true;
  }
  if (ev.cancelable) ev.preventDefault();
  dx.value = mx;
}

function onTouchEnd() {
  if (!tracking) return;
  tracking = false;
  if (!swiping.value) return;
  lastSwipeAt = Date.now();
  const width = rowEl.value?.offsetWidth || 360;
  const velocity = Math.abs(dx.value) / Math.max(1, Date.now() - startT);   // px/ms
  const commit = Math.abs(dx.value) > width * 0.35 || (Math.abs(dx.value) > 48 && velocity > 0.6);
  swiping.value = false;
  if (!commit) {
    dx.value = 0;
    return;
  }
  leaving.value = true;
  dx.value = dx.value > 0 ? width : -width;
  setTimeout(() => emit("swipe-delete"), 200);
}

const armed = computed(() => Math.abs(dx.value) > (rowEl.value?.offsetWidth || 360) * 0.35);
const rowStyle = computed(() =>
  dx.value || leaving.value
    ? {
        transform: `translateX(${dx.value}px)`,
        transition: swiping.value ? "none" : "transform .2s ease-out, opacity .2s ease-out",
        opacity: leaving.value ? 0 : 1,
      }
    : undefined,
);
</script>

<template>
  <div class="gm-row-wrap">
    <div v-if="dx" class="gm-swipe-bg" :class="{ 'is-right': dx < 0, 'is-armed': armed }">
      <NIcon :component="DeleteOutlined" :size="24" />
    </div>
    <div
      ref="rowEl"
      class="gm-row"
      :class="{
        'is-unread': unread,
        'is-selected': selected,
        'is-active': active,
        'is-focused': focused,
        'is-compact': compact,
      }"
      :style="rowStyle"
      role="listitem"
      @click="onClick"
      @touchstart.passive="onTouchStart"
      @touchmove="onTouchMove"
      @touchend="onTouchEnd"
      @touchcancel="tracking = false; swiping = false; dx = 0"
    >
      <!-- 宽屏：复选框列；紧凑模式：点头像选中 -->
      <div v-if="!compact" class="gm-row-check" @click.stop>
        <NCheckbox
          :checked="selected"
          :aria-label="`选择邮件 ${item.subject || '(无主题)'}`"
          @update:checked="emit('toggle-select')"
        />
      </div>
      <span class="gm-row-dot" :title="unread ? '未读' : ''" />
      <span
        class="gm-avatar gm-row-avatar"
        :style="{ background: selected ? 'var(--gm-primary)' : avatarColor(peer) }"
        aria-hidden="true"
        @click.stop="emit('toggle-select')"
      >{{ selected ? "✓" : initial(peer) }}</span>

      <div class="gm-row-sender" :title="item.peer_full">
        <template v-if="item.folder_kind === 'sent'">收件人：</template>{{ peer }}
        <span v-if="item.thread_count > 1" class="gm-thread-count">({{ item.thread_count }})</span>
      </div>
      <div class="gm-row-main">
        <div class="gm-row-line">
          <span v-if="showFolder && FOLDER_NAMES[item.folder_kind]" class="gm-chip">{{ FOLDER_NAMES[item.folder_kind] }}</span>
          <span
            v-for="lb in item.labels"
            :key="lb.id"
            class="gm-chip is-label"
            :title="lb.name"
            @click.stop="emit('label', lb)"
          >{{ lb.name.split("/").pop() }}</span>
          <span
            v-if="account"
            class="gm-chip is-account"
            :style="{ '--label': account.color }"
            :title="account.email"
          >{{ account.name }}</span>
          <span class="gm-row-subject">{{ item.subject || "(无主题)" }}</span>
          <span v-if="item.snippet" class="gm-row-snippet"><span class="gm-row-dash"> — </span>{{ item.snippet }}</span>
        </div>
        <div v-if="atts.length" class="gm-row-atts">
          <span v-for="(a, i) in atts.slice(0, 3)" :key="i" class="gm-att-chip" :title="a.name">
            <NIcon :component="ATT_ICONS[attachmentKind(a.mime_type)][0]" :color="ATT_ICONS[attachmentKind(a.mime_type)][1]" :size="14" />
            <span class="gm-att-chip-name">{{ a.name }}</span>
          </span>
          <span v-if="atts.length > 3" class="gm-att-more">+{{ atts.length - 3 }}</span>
        </div>
      </div>
      <div class="gm-row-end">
        <NIcon v-if="item.has_attachments" :component="AttachFileOutlined" :size="15" class="gm-row-clip" />
        <span v-if="item.snoozed_until" class="gm-row-date is-snoozed" :title="`延后到 ${fullDate(item.snoozed_until)}`">
          <NIcon :component="SnoozeOutlined" :size="14" /> {{ futureDate(item.snoozed_until) }}
        </span>
        <span v-else class="gm-row-date" :title="fullDate(item.date)">{{ listDate(item.date) }}</span>
      </div>
      <button
        type="button"
        class="gm-row-star"
        :class="{ 'is-starred': item.is_starred }"
        :aria-pressed="item.is_starred"
        :title="item.is_starred ? '已加星标' : '未加星标'"
        aria-label="星标"
        @click.stop="emit('star')"
      >
        <NIcon :component="item.is_starred ? StarFilled : StarBorderOutlined" :size="18" />
      </button>
      <div v-if="!compact" class="gm-row-actions" @click.stop>
        <IconBtn :icon="DeleteOutlined" title="删除" size="small" :icon-size="17" @click="emit('action', 'delete')" />
        <IconBtn
          v-if="unread"
          :icon="DraftsOutlined"
          title="标记为已读"
          size="small"
          :icon-size="17"
          @click="emit('action', 'read')"
        />
        <IconBtn v-else :icon="MarkEmailUnreadOutlined" title="标记为未读" size="small" :icon-size="17" @click="emit('action', 'unread')" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.gm-row-wrap { position: relative; }

/* ---- 宽屏：表格式一行 ---- */
.gm-row {
    position: relative;
    display: grid;
    grid-template-columns: 22px 8px 28px minmax(120px, 190px) 1fr auto 28px;
    grid-template-areas: "check dot avatar sender main end star";
    align-items: center;
    column-gap: 10px;
    min-height: 48px;
    padding: 6px 12px 6px 16px;
    background: var(--gm-surface);
    border-bottom: 1px solid var(--gm-divider);
    cursor: pointer;
    color: var(--gm-text-2);
    transition: background-color .15s;
}
.gm-row:hover { background: var(--gm-hover); }
.gm-row.is-selected { background: var(--gm-primary-soft); }
.gm-row.is-active { background: var(--gm-selected); }
.gm-row.is-active::after,
.gm-row.is-focused::after {
    content: "";
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 3px;
    background: var(--gm-primary);
}
.gm-row-check { grid-area: check; display: flex; }
.gm-row-dot { grid-area: dot; width: 8px; height: 8px; border-radius: 50%; }
.gm-row.is-unread .gm-row-dot { background: var(--gm-primary); }
.gm-row-avatar { grid-area: avatar; width: 28px; height: 28px; font-size: 12px; transition: background-color .15s; }
.gm-row-sender { grid-area: sender; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.gm-row.is-unread .gm-row-sender,
.gm-row.is-unread .gm-row-subject { font-weight: 600; color: var(--gm-text); }
.gm-thread-count { color: var(--gm-text-3); font-weight: 400; font-size: 12px; margin-left: 4px; }
.gm-row-main { grid-area: main; min-width: 0; }
.gm-row-line { display: flex; align-items: center; min-width: 0; white-space: nowrap; overflow: hidden; }
.gm-row-subject { flex: none; max-width: 70%; overflow: hidden; text-overflow: ellipsis; color: var(--gm-text-2); }
.gm-row-snippet { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--gm-text-3); }
.gm-row-atts { display: flex; gap: 6px; padding-top: 6px; }
.gm-att-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    max-width: 180px;
    height: 24px;
    padding: 0 8px;
    border: 1px solid var(--gm-border);
    border-radius: var(--gm-radius);
    font-size: 12px;
    color: var(--gm-text-2);
}
.gm-att-chip-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-att-more { align-self: center; color: var(--gm-text-3); font-size: 12px; }
.gm-row-end { grid-area: end; display: flex; align-items: center; gap: 6px; justify-self: end; }
.gm-row-date { font-size: 12px; color: var(--gm-text-3); white-space: nowrap; min-width: 52px; text-align: right; }
.gm-row.is-unread .gm-row-date { color: var(--gm-text); font-weight: 600; }
.gm-row-clip { color: var(--gm-text-3); }
.gm-row-date.is-snoozed { display: inline-flex; align-items: center; justify-content: flex-end; gap: 3px; color: var(--gm-primary); font-weight: 600; }
.gm-row-star {
    grid-area: star;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border: 0;
    border-radius: 50%;
    background: none;
    color: var(--gm-text-4);
    cursor: pointer;
    padding: 0;
}
.gm-row-star:hover { color: var(--gm-star); background: var(--gm-hover); }
.gm-row-star.is-starred { color: var(--gm-star); }
.gm-row-actions {
    position: absolute;
    right: 48px;
    top: 50%;
    transform: translateY(-50%);
    display: none;
    gap: 2px;
    padding: 2px 4px;
    border-radius: var(--gm-radius);
    background: var(--gm-popover);
    box-shadow: var(--gm-shadow-1);
}
@media (hover: hover) {
    .gm-row:not(.is-compact):hover .gm-row-actions { display: flex; }
}

/* ---- 紧凑模式：两行卡片（分栏阅读、手机端） ---- */
.gm-row.is-compact {
    grid-template-columns: 8px 36px 1fr auto;
    grid-template-areas:
        "dot avatar sender end"
        "dot avatar main star";
    row-gap: 2px;
    align-items: start;
    padding: 10px 12px 10px 8px;
}
.gm-row.is-compact .gm-row-dot { align-self: center; }
.gm-row.is-compact .gm-row-avatar { width: 36px; height: 36px; font-size: 15px; align-self: center; }
.gm-row.is-compact .gm-row-line { flex-direction: column; align-items: flex-start; white-space: normal; }
.gm-row.is-compact .gm-row-subject { max-width: 100%; white-space: nowrap; }
.gm-row.is-compact .gm-row-snippet { width: 100%; white-space: nowrap; font-size: 13px; }
.gm-row.is-compact .gm-row-dash { display: none; }
.gm-row.is-compact .gm-row-star { justify-self: end; align-self: end; }
.gm-row.is-compact .gm-chip,
.gm-row.is-compact .gm-row-atts { display: none; }

.gm-swipe-bg {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    padding: 0 24px;
    background: var(--gm-text-4);
    color: #fff;
    transition: background .15s;
}
.gm-swipe-bg.is-right { justify-content: flex-end; }
.gm-swipe-bg.is-armed { background: var(--gm-error); }
</style>
