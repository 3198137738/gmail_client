<script setup>
// 登录 / 两步验证页：居中的卡片
import { NCard } from "naive-ui";
import { store, setThemeMode } from "../store";

defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: "" },
});

const NEXT_THEME = { light: "dark", dark: "auto", auto: "light" };
const THEME_TEXT = { light: "浅色", dark: "深色", auto: "跟随系统" };
</script>

<template>
  <main class="gm-auth">
    <div class="gm-auth-brand">
      <img src="/img/logo.svg" alt="" width="40" height="40" />
      <span>Roundcube</span>
    </div>
    <NCard class="gm-auth-card" :bordered="true" size="large">
      <h1 class="gm-auth-title">{{ title }}</h1>
      <p class="gm-auth-sub">{{ subtitle }}</p>
      <slot />
    </NCard>
    <footer class="gm-auth-footer">
      <span>Roundcube 邮件客户端</span>
      <a href="#" @click.prevent="setThemeMode(NEXT_THEME[store.themeMode])">外观：{{ THEME_TEXT[store.themeMode] }}</a>
    </footer>
  </main>
</template>

<style scoped>
.gm-auth {
    min-height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 20px;
    padding: 32px 16px;
    background:
        radial-gradient(circle at 20% 10%, var(--gm-primary-soft), transparent 40%),
        radial-gradient(circle at 85% 90%, var(--gm-primary-soft), transparent 40%),
        var(--gm-bg);
}
.gm-auth-brand { display: flex; align-items: center; gap: 10px; font-size: 22px; font-weight: 600; }
.gm-auth-card { width: min(420px, 100%); box-shadow: var(--gm-shadow-2); }
.gm-auth-title { font-size: 22px; font-weight: 600; margin: 0 0 6px; }
.gm-auth-sub { color: var(--gm-text-3); margin: 0 0 24px; line-height: 1.6; }
.gm-auth-card :deep(.gm-auth-alert) { margin-bottom: 16px; }
.gm-auth-card :deep(.gm-auth-meta) { color: var(--gm-text-3); font-size: 12px; margin-bottom: 20px; }
.gm-auth-card :deep(.gm-auth-actions) { display: flex; justify-content: flex-end; gap: 8px; }
.gm-auth-footer { display: flex; gap: 24px; color: var(--gm-text-3); font-size: 12px; }
</style>
