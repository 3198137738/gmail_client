// 冒烟测试步骤。每步：{ name, run(t), viewport?, touch?, dark?, allow?: [RegExp 匹配 "状态码 路径"] }
// t：{ page, base, data, login(), shot(name), expect(cond, message) }
// 新阶段在这里追加步骤；功能清单见 checklist.md。

const MOBILE = { width: 390, height: 844 };

// 旧地址（书签、Telegram 通知、OAuth 回调）必须继续可用
const PAGES = [
  ["/compose/drafts", "草稿"],
  ["/compose/scheduled", "定时发送"],
  ["/compose/sent", "发件记录"],
  ["/files", "附件"],
  ["/cleanup", "批量清理"],
  ["/accounts/", "账号", "/settings/accounts"],
  ["/accounts/rules", "规则", "/settings/rules"],
  ["/accounts/templates", "模板", "/settings/templates"],
  ["/accounts/security", "两步验证", "/settings/security"],
  ["/accounts/security/audit", "审计", "/settings/audit"],
  ["/messages/blocked-senders", "屏蔽", "/settings/senders"],
];

const SETTINGS_SECTIONS = [
  ["accounts", "添加账号"],
  ["signatures", "签名"],
  ["groups", "分组"],
  ["rules", "自动规则"],
  ["templates", "邮件模板"],
  ["senders", "屏蔽的地址"],
  ["appearance", "滑动手势"],
  ["notifications", "桌面通知"],
  ["shortcuts", "快捷键"],
  ["security", "两步验证"],
  ["backup", "立即备份"],
  ["audit", "事件"],
];

const row = (page, text) => page.locator(".gm-mr", { hasText: text }).first();

async function openRow(page, text) {
  await row(page, text).click();
  await page.waitForURL(/\/messages\/\d+/);
  // 阅读区加载新邮件时仍显示上一封，等主题变成这一封
  await page.locator(".gm-rd-subject", { hasText: text }).waitFor();
}

// 触屏滑动（CDP 触摸事件）
async function swipe(page, locator, dx) {
  const box = await locator.boundingBox();
  const y = box.y + box.height / 2;
  const x0 = box.x + box.width / 2;
  const cdp = await page.context().newCDPSession(page);
  const send = (type, x) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x, y }] });
  await send("touchStart", x0);
  for (let i = 1; i <= 8; i++) await send("touchMove", x0 + (dx * i) / 8);
  await send("touchEnd", x0 + dx);
  await cdp.detach();
}

async function longPress(page, locator) {
  const box = await locator.boundingBox();
  const cdp = await page.context().newCDPSession(page);
  const p = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [p] });
  await new Promise((r) => setTimeout(r, 650));
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await cdp.detach();
}

async function panelText(page) {
  return (await page.locator(".gm-fp").first().innerText()).replace(/\s+/g, " ");
}

export const steps = [
  {
    name: "shell-desktop",
    async run(t) {
      const { page } = t;
      await t.login();
      t.expect(await page.locator(".gm-rail").isVisible(), "图标栏没有显示");
      const text = await panelText(page);
      for (const word of ["收件箱", "已加星标", "已发送", "所有邮件", "标签", "Gmail", "Outlook", "工作", "注册用", "全部账号"]) {
        t.expect(text.includes(word), `文件夹面板缺少「${word}」：${text.slice(0, 200)}`);
      }
      await t.shot("inbox");
      // 分组折叠后刷新仍保持
      await page.locator(".gm-fp-group", { hasText: "注册用" }).first().click();
      t.expect(!(await panelText(page)).includes("开发注册"), "分组没有折叠");
      await page.reload();
      await page.waitForSelector(".gm-fp");
      t.expect(!(await panelText(page)).includes("开发注册"), "分组折叠状态没有记住");
    },
  },
  {
    name: "shell-account-switch",
    async run(t) {
      const { page, data } = t;
      await t.login();
      // ' 打开账号切换器，搜索 Outlook 账号
      await page.keyboard.press("'");
      await page.waitForSelector(".gm-sw-card");
      await page.keyboard.type("bob@out");
      await t.shot("switcher");
      await page.keyboard.press("Enter");
      await page.waitForURL(new RegExp(`account_id=${data.outlook}`));
      await page.locator(".gm-fp-scope-name", { hasText: "Bob Outlook" }).waitFor({ timeout: 5000 });
      // Alt+0 全部账号，Alt+1 第一个账号（侧栏顺序）
      await page.keyboard.press("Alt+Digit0");
      await page.waitForURL(/account_id=(&|$)/);
      await page.locator(".gm-fp-scope-name", { hasText: "全部账号" }).waitFor({ timeout: 5000 });
      await page.keyboard.press("Alt+Digit1");
      await page.locator(".gm-fp-scope-name", { hasText: "Alice 工作" }).waitFor({ timeout: 5000 });
      await page.waitForLoadState("networkidle");
      // 最近使用置顶
      await page.keyboard.press("'");
      await page.locator(".gm-sw-heading", { hasText: "最近使用" }).waitFor({ timeout: 5000 });
      await page.keyboard.press("Escape");
    },
  },
  {
    name: "shell-palette",
    async run(t) {
      const { page } = t;
      await t.login();
      await page.keyboard.press("Control+k");
      await page.waitForSelector(".gm-pal");
      await t.shot("palette");
      await page.keyboard.type("已发送");
      await page.keyboard.press("Enter");
      await page.waitForURL(/kind=sent/);
      // 命令面板切换主题
      await page.keyboard.press("Control+k");
      await page.keyboard.type("深色");
      await page.keyboard.press("Enter");
      await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
      // 命令面板跳转设置
      await page.keyboard.press("Control+k");
      await page.keyboard.type("审计");
      await page.keyboard.press("Enter");
      await page.waitForURL(/settings\/audit/);
    },
  },
  {
    name: "shell-panel-resize",
    async run(t) {
      const { page } = t;
      await t.login();
      const panel = page.locator(".gm-app-panel");
      const before = (await panel.boundingBox()).width;
      const handle = page.locator(".gm-resizer").first();
      const box = await handle.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + 60, box.y + box.height / 2, { steps: 5 });
      await page.mouse.up();
      const after = (await panel.boundingBox()).width;
      t.expect(after > before + 40, `拖动后宽度没有变化：${before} → ${after}`);
      // [ 收起面板，刷新后保持
      await page.evaluate(() => document.activeElement?.blur());
      await page.keyboard.press("[");
      await page.waitForSelector(".gm-app-panel", { state: "detached" });
      await page.reload();
      await page.waitForSelector(".gm-row");
      t.expect(!(await page.locator(".gm-app-panel").count()), "收起状态没有记住");
      await page.locator(".gm-app-toggle").click();
      const restored = (await page.locator(".gm-app-panel").boundingBox()).width;
      t.expect(Math.abs(restored - after) < 2, `展开后宽度没有保持：${restored} vs ${after}`);
    },
  },
  {
    name: "shell-mail-split",
    async run(t) {
      const { page } = t;
      await t.login();
      await page.locator(".gm-row").nth(3).click();
      await page.waitForURL(/\/messages\/\d+/);
      await page.waitForSelector(".gm-split-reader");
      await t.shot("reading");
      await page.keyboard.press("u");
      await page.waitForURL(/\/messages\/\?/);
    },
  },
  {
    name: "shell-old-pages",
    async run(t) {
      const { page, base } = t;
      await t.login();
      for (const [path, word, target] of PAGES) {
        await page.goto(`${base}${path}`);
        await page.waitForLoadState("networkidle");
        if (target) t.expect(new URL(page.url()).pathname === target, `${path} 应跳转到 ${target}，实际 ${page.url()}`);
        const text = await page.locator(".gm-app-content").innerText();
        t.expect(text.includes(word), `${path} 没有显示「${word}」`);
      }
      await t.shot("audit");
    },
  },
  {
    name: "shell-compose",
    async run(t) {
      const { page } = t;
      await t.login();
      await page.keyboard.press("c");
      await page.waitForSelector(".gm-cw .gm-cw-subject", { timeout: 10000 });
      await t.shot("compose");
    },
  },
  {
    name: "shell-dark",
    dark: true,
    async run(t) {
      const { page } = t;
      await page.addInitScript(() => localStorage.setItem("gm:prefs:v1", JSON.stringify({ theme: "dark" })));
      await t.login();
      t.expect(await page.evaluate(() => document.documentElement.dataset.theme) === "dark", "没有切换到深色");
      await t.shot("inbox");
      await page.locator(".gm-row").nth(1).click();
      await page.waitForSelector(".gm-split-reader");
      await t.shot("reading");
    },
  },
  {
    name: "shell-narrow",
    viewport: { width: 1100, height: 800 },
    async run(t) {
      const { page } = t;
      await t.login();
      t.expect(!(await page.locator(".gm-app-panel").count()), "窄窗口时文件夹面板不应停靠");
      await page.locator(".gm-app-toggle").click();
      await page.waitForSelector(".gm-app-panel.is-overlay");
      await page.waitForTimeout(300);
      await t.shot("overlay");
      await page.locator(".gm-fp-item", { hasText: "已发送" }).click();
      await page.waitForURL(/kind=sent/);
      await page.waitForSelector(".gm-app-panel", { state: "detached" });
    },
  },
  {
    name: "shell-mobile",
    viewport: MOBILE,
    touch: true,
    async run(t) {
      const { page } = t;
      await t.login();
      t.expect(await page.locator(".gm-tabbar").isVisible(), "手机端没有底部标签栏");
      t.expect(!(await page.locator(".gm-rail").count()), "手机端不应显示图标栏");
      await t.shot("inbox");
      // 文件夹面板
      await page.locator(".gm-tab", { hasText: "文件夹" }).tap();
      await page.waitForSelector(".gm-fp.is-mobile");
      await page.waitForTimeout(400);
      await t.shot("folders");
      await page.locator(".gm-fp-item", { hasText: "已加星标" }).tap();
      await page.waitForURL(/kind=starred/);
      // 我的
      await page.locator(".gm-tab", { hasText: "我的" }).tap();
      await page.waitForSelector(".gm-me");
      await page.waitForTimeout(400);
      await t.shot("me");
      await page.locator(".gm-me-item", { hasText: "草稿" }).tap();
      await page.waitForURL(/compose\/drafts/);
      // 账号切换器全屏
      await page.locator(".gm-tab", { hasText: "账号" }).tap();
      await page.waitForSelector(".gm-sw-card.is-mobile");
      await page.waitForTimeout(400);
      await t.shot("switcher");
      await page.locator(".gm-sw-cancel").tap();
      // 阅读时隐藏标签栏
      await page.goto(`${t.base}/messages/?kind=inbox`);
      await page.waitForSelector(".gm-row");
      await page.locator(".gm-row").nth(2).tap();
      await page.waitForURL(/\/messages\/\d+/);
      await page.waitForSelector(".gm-split-reader");
      t.expect(!(await page.locator(".gm-tabbar").count()), "阅读时应隐藏标签栏");
      await t.shot("reading");
    },
  },

  // ---------------- 阶段 1：列表与阅读 ----------------
  {
    name: "mail-reader",
    allow: [/^404 \/mail-image/],   // 种子数据里的外部图片地址不存在
    async run(t) {
      const { page } = t;
      await t.login();
      // 钓鱼提示
      await openRow(page, "Apple ID");
      await page.waitForSelector(".gm-tm-alert");
      t.expect((await page.locator(".gm-tm-alert").innerText()).includes("钓鱼"), "没有钓鱼提示");
      await t.shot("phishing");
      // 验证码横幅 + 复制
      await openRow(page, "阿里云");
      await page.locator(".gm-tm-otp", { hasText: "825106" }).waitFor();
      await page.locator(".gm-tm-otp button").click();
      await page.locator(".gm-snackbar, .n-message", { hasText: "825106" }).first().waitFor();
      // 外部图片：服务器设置为拦截时显示提示，点「显示图片」后正文改为带 images=1
      await openRow(page, "新品上市");
      if (await page.locator(".gm-tm-images button").count()) {
        await page.locator(".gm-tm-images button").click();
        await page.waitForSelector('iframe[src*="images=1"]');
      }
      // 会话（默认开启会话视图）：同一会话的 3 封合并显示，可全部展开
      await openRow(page, "周报讨论");
      const cards = page.locator(".gm-tm");
      t.expect((await cards.count()) === 3, `会话应有 3 封，实际 ${await cards.count()}`);
      await page.locator(".gm-rd-subject-actions button[title='全部展开']").click();
      t.expect((await page.locator(".gm-tm.is-open").count()) === 3, "全部展开失败");
      await t.shot("thread");
      // j / k 上下封
      const idOf = (url) => new URL(url).pathname.split("/").pop();
      const before = idOf(page.url());
      await page.keyboard.press("j");
      await page.keyboard.press("k");   // 新邮件还没加载完时按 k 不应跳过头
      await page.waitForFunction((id) => !location.pathname.endsWith(`/${id}`), before);
      await page.locator(".gm-rd-subject", { hasText: "周报讨论" }).waitFor({ state: "detached" });
      await page.keyboard.press("k");
      await page.waitForFunction((id) => location.pathname.endsWith(`/${id}`), before);
      await page.locator(".gm-rd-subject", { hasText: "周报讨论" }).waitFor();
      // 星标（s）同步到列表
      await page.keyboard.press("s");
      await page.locator(".gm-mr.is-active .gm-mr-star.is-on").waitFor({ timeout: 5000 });
      // 标签（l）
      await page.keyboard.press("l");
      await page.locator(".gm-lp").waitFor();
      await page.locator(".gm-lp-item", { hasText: "账单" }).click();
      await page.locator(".gm-lp-foot button", { hasText: "应用" }).click();
      await page.locator(".gm-rd-tag.is-label", { hasText: "账单" }).waitFor();
      // 标为未读不离开阅读
      const url = page.url();
      await page.keyboard.press("Shift+U");
      await page.locator(".gm-snackbar", { hasText: "未读" }).waitFor();
      t.expect(page.url() === url, "标为未读后不应离开阅读");
      // Esc 关闭阅读窗格
      await page.keyboard.press("Escape");
      await page.waitForSelector(".gm-split-empty");
    },
  },
  {
    name: "mail-bulk-undo",
    async run(t) {
      const { page } = t;
      await t.login();
      const total = async () => Number((await page.locator(".gm-ml-range").innerText()).split("/")[1]);
      const start = await total();
      // x 选中两封（j 移动光标）
      await page.keyboard.press("j");
      await page.keyboard.press("x");
      await page.keyboard.press("j");
      await page.keyboard.press("x");
      await page.locator(".gm-ml-selcount", { hasText: "已选 2" }).waitFor();
      await t.shot("selected");
      // 删除 → 撤销
      await page.locator(".gm-ml-bulk button[title^='删除']").click();
      await page.locator(".gm-snackbar", { hasText: "已删除 2 封" }).waitFor();
      t.expect((await page.locator(".gm-mr").count()) <= 23, "删除后邮件没有隐藏");
      await page.locator(".gm-snackbar-btn", { hasText: "撤销" }).click();
      await page.waitForFunction(() => document.querySelectorAll(".gm-mr").length === 25);
      // Shift 区间选择 3 封后删除，等待提交
      await page.locator(".gm-mr").nth(2).locator(".gm-mr-lead").click();
      await page.locator(".gm-mr").nth(4).locator(".gm-mr-lead").click({ modifiers: ["Shift"] });
      await page.locator(".gm-ml-selcount", { hasText: "已选 3" }).waitFor();
      await page.keyboard.press("#");
      await page.waitForTimeout(5600);
      await page.waitForFunction((n) => {
        const el = document.querySelector(".gm-ml-range");
        return el && Number(el.textContent.split("/")[1]) === n;
      }, start - 3, { timeout: 8000 });
    },
  },
  {
    name: "mail-context-snooze",
    async run(t) {
      const { page, base } = t;
      await t.login();
      // 右键菜单 → 加星标
      const target = row(page, "测试邮件 2：");
      await target.click({ button: "right" });
      await page.locator(".n-dropdown-option", { hasText: "加星标" }).waitFor();
      await t.shot("menu");
      await page.locator(".n-dropdown-option", { hasText: "加星标" }).click();
      await target.locator(".gm-mr-star.is-on").waitFor();
      // 悬停操作：延后到明天上午 → 离开收件箱，出现在「已延后」
      const snoozeBtn = target.locator(".gm-mr-hover-btn[aria-label^='延后']");
      // 前面步骤的删除提交后列表可能刷新、行位置移动：悬停落空时重新悬停
      for (let i = 0; ; i++) {
        await target.hover();
        try {
          await snoozeBtn.click({ timeout: 3000 });
          break;
        } catch (err) {
          if (i >= 3) throw err;
        }
      }
      await page.locator(".n-modal").getByText("明天上午").click();
      await page.locator(".gm-snackbar", { hasText: "已延后到" }).waitFor();
      await page.locator(".gm-mr", { hasText: "测试邮件 2：" }).waitFor({ state: "detached" });
      await page.goto(`${base}/messages/?kind=snoozed`);
      await row(page, "测试邮件 2：").waitFor();
      t.expect((await row(page, "测试邮件 2：").locator(".gm-mr-date.is-snoozed").count()) === 1, "已延后列表没有显示延后时间");
    },
  },
  {
    name: "mail-filters",
    async run(t) {
      const { page } = t;
      await t.login();
      await page.locator(".gm-ml-seg button", { hasText: "未读" }).click();
      await page.waitForURL(/unread=1/);
      await page.locator(".gm-ml-seg button.is-on", { hasText: "未读" }).waitFor();
      // 会话视图开关
      await page.locator("button[title^='会话视图']").click();
      await page.locator(".gm-snackbar", { hasText: "会话视图" }).waitFor();
      // 全部标为已读
      await page.locator(".gm-ml-bar button[title='更多']").click();
      await page.locator(".n-dropdown-option", { hasText: "全部标为已读" }).click();
      await page.locator(".n-dialog .n-button", { hasText: "确定" }).click();
      await page.locator(".gm-snackbar", { hasText: "标记为已读" }).waitFor();
      await t.shot("unread");
    },
  },
  {
    name: "mail-pane-off",
    async run(t) {
      const { page } = t;
      await t.login();
      await page.locator(".gm-ml-bar button[title='更多']").click();
      await page.locator(".n-dropdown-option", { hasText: "阅读窗格" }).hover();
      await page.locator(".n-dropdown-option", { hasText: "关闭（整页阅读）" }).last().click();
      await page.waitForSelector(".gm-mr.is-wide");
      await t.shot("wide");
      const scrolled = await page.locator(".gm-ml-scroll").evaluate((el) => {
        el.scrollTop = 400;
        return el.scrollTop;   // 内容不够高时实际滚动距离会小于 400
      });
      t.expect(scrolled > 50, `列表太短，无法测试滚动恢复：${scrolled}`);
      await page.locator(".gm-mr").nth(14).click();
      await page.waitForSelector(".gm-rd-thread");
      t.expect(!(await page.locator(".gm-split-list").isVisible()), "整页阅读时列表应隐藏");
      await page.keyboard.press("u");
      await page.waitForSelector(".gm-mr.is-wide");
      const top = await page.locator(".gm-ml-scroll").evaluate((el) => el.scrollTop);
      t.expect(Math.abs(top - scrolled) < 5, `返回后滚动位置没有恢复：${scrolled} → ${top}`);
    },
  },
  {
    name: "mail-trash",
    async run(t) {
      const { page, base } = t;
      await t.login();
      await page.goto(`${base}/messages/?kind=trash`);
      await row(page, "已删除的旧邮件").waitFor();
      t.expect((await page.locator(".gm-banner-x").first().innerText()).includes("永久删除"), "已删除没有说明条");
      await openRow(page, "已删除的旧邮件");
      t.expect((await page.locator(".gm-rd-actions button[title^='移至收件箱']").count()) === 1, "已删除的邮件应能移回收件箱");
      await page.locator(".gm-rd-actions button[title^='永久删除']").click();
      await page.locator(".n-dialog", { hasText: "无法恢复" }).waitFor();
      await page.locator(".n-dialog .n-button", { hasText: "取消" }).click();
    },
  },
  {
    name: "mail-sent-record",
    async run(t) {
      const { page, base } = t;
      await t.login();
      await page.goto(`${base}/compose/sent`);
      await page.getByText("已发送：会议纪要").first().click();
      await page.waitForURL(/compose\/sent\/\d+/);
      await page.locator(".gm-sr-subject", { hasText: "会议纪要" }).waitFor();
      await t.shot("detail");
    },
  },
  {
    name: "mail-palette-actions",
    async run(t) {
      const { page } = t;
      await t.login();
      await page.keyboard.press("j");
      await page.keyboard.press("Control+k");
      await page.locator(".gm-pal input:focus").waitFor();
      await page.keyboard.type("加星标");
      await page.locator(".gm-pal-item.is-active", { hasText: "加星标" }).waitFor();
      await page.keyboard.press("Enter");
      await page.locator(".gm-mr.is-focused .gm-mr-star.is-on").waitFor();
    },
  },
  {
    name: "mail-mobile-gestures",
    viewport: MOBILE,
    touch: true,
    async run(t) {
      const { page } = t;
      await t.login();
      // 左滑删除 → 撤销
      const first = page.locator(".gm-mr").first();
      const subject = await first.locator(".gm-mr-subject").innerText();
      await swipe(page, first, -260);
      await page.locator(".gm-snackbar", { hasText: "已删除" }).waitFor();
      await page.locator(".gm-snackbar-btn", { hasText: "撤销" }).tap();
      await page.locator(".gm-mr", { hasText: subject }).first().waitFor();
      // 右滑：标为已读 / 未读
      await swipe(page, page.locator(".gm-mr").nth(1), 260);
      await page.waitForTimeout(500);
      // 长按进入多选
      await longPress(page, page.locator(".gm-mr").nth(2));
      await page.locator(".gm-ml-selcount", { hasText: "已选 1" }).waitFor();
      await page.locator(".gm-mr").nth(3).tap();
      await page.locator(".gm-ml-selcount", { hasText: "已选 2" }).waitFor();
      await t.shot("selection");
      await page.locator("button[title^='取消选择']").tap();
      // 阅读：底部操作栏
      await page.locator(".gm-mr").nth(4).tap();
      await page.waitForSelector(".gm-rd-dock");
      await page.waitForSelector(".gm-rd-thread");
      await t.shot("reader");
      // 收件箱邮件：底部是 归档 / 删除 / 延后，归档后自动打开下一封
      await page.locator(".gm-rd-dock-btn", { hasText: "归档" }).tap();
      await page.locator(".gm-snackbar", { hasText: "已归档" }).waitFor();
    },
  },
  {
    name: "mail-dark",
    dark: true,
    async run(t) {
      const { page } = t;
      await page.addInitScript(() => localStorage.setItem("gm:prefs:v1", JSON.stringify({ theme: "dark" })));
      await t.login();
      await openRow(page, "阿里云");
      await page.waitForTimeout(500);
      await t.shot("reader");
    },
  },

  // ---------------- 阶段 2：批量整理 ----------------
  {
    name: "triage-archive-spam",
    async run(t) {
      const { page } = t;
      await t.login();
      const total = async () => Number((await page.locator(".gm-ml-range").innerText()).split("/")[1]);
      const start = await total();
      // e 归档两封 → 撤销 → 再归档并等待提交
      await page.keyboard.press("j");
      await page.keyboard.press("x");
      await page.keyboard.press("j");
      await page.keyboard.press("x");
      await page.keyboard.press("e");
      await page.locator(".gm-snackbar", { hasText: "已归档 2 封" }).waitFor();
      await page.locator(".gm-snackbar-btn", { hasText: "撤销" }).click();
      await page.waitForFunction(() => document.querySelectorAll(".gm-mr").length === 25);
      await page.locator(".gm-mr").nth(0).locator(".gm-mr-lead").click();
      await page.locator(".gm-mr").nth(1).locator(".gm-mr-lead").click();
      await page.locator(".gm-ml-bulk button[title^='归档']").click();
      await page.waitForTimeout(5600);
      await page.waitForFunction((n) => Number(document.querySelector(".gm-ml-range")?.textContent.split("/")[1]) === n, start - 2, { timeout: 8000 });
      // 右键 → 举报垃圾邮件
      await page.locator(".gm-mr").nth(2).click({ button: "right" });
      await page.locator(".n-dropdown-option", { hasText: "举报垃圾邮件" }).click();
      await page.locator(".gm-snackbar", { hasText: "举报为垃圾邮件" }).waitFor();
      await t.shot("spam");
    },
  },
  {
    name: "triage-bulk-labels",
    async run(t) {
      const { page, data, base } = t;
      await t.login();
      await page.goto(`${base}/messages/?kind=inbox&account_id=${data.main}`);
      await page.waitForSelector(".gm-mr");
      const plain = page.locator(".gm-mr", { hasText: "测试邮件 8：" }).first();
      const labeled = page.locator(".gm-mr", { hasText: "测试邮件 4：" }).first();
      await plain.locator(".gm-mr-lead").click();
      await labeled.locator(".gm-mr-lead").click();
      await page.keyboard.press("l");
      await page.locator(".gm-blp").waitFor();
      // 「项目」只有其中一封有：半选；点一下变为全部加上
      const item = page.locator(".gm-blp-item", { hasText: "项目" });
      t.expect(((await item.getAttribute("class")) || "").includes("indeterminate"), "部分邮件有的标签应显示为半选");
      await t.shot("picker");
      await item.click();
      await page.locator(".gm-blp-foot button", { hasText: "应用" }).click();
      await page.locator(".gm-snackbar", { hasText: "已更新 2 封邮件的标签" }).waitFor();
      await plain.locator(".gm-mr-chip.is-label", { hasText: "项目" }).waitFor();
    },
  },
  {
    name: "triage-prefetch",
    async run(t) {
      const { page } = t;
      await t.login();
      const req = page.waitForRequest((r) => /\/api\/messages\/\d+\?.*peek=1/.test(r.url()));
      await page.locator(".gm-mr").nth(5).hover();
      await req;
      // 打开时立即显示（不出现加载骨架）
      await page.locator(".gm-mr").nth(5).click();
      await page.waitForSelector(".gm-rd-thread", { timeout: 1500 });
      t.expect(!(await page.locator(".gm-rd-skeleton").count()), "预加载后打开不应显示加载骨架");
    },
  },

  // ---------------- 阶段 3：写邮件 ----------------
  {
    name: "compose-multi",
    async run(t) {
      const { page } = t;
      await t.login();
      const openNew = async () => {
        await page.locator(".gm-rail-compose").click();
        await page.locator(".gm-cw:not(.is-min) .gm-cw-subject").waitFor();
      };
      await openNew();
      await page.locator(".gm-cw:not(.is-min) .gm-cw-subject").fill("第一封");
      await openNew();
      await page.locator(".gm-cw:not(.is-min) .gm-cw-subject").fill("第二封");
      t.expect((await page.locator(".gm-cw").count()) === 2, "应有两个写信窗口");
      t.expect((await page.locator(".gm-cw.is-min").count()) === 1, "打开新窗口时旧窗口应最小化");
      await openNew();
      await page.locator(".gm-cw:not(.is-min) .gm-cw-subject").fill("第三封");
      await t.shot("three");
      // 都有未保存内容时不能再开第四个
      await page.locator(".gm-rail-compose").click();
      await page.locator(".gm-snackbar", { hasText: "最多同时写 3 封" }).waitFor();
      // 点最小化的标签恢复
      await page.locator(".gm-cw.is-min .gm-cw-head", { hasText: "第一封" }).click();
      await page.locator(".gm-cw:not(.is-min) .gm-cw-title", { hasText: "第一封" }).waitFor();
      // 全屏 / 退出
      await page.locator(".gm-cw:not(.is-min) button[title='全屏']").click();
      await page.waitForSelector(".gm-cw.is-max");
      await page.locator(".gm-dock-backdrop").click({ position: { x: 5, y: 5 } });
      await page.waitForSelector(".gm-cw.is-max", { state: "detached" });
    },
  },
  {
    name: "compose-draft-attachments",
    async run(t) {
      const { page, base } = t;
      await t.login();
      await page.keyboard.press("c");
      const win = page.locator(".gm-cw").first();
      await win.locator(".gm-cw-subject").waitFor();
      await win.locator(".gm-cw-subject").fill("带附件的草稿");
      await win.locator("input[type=file]:not([accept])").setInputFiles({ name: "计划.txt", mimeType: "text/plain", buffer: Buffer.from("plan") });
      await win.locator(".gm-cw-att.is-done", { hasText: "计划.txt" }).waitFor({ timeout: 10000 });
      await t.shot("uploaded");
      await win.locator("button[title='保存草稿并关闭']").click();
      await page.waitForSelector(".gm-cw", { state: "detached" });
      await page.goto(`${base}/compose/drafts`);
      await page.getByText("带附件的草稿").first().click();
      await page.locator(".gm-cw .gm-cw-att", { hasText: "计划.txt" }).waitFor();
    },
  },
  {
    name: "compose-editor",
    async run(t) {
      const { page } = t;
      await t.login();
      await page.keyboard.press("c");
      const win = page.locator(".gm-cw").first();
      const editor = win.locator(".gm-editor");
      await editor.waitFor();
      await editor.click();
      await page.keyboard.type("你好，");
      // 粘贴图片：内嵌到正文
      await page.evaluate(async () => {
        const canvas = document.createElement("canvas");
        canvas.width = 40;
        canvas.height = 30;
        canvas.getContext("2d").fillRect(0, 0, 40, 30);
        const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
        const dt = new DataTransfer();
        dt.items.add(new File([blob], "shot.png", { type: "image/png" }));
        document.querySelector(".gm-cw .gm-editor").dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
      });
      await editor.locator("img[src^='data:image/']").waitFor();
      // 粘贴带格式的网页内容：去掉 class、脚本，保留粗体
      await page.evaluate(() => {
        const dt = new DataTransfer();
        dt.setData("text/html", '<p class="MsoNormal" style="mso-line-height:1"><b>加粗</b><script>alert(1)</scr' + 'ipt></p>');
        dt.setData("text/plain", "加粗");
        document.querySelector(".gm-cw .gm-editor").dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
      });
      const html = await editor.innerHTML();
      t.expect(html.includes("<b>加粗</b>") && !html.includes("MsoNormal") && !html.includes("script"), `粘贴清洗不正确：${html.slice(0, 200)}`);
      // Ctrl+K 插入链接（输入对话框）
      await page.keyboard.press("Control+k");
      await page.locator(".n-dialog input").fill("example.com");
      await page.locator(".n-dialog .n-button", { hasText: "插入" }).click();
      await editor.locator("a[href='https://example.com']").waitFor();
      // 格式工具栏
      await win.locator("button[title='格式工具栏']").click();
      await win.locator(".gm-format-bar button[title^='撤销']").waitFor();
      await t.shot("editor");
    },
  },
  {
    name: "compose-reply-quote",
    async run(t) {
      const { page } = t;
      await t.login();
      await openRow(page, "测试邮件 5：");
      await page.keyboard.press("r");
      const win = page.locator(".gm-cw").first();
      await win.locator(".gm-cw-title", { hasText: "Re:" }).waitFor();
      await win.locator(".gm-quote-toggle", { hasText: "显示引用内容" }).waitFor();
      await win.locator(".gm-quote-toggle").click();
      await win.locator(".gm-quote-toggle", { hasText: "隐藏引用内容" }).waitFor();
      await t.shot("reply");
    },
  },
  {
    name: "compose-send-error",
    allow: [/^400 \/api\/compose\/send/],   // 测试账号连不上 SMTP
    async run(t) {
      const { page } = t;
      await page.addInitScript(() => {
        // 没有撤销等待期，直接发送
        const orig = window.fetch;
        window.fetch = async (...args) => {
          const resp = await orig(...args);
          if (String(args[0]).includes("/api/session")) {
            const data = await resp.clone().json();
            if (data.config) data.config.undo_send_seconds = 0;
            return new Response(JSON.stringify(data), { status: resp.status, headers: resp.headers });
          }
          return resp;
        };
      });
      await t.login();
      await page.keyboard.press("c");
      const win = page.locator(".gm-cw").first();
      await win.locator(".gm-chip-input").first().fill("zhang@example.com");
      await win.locator(".gm-cw-subject").fill("会失败的邮件");
      await win.locator(".gm-cw-send button", { hasText: "发送" }).click();
      // 发送失败：窗口保留内容并显示错误
      await win.locator(".n-alert").waitFor({ timeout: 40000 });
      t.expect((await win.locator(".gm-cw-subject").inputValue()) === "会失败的邮件", "发送失败后内容应保留");
    },
  },
  {
    name: "compose-mobile",
    viewport: MOBILE,
    touch: true,
    async run(t) {
      const { page } = t;
      await t.login();
      await page.locator(".gm-tab.is-compose").tap();
      await page.locator(".gm-cw.is-mobile .gm-cw-subject").waitFor();
      t.expect(!(await page.locator(".gm-tabbar").count()), "写信时应隐藏标签栏");
      await t.shot("compose");
      await page.locator(".gm-cw.is-mobile button[title='保存草稿并关闭']").tap();
      await page.locator(".gm-tabbar").waitFor();
    },
  },

  // ---------------- 阶段 4：功能页 ----------------
  {
    name: "compose-address-input",
    async run(t) {
      const { page } = t;
      await t.login();
      await page.keyboard.press("c");
      const to = page.locator(".gm-cw input[aria-label='收件人']");
      await to.waitFor();
      // 粘贴多个地址（逗号、换行分隔）一次拆成多个地址块
      await to.evaluate((el) => {
        const dt = new DataTransfer();
        dt.setData("text/plain", "a@example.com, 小王 <b@example.com>\nc@example.com");
        el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
      });
      const chips = page.locator(".gm-cw .gm-addr-chip");
      await page.waitForFunction(() => document.querySelectorAll(".gm-cw .gm-addr-chip").length === 3);
      t.expect((await chips.nth(1).innerText()).includes("小王"), "带名称的地址应显示名称");
      // 输入框为空时退格：最后一个地址块放回输入框编辑
      await to.click();
      await page.keyboard.press("Backspace");
      await page.waitForFunction(() => document.querySelectorAll(".gm-cw .gm-addr-chip").length === 2);
      t.expect((await to.inputValue()) === "c@example.com", `退格后输入框应为最后一个地址：${await to.inputValue()}`);
      // 双击地址块编辑（输入框里正在编辑的地址先变回地址块）
      await chips.nth(1).dblclick();
      await page.waitForFunction(() => {
        const names = [...document.querySelectorAll(".gm-cw .gm-addr-chip")].map((c) => c.innerText);
        return names.length === 2 && !names.some((n) => n.includes("小王"));
      });
      t.expect((await to.inputValue()).includes("b@example.com"), "双击后应把该地址放回输入框");
      await t.shot("address");
    },
  },
  {
    name: "pages-error-retry",
    // 下面故意让接口返回 500
    allow: [/^500 \/api\/(drafts|scheduled|sent|files|cleanup)/],
    async run(t) {
      const { page, base } = t;
      await t.login();
      const cases = [
        ["/compose/drafts", "**/api/drafts", "草稿"],
        ["/compose/scheduled", "**/api/scheduled", "定时发送"],
        ["/compose/sent", "**/api/sent", "发件记录"],
        ["/files", "**/api/files**", "附件"],
        ["/cleanup", "**/api/cleanup/senders", "批量清理"],
      ];
      for (const [path, pattern, title] of cases) {
        await page.route(pattern, (r) => r.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"服务器出错了"}' }));
        await page.goto(`${base}${path}`);
        await page.locator(".gm-state.is-error", { hasText: `${title}加载失败` }).waitFor();
        await page.unroute(pattern);
        await page.locator(".gm-state.is-error button", { hasText: "重试" }).click();
        await page.locator(".gm-state.is-error").waitFor({ state: "detached" });
      }
      await t.shot("cleanup");
    },
  },
  {
    name: "pages-scheduled",
    async run(t) {
      const { page, base } = t;
      await t.login();
      await page.goto(`${base}/compose/scheduled`);
      const item = page.locator(".gm-lr", { hasText: "定时：生日祝福" });
      await item.waitFor();
      t.expect((await item.innerText()).includes("将于"), "定时发送应显示发送时间");
      await t.shot("list");
      await item.hover();
      await item.locator("button[title='取消定时并编辑']").click();
      await page.locator(".n-dialog .n-button", { hasText: "取消定时" }).click();
      await page.locator(".gm-cw .gm-cw-subject").waitFor();
      t.expect((await page.locator(".gm-cw .gm-cw-subject").inputValue()).includes("生日祝福"), "取消定时后应打开草稿继续编辑");
    },
  },
  {
    name: "pages-mobile-actions",
    viewport: MOBILE,
    touch: true,
    async run(t) {
      const { page, base } = t;
      await t.login();
      await page.goto(`${base}/compose/drafts`);
      const row = page.locator(".gm-lr", { hasText: "下周计划" });
      await row.waitFor();
      // 触屏上操作总是可见（「⋯」菜单），不依赖悬停
      await row.locator("button[title='操作']").tap();
      await t.shot("menu");
      await page.locator(".n-dropdown-option", { hasText: "舍弃草稿" }).tap();
      await page.locator(".n-dialog .n-button", { hasText: "舍弃" }).tap();
      await row.waitFor({ state: "detached" });
    },
  },
  {
    name: "settings-sections",
    async run(t) {
      const { page, base } = t;
      await t.login();
      // OAuth 授权回调回到旧地址 /accounts/?... 时保留参数跳到新地址
      await page.goto(`${base}/accounts/?from=oauth`);
      await page.waitForURL(/\/settings\/accounts\?from=oauth/);
      // 电脑端 /settings 直接显示第一个分区
      await page.goto(`${base}/settings`);
      await page.waitForURL(/\/settings\/accounts$/);
      for (const [key, word] of SETTINGS_SECTIONS) {
        await page.locator(".gm-st-item", { hasText: { accounts: "账号", signatures: "签名", groups: "分组", rules: "自动规则", templates: "模板", senders: "屏蔽与信任", appearance: "外观与阅读", notifications: "通知", shortcuts: "快捷键", security: "安全", backup: "备份与证书", audit: "审计日志" }[key] }).first().click();
        await page.waitForURL(new RegExp(`/settings/${key}$`));
        await page.locator(".gm-st-content", { hasText: word }).waitFor({ timeout: 8000 });
        t.expect(!(await page.locator(".gm-st-content .gm-state.is-error").count()), `${key} 显示了错误状态`);
        t.expect(await page.locator(`.gm-st-item.is-active[href="/settings/${key}"]`).count() === 1, `${key} 导航没有高亮`);
      }
      await t.shot("audit");
      await page.goto(`${base}/settings/accounts/add`);
      await page.locator(".gm-st-content", { hasText: "使用 Google 账号授权添加" }).waitFor();
      t.expect(await page.locator('.gm-st-item.is-active[href="/settings/accounts"]').count() === 1, "添加账号页应高亮「账号」");
      await t.shot("add-account");
    },
  },
  {
    name: "settings-appearance",
    async run(t) {
      const { page, base } = t;
      await t.login();
      await page.goto(`${base}/settings/appearance`);
      await page.locator(".gm-ap-theme", { hasText: "深色" }).click();
      await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
      await page.locator(".n-radio", { hasText: "紧凑" }).click();
      await page.waitForFunction(() => document.documentElement.dataset.density === "compact");
      await t.shot("dark");
      await page.reload();
      await page.waitForFunction(() => document.documentElement.dataset.theme === "dark" && document.documentElement.dataset.density === "compact");
      await page.locator(".gm-ap-theme", { hasText: "浅色" }).click();
      await page.locator(".n-radio", { hasText: "舒适" }).click();
      await page.waitForFunction(() => document.documentElement.dataset.density !== "compact");
    },
  },
  {
    name: "settings-groups-signature",
    async run(t) {
      const { page, base } = t;
      await t.login();
      await page.goto(`${base}/settings/groups`);
      const group = page.locator(".gm-gp-row", { hasText: "注册用" });
      await group.locator("button", { hasText: "重命名" }).click();
      const input = page.locator(".n-dialog input");
      await input.fill("注册账号");
      await page.locator(".n-dialog .n-button", { hasText: "保存" }).click();
      await page.locator(".gm-snackbar", { hasText: "已重命名为「注册账号」" }).waitFor();
      t.expect(!(await page.locator(".gm-fp").count()), "设置页不应显示文件夹面板");
      await page.goto(`${base}/messages/`);
      await page.locator(".gm-fp-group", { hasText: "注册账号" }).first().waitFor();
      await page.goto(`${base}/settings/groups`);
      await page.locator(".gm-gp-row", { hasText: "注册账号" }).locator("button", { hasText: "重命名" }).click();
      await page.locator(".n-dialog input").fill("注册用");
      await page.locator(".n-dialog .n-button", { hasText: "保存" }).click();
      await page.locator(".gm-gp-row", { hasText: "注册用" }).waitFor();

      await page.goto(`${base}/settings/signatures`);
      const editor = page.locator(".gm-sg-editor [contenteditable=true]").first();
      await editor.click();
      await page.keyboard.type("张三 · 冒烟测试签名");
      await page.locator("button", { hasText: "保存签名" }).click();
      await page.locator(".gm-snackbar", { hasText: "签名已保存" }).waitFor();
      await page.reload();
      await page.locator(".gm-sg-editor", { hasText: "冒烟测试签名" }).waitFor();
      await page.locator("button", { hasText: "清除签名" }).click();
      await page.locator(".gm-snackbar", { hasText: "已清除签名" }).waitFor();
    },
  },
  {
    name: "settings-telegram",
    allow: [/^400 \/api\/telegram\/settings/],   // 故意填错令牌格式
    async run(t) {
      const { page, base } = t;
      await t.login();
      await page.goto(`${base}/settings/notifications`);
      await page.locator(".gm-nt-status", { hasText: "未配置" }).waitFor();
      const save = page.locator(".gm-nt-form button", { hasText: "保存" });
      t.expect(await save.isDisabled(), "没有修改时「保存」应不可用");
      // 格式不对时提示
      await page.locator(".gm-nt-form input[type=password]").fill("bad-token");
      await save.click();
      await page.locator(".gm-snackbar", { hasText: "BotFather" }).waitFor();
      // 保存后立即生效（推送中），令牌只显示末 4 位
      await page.locator(".gm-nt-form input[type=password]").fill("123456789:AAHabcdefghijklmnopqrstuvwxyz012345");
      await page.locator(".gm-nt-form input[placeholder^='接收通知']").fill("111, -100222");
      // 冒烟环境的 .env 写了 TELEGRAM_NOTIFY_ENABLED=false：在网页上打开
      await page.locator(".gm-nt-switch", { hasText: "推送新邮件" }).locator(".n-switch").click();
      await save.click();
      await page.locator(".gm-snackbar", { hasText: "Telegram 设置已保存" }).waitFor();
      await page.locator(".gm-nt-status", { hasText: "推送中" }).waitFor();
      await page.waitForTimeout(300);
      t.expect(await save.isDisabled(), "保存后表单应回到无修改状态");
      t.expect((await page.locator(".gm-nt-form input[type=password]").getAttribute("placeholder")).includes("123456789:…2345"), "令牌应只显示末 4 位");
      await t.shot("saved");
      // 恢复为 .env
      await page.locator(".gm-nt-form button", { hasText: "恢复为 .env 配置" }).click();
      await page.locator(".n-dialog .n-button", { hasText: "恢复" }).click();
      await page.locator(".gm-nt-status", { hasText: "未配置" }).waitFor();
    },
  },
  {
    name: "settings-error-retry",
    // 下面故意让接口返回 500
    allow: [/^500 \/api\/(rules|security|senders)/],
    async run(t) {
      const { page, base } = t;
      await t.login();
      for (const [path, pattern] of [["/settings/rules", "**/api/rules"], ["/settings/security", "**/api/security"], ["/settings/senders", "**/api/senders**"]]) {
        await page.route(pattern, (r) => r.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"服务器出错了"}' }));
        await page.goto(`${base}${path}`);
        await page.locator(".gm-st-content .gm-state.is-error").waitFor();
        await page.unroute(pattern);
        await page.locator(".gm-st-content .gm-state.is-error button", { hasText: "重试" }).click();
        await page.locator(".gm-st-content .gm-state.is-error").waitFor({ state: "detached" });
      }
    },
  },
  {
    name: "settings-mobile",
    viewport: MOBILE,
    touch: true,
    async run(t) {
      const { page, base } = t;
      await t.login();
      await page.goto(`${base}/settings`);
      await page.locator(".gm-st-item", { hasText: "两步验证" }).waitFor();
      t.expect(!(await page.locator(".gm-st-main").count()), "手机上 /settings 应只显示分区列表");
      await t.shot("list");
      await page.locator(".gm-st-item", { hasText: "外观与阅读" }).tap();
      await page.waitForURL(/\/settings\/appearance$/);
      await page.locator(".gm-st-back", { hasText: "外观与阅读" }).waitFor();
      t.expect(!(await page.locator(".gm-st-nav").count()), "手机上进入分区后应隐藏分区列表");
      await t.shot("detail");
      await page.locator(".gm-st-back button").tap();
      await page.waitForURL(/\/settings$/);
      await page.locator(".gm-st-nav").waitFor();
    },
  },
];
