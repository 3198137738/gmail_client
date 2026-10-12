import { onBeforeUnmount, ref } from "vue";
import { get } from "../api";
import { store } from "../store";

// 手机端 / 窄窗口（导航变为抽屉，写邮件窗口全屏）
const mobileQuery = window.matchMedia("(max-width: 768px)");
export const isMobile = ref(mobileQuery.matches);
mobileQuery.addEventListener("change", (e) => {
  isMobile.value = e.matches;
});

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
