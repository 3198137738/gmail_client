<script setup>
// 功能页的列表行（草稿、定时发送、发件记录等）：头像 · 标题 / 标记 · 主题 — 摘要 · 时间 · 操作。
// 操作：电脑端悬停时显示；触屏上总是显示「⋯」菜单（不依赖悬停）。
import { computed } from "vue";
import { NDropdown, NIcon } from "naive-ui";
import { MoreVertOutlined } from "@vicons/material";
import GmAvatar from "./GmAvatar.vue";
import GmIconButton from "./GmIconButton.vue";
import { fullDate, listDate } from "../format";
import { isTouch } from "../composables/common";

const props = defineProps({
  avatar: { type: String, default: "" },
  title: { type: String, default: "" },
  titleAttr: { type: String, default: "" },
  subject: { type: String, default: "" },
  snippet: { type: String, default: "" },
  date: { type: String, default: null },
  // 日期旁边的说明（如「将于 明天 上午8:00 发送」），有时代替日期
  when: { type: String, default: "" },
  // [{ key, label, icon, danger? }]
  actions: { type: Array, default: () => [] },
});
const emit = defineEmits(["open", "action"]);

const menu = computed(() => props.actions.map((a) => ({ key: a.key, label: a.label })));
</script>

<template>
  <div class="gm-lr" role="listitem" tabindex="0" @click="emit('open')" @keydown.enter.self="emit('open')">
    <GmAvatar :name="avatar || title" :size="36" class="gm-lr-avatar" />
    <div class="gm-lr-top">
      <span class="gm-lr-title" :title="titleAttr || title"><slot name="title">{{ title }}</slot></span>
      <span class="gm-lr-chips"><slot name="chips" /></span>
    </div>
    <span class="gm-lr-when" :title="date ? fullDate(date) : ''">{{ when || listDate(date) }}</span>
    <div class="gm-lr-body">
      <span class="gm-lr-subject">{{ subject || "(无主题)" }}</span>
      <span v-if="snippet" class="gm-lr-snippet">{{ snippet }}</span>
      <div v-if="$slots.extra" class="gm-lr-extra"><slot name="extra" /></div>
    </div>
    <div v-if="actions.length" class="gm-lr-actions" :class="{ 'is-touch': isTouch }" @click.stop>
      <NDropdown v-if="isTouch" trigger="click" placement="bottom-end" :options="menu" @select="(k) => emit('action', k)">
        <GmIconButton :icon="MoreVertOutlined" title="操作" :size="34" />
      </NDropdown>
      <template v-else>
        <GmIconButton
          v-for="a in actions"
          :key="a.key"
          :icon="a.icon"
          :title="a.label"
          :danger="a.danger"
          :size="32"
          :icon-size="18"
          @click="emit('action', a.key)"
        />
      </template>
    </div>
  </div>
</template>

<style scoped>
.gm-lr {
    position: relative;
    display: grid;
    grid-template-columns: 36px 1fr auto;
    grid-template-areas:
        "avatar top when"
        "avatar body actions";
    column-gap: var(--sp-3);
    row-gap: 2px;
    padding: var(--row-pad-y) var(--sp-4) var(--row-pad-y) var(--sp-5);
    border-bottom: 1px solid var(--gm-divider);
    cursor: pointer;
    outline: none;
}
.gm-lr:hover { background: var(--gm-hover); }
.gm-lr:focus-visible { box-shadow: inset 3px 0 0 var(--gm-primary); }
.gm-lr-avatar { grid-area: avatar; margin-top: 2px; }
.gm-lr-top { grid-area: top; display: flex; align-items: center; gap: var(--sp-2); min-width: 0; }
.gm-lr-title { min-width: 0; font-weight: 600; color: var(--gm-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gm-lr-chips { display: flex; align-items: center; gap: 6px; flex: none; }
.gm-lr-when { grid-area: when; justify-self: end; font-size: var(--fs-xs); color: var(--gm-text-3); white-space: nowrap; }
.gm-lr-body { grid-area: body; display: flex; flex-direction: column; min-width: 0; }
.gm-lr-subject { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--gm-text-2); }
.gm-lr-snippet { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--fs-sm); color: var(--gm-text-3); }
.gm-lr-extra { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 4px; font-size: var(--fs-xs); }
.gm-lr-actions { grid-area: actions; display: flex; align-items: center; justify-self: end; gap: 2px; visibility: hidden; }
.gm-lr-actions.is-touch { visibility: visible; }
.gm-lr:hover .gm-lr-actions, .gm-lr:focus-within .gm-lr-actions { visibility: visible; }
</style>
