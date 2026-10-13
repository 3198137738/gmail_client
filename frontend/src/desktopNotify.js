// 浏览器桌面通知：收到新邮件且页面不在前台时，弹出系统通知；点击通知打开该邮件。
// 开关保存在本浏览器（localStorage），首次开启时向浏览器申请通知权限。
import { reactive } from "vue";
import { onRealtime } from "./realtime";
import { router } from "./router";
import { navAccount } from "./store";
import { toast } from "./ui";

const KEY = "gm:desktop-notify";
const supported = typeof window !== "undefined" && "Notification" in window;

export const desktopNotify = reactive({
  supported,
  enabled: supported && localStorage.getItem(KEY) === "1" && Notification.permission === "granted",
});

export async function setDesktopNotify(on) {
  if (!supported) {
    toast("这个浏览器不支持桌面通知");
    return;
  }
  if (on) {
    const permission = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
    if (permission !== "granted") {
      toast("浏览器已阻止通知，请在地址栏左侧的网站设置中允许「通知」");
      return;
    }
  }
  desktopNotify.enabled = on;
  localStorage.setItem(KEY, on ? "1" : "0");
  toast(on ? "已开启桌面通知：页面不在前台时，新邮件会弹出提醒" : "已关闭桌面通知");
}

function line(m) {
  const code = m.otp_code ? `（验证码 ${m.otp_code}）` : "";
  return `${m.subject || "(无主题)"}${code}`;
}

function show(ev) {
  if (!desktopNotify.enabled || Notification.permission !== "granted") return;
  if (!ev.inbox || (document.visibilityState === "visible" && document.hasFocus())) return;
  const account = navAccount(ev.account_id);
  const preview = ev.preview || [];
  let title;
  let body;
  if (ev.inbox === 1 && preview.length) {
    title = preview[0].from || "新邮件";
    body = line(preview[0]);
  } else {
    title = `${ev.inbox} 封新邮件`;
    body = preview.map((m) => `${m.from}：${line(m)}`).join("\n");
    if (ev.inbox > preview.length) body += body ? "\n……" : "";
  }
  if (account) title += ` · ${account.email}`;
  // 同一账号的通知用同一个 tag：新的替换旧的；多个标签页打开时也只显示一条
  const n = new Notification(title, { body, tag: `gm-new-${ev.account_id}`, icon: `${import.meta.env.BASE_URL}img/favicon.svg` });
  n.onclick = () => {
    window.focus();
    n.close();
    if (ev.inbox === 1 && preview.length) router.push({ name: "message", params: { id: preview[0].id } });
    else router.push({ path: "/messages/", query: { kind: "inbox", account_id: ev.account_id } });
  };
}

let started = false;
export function startDesktopNotify() {
  if (started || !supported) return;
  started = true;
  onRealtime("new_mail", show);
}
