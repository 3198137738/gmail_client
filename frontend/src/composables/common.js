import { onBeforeUnmount, ref } from "vue";
import { get } from "../api";
import { store } from "../store";

function mediaRef(query) {
  const mq = window.matchMedia(query);
  const value = ref(mq.matches);
  mq.addEventListener("change", (e) => {
    value.value = e.matches;
  });
  return value;
}

// 手机端（底部标签栏，阅读和写邮件全屏）
export const isMobile = mediaRef("(max-width: 768px)");
// 窄窗口（文件夹面板改为浮层，不占列宽）
export const isNarrow = mediaRef("(max-width: 1279px)");
// 放不下列表 + 阅读窗格（阅读时整页显示）
export const isCramped = mediaRef("(max-width: 1023px)");
// 触屏设备（没有悬停）：启用滑动手势、长按多选
export const isTouch = mediaRef("(hover: none)");

// 合并频繁的刷新请求：刷新进行中又被调用时只做标记，结束后立刻再刷新一次，
// 既不丢失更新，也能把短时间内的多个事件合并成一次请求。
// （不用 setTimeout 防抖：浏览器会把后台标签页的定时器限速到每分钟一次。）
export function makeRefresher(fn) {
  let running = false;
  let pending = false;
  return async function run() {
    if (running) {
      pending = true;
      return;
    }
    running = true;
    pending = false;
    try {
      await fn();
    } catch {
      /* 临时错误：保持当前内容 */
    } finally {
      running = false;
      if (pending) run();
    }
  };
}

// 左侧导航数据（账号、未读数、标签、草稿数）
export const refreshNav = makeRefresher(async () => {
  store.nav = await get("/api/messages/nav");
});

// 组件卸载时自动取消的事件订阅：useCleanup(onRealtime("new_mail", fn))
export function useCleanup(...disposers) {
  onBeforeUnmount(() => disposers.forEach((d) => d && d()));
}

// 在当前标签页打开一个页面地址（OAuth 等需要整页跳转的地址）
export function hardNavigate(url) {
  window.location.href = url;
}
