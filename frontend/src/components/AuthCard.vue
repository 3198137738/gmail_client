<script setup>
// 登录 / 两步验证页的卡片（Google 账号登录页样式）
defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: "" },
});
</script>

<template>
  <main class="gm-auth">
    <div class="gm-auth-card">
      <div class="gm-auth-intro">
        <img class="gm-auth-logo" src="/img/logo.svg" alt="" width="48" height="36" />
        <h1 class="gm-auth-title">{{ title }}</h1>
        <p class="gm-auth-sub">{{ subtitle }}</p>
      </div>
      <div class="gm-auth-form">
        <slot />
      </div>
    </div>
    <footer class="gm-auth-footer">
      <span>简体中文</span>
      <span>Roundcube 邮件客户端</span>
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
    padding: 24px 16px;
    background: var(--gm-bg);
}
.gm-auth-card {
    width: min(1040px, 100%);
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px 48px;
    background: #fff;
    border-radius: 28px;
    padding: 36px 40px 32px;
}
.gm-auth-title { font: 400 36px/1.3 var(--gm-font); margin: 16px 0 8px; }
.gm-auth-sub { color: var(--gm-text-2); font-size: 16px; margin: 0; line-height: 1.5; }
.gm-auth-form { padding-top: 32px; }
.gm-auth-form :deep(.gm-auth-alert) { margin-bottom: 16px; }
.gm-auth-form :deep(.gm-auth-meta) { color: var(--gm-text-3); font-size: 13px; margin-bottom: 24px; }
.gm-auth-form :deep(.gm-auth-actions) { display: flex; justify-content: flex-end; gap: 8px; }
.gm-auth-footer {
    width: min(1040px, 100%);
    display: flex;
    justify-content: space-between;
    padding: 16px 8px;
    color: var(--gm-text-2);
    font-size: 12px;
}

@media (max-width: 840px) {
    .gm-auth { justify-content: flex-start; padding-top: 48px; }
    .gm-auth-card { grid-template-columns: 1fr; padding: 24px; gap: 0; }
    .gm-auth-title { font-size: 28px; }
}
</style>
