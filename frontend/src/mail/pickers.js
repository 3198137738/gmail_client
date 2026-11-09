// 操作需要用户选择时间（延后）时用的全局选择器：pickTime() 返回 Promise<毫秒时间戳 | null>。
// 由 mail/MailPickers.vue 显示。
import { reactive } from "vue";

export const timePicker = reactive({ show: false, title: "", confirmText: "", resolve: null });

export function pickTime({ title = "延后到…", confirmText = "延后" } = {}) {
  timePicker.resolve?.(null);
  return new Promise((resolve) => {
    Object.assign(timePicker, { show: true, title, confirmText, resolve });
  });
}

export function settleTime(value) {
  const resolve = timePicker.resolve;
  timePicker.resolve = null;
  timePicker.show = false;
  resolve?.(value);
}
