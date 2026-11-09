// 页面地址与旧版（服务端渲染）保持一致，书签和 Telegram 通知里的链接继续有效。
import { createRouter, createWebHistory } from "vue-router";
import { refreshSession, setUnauthorizedHandler } from "./api";
import { openCompose, store } from "./store";
import { APP_NAME } from "./brand";
import AppShell from "./shell/AppShell.vue";
// 列表页和详情页用同一个组件实例（分栏阅读），必须是同一个组件对象
import MailSplit from "./views/MailSplit.vue";
import { SECTIONS } from "./settings/sections";

const LEGACY_SETTINGS = {
  "accounts/": "/settings/accounts",
  "accounts/rules": "/settings/rules",
  "accounts/templates": "/settings/templates",
  "accounts/security": "/settings/security",
  "accounts/security/audit": "/settings/audit",
  "messages/blocked-senders": "/settings/senders",
};

// 只允许站内相对路径，防止登录后被跳转到外部网站
export function safeNext(value, fallback = "/messages/") {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return fallback;
  }
  return value;
}

const routes = [
  { path: "/login", name: "login", component: () => import("./views/LoginView.vue"), meta: { public: true, title: "登录" } },
  { path: "/login/2fa", name: "login2fa", component: () => import("./views/TwoFactorView.vue"), meta: { public: true, title: "两步验证" } },
  {
    path: "/",
    component: AppShell,
    children: [
      { path: "", redirect: "/messages/" },
      { path: "messages/", name: "list", component: MailSplit },
      { path: "messages/:id(\\d+)", name: "message", component: MailSplit },
      {
        // 旧版的整页写邮件地址：在当前界面打开写邮件窗口
        path: "compose/",
        name: "compose",
        redirect: (to) => {
          const { reply_to, mode, forward, draft } = to.query;
          openCompose({ reply_to, mode, forward, draft });
          return { path: "/messages/", query: {} };
        },
      },
      { path: "compose/drafts", name: "drafts", component: () => import("./views/DraftsView.vue"), meta: { title: "草稿", section: "drafts" } },
      { path: "compose/scheduled", name: "scheduled", component: () => import("./views/ScheduledView.vue"), meta: { title: "定时发送", section: "scheduled" } },
      { path: "compose/sent", name: "sent", component: () => import("./views/SentView.vue"), meta: { title: "发件记录", section: "sent_log" } },
      { path: "files", name: "files", component: () => import("./views/FilesView.vue"), meta: { title: "附件", section: "files" } },
      { path: "cleanup", name: "cleanup", component: () => import("./views/CleanupView.vue"), meta: { title: "批量清理", section: "cleanup" } },
      { path: "compose/sent/:id(\\d+)", name: "sentDetail", component: () => import("./mail/SentReader.vue"), meta: { section: "sent_log" } },
      {
        path: "settings",
        component: () => import("./settings/SettingsShell.vue"),
        children: [
          { path: "", name: "settings", component: { render: () => null }, meta: { title: "设置", section: "settings" } },
          {
            path: "accounts/add",
            name: "settings-accounts-add",
            component: () => import("./settings/sections/AddAccountSection.vue"),
            meta: { title: "设置 - 添加账号", section: "settings", settingsSection: "accounts-add" },
          },
          ...SECTIONS.map((s) => ({
            path: s.key,
            name: `settings-${s.key}`,
            component: s.component,
            meta: { title: `设置 - ${s.label}`, section: "settings", settingsSection: s.key },
          })),
        ],
      },
      // 旧版设置地址（书签、OAuth 授权回调 /accounts/?...）：带上参数跳到新地址
      ...Object.entries(LEGACY_SETTINGS).map(([path, target]) => ({
        path,
        redirect: (to) => ({ path: target, query: to.query, hash: to.hash }),
      })),
      { path: ":pathMatch(.*)*", name: "notFound", component: () => import("./views/NotFound.vue"), meta: { title: "找不到页面" } },
    ],
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, from, saved) {
    return saved || { top: 0 };
  },
});

router.beforeEach(async (to) => {
  if (!store.session) {
    try {
      await refreshSession();
    } catch {
      return true;   // 服务器暂时连不上：照常显示，页面里的请求会提示错误
    }
  }
  const session = store.session;
  if (to.name === "login") {
    return session.authenticated ? safeNext(to.query.next) : true;
  }
  if (to.name === "login2fa") {
    if (session.authenticated) return safeNext(to.query.next);
    return session.two_factor_pending ? true : { name: "login", query: to.query };
  }
  if (!session.authenticated) {
    return { name: "login", query: { next: to.fullPath } };
  }
  return true;
});

router.afterEach((to) => {
  if (to.meta.title) document.title = `${to.meta.title} - ${APP_NAME}`;
});

// 接口返回 401（登录过期、设备被登出）：回到登录页，登录后回到当前页面
setUnauthorizedHandler(() => {
  const current = router.currentRoute.value;
  if (current.meta.public) return;
  router.replace({ name: "login", query: { next: current.fullPath } });
});
