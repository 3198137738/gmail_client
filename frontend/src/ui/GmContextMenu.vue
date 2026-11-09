<script setup>
// 右键菜单：在鼠标位置显示 Naive 下拉菜单。items：[{ key, label, icon?, danger?, divider? }]
import { computed, h } from "vue";
import { NDropdown, NIcon } from "naive-ui";

const props = defineProps({
  show: { type: Boolean, default: false },
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
  items: { type: Array, default: () => [] },
});
const emit = defineEmits(["update:show", "select"]);

const options = computed(() =>
  props.items.map((it) =>
    it.divider
      ? { type: "divider", key: it.key }
      : {
          key: it.key,
          label: it.label,
          icon: it.icon ? () => h(NIcon, { component: it.icon }) : undefined,
          props: it.danger ? { class: "gm-cm-danger" } : undefined,
        },
  ),
);

function onSelect(key) {
  emit("update:show", false);
  emit("select", key);
}
</script>

<template>
  <NDropdown
    trigger="manual"
    placement="bottom-start"
    :show="show"
    :x="x"
    :y="y"
    :options="options"
    @select="onSelect"
    @clickoutside="emit('update:show', false)"
  />
</template>

<style>
.gm-cm-danger .n-dropdown-option-body__label,
.gm-cm-danger .n-dropdown-option-body__prefix { color: var(--gm-error); }
</style>
