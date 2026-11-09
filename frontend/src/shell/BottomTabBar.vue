<script setup>
// 手机端底部标签栏：邮件 · 搜索 · 写信 · 账号 · 我的。
// 已在邮件列表时再点「邮件」打开文件夹面板。
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NIcon } from "naive-ui";
import { AccountCircleOutlined, AddOutlined, InboxOutlined, MenuOutlined, SearchOutlined } from "@vicons/material";
import GmAvatar from "../ui/GmAvatar.vue";
import GmCountBadge from "../ui/GmCountBadge.vue";
import { openCompose, store } from "../store";
import { currentAccount, listUrl, nav } from "./navModel";

const route = useRoute();
const router = useRouter();
const onMail = computed(() => route.name === "list" || route.name === "message");
const unread = computed(() => (currentAccount.value ? currentAccount.value.unread_inbox : nav.value?.unread_inbox || 0));

function mail() {
  if (route.name === "list") store.mobileSheet = "folders";
  else router.push(listUrl(store.view.kind || "inbox"));
}

async function search() {
  if (route.name !== "list") await router.push(listUrl(store.view.kind || "inbox"));
  const input = document.querySelector("input[data-search]");
  input?.focus();
}
</script>

<template>
  <nav class="gm-tabbar" aria-label="主导航">
    <button type="button" class="gm-tab" :class="{ 'is-active': onMail && !store.mobileSheet }" @click="mail">
      <span class="gm-tab-icon">
        <NIcon :component="route.name === 'list' ? MenuOutlined : InboxOutlined" :size="24" />
        <GmCountBadge class="gm-tab-badge" :count="unread" tone="solid" />
      </span>
      <span>{{ route.name === "list" ? "文件夹" : "邮件" }}</span>
    </button>
    <button type="button" class="gm-tab" @click="search">
      <span class="gm-tab-icon"><NIcon :component="SearchOutlined" :size="24" /></span>
      <span>搜索</span>
    </button>
    <button type="button" class="gm-tab is-compose" aria-label="写邮件" @click="openCompose()">
      <span class="gm-tab-fab"><NIcon :component="AddOutlined" :size="28" /></span>
    </button>
    <button type="button" class="gm-tab" @click="store.accountSwitcher = true">
      <span class="gm-tab-icon">
        <GmAvatar v-if="currentAccount" :name="currentAccount.name" :color="currentAccount.color" :size="24" />
        <NIcon v-else :component="AccountCircleOutlined" :size="24" />
      </span>
      <span>账号</span>
    </button>
    <button type="button" class="gm-tab" :class="{ 'is-active': store.mobileSheet === 'me' || (!onMail && route.name) }" @click="store.mobileSheet = 'me'">
      <span class="gm-tab-icon"><NIcon :component="MenuOutlined" :size="24" /></span>
      <span>我的</span>
    </button>
  </nav>
</template>

<style scoped>
.gm-tabbar {
    display: flex;
    align-items: stretch;
    height: calc(var(--tabbar-h) + env(safe-area-inset-bottom));
    padding-bottom: env(safe-area-inset-bottom);
    border-top: 1px solid var(--gm-divider);
    background: var(--gm-surface);
    flex: none;
}
.gm-tab {
    display: flex;
    flex: 1;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    border: 0;
    background: none;
    color: var(--gm-text-3);
    font: 500 11px var(--gm-font);
    cursor: pointer;
}
.gm-tab.is-active { color: var(--gm-primary); }
.gm-tab-icon { position: relative; display: inline-flex; height: 26px; align-items: center; }
.gm-tab-badge { position: absolute; top: -4px; left: 16px; }
.gm-tab-fab {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 48px;
    height: 40px;
    border-radius: var(--r-lg);
    background: var(--gm-primary);
    color: #fff;
    box-shadow: 0 4px 12px color-mix(in srgb, var(--gm-primary) 35%, transparent);
}
</style>
