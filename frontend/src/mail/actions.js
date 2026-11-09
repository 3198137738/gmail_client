// 邮件操作注册表：列表批量栏、阅读工具栏、右键菜单、命令面板、滑动手势、快捷键共用同一份定义，
// 保证各处的操作、文字、可用条件一致。
//
// 操作上下文 ctx：
//   ids       目标邮件 id
//   items     目标邮件的数据（列表行或阅读中的邮件），用于判断已读 / 星标 / 验证码等状态
//   kind      当前文件夹（inbox / sent / junk / trash / blocked / snoozed / starred / all / label-N）
//   source    list / reader / context / palette / swipe
//   message   阅读中的邮件详情（GET /api/messages/:id 的结果），仅阅读区
//   hooks     { removed(ids)：邮件离开当前视图；updated()：状态变了，刷新显示 }
import {
  ArchiveOutlined,
  BlockOutlined,
  ContentCopyOutlined,
  DeleteForeverOutlined,
  DeleteOutlined,
  DraftsOutlined,
  ForwardOutlined,
  LabelOutlined,
  MarkEmailUnreadOutlined,
  OpenInNewOutlined,
  PrintOutlined,
  ReplyAllOutlined,
  ReplyOutlined,
  ReportOffOutlined,
  ReportOutlined,
  RestoreFromTrashOutlined,
  SnoozeOutlined,
  StarBorderOutlined,
  StarFilled,
} from "@vicons/material";
import { post } from "../api";
import { openCompose, store } from "../store";
import { confirm, copyText, run, toast } from "../ui";
import { refreshNav } from "../composables/common";
import { snoozeMessages } from "../actions";
import { upsertPurgeJob } from "../purgeJobs";
import { router } from "../router";
import { queueAction, queueDelete } from "./pendingQueue";
import { pickTime } from "./pickers";
import { providerOf, trashRetention } from "./wording";

const isTrash = (ctx) => ctx.kind === "trash" || ctx.kind === "blocked";
const isUnread = (m) => !m.is_read || m.thread_unread > 0;
const one = (ctx) => ctx.ids.length === 1;
const accountsOf = (ctx) => [...new Set(ctx.items.map((m) => m.account_id).filter(Boolean))];

// 邮件状态变了：刷新导航未读数和列表
function changed() {
  refreshNav();
  store.listVersion += 1;
}

async function setRead(ctx, read) {
  const result = await run(post("/api/messages/actions", { action: read ? "read" : "unread", ids: ctx.ids }));
  if (!result) return;
  for (const m of ctx.items) {
    m.is_read = read;
    if ("thread_unread" in m) m.thread_unread = read ? 0 : Math.max(1, m.thread_unread || 0);
  }
  if (ctx.source === "reader" && !read) toast("已标记为未读");
  changed();
}

async function setStar(ctx, starred) {
  const before = ctx.items.map((m) => m.is_starred);
  ctx.items.forEach((m) => (m.is_starred = starred));   // 先在界面上切换，失败时恢复
  const result = await run(post("/api/messages/actions", { action: starred ? "star" : "unstar", ids: ctx.ids }));
  if (!result) ctx.items.forEach((m, i) => (m.is_starred = before[i]));
  else if (ctx.kind === "starred" && !starred) ctx.hooks?.removed?.(ctx.ids);
  changed();
}

// 可以给这些邮件加的标签（各自账号的标签；只有 Gmail 账号有标签）
export function labelsFor(ctx) {
  if (ctx.message) return ctx.message.account_labels || [];
  const accounts = new Set(accountsOf(ctx));
  return (store.nav?.labels || []).filter((lb) => accounts.has(lb.account_id));
}

const inInbox = (m) => (m.folder_kind || "inbox") === "inbox";

export const ACTIONS = [
  {
    id: "reply",
    label: "回复",
    icon: ReplyOutlined,
    shortcut: "r",
    scopes: ["reader", "context", "palette"],
    available: one,
    run: (ctx) => openCompose({ reply_to: ctx.ids[0] }),
  },
  {
    id: "replyAll",
    label: "回复全部",
    icon: ReplyAllOutlined,
    shortcut: "a",
    scopes: ["reader", "context", "palette"],
    available: one,
    run: (ctx) => openCompose({ reply_to: ctx.ids[0], mode: "all" }),
  },
  {
    id: "forward",
    label: "转发",
    icon: ForwardOutlined,
    shortcut: "f",
    scopes: ["reader", "context", "palette"],
    available: one,
    run: (ctx) => openCompose({ forward: ctx.ids[0] }),
  },
  {
    id: "archive",
    label: "归档",
    icon: ArchiveOutlined,
    shortcut: "e",
    swipe: true,
    scopes: ["list", "reader", "context", "palette", "swipe"],
    // 只有收件箱里的邮件可以归档（Gmail 移到「所有邮件」，Outlook 移到「存档」）
    available: (ctx) => ctx.items.some(inInbox) && !isTrash(ctx) && ctx.kind !== "sent",
    run(ctx) {
      queueAction("archive", ctx.items.filter(inInbox).map((m) => m.id));
    },
  },
  {
    id: "spam",
    label: "举报垃圾邮件",
    icon: ReportOutlined,
    shortcut: "!",
    swipe: true,
    scopes: ["list", "reader", "context", "palette", "swipe"],
    available: (ctx) => !isTrash(ctx) && ctx.kind !== "sent" && ctx.kind !== "junk" && ctx.items.some((m) => !["junk", "trash", "sent"].includes(m.folder_kind)),
    run(ctx) {
      queueAction("spam", ctx.ids);
    },
  },
  {
    id: "restore",
    label: "移至收件箱",
    icon: RestoreFromTrashOutlined,
    scopes: ["list", "reader", "context", "palette"],
    available: (ctx) => ctx.kind === "trash" && !ctx.message?.sender_blocked,
    async run(ctx) {
      const result = await run(post("/api/messages/actions", { action: "restore", ids: ctx.ids }));
      if (!result) return;
      toast(`已将 ${result.affected} 封邮件移至收件箱`);
      ctx.hooks?.removed?.(ctx.ids);
      changed();
    },
  },
  {
    id: "notSpam",
    label: "不是垃圾邮件",
    icon: ReportOffOutlined,
    scopes: ["reader", "context", "palette"],
    available: (ctx) => ctx.kind === "junk" && one(ctx),
    async run(ctx) {
      const who = ctx.message?.sender_email || "该发件人";
      const ok = await confirm(
        `这不是垃圾邮件？\n将把该发件人的垃圾邮件全部移回收件箱，并信任 ${who}：以后其邮件即使被判为垃圾邮件，也会自动移回收件箱。`,
      );
      if (!ok) return;
      const result = await run(post(`/api/messages/${ctx.ids[0]}/not-spam`));
      if (!result) return;
      toast(result.message || "已移回收件箱");
      ctx.hooks?.removed?.(ctx.ids);
      changed();
    },
  },
  {
    id: "delete",
    label: (ctx) => (isTrash(ctx) ? "永久删除" : "删除"),
    icon: (ctx) => (isTrash(ctx) ? DeleteForeverOutlined : DeleteOutlined),
    shortcut: "#",
    danger: true,
    swipe: true,
    scopes: ["list", "reader", "context", "palette", "swipe"],
    available: () => true,
    async run(ctx) {
      if (!isTrash(ctx)) {
        // 移到「已删除」可以撤销：先隐藏（列表、阅读区都会响应），几秒后才真正删除
        queueDelete(ctx.ids);
        return;
      }
      const n = ctx.ids.length;
      const ok = await confirm(
        `确定永久删除${n > 1 ? `这 ${n} 封` : "这封"}邮件吗？邮件会从邮箱服务器中彻底删除，无法恢复。`,
        { danger: true, positiveText: "永久删除" },
      );
      if (!ok) return;
      if (!(await run(post("/api/messages/actions", { action: "delete", ids: ctx.ids })))) return;
      toast(n > 1 ? `已永久删除 ${n} 封邮件` : "已永久删除邮件");
      ctx.hooks?.removed?.(ctx.ids);
      changed();
    },
  },
  {
    id: "read",
    label: "标为已读",
    icon: DraftsOutlined,
    shortcut: "I",
    scopes: ["list", "context", "palette"],
    available: (ctx) => ctx.items.some(isUnread),
    run: (ctx) => setRead(ctx, true),
  },
  {
    id: "unread",
    label: "标为未读",
    icon: MarkEmailUnreadOutlined,
    shortcut: "U",
    scopes: ["list", "reader", "context", "palette"],
    available: (ctx) => ctx.source === "reader" || ctx.items.some((m) => !isUnread(m)),
    run: (ctx) => setRead(ctx, false),
  },
  {
    // 滑动手势用：已读 ↔ 未读
    id: "toggleRead",
    label: (ctx) => (ctx.items.some(isUnread) ? "标为已读" : "标为未读"),
    icon: (ctx) => (ctx.items.some(isUnread) ? DraftsOutlined : MarkEmailUnreadOutlined),
    swipe: true,
    scopes: ["swipe"],
    available: () => true,
    run: (ctx) => setRead(ctx, ctx.items.some(isUnread)),
  },
  {
    id: "star",
    label: (ctx) => (ctx.items.length && ctx.items.every((m) => m.is_starred) ? "取消星标" : "加星标"),
    icon: (ctx) => (ctx.items.length && ctx.items.every((m) => m.is_starred) ? StarFilled : StarBorderOutlined),
    active: (ctx) => ctx.items.length > 0 && ctx.items.every((m) => m.is_starred),
    shortcut: "s",
    swipe: true,
    scopes: ["list", "reader", "context", "palette", "swipe"],
    available: (ctx) => !isTrash(ctx),
    run: (ctx) => setStar(ctx, !ctx.items.every((m) => m.is_starred)),
  },
  {
    id: "snooze",
    label: (ctx) => (ctx.kind === "snoozed" || ctx.items.every((m) => m.snoozed_until) ? "取消延后" : "延后…"),
    icon: SnoozeOutlined,
    active: (ctx) => ctx.items.length > 0 && ctx.items.every((m) => m.snoozed_until),
    shortcut: "b",
    swipe: true,
    scopes: ["list", "reader", "context", "palette", "swipe"],
    available: (ctx) => !isTrash(ctx) && ctx.kind !== "sent" && ctx.kind !== "junk",
    async run(ctx) {
      const previous = ctx.items.length === 1 ? ctx.items[0].snoozed_until : null;
      if (ctx.kind === "snoozed" || ctx.items.every((m) => m.snoozed_until)) {
        await snoozeMessages(ctx.ids, null, { previous, onChange: changed });
        ctx.items.forEach((m) => (m.snoozed_until = null));
        if (ctx.kind === "snoozed") ctx.hooks?.removed?.(ctx.ids);
        return;
      }
      const until = await pickTime();
      if (!until) return;
      if (await snoozeMessages(ctx.ids, until, { previous, onChange: changed })) ctx.hooks?.removed?.(ctx.ids);
    },
  },
  {
    id: "labels",
    label: "标签…",
    icon: LabelOutlined,
    shortcut: "l",
    scopes: ["list", "reader"],
    // 只有 Gmail 账号有标签；阅读区由 LabelPicker、列表由 BulkLabelPicker 处理
    available: (ctx) => !isTrash(ctx) && labelsFor(ctx).length > 0,
    run: (ctx) => ctx.hooks?.pickLabels?.(),
  },
  {
    id: "block",
    label: "屏蔽发件人",
    icon: BlockOutlined,
    scopes: ["list", "reader", "context", "palette"],
    available: (ctx) => !isTrash(ctx) && ctx.kind !== "sent" && !ctx.message?.sender_blocked,
    async run(ctx) {
      const n = ctx.ids.length;
      const who = ctx.message?.sender_email || (n > 1 ? `选中 ${n} 封邮件的发件人` : "这个发件人");
      const ok = await confirm(
        `永久屏蔽${who}？\n已有邮件会移到「已删除」，之后收到的新邮件也会自动移到「已删除」（${trashRetention(providerOf(accountsOf(ctx)))}）。`,
        { danger: true, positiveText: "屏蔽" },
      );
      if (!ok) return;
      const result = await run(post("/api/senders/block", { ids: ctx.ids }));
      if (!result) return;
      const count = result.senders?.length || 0;
      if (!count) toast("没有可屏蔽的发件人");
      else toast(`已屏蔽 ${count === 1 ? result.senders[0] : `${count} 个发件人`}${result.job ? "，正在把其邮件移到「已删除」" : ""}`);
      if (result.job) upsertPurgeJob(result.job);
      ctx.hooks?.removed?.(ctx.ids);
      changed();
    },
  },
  {
    id: "copyOtp",
    label: (ctx) => `复制验证码 ${ctx.items[0]?.otp_code || ""}`,
    icon: ContentCopyOutlined,
    scopes: ["context", "palette"],
    available: (ctx) => one(ctx) && !!ctx.items[0]?.otp_code,
    run: (ctx) => copyText(ctx.items[0].otp_code, `已复制验证码 ${ctx.items[0].otp_code}`),
  },
  {
    id: "openTab",
    label: "在新标签页中打开",
    icon: OpenInNewOutlined,
    scopes: ["context"],
    available: one,
    run: (ctx) => window.open(router.resolve({ name: "message", params: { id: ctx.ids[0] } }).href, "_blank"),
  },
  {
    id: "print",
    label: "打印",
    icon: PrintOutlined,
    scopes: ["reader", "palette"],
    available: (ctx) => ctx.source === "reader" || (ctx.source === "palette" && !!ctx.message),
    run: () => window.print(),
  },
  {
    id: "openBody",
    label: "在新窗口中打开正文",
    icon: OpenInNewOutlined,
    scopes: ["reader", "palette"],
    available: (ctx) => one(ctx) && !!ctx.message,
    run: (ctx) => window.open(`/messages/${ctx.ids[0]}/body?images=1`, "_blank", "noopener"),
  },
];

const BY_ID = Object.fromEntries(ACTIONS.map((a) => [a.id, a]));

export function getAction(id) {
  return BY_ID[id];
}

// 某处（scope）当前可用的操作
export function actionsFor(scope, ctx) {
  if (!ctx?.ids?.length) return [];
  return ACTIONS.filter((a) => a.scopes.includes(scope) && a.available(ctx));
}

export function actionLabel(action, ctx) {
  return typeof action.label === "function" ? action.label(ctx) : action.label;
}

export function actionIcon(action, ctx) {
  // @vicons 的图标是组件对象；写成函数的是「按状态选图标」
  return typeof action.icon === "function" ? action.icon(ctx) : action.icon;
}

export function actionTitle(action, ctx) {
  const label = actionLabel(action, ctx);
  if (!action.shortcut) return label;
  const key = action.shortcut === "I" || action.shortcut === "U" ? `Shift+${action.shortcut}` : action.shortcut;
  return `${label}（${key}）`;
}

export async function runAction(id, ctx) {
  const action = BY_ID[id];
  if (!action || !ctx?.ids?.length || !action.available(ctx)) return false;
  await action.run(ctx);
  return true;
}

// 滑动手势可选的动作（设置里用）
export const SWIPE_CHOICES = [
  { value: "none", label: "无" },
  { value: "archive", label: "归档" },
  { value: "delete", label: "删除" },
  { value: "toggleRead", label: "标为已读 / 未读" },
  { value: "star", label: "加星标" },
  { value: "snooze", label: "延后" },
];
