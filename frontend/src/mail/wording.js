// 按服务商区分的说明文字：Gmail 与 Outlook 的「已删除」保留规则不同，不能都写成 Gmail。
import { store } from "../store";

// 一组账号 id → "gmail" / "outlook" / "mixed"（全部账号时按已有账号判断）
export function providerOf(accountIds) {
  const accounts = store.nav?.accounts || [];
  const ids = accountIds?.length ? new Set(accountIds) : null;
  const providers = new Set(accounts.filter((a) => !ids || ids.has(a.id)).map((a) => a.provider || "gmail"));
  if (providers.size === 1) return [...providers][0];
  return providers.size ? "mixed" : "gmail";
}

export function trashRetention(provider) {
  if (provider === "gmail") return "30 天后由 Gmail 自动永久删除";
  if (provider === "outlook") return "之后由 Outlook 按保留策略自动清除";
  return "之后由邮箱服务商自动清除（Gmail 为 30 天）";
}

export function serviceName(provider) {
  if (provider === "gmail") return "Gmail";
  if (provider === "outlook") return "Outlook";
  return "邮箱服务器";
}
