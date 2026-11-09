import { h, reactive, ref } from "vue";
import { NInput } from "naive-ui";

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

// 输入对话框（插入链接、保存模板等），返回 Promise<输入的文字 | null（取消）>。
// validate(value) 返回错误文字时不关闭对话框并提示。
export function prompt({ title, label = "", value = "", placeholder = "", positiveText = "确定", validate } = {}) {
  return new Promise((resolve) => {
    if (!ui.dialog) {
      resolve(window.prompt(title, value));
      return;
    }
    const text = ref(value);
    const error = ref("");
    // 对话框完全关闭（焦点交还页面）后才返回结果：调用方随后要把焦点放回编辑器等位置
    let result = null;
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    const dialog = ui.dialog.create({
      title,
      showIcon: false,
      autoFocus: false,
      content: () =>
        h("div", { class: "gm-prompt" }, [
          label ? h("div", { class: "gm-prompt-label" }, label) : null,
          h(NInput, {
            value: text.value,
            placeholder,
            autofocus: true,
            status: error.value ? "error" : undefined,
            "onUpdate:value": (v) => {
              text.value = v;
              error.value = "";
            },
            onKeydown: (ev) => {
              if (ev.key === "Enter") {
                ev.preventDefault();
                submit();
              }
            },
          }),
          error.value ? h("div", { class: "gm-prompt-error" }, error.value) : null,
        ]),
      positiveText,
      negativeText: "取消",
      onPositiveClick: () => submit(),
      onAfterLeave: done,
    });
    function submit() {
      const message = validate?.(text.value);
      if (message) {
        error.value = message;
        return false;
      }
      result = text.value;
      dialog.destroy();
      return true;
    }
  });
}

// 执行一个接口操作：成功时提示 success（字符串，或根据返回值生成的函数），失败时提示错误信息。
// 提示统一显示在底部提示条（失败为红色）。返回接口结果；失败时返回 null。
export async function run(promise, success) {
  try {
    const result = await promise;
    const text = typeof success === "function" ? success(result) : success;
    if (text) toast(text);
    return result ?? {};
  } catch (err) {
    if (err?.status !== 401) toast(err?.message || "操作失败", { tone: "error", duration: 6000 });
    return null;
  }
}

// 底部提示条（Gmail snackbar），可带一个操作按钮（如「撤销」）。返回关闭函数。
// 同一时间只显示一条，新的替换旧的（由 components/AppSnackbar.vue 显示）。
export const snackbar = reactive({ item: null });
let snackSeq = 0;
let snackTimer = null;

// tone：normal / error（失败提示，红色）
export function toast(text, { actionText, onAction, linkText, onLink, duration = 4000, tone = "normal" } = {}) {
  snackSeq += 1;
  const key = snackSeq;
  clearTimeout(snackTimer);
  const close = () => {
    if (snackbar.item?.key === key) snackbar.item = null;
  };
  snackbar.item = { key, text, actionText, onAction, linkText, onLink, close, tone };
  if (duration > 0) snackTimer = setTimeout(close, duration);
  return close;
}

// 复制到剪贴板（非 HTTPS 页面没有 navigator.clipboard 时用旧方法兜底）
export async function copyText(text, successText = "已复制") {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  ui.message?.success(successText);
}
