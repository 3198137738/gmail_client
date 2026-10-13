// 多个页面共用的操作：同步、退出登录、延后邮件。
import { post } from "./api";
import { router } from "./router";
import { stopRealtime } from "./realtime";
import { store } from "./store";
import { run, toast } from "./ui";
import { futureDate } from "./format";

export async function syncAll() {
  const result = await run(post("/api/sync"));
  if (result) toast(result.queued ? `已开始同步 ${result.queued} 个账号` : "没有已启用的账号");
}

export async function syncAccount(accountId) {
  if (await run(post(`/api/sync/${accountId}`))) toast("已开始同步");
}

// 退出登录后清空页面上的数据，回到登录页
export function resetAfterLogout() {
  stopRealtime();
  store.nav = null;
  store.compose = null;
  store.composeBusy = false;
  if (store.session) store.session.authenticated = false;
}

export async function logout() {
  await run(post("/api/logout"));
  resetAfterLogout();
  router.push({ name: "login" });
}

// 延后（稍后提醒）邮件；until 为 null 时取消延后。成功后提示，可撤销（恢复原来的状态）。
export async function snoozeMessages(ids, until, { previous = null, onChange } = {}) {
  const result = await run(post("/api/messages/snooze", { ids, until_ms: until }));
  if (!result) return false;
  onChange?.();
  const text = until ? `已延后到 ${futureDate(new Date(until))}` : "已取消延后，邮件回到收件箱";
  toast(text, {
    actionText: "撤销",
    onAction: async () => {
      const prev = previous ? new Date(previous).getTime() : null;
      if (await run(post("/api/messages/snooze", { ids, until_ms: prev && prev > Date.now() ? prev : null }))) onChange?.();
    },
    duration: 6000,
  });
  return true;
}
