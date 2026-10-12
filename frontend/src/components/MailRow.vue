<script setup>
// 邮件列表中的一行（Gmail 样式）：头像 / 复选框、星标、发件人、标签、主题 - 摘要、附件、日期，
// 悬停时显示删除 / 标为已读等按钮；触屏上左右滑动删除。
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
  StarFilled,
  StarBorderOutlined,
  TableChartOutlined,
} from "@vicons/material";
import IconBtn from "./IconBtn.vue";
import { attachmentKind, avatarColor, fullDate, initial, listDate } from "../format";

const props = defineProps({
  item: { type: Object, required: true },
  selected: { type: Boolean, default: false },
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
      :class="{ 'is-unread': unread, 'is-selected': selected, 'is-focused': focused, 'has-atts': atts.length }"
      :style="rowStyle"
      role="listitem"
      @click="onClick"
      @touchstart.passive="onTouchStart"
      @touchmove="onTouchMove"
      @touchend="onTouchEnd"
      @touchcancel="tracking = false; swiping = false; dx = 0"
    >
      <div class="gm-row-check" @click.stop="emit('toggle-select')">
        <span class="gm-avatar gm-row-avatar" :style="{ background: avatarColor(peer) }" aria-hidden="true">{{ initial(peer) }}</span>
        <NCheckbox
          class="gm-row-checkbox"
          :checked="selected"
          :aria-label="`选择邮件 ${item.subject || '(无主题)'}`"
          @click.stop
          @update:checked="emit('toggle-select')"
        />
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
        <NIcon :component="item.is_starred ? StarFilled : StarBorderOutlined" :size="20" />
      </button>
      <div class="gm-row-sender" :title="item.peer_full">
        <template v-if="item.folder_kind === 'sent'">收件人：</template>{{ peer }}
        <span v-if="item.thread_count > 1" class="gm-thread-count">{{ item.thread_count }}</span>
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
          <span v-if="item.snippet" class="gm-row-snippet"><span class="gm-row-dash"> - </span>{{ item.snippet }}</span>
        </div>
        <div v-if="atts.length" class="gm-row-atts">
          <span v-for="(a, i) in atts.slice(0, 3)" :key="i" class="gm-att-chip" :title="a.name">
            <NIcon :component="ATT_ICONS[attachmentKind(a.mime_type)][0]" :color="ATT_ICONS[attachmentKind(a.mime_type)][1]" :size="16" />
            <span class="gm-att-chip-name">{{ a.name }}</span>
          </span>
          <span v-if="atts.length > 3" class="gm-att-more">+{{ atts.length - 3 }}</span>
        </div>
      </div>
      <div class="gm-row-end">
        <NIcon v-if="item.has_attachments && !atts.length" :component="AttachFileOutlined" :size="16" class="gm-row-clip" />
        <span class="gm-row-date" :title="fullDate(item.date)">{{ listDate(item.date) }}</span>
      </div>
      <div class="gm-row-actions" @click.stop>
        <IconBtn :icon="DeleteOutlined" title="删除" size="small" :icon-size="18" @click="emit('action', 'delete')" />
        <IconBtn
          v-if="unread"
          :icon="DraftsOutlined"
          title="标记为已读"
          size="small"
          :icon-size="18"
          @click="emit('action', 'read')"
        />
        <IconBtn v-else :icon="MarkEmailUnreadOutlined" title="标记为未读" size="small" :icon-size="18" @click="emit('action', 'unread')" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.gm-row-wrap { position: relative; }
.gm-row {
    position: relative;
    display: grid;
    grid-template-columns: auto auto minmax(120px, 200px) 1fr auto;
    align-items: center;
    column-gap: 4px;
    min-height: 40px;
    padding: 0 16px 0 8px;
    background: var(--gm-row-read);
    border-bottom: 1px solid var(--gm-divider);
    cursor: pointer;
    font-size: 14px;
    color: var(--gm-text);
}
.gm-row.is-unread { background: #fff; }
.gm-row.is-unread .gm-row-sender,
.gm-row.is-unread .gm-row-subject,
.gm-row.is-unread .gm-row-date { font-weight: 700; color: var(--gm-text); }
.gm-row:hover { box-shadow: inset 1px 0 0 #dadce0, inset -1px 0 0 #dadce0, 0 1px 2px 0 rgba(60, 64, 67, .3), 0 1px 3px 1px rgba(60, 64, 67, .15); z-index: 1; }
.gm-row.is-selected { background: var(--gm-row-selected); }
.gm-row.is-focused::before {
    content: "";
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 3px;
    background: var(--gm-primary);
}

.gm-row-check { position: relative; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; }
.gm-row-avatar { display: none; }
.gm-row-checkbox { --n-size: 18px !important; }
.gm-row-star {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border: 0;
    border-radius: 50%;
    background: none;
    color: #747775;
    cursor: pointer;
    padding: 0;
}
.gm-row-star:hover { background: var(--gm-hover); color: var(--gm-text); }
.gm-row-star.is-starred { color: var(--gm-star); }

.gm-row-sender {
    padding: 0 12px 0 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--gm-text-2);
}
.gm-thread-count { color: var(--gm-text-3); font-weight: 400; font-size: 12px; margin-left: 4px; }
.gm-row-main { min-width: 0; }
.gm-row-line { display: flex; align-items: center; min-width: 0; white-space: nowrap; overflow: hidden; }
.gm-row-subject { flex: none; max-width: 70%; overflow: hidden; text-overflow: ellipsis; color: var(--gm-text-2); }
.gm-row-snippet { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--gm-text-3); }
.gm-row-atts { display: flex; gap: 8px; padding: 4px 0 8px; }
.gm-att-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: 200px;
    height: 32px;
    padding: 0 12px 0 8px;
    border: 1px solid var(--gm-outline-variant);
    border-radius: 16px;
    background: #fff;
    font-size: 13px;
    color: var(--gm-text-2);
}
.gm-att-chip-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-att-more { align-self: center; color: var(--gm-text-3); font-size: 13px; }
.gm-row-end { display: flex; align-items: center; gap: 6px; padding-left: 12px; justify-self: end; }
.gm-row-date { font-size: 12px; color: var(--gm-text-2); white-space: nowrap; min-width: 56px; text-align: right; }
.gm-row-clip { color: var(--gm-text-3); }
.gm-row-actions {
    position: absolute;
    right: 8px;
    top: 50%;
    transform: translateY(-50%);
    display: none;
    gap: 2px;
    background: inherit;
    padding-left: 8px;
}
.gm-row.has-atts .gm-row-actions { top: 20px; }
@media (hover: hover) {
    .gm-row:hover .gm-row-actions { display: flex; }
    .gm-row:hover .gm-row-end { visibility: hidden; }
}

.gm-swipe-bg {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    padding: 0 24px;
    background: #c4c7c5;
    color: #fff;
    transition: background .15s;
}
.gm-swipe-bg.is-right { justify-content: flex-end; }
.gm-swipe-bg.is-armed { background: #d93025; }

/* 手机端：Gmail App 样式，两行布局（发件人 + 日期 / 主题 + 摘要），左侧头像点击选中 */
@media (max-width: 768px) {
    .gm-row {
        grid-template-columns: auto 1fr auto;
        grid-template-areas:
            "check sender end"
            "check main star";
        padding: 10px 12px 10px 8px;
        background: #fff;
        border-bottom: 0;
        row-gap: 2px;
    }
    .gm-row:hover { box-shadow: none; }
    .gm-row-check { grid-area: check; width: 48px; height: 48px; align-self: start; }
    .gm-row-avatar { display: inline-flex; width: 40px; height: 40px; font-size: 18px; }
    .gm-row-checkbox { display: none; }
    .gm-row.is-selected .gm-row-avatar { background: var(--gm-primary) !important; }
    .gm-row.is-selected .gm-row-avatar::after { content: "✓"; position: absolute; font-size: 20px; }
    .gm-row.is-selected .gm-row-avatar { color: transparent; position: relative; }
    .gm-row.is-selected .gm-row-avatar::after { color: #fff; }
    .gm-row-star { grid-area: star; justify-self: end; }
    .gm-row-sender { grid-area: sender; padding: 0 0 0 4px; font-size: 16px; }
    .gm-row-main { grid-area: main; padding-left: 4px; }
    .gm-row-line { flex-direction: column; align-items: flex-start; white-space: normal; }
    .gm-row-subject { max-width: 100%; white-space: nowrap; }
    .gm-row-snippet { width: 100%; white-space: nowrap; }
    .gm-row-dash { display: none; }
    .gm-row-end { grid-area: end; }
    .gm-row-actions { display: none !important; }
    .gm-chip { display: none; }
}
</style>
