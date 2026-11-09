# 界面功能清单（每个阶段全量回归）

自动化步骤在 `steps.mjs`（`node run.mjs` 运行）；带 ☐ 的暂为人工检查，后续阶段补自动化。

## 外壳（阶段 0）
- [x] 图标栏：写邮件、邮件（未读角标）、账号切换、草稿 / 定时发送（计数）、附件、批量清理、实时状态、命令面板、设置、个人菜单 — `shell-desktop`
- [x] 文件夹面板：文件夹、标签、账号（服务商分区 + 自定义分组 + 未读数）、更多页面；分组折叠记住 — `shell-desktop`
- [x] 面板拖动调宽、`[` 收起 / 展开，刷新后保持 — `shell-panel-resize`
- [x] 账号切换器：`'` 打开、搜索、回车切换、最近使用置顶；`Alt+0` 全部账号、`Alt+1…9` — `shell-account-switch`
- [x] 命令面板 `Ctrl+K`：跳转文件夹、切换主题、打开设置 — `shell-palette`
- [x] 窄窗口（<1280）文件夹面板为浮层，导航后自动关闭 — `shell-narrow`
- [x] 深色模式 — `shell-dark`
- [x] 手机端：底部标签栏、文件夹面板、「我的」、账号切换器全屏、阅读时隐藏标签栏 — `shell-mobile`
- [x] 写邮件窗口按需加载 — `shell-compose`
- [x] 旧地址全部可用：草稿、定时发送、发件记录、附件、批量清理、账号、规则、模板、安全、审计、屏蔽 — `shell-old-pages`
- ☐ 个人菜单：外观、桌面通知开关、同步所有账号、快捷键、退出登录
- ☐ 打印只输出邮件内容（不含图标栏、面板、搜索栏）

## 列表 / 阅读（阶段 1）
- [x] 分栏阅读、`u` / Esc 返回；未打开邮件时右侧显示提示 — `shell-mail-split`、`mail-reader`
- [x] 钓鱼提示、验证码横幅与复制、外部图片提示（服务器拦截时）、会话全部展开、j / k 上下封（加载中连按不跳错） — `mail-reader`
- [x] 星标 `s` 同步列表、标签 `l`、标为未读不离开阅读 — `mail-reader`
- [x] x / Shift 区间选择、批量删除与撤销、删除 5 秒后提交 — `mail-bulk-undo`
- [x] 右键菜单、悬停操作、延后到预设时间并出现在「已延后」 — `mail-context-snooze`
- [x] 仅未读、会话视图开关、全部标为已读 — `mail-filters`
- [x] 关闭阅读窗格（宽列表、整页阅读、返回恢复滚动位置） — `mail-pane-off`
- [x] 已删除：说明条、移回收件箱、永久删除需确认 — `mail-trash`
- [x] 发件记录详情 — `mail-sent-record`
- [x] 命令面板对选中邮件执行操作 — `mail-palette-actions`
- [x] 手机：左滑删除与撤销、右滑已读 / 未读、长按多选、阅读底部操作栏 — `mail-mobile-gestures`
- [x] 深色模式阅读 — `mail-dark`
- ☐ 屏蔽发件人（进度卡片）、不是垃圾邮件、清空已删除；新邮件提示；服务器搜索；每页封数；下拉刷新；Ctrl+点击新标签页

## 批量整理（阶段 2）
- [x] 归档 `e`（Gmail 移到所有邮件、Outlook 移到存档）与撤销、举报垃圾邮件 `!` — `triage-archive-spam`（后端：`tests/test_bulk_actions.py`）
- [x] 跨账号批量标签（全部 / 部分 / 没有三种状态） — `triage-bulk-labels`
- [x] 悬停预加载（不标已读），打开时立即显示 — `triage-prefetch`
- [x] 手机阅读底部：归档 / 删除 / 延后 — `mail-mobile-gestures`

## 写邮件（阶段 3）
- [x] 多个写邮件窗口停靠在右下角（最多 3 个），打开新窗口时其余最小化，超过上限提示 — `compose-multi`
- [x] 草稿附件：上传进度、关闭后从草稿重新打开仍在、删除附件 — `compose-draft-attachments`（后端：`tests/test_compose_upgrade.py`）
- [x] 编辑器：格式工具栏、选中浮动工具条、`Ctrl+K` 插入链接、粘贴清洗、`Ctrl+Shift+V` 纯文本、粘贴图片内嵌 — `compose-editor`
- [x] 回复引用默认折叠、可展开 — `compose-reply-quote`
- [x] 发送失败提示并保留窗口 — `compose-send-error`
- [x] 手机全屏写邮件、隐藏标签栏 — `compose-mobile`
- [x] 地址输入：粘贴多个地址、退格 / 双击编辑 — `compose-address-input`
- ☐ 切换发件账号时替换签名；插入 / 保存模板；25MB 提示；撤销发送；定时发送；纯文本；发件人姓名提示

## 功能页（阶段 4）
- [x] 草稿、定时发送、发件记录、附件、批量清理加载失败时显示错误和重试 — `pages-error-retry`
- [x] 定时发送显示发送时间、取消定时后打开草稿继续编辑 — `pages-scheduled`
- [x] 手机上列表行操作总是可见（「⋯」菜单），不依赖悬停 — `pages-mobile-actions`
- ☐ 批量清理进度卡；附件按类型筛选与下载

## 设置（阶段 5）
- [x] 旧设置地址跳到 `/settings/...`（OAuth 回调 `/accounts/?...` 保留参数）；电脑端 `/settings` 打开第一个分区；12 个分区都能打开且导航高亮；添加账号页 — `settings-sections`、`shell-old-pages`
- [x] 外观：主题、密度即时生效并在刷新后保持 — `settings-appearance`
- [x] 分组重命名同步到文件夹面板；签名保存 / 清除 — `settings-groups-signature`
- [x] 规则、安全、屏蔽与信任加载失败可重试 — `settings-error-retry`
- [x] 手机：分区列表 → 详情（顶部返回） — `settings-mobile`
- [x] 设置页不显示文件夹面板和邮件搜索 — `settings-groups-signature`
- ☐ 两步验证开启 / 关闭、设备登出、IP 拉黑 / 解除、立即备份、证书检测 / 续订、规则与模板编辑、通知（Telegram、各账号开关）

## 打磨（阶段 6）
- ☐ 键盘 Tab 时焦点框清晰；系统开启「减少动态效果」时没有过渡动画
- ☐ Outlook 账号的界面文案不出现 Gmail 专属说法
