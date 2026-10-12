// 页面地址与旧版（服务端渲染）保持一致，书签和 Telegram 通知里的链接继续有效。
import { createRouter, createWebHistory } from "vue-router";
import { refreshSession, setUnauthorizedHandler } from "./api";
import { openCompose, store } from "./store";
import AppLayout from "./components/AppLayout.vue";

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
    component: AppLayout,
    children: [
      { path: "", redirect: "/messages/" },
      { path: "messages/", name: "list", component: () => import("./views/MailListView.vue") },
      {
        path: "messages/blocked-senders",
        name: "blocked",
        component: () => import("./views/settings/BlockedSettings.vue"),
        meta: { title: "设置 - 过滤器和屏蔽的地址", settings: "blocked", section: "blocked" },
      },
      { path: "messages/:id(\\d+)", name: "message", component: () => import("./views/MessageView.vue") },
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
      { path: "compose/sent", name: "sent", component: () => import("./views/SentView.vue"), meta: { title: "发件记录", section: "sent_log" } },
      { path: "compose/sent/:id(\\d+)", name: "sentDetail", component: () => import("./views/SentDetailView.vue"), meta: { section: "sent_log" } },
      {
        path: "accounts/",
        name: "accounts",
        component: () => import("./views/settings/AccountsSettings.vue"),
        meta: { title: "设置 - 账号和导入", settings: "accounts", section: "settings" },
      },
      {
        path: "accounts/security",
        name: "security",
        component: () => import("./views/settings/SecuritySettings.vue"),
        meta: { title: "设置 - 安全", settings: "security", section: "settings" },
      },
      {
        path: "accounts/security/audit",
        name: "audit",
        component: () => import("./views/settings/AuditLog.vue"),
        meta: { title: "设置 - 审计日志", settings: "audit", section: "settings" },
      },
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
  if (to.meta.title) document.title = `${to.meta.title} - Roundcube`;
});

// 接口返回 401（登录过期、设备被登出）：回到登录页，登录后回到当前页面
setUnauthorizedHandler(() => {
  const current = router.currentRoute.value;
  if (current.meta.public) return;
  router.replace({ name: "login", query: { next: current.fullPath } });
});
