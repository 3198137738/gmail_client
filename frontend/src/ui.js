import { reactive } from "vue";

// 在组件之外也能使用 Naive UI 的提示、对话框（由 App.vue 中的 UiBridge 注入实例）。
export const ui = {
  message: null,
  dialog: null,
  notification: null,
  loadingBar: null,
};

// 确认对话框，返回 Promise<boolean>。danger: 红色确认按钮（删除等不可恢复的操作）。
export function confirm(content, { title = "确认", positiveText = "确定", danger = false } = {}) {
  return new Promise((resolve) => {
    if (!ui.dialog) {
      resolve(window.confirm(content));
      return;
    }
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    ui.dialog[danger ? "error" : "info"]({
      title,
      content,
      positiveText,
      negativeText: "取消",
      showIcon: false,
      autoFocus: true,
      style: "white-space: pre-line;",
      onPositiveClick: () => done(true),
      onNegativeClick: () => done(false),
      onClose: () => done(false),
      onMaskClick: () => done(false),
      onAfterLeave: () => done(false),
    });
  });
}

// 执行一个接口操作：成功时提示 success（字符串，或根据返回值生成的函数），失败时提示错误信息。
// 返回接口结果；失败时返回 null。
export async function run(promise, success) {
  try {
    const result = await promise;
    const text = typeof success === "function" ? success(result) : success;
    if (text) ui.message?.success(text);
    return result ?? {};
  } catch (err) {
    if (err?.status !== 401) ui.message?.error(err?.message || "操作失败");
    return null;
  }
}

// 底部提示条（Gmail snackbar），可带一个操作按钮（如「撤销」）。返回关闭函数。
// 同一时间只显示一条，新的替换旧的（由 components/AppSnackbar.vue 显示）。
export const snackbar = reactive({ item: null });
let snackSeq = 0;
let snackTimer = null;

export function toast(text, { actionText, onAction, linkText, onLink, duration = 4000 } = {}) {
  snackSeq += 1;
  const key = snackSeq;
  clearTimeout(snackTimer);
  const close = () => {
    if (snackbar.item?.key === key) snackbar.item = null;
  };
  snackbar.item = { key, text, actionText, onAction, linkText, onLink, close };
  if (duration > 0) snackTimer = setTimeout(close, duration);
  return close;
}
