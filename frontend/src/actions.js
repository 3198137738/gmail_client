// 多个页面共用的操作：同步、退出登录。
import { post } from "./api";
import { router } from "./router";
import { stopRealtime } from "./realtime";
import { store } from "./store";
import { run, toast } from "./ui";

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
