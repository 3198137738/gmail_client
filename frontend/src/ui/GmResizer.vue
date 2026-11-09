<script setup>
// 分栏拖动条：按住拖动调整左侧面板宽度（v-model 为像素宽度），双击恢复默认。键盘 ←/→ 每次 16px。
const props = defineProps({
  modelValue: { type: Number, required: true },
  min: { type: Number, default: 200 },
  max: { type: Number, default: 640 },
  defaultValue: { type: Number, default: null },
  label: { type: String, default: "调整宽度" },
});
const emit = defineEmits(["update:modelValue"]);

const clamp = (v) => Math.round(Math.min(props.max, Math.max(props.min, v)));

function onPointerDown(ev) {
  if (ev.button !== 0) return;
  ev.preventDefault();
  const startX = ev.clientX;
  const startW = props.modelValue;
  const handle = ev.currentTarget;
  handle.setPointerCapture(ev.pointerId);
  document.body.classList.add("gm-resizing");
  const move = (e) => emit("update:modelValue", clamp(startW + e.clientX - startX));
  const up = () => {
    handle.removeEventListener("pointermove", move);
    handle.removeEventListener("pointerup", up);
    handle.removeEventListener("pointercancel", up);
    document.body.classList.remove("gm-resizing");
  };
  handle.addEventListener("pointermove", move);
  handle.addEventListener("pointerup", up);
  handle.addEventListener("pointercancel", up);
}

function onKeydown(ev) {
  if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
    ev.preventDefault();
    emit("update:modelValue", clamp(props.modelValue + (ev.key === "ArrowRight" ? 16 : -16)));
  }
}

function reset() {
  if (props.defaultValue) emit("update:modelValue", clamp(props.defaultValue));
}
</script>

<template>
  <div
    class="gm-resizer"
    role="separator"
    aria-orientation="vertical"
    :aria-label="label"
    :aria-valuenow="modelValue"
    :aria-valuemin="min"
    :aria-valuemax="max"
    tabindex="0"
    :title="`${label}（双击恢复默认）`"
    @pointerdown="onPointerDown"
    @dblclick="reset"
    @keydown="onKeydown"
  />
</template>

<style scoped>
.gm-resizer {
    position: relative;
    flex: none;
    width: var(--pane-gap);
    cursor: col-resize;
    touch-action: none;
    outline: none;
}
.gm-resizer::after {
    content: "";
    position: absolute;
    top: 50%;
    left: 50%;
    width: 4px;
    height: 36px;
    border-radius: var(--r-pill);
    background: var(--gm-border);
    transform: translate(-50%, -50%);
    opacity: 0;
    transition: opacity var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease);
}
.gm-resizer:hover::after, .gm-resizer:focus-visible::after { opacity: 1; }
.gm-resizer:focus-visible::after { background: var(--gm-primary); }
</style>

<style>
/* 拖动时整页显示拖动光标，并禁止选中文字、禁止 iframe 截获鼠标 */
body.gm-resizing { cursor: col-resize; user-select: none; }
body.gm-resizing iframe { pointer-events: none; }
</style>
