# 前端（Vue 3 + Naive UI）

单页应用，构建产物输出到 `app/static/dist/`（不入库），由 FastAPI 提供：除 `/api/*` 等接口外，
所有页面地址（`/messages/`、`/accounts/` 等）都返回 `index.html`，由前端路由显示对应页面。

## 开发

```bash
cd frontend
npm ci                  # 按 package-lock.json 安装依赖
npm run dev             # 开发服务器 http://localhost:5173/，接口转发到 127.0.0.1:8000 的后端
npm run build           # 构建到 app/static/dist，之后直接访问后端即可
```

后端单独启动：`python run.py`（或 `uvicorn app.main:app`）。

## 部署

`docker compose build` 时在镜像的 Node 构建阶段执行 `npm ci --ignore-scripts && npm run build`，
服务器上不需要安装 Node。修改依赖后用 `npm install` 更新 `package-lock.json` 并提交。

## 目录

| 路径 | 内容 |
|---|---|
| `src/api.js` | 接口封装：CSRF 请求头、401 跳转登录、token 过期自动重试 |
| `src/store.js` | 全局状态：登录会话、左侧导航、写邮件窗口 |
| `src/realtime.js` | 实时推送（SSE `/events/stream`） |
| `src/views/` | 页面：登录、邮件列表、邮件详情、草稿、发件记录、设置 |
| `src/components/` | 顶栏、侧栏、写邮件窗口、正文 iframe、附件卡片等 |
| `src/composables/shortcuts.js` | 键盘快捷键（与 Gmail 一致，按 `?` 查看） |

图标使用 `@vicons/material`（Material Icons 的 SVG 组件，按需打包）。
