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
  ["/accounts/", "账号"],
  ["/accounts/rules", "规则"],
  ["/accounts/templates", "模板"],
  ["/accounts/security", "两步验证"],
  ["/accounts/security/audit", "审计"],
  ["/messages/blocked-senders", "屏蔽"],
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
      await page.waitForURL(/security\/audit/);
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
      for (const [path, word] of PAGES) {
        await page.goto(`${base}${path}`);
        await page.waitForLoadState("networkidle");
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
      await page.waitForSelector(".gm-compose", { timeout: 10000 });
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
      await target.hover();
      await target.locator(".gm-mr-hover-btn[aria-label^='延后']").click();
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
      await page.locator(".gm-rd-dock-btn", { hasText: "星标" }).tap();
      await page.locator(".gm-rd-dock-btn.is-on").waitFor();
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
];
