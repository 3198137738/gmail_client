<script setup>
// 邮件正文：在 sandbox iframe 中渲染（不允许脚本），高度随内容自适应。
// iframe 带 allow-same-origin，父页面可以读取其文档高度；正文内部不能执行脚本。
import { onBeforeUnmount, ref, watch } from "vue";

const props = defineProps({
  src: { type: String, required: true },
  // 折叠的邮件不加载正文，展开时才加载
  active: { type: Boolean, default: true },
});

const frame = ref(null);
const height = ref(120);
let observer = null;

function measure() {
  const doc = frame.value?.contentDocument;
  if (!doc || !doc.documentElement) return;
  height.value = Math.max(60, doc.documentElement.scrollHeight);
}

function onLoad() {
  observer?.disconnect();
  measure();
  const doc = frame.value?.contentDocument;
  if (!doc?.body) return;
  // 图片加载完、窗口宽度变化时内容高度会变
  observer = new ResizeObserver(measure);
  observer.observe(doc.body);
  doc.querySelectorAll("img").forEach((img) => img.addEventListener("load", measure));
}

const loadedSrc = ref(props.active ? props.src : "");
watch(
  () => [props.src, props.active],
  () => {
    if (props.active) loadedSrc.value = props.src;
  },
);

onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
  <iframe
    v-if="loadedSrc"
    ref="frame"
    class="gm-body-frame"
    :src="loadedSrc"
    :style="{ height: height + 'px' }"
    sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
    referrerpolicy="no-referrer"
    title="邮件正文"
    @load="onLoad"
  />
</template>

<style scoped>
.gm-body-frame { display: block; width: 100%; border: 0; background: transparent; }
</style>
