<script setup>
// Gmail 式底部提示条：「邮件已发送 查看邮件」「已删除 1 封邮件 撤销」
import { snackbar } from "../ui";

function onAction() {
  const item = snackbar.item;
  if (!item) return;
  item.close();
  item.onAction?.();
}

function onLink() {
  const item = snackbar.item;
  if (!item) return;
  item.close();
  item.onLink?.();
}
</script>

<template>
  <Transition name="gm-snack">
    <div v-if="snackbar.item" :key="snackbar.item.key" class="gm-snackbar" role="status" aria-live="polite">
      <span class="gm-snackbar-text">{{ snackbar.item.text }}</span>
      <button v-if="snackbar.item.linkText" type="button" class="gm-snackbar-btn" @click="onLink">
        {{ snackbar.item.linkText }}
      </button>
      <button v-if="snackbar.item.actionText" type="button" class="gm-snackbar-btn" @click="onAction">
        {{ snackbar.item.actionText }}
      </button>
      <button type="button" class="gm-snackbar-close" aria-label="关闭" @click="snackbar.item.close()">✕</button>
    </div>
  </Transition>
</template>

<style scoped>
.gm-snackbar {
    position: fixed;
    left: 24px;
    bottom: 24px;
    z-index: 3000;
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 288px;
    max-width: min(560px, calc(100vw - 32px));
    padding: 6px 8px 6px 16px;
    border-radius: var(--gm-radius);
    background: #303033;
    color: #f2f2f2;
    box-shadow: var(--gm-shadow-3);
    font-size: 14px;
}
.gm-snackbar-text { flex: 1; padding: 8px 0; line-height: 1.4; }
.gm-snackbar-btn {
    border: 0;
    background: none;
    color: #70c0e8;
    font: 500 14px var(--gm-font);
    padding: 8px 12px;
    border-radius: 4px;
    cursor: pointer;
}
.gm-snackbar-btn:hover { background: rgba(112, 192, 232, .12); }
.gm-snackbar-close {
    border: 0;
    background: none;
    color: #c4c7c5;
    padding: 8px;
    cursor: pointer;
    border-radius: 50%;
    line-height: 1;
}
.gm-snackbar-close:hover { background: rgba(255, 255, 255, .1); }
.gm-snack-enter-active, .gm-snack-leave-active { transition: opacity .15s, transform .15s; }
.gm-snack-enter-from, .gm-snack-leave-to { opacity: 0; transform: translateY(12px); }

@media (max-width: 768px) {
    .gm-snackbar { left: 8px; right: 8px; bottom: 8px; min-width: 0; }
}
</style>
