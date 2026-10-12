<script setup>
// 邮件正文：在 sandbox iframe 中渲染（不允许脚本），高度随内容自适应。
// iframe 带 allow-same-origin，父页面可以读取其文档高度；正文内部不能执行脚本。
// 加载完、量好高度之前先显示占位骨架，不显示 iframe：否则会先露出一个带滚动条的小空白框，再突然撑开。
// 正文文档解析完（DOM 就绪）就显示，不等 load 事件：load 要等所有图片下载完，图片多的邮件会明显卡顿。
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { NSkeleton } from "naive-ui";

const props = defineProps({
  src: { type: String, required: true },
  // 折叠的邮件不加载正文，展开时才加载
  active: { type: Boolean, default: true },
});

const frame = ref(null);
const height = ref(0);
const ready = ref(false);
let observer = null;
let pollTimer = null;
let setupDoc = null;

function measure() {
  const doc = frame.value?.contentDocument;
  if (!doc || !doc.documentElement) return;
  height.value = Math.max(40, doc.documentElement.scrollHeight);
}

// 正文文档可以操作时调用一次（DOM 就绪或 load，先到者为准）
function setup() {
  const doc = frame.value?.contentDocument;
  if (!doc?.body || doc === setupDoc) {
    measure();
    return;
  }
  setupDoc = doc;
  observer?.disconnect();
  // 高度由外层自适应，正文文档自身不出现滚动条
  doc.documentElement.style.overflow = "hidden";
  measure();
  ready.value = true;
  // 图片加载完、窗口宽度变化时内容高度会变
  observer = new ResizeObserver(measure);
  observer.observe(doc.body);
  doc.querySelectorAll("img").forEach((img) => img.addEventListener("load", measure));
}

// iframe 换了地址后轮询新文档是否已解析完；拿不到新文档时由 load 事件兜底
function waitForDocument() {
  clearInterval(pollTimer);
  const started = Date.now();
  pollTimer = setInterval(() => {
    const doc = frame.value?.contentDocument;
    const fresh = doc && doc !== setupDoc && doc.location.href !== "about:blank";
    if (fresh && doc.readyState !== "loading" && doc.body) {
      clearInterval(pollTimer);
      setup();
    } else if (Date.now() - started > 15000) {
      clearInterval(pollTimer);
    }
  }, 30);
}

function onLoad() {
  clearInterval(pollTimer);
  setup();
}

const loadedSrc = ref(props.active ? props.src : "");
watch(
  () => [props.src, props.active],
  () => {
    if (props.active && loadedSrc.value !== props.src) {
      ready.value = false;
      loadedSrc.value = props.src;
      waitForDocument();
    }
  },
);

onMounted(() => loadedSrc.value && waitForDocument());
onBeforeUnmount(() => {
  observer?.disconnect();
  clearInterval(pollTimer);
});
</script>

<template>
  <div v-if="loadedSrc" class="gm-body-wrap">
    <div v-if="!ready" class="gm-body-loading" aria-hidden="true">
      <NSkeleton text :repeat="3" />
      <NSkeleton text style="width: 60%" />
    </div>
    <iframe
      ref="frame"
      class="gm-body-frame"
      :class="{ 'is-ready': ready }"
      :src="loadedSrc"
      :style="{ height: height + 'px' }"
      scrolling="no"
      sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
      referrerpolicy="no-referrer"
      title="邮件正文"
      @load="onLoad"
    />
  </div>
</template>

<style scoped>
.gm-body-frame { display: block; width: 100%; border: 0; background: transparent; opacity: 0; transition: opacity .12s; }
.gm-body-frame.is-ready { opacity: 1; }
.gm-body-loading { display: flex; flex-direction: column; gap: 10px; padding: 8px 0; }
</style>
