// 界面冒烟测试：建临时数据目录 → 写入种子数据 → 启动本地服务 → 用本机 Chrome 逐项检查 → 清理。
//
// 用法（项目根目录）：
//   cd tools/ui-smoke && npm install && node run.mjs            全部步骤
//   node run.mjs shell mobile                                    只跑名称包含这些关键字的步骤
// 环境变量：CHROME_PATH（Chrome 路径）、PYTHON（Python 解释器，默认项目 .venv）、KEEP=1（结束后保留服务和数据）
//
// 任一步骤出错、页面有 JS 错误或 console.error、接口返回 5xx（或未预期的 4xx）即判为失败；截图保存在 out/。
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { steps } from "./steps.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const OUT = join(HERE, "out");
const PORT = Number(process.env.PORT || 8765);
const BASE = `http://127.0.0.1:${PORT}`;
const WIN = process.platform === "win32";
const PYTHON = process.env.PYTHON || join(ROOT, ".venv", WIN ? "Scripts/python.exe" : "bin/python");
const CHROME = process.env.CHROME_PATH || (WIN
  ? "C:/Program Files/Google/Chrome/Application/chrome.exe"
  : process.platform === "darwin"
    ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    : "/usr/bin/google-chrome");

const filters = process.argv.slice(2);
const DATA = mkdtempSync(join(tmpdir(), "gm-smoke-"));
rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

// 测试服务器：独立数据目录、固定密码；清空 Telegram / 备份等对外配置，避免向真实账号发通知
const serverEnv = {
  ...process.env,
  APP_DATA_ROOT: DATA,
  WEB_AUTH_PASSWORD: "test-pass",
  SESSION_COOKIE_SECURE: "false",
  TELEGRAM_BOT_TOKEN: "",
  TELEGRAM_CHAT_ID: "",
  TELEGRAM_NOTIFY_ENABLED: "false",
  TELEGRAM_BOT_COMMANDS_ENABLED: "false",
  BACKUP_ENABLED: "false",
  AUDIT_ALERT_NEW_IP: "false",
  REALTIME_ENABLED: process.env.REALTIME_ENABLED || "true",
  WEB_AUTH_MAX_FAILURES: "50",
  PYTHONIOENCODING: "utf-8",
};

function log(...args) {
  console.log(...args);
}

function seed() {
  const res = spawnSync(PYTHON, [join(HERE, "seed.py")], { cwd: ROOT, env: serverEnv, encoding: "utf-8" });
  if (res.status !== 0) throw new Error(`seed.py 失败：\n${res.stderr}`);
  const line = res.stdout.trim().split("\n").pop();
  return JSON.parse(line);
}

async function startServer() {
  // KEEP=1 时服务独立于本进程运行（脱离并且不接输出管道），测试结束后仍可手动访问
  const keep = process.env.KEEP === "1";
  const proc = spawn(PYTHON, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(PORT), "--log-level", "warning"], {
    cwd: ROOT,
    env: serverEnv,
    stdio: keep ? "ignore" : ["ignore", "pipe", "pipe"],
    detached: keep,
  });
  if (keep) proc.unref();
  let output = "";
  proc.stdout?.on("data", (d) => (output += d));
  proc.stderr?.on("data", (d) => (output += d));
  for (let i = 0; i < 80; i++) {
    try {
      const r = await fetch(`${BASE}/healthz`);
      if (r.ok) return proc;
    } catch {
      /* 还没启动 */
    }
    if (proc.exitCode !== null) break;
    await new Promise((r) => setTimeout(r, 250));
  }
  proc.kill();
  throw new Error(`服务启动失败：\n${output.slice(-3000)}`);
}

// 每个页面收集的问题：JS 异常、console.error、意外的接口错误
function watchPage(page, problems, allowed) {
  page.on("pageerror", (err) => problems.push(`页面异常：${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error" && !/Failed to load resource/.test(msg.text())) problems.push(`console.error：${msg.text()}`);
  });
  page.on("response", (res) => {
    const url = res.url();
    const status = res.status();
    if (!url.startsWith(BASE) || status < 400) return;
    const path = url.slice(BASE.length);
    if (allowed.some((re) => re.test(`${status} ${path}`))) return;
    problems.push(`接口 ${status}：${path}`);
  });
}

async function main() {
  if (!existsSync(CHROME)) throw new Error(`找不到 Chrome：${CHROME}（用 CHROME_PATH 指定）`);
  const data = seed();
  log(`种子数据：${data.accounts.length} 个账号，数据目录 ${DATA}`);
  const server = await startServer();
  const browser = await chromium.launch({ executablePath: CHROME, headless: process.env.HEADED !== "1" });
  const results = [];
  try {
    const selected = steps.filter((s) => !filters.length || filters.some((f) => s.name.includes(f)));
    for (const step of selected) {
      const problems = [];
      // 预先加载的邮件可能已被删除：404 不影响使用
      const allowed = [/^401 \/api\/session/, /^404 \/api\/messages\/\d+\?.*peek=1/, ...(step.allow || [])];
      const context = await browser.newContext({
        viewport: step.viewport || { width: 1440, height: 900 },
        deviceScaleFactor: 1,
        hasTouch: !!step.touch,
        isMobile: !!step.touch,
        colorScheme: step.dark ? "dark" : "light",
        locale: "zh-CN",
      });
      const page = await context.newPage();
      watchPage(page, problems, allowed);
      const t = {
        page,
        context,
        base: BASE,
        data,
        shot: (name) => page.screenshot({ path: join(OUT, `${step.name}-${name}.png`) }),
        expect(cond, message) {
          if (!cond) throw new Error(message);
        },
        async login() {
          await page.goto(`${BASE}/login`);
          await page.fill("input[type=password]", "test-pass");
          await page.keyboard.press("Enter");
          await page.waitForURL(/\/messages\//);
          await page.waitForSelector(".gm-row", { timeout: 15000 });
        },
      };
      const started = Date.now();
      try {
        await step.run(t);
        await page.waitForTimeout(150);   // 收集最后的异步错误
      } catch (err) {
        // 第一行是错误类型；Playwright 调用日志里的「waiting for …」说明卡在哪个元素
        const lines = err.message.split("\n");
        const waiting = lines.find((l) => /waiting for/.test(l));
        // eslint-disable-next-line no-control-regex
        const clean = (text) => text.replace(/\x1b\[[0-9;]*m/g, "").trim();
        problems.unshift(`步骤失败：${lines[0]}${waiting ? ` ← ${clean(waiting)}` : ""}`);
        await t.shot("failure").catch(() => {});
      }
      await context.close();
      results.push({ name: step.name, ms: Date.now() - started, problems });
      log(`${problems.length ? "✗" : "✓"} ${step.name} (${Date.now() - started}ms)`);
      for (const p of problems) log(`    ${p}`);
    }
  } finally {
    await browser.close();
    if (process.env.KEEP === "1") {
      log(`KEEP=1：服务仍在 ${BASE} 运行（pid ${server.pid}），数据目录 ${DATA}`);
    } else {
      server.kill();
      await new Promise((r) => setTimeout(r, 500));
      rmSync(DATA, { recursive: true, force: true });
    }
  }
  const failed = results.filter((r) => r.problems.length);
  log(`\n${results.length - failed.length}/${results.length} 通过；截图在 ${OUT}`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(2);
});
