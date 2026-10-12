<script setup>
// 邮件列表 + 阅读窗格。电脑端打开邮件时左右分栏：列表收窄留在左侧（不重新加载），正文显示在右侧；
// 手机端只显示其中之一。列表页（/messages/?...）和详情页（/messages/:id?back=...）共用这个组件，
// 路由切换时组件实例保留，列表不会丢失滚动位置和勾选。
import { computed } from "vue";
import { useRoute } from "vue-router";
import MailListView from "./MailListView.vue";
import MessageView from "./MessageView.vue";
import { isMobile } from "../composables/common";

const route = useRoute();

const reading = computed(() => route.name === "message");

// 详情页时列表的筛选条件取自 back 参数（打开邮件前所在的列表地址）
const listQuery = computed(() => {
  if (!reading.value) return route.query;
  const back = String(route.query.back || "");
  const qs = back.startsWith("/messages/?") ? back.slice("/messages/?".length) : "";
  return Object.fromEntries(new URLSearchParams(qs));
});

const showList = computed(() => !reading.value || !isMobile.value);
</script>

<template>
  <div class="gm-split" :class="{ 'is-reading': reading && !isMobile }">
    <MailListView
      v-if="showList"
      class="gm-split-list"
      :list-query="listQuery"
      :active-id="reading ? Number(route.params.id) : null"
      :compact="reading || isMobile"
    />
    <MessageView v-if="reading" class="gm-split-reader" :split="!isMobile" />
  </div>
</template>

<style scoped>
.gm-split { display: flex; height: 100%; min-height: 0; }
.gm-split-list { flex: 1 1 auto; min-width: 0; }
.gm-split.is-reading .gm-split-list {
    flex: 0 0 clamp(340px, 36%, 520px);
    border-right: 1px solid var(--gm-divider);
}
.gm-split-reader { flex: 1 1 auto; min-width: 0; }
</style>
