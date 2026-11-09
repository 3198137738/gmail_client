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
];
