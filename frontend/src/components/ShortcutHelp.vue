<script setup>
import { NModal } from "naive-ui";
import { store } from "../store";
import { SHORTCUT_HELP } from "../composables/shortcuts";
</script>

<template>
  <NModal
    v-model:show="store.shortcutHelp"
    preset="card"
    title="键盘快捷键"
    :bordered="false"
    style="width: min(860px, calc(100vw - 32px))"
  >
    <div class="gm-kbd-grid">
      <section v-for="[group, items] in SHORTCUT_HELP" :key="group">
        <h3>{{ group }}</h3>
        <div v-for="[key, label] in items" :key="key" class="gm-kbd-row">
          <kbd>{{ key }}</kbd>
          <span>{{ label }}</span>
        </div>
      </section>
    </div>
  </NModal>
</template>

<style scoped>
.gm-kbd-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px 32px; }
.gm-kbd-grid h3 { margin: 0 0 8px; font-size: 14px; font-weight: 600; color: var(--gm-text-2); }
.gm-kbd-row { display: flex; align-items: center; gap: 12px; padding: 4px 0; }
.gm-kbd-row kbd {
    min-width: 88px;
    font: 500 12px ui-monospace, Consolas, monospace;
    color: var(--gm-text);
    background: var(--gm-code);
    border: 1px solid var(--gm-border);
    border-radius: 4px;
    padding: 2px 6px;
}
</style>
