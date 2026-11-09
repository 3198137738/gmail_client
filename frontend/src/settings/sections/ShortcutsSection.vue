<script setup>
// 键盘快捷键一览（与按 ? 弹出的帮助相同）
import { SHORTCUT_HELP } from "../../composables/shortcuts";
</script>

<template>
  <div class="gm-settings">
    <p class="gm-settings-intro">在任何页面按 <kbd>?</kbd> 也可以查看。输入文字时快捷键不生效（Ctrl+K 除外）。</p>
    <section v-for="[group, items] in SHORTCUT_HELP" :key="group" class="gm-settings-row">
      <div class="gm-settings-label">{{ group }}</div>
      <div class="gm-settings-content">
        <div v-for="[key, label] in items" :key="key" class="gm-sc-row">
          <span class="gm-sc-keys">
            <template v-for="(part, i) in key.split(' ')" :key="i">
              <kbd v-if="!['然后', '/', '或', '+'].includes(part)">{{ part }}</kbd>
              <span v-else class="gm-sc-sep">{{ part }}</span>
            </template>
          </span>
          <span>{{ label }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.gm-sc-row { display: flex; align-items: center; gap: var(--sp-4); padding: 6px 0; border-bottom: 1px solid var(--gm-divider); }
.gm-sc-row:last-child { border-bottom: 0; }
.gm-sc-keys { display: flex; align-items: center; gap: 4px; min-width: 160px; }
.gm-sc-sep { color: var(--gm-text-3); font-size: var(--fs-xs); }
kbd {
    padding: 0 6px;
    border: 1px solid var(--gm-border);
    border-bottom-width: 2px;
    border-radius: 4px;
    font: 12px/20px ui-monospace, Consolas, monospace;
    color: var(--gm-text-2);
    background: var(--gm-surface-2);
}
</style>
