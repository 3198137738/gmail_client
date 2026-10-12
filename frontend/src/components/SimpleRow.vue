<script setup>
// 草稿、发件记录列表的一行：头像、对方、标签、主题 - 摘要、日期，悬停时显示删除按钮。
import { DeleteOutlined } from "@vicons/material";
import IconBtn from "./IconBtn.vue";
import { avatarColor, fullDate, initial, listDate } from "../format";

defineProps({
  name: { type: String, default: "" },
  sender: { type: String, default: "" },
  senderTitle: { type: String, default: "" },
  subject: { type: String, default: "" },
  snippet: { type: String, default: "" },
  date: { type: String, default: null },
  deleteTitle: { type: String, default: "删除" },
});
const emit = defineEmits(["open", "delete"]);
</script>

<template>
  <div class="gm-srow" role="listitem" @click="emit('open')">
    <span class="gm-avatar" :style="{ background: avatarColor(name) }" aria-hidden="true">{{ initial(name) }}</span>
    <div class="gm-srow-sender" :title="senderTitle">
      <slot name="sender">{{ sender }}</slot>
    </div>
    <div class="gm-srow-main">
      <slot name="chips" />
      <span class="gm-srow-subject">{{ subject || "(无主题)" }}</span>
      <span v-if="snippet" class="gm-srow-snippet"> - {{ snippet }}</span>
    </div>
    <span class="gm-srow-date" :title="fullDate(date)">{{ listDate(date) }}</span>
    <div class="gm-srow-actions" @click.stop>
      <IconBtn :icon="DeleteOutlined" :title="deleteTitle" size="small" :icon-size="18" @click="emit('delete')" />
    </div>
  </div>
</template>

<style scoped>
.gm-srow {
    position: relative;
    display: grid;
    grid-template-columns: auto minmax(120px, 220px) 1fr auto;
    align-items: center;
    gap: 12px;
    min-height: 48px;
    padding: 0 16px;
    border-bottom: 1px solid var(--gm-divider);
    cursor: pointer;
}
.gm-srow:hover { background: var(--gm-hover); }
.gm-srow-sender { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
.gm-srow-main { display: flex; align-items: center; min-width: 0; white-space: nowrap; overflow: hidden; }
.gm-srow-subject { flex: none; max-width: 70%; overflow: hidden; text-overflow: ellipsis; }
.gm-srow-snippet { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--gm-text-3); }
.gm-srow-date { font-size: 12px; color: var(--gm-text-2); white-space: nowrap; }
.gm-srow-actions { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); display: none; background: var(--gm-popover); box-shadow: var(--gm-shadow-1); border-radius: var(--gm-radius); }
@media (hover: hover) {
    .gm-srow:hover .gm-srow-actions { display: flex; }
    .gm-srow:hover .gm-srow-date { visibility: hidden; }
}
@media (max-width: 768px) {
    .gm-srow { grid-template-columns: auto 1fr auto; grid-template-areas: "avatar sender date" "avatar main main"; row-gap: 2px; padding: 10px 16px; border-bottom: 0; }
    .gm-srow > .gm-avatar { grid-area: avatar; width: 40px; height: 40px; }
    .gm-srow-sender { grid-area: sender; }
    .gm-srow-main { grid-area: main; }
    .gm-srow-date { grid-area: date; }
}
</style>
