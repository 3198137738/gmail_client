// 全站公共脚本（CSP 禁止内联脚本和 onclick/onsubmit，交互统一在这里绑定）。

// 提交前确认：表单或提交按钮上的 data-confirm="提示文字"（按钮优先）。
// 必须先于下面的「处理中」逻辑注册，取消时阻止后续监听器。
(function () {
    document.addEventListener("submit", function (ev) {
        var form = ev.target;
        var submitter = ev.submitter;
        var message = (submitter && submitter.getAttribute("data-confirm"))
            || form.getAttribute("data-confirm");
        if (message && !window.confirm(message)) {
            ev.preventDefault();
            ev.stopImmediatePropagation();
        }
    }, true);
})();

// 账号页等需要定时刷新的页面：<div data-auto-reload="毫秒"></div>
(function () {
    var el = document.querySelector("[data-auto-reload]");
    if (!el) return;
    var ms = parseInt(el.getAttribute("data-auto-reload"), 10);
    if (ms > 0) setTimeout(function reload() {
        // 写邮件窗口打开时推迟刷新，避免丢失正在编写的内容
        if (window.GM && window.GM.composeOpen && window.GM.composeOpen()) { setTimeout(reload, ms); return; }
        window.location.reload();
    }, ms);
})();

// 提交时按钮显示「处理中…」，防止重复提交
(function () {
    document.addEventListener("submit", function (ev) {
        var form = ev.target;
        if (ev.defaultPrevented) return;
        if (form.hasAttribute("data-no-loading")) return;
        var btn = form.querySelector('button[type="submit"], input[type="submit"]');
        if (!btn || btn.disabled) return;
        // Icon-only / small inline buttons opt out of the loading takeover
        if (btn.dataset.noLoading !== undefined
            || btn.classList.contains("gm-icon-btn")
            || btn.classList.contains("dropdown-item")
            || btn.classList.contains("gm-link-btn")
            || btn.classList.contains("gc-icon-btn")) {
            return;
        }
        var original = btn.innerHTML;
        btn.dataset.originalLabel = original;
        btn.disabled = true;
        btn.innerHTML = '<span class="gm-spinner" role="status" aria-hidden="true"></span> 处理中…';
        setTimeout(function () {
            if (btn.disabled && btn.innerHTML.indexOf("处理中") !== -1) {
                btn.disabled = false;
                btn.innerHTML = original;
            }
        }, 8000);
    }, true);
})();

// 左上角菜单按钮（与 Gmail 一致）：
//   电脑端：折叠 / 展开左侧导航（折叠后只显示图标，悬停时临时展开），状态记在 localStorage；
//   手机端：从左侧滑出导航抽屉。
(function () {
    var toggle = document.querySelector(".gm-menu-btn");
    var nav = document.getElementById("gm-nav");
    var backdrop = document.querySelector(".gm-drawer-backdrop");
    if (!toggle) return;
    if (!nav) { toggle.hidden = true; return; }
    var COLLAPSE_KEY = "gm:nav-collapsed";
    var mobile = window.matchMedia("(max-width: 768px)");

    function setCollapsed(collapsed) {
        document.body.classList.toggle("gm-nav-collapsed", collapsed);
        toggle.setAttribute("aria-expanded", String(!collapsed));
        try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0"); } catch (e) {}
    }
    function setDrawer(open) {
        document.body.classList.toggle("gm-drawer-open", open);
        toggle.setAttribute("aria-expanded", String(open));
    }
    try {
        if (localStorage.getItem(COLLAPSE_KEY) === "1") {
            document.body.classList.add("gm-nav-collapsed");
            toggle.setAttribute("aria-expanded", "false");
        }
    } catch (e) {}

    toggle.addEventListener("click", function () {
        if (mobile.matches) {
            setDrawer(!document.body.classList.contains("gm-drawer-open"));
        } else {
            setCollapsed(!document.body.classList.contains("gm-nav-collapsed"));
        }
    });
    if (backdrop) backdrop.addEventListener("click", function () { setDrawer(false); });
    document.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape") setDrawer(false);
    });
    // 点了抽屉里的链接后自动收起（页面跳转前的视觉反馈）
    document.addEventListener("click", function (ev) {
        if (ev.target.closest && ev.target.closest(".gm-nav a")) setDrawer(false);
    });
})();

// 打印（详情页「全部打印」）
document.addEventListener("click", function (ev) {
    if (ev.target.closest && ev.target.closest("[data-print]")) window.print();
});

// 底部提示（Gmail snackbar）：window.GM.toast("邮件已发送", {href, linkText})
// 也可以带一个按钮：window.GM.toast("已删除", {actionText: "撤销", onAction: fn})
(function () {
    var el = document.getElementById("gm-snackbar");
    var timer = null;
    window.GM = window.GM || {};
    function hide() {
        clearTimeout(timer);
        if (el) el.hidden = true;
    }
    window.GM.toast = function (text, opts) {
        if (!el) return;
        opts = opts || {};
        el.textContent = "";
        var span = document.createElement("span");
        span.textContent = text;
        el.appendChild(span);
        if (opts.href) {
            var link = document.createElement("a");
            link.href = opts.href;
            link.textContent = opts.linkText || "查看";
            el.appendChild(link);
        }
        if (opts.actionText) {
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "gm-snackbar-action";
            btn.textContent = opts.actionText;
            btn.addEventListener("click", function () {
                hide();
                if (opts.onAction) opts.onAction();
            });
            el.appendChild(btn);
        }
        el.hidden = false;
        clearTimeout(timer);
        timer = setTimeout(hide, opts.duration || 6000);
    };
    window.GM.toastHide = hide;
})();

// 邮件正文 iframe 高度自适应（iframe 带 allow-same-origin，父页面可以读取其文档高度；
// 正文内部不允许执行脚本）。
(function () {
    function fit(frame) {
        try {
            var doc = frame.contentDocument;
            if (!doc || !doc.documentElement) return;
            frame.style.height = doc.documentElement.scrollHeight + "px";
        } catch (err) {}
    }
    document.querySelectorAll("iframe.gm-body-frame").forEach(function (frame) {
        function onLoad() {
            fit(frame);
            try {
                // 图片加载完、窗口宽度变化时内容高度会变
                new ResizeObserver(function () { fit(frame); })
                    .observe(frame.contentDocument.documentElement);
            } catch (err) {}
        }
        frame.addEventListener("load", onLoad);
        if (frame.contentDocument && frame.contentDocument.readyState === "complete"
            && frame.contentDocument.URL !== "about:blank") {
            onLoad();
        }
    });
    window.addEventListener("resize", function () {
        document.querySelectorAll("iframe.gm-body-frame").forEach(fit);
    });
})();

// 写邮件悬浮窗（Gmail 式）：电脑端点击「写邮件 / 回复 / 转发」（带 data-compose 的链接）时，
// 在右下角打开悬浮窗口而不是跳转页面，可最小化、全屏、关闭；发送成功后提示「邮件已发送」。
// 手机端或窄窗口仍然跳转到整页写邮件。
(function () {
    var wide = window.matchMedia("(min-width: 900px)");
    var win = null;
    var frame = null;
    var backdrop = null;
    var state = "normal";

    function setState(next) {
        state = next;
        if (win) win.setAttribute("data-state", next);
        if (backdrop) backdrop.hidden = next !== "max";
        if (frame && frame.contentWindow) {
            frame.contentWindow.postMessage({ type: "gm:compose:state", state: next }, window.location.origin);
        }
    }

    function isDirty() {
        try {
            var api = frame && frame.contentWindow && frame.contentWindow.GCCompose;
            return !!(api && api.isDirty());
        } catch (e) { return false; }
    }

    function close() {
        if (win) win.remove();
        if (backdrop) backdrop.remove();
        win = frame = backdrop = null;
    }

    function open(href) {
        if (win && state === "sending") return;   // 上一封还在撤销等待期，先不打开新窗口
        if (win) {
            // 只保留一个写邮件窗口：已有未发送的内容时恢复并聚焦它，否则直接替换
            if (isDirty()) {
                if (state === "min") setState("normal");
                if (frame) frame.focus();
                return;
            }
            close();
        }
        var url = new URL(href, window.location.origin);
        url.searchParams.set("embed", "1");
        backdrop = document.createElement("div");
        backdrop.className = "gm-compose-backdrop";
        backdrop.hidden = true;
        backdrop.addEventListener("click", function () { setState("normal"); });
        win = document.createElement("div");
        win.className = "gm-compose-win";
        win.setAttribute("role", "dialog");
        win.setAttribute("aria-label", "写邮件");
        frame = document.createElement("iframe");
        frame.className = "gm-compose-frame";
        frame.title = "写邮件";
        frame.src = url.pathname + url.search;
        frame.addEventListener("load", function () { if (state !== "sending") setState(state); });
        win.appendChild(frame);
        document.body.appendChild(backdrop);
        document.body.appendChild(win);
        setState("normal");
    }

    document.addEventListener("click", function (ev) {
        var link = ev.target.closest && ev.target.closest("a[data-compose]");
        if (!link || !wide.matches) return;
        if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button !== 0) return;  // 新标签页打开时保持默认
        ev.preventDefault();
        open(link.getAttribute("href"));
    });

    window.addEventListener("message", function (ev) {
        if (ev.origin !== window.location.origin || !ev.data || !frame) return;
        if (ev.source !== frame.contentWindow) return;
        switch (ev.data.type) {
            case "gm:compose:minimize":
                setState(state === "min" ? "normal" : "min");
                break;
            case "gm:compose:maximize":
                setState(state === "max" ? "normal" : "max");
                break;
            case "gm:compose:close":
                close();
                break;
            case "gm:compose:pending":
                startPending(ev.data.seconds || 5);
                break;
            case "gm:compose:ready":
                // 提交后又回到了写邮件页（发送失败），恢复窗口显示错误
                if (state === "sending") setState("normal");
                break;
            case "gm:compose:sent":
                close();
                if (window.GM && window.GM.toast) {
                    window.GM.toast("邮件已发送", { href: ev.data.url, linkText: "查看邮件" });
                }
                break;
        }
    });

    // 撤销发送：隐藏窗口（iframe 保留，内容和附件都在），倒计时结束后通知 iframe 真正提交
    var pendingTimer = null;
    function tellFrame(type) {
        if (frame && frame.contentWindow) {
            frame.contentWindow.postMessage({ type: type }, window.location.origin);
        }
    }
    function startPending(seconds) {
        setState("sending");
        clearTimeout(pendingTimer);
        pendingTimer = setTimeout(function () {
            pendingTimer = null;
            tellFrame("gm:compose:commit");
        }, seconds * 1000);
        if (window.GM && window.GM.toast) {
            window.GM.toast("正在发送…（" + seconds + " 秒内可撤销）", {
                actionText: "撤销",
                duration: seconds * 1000,
                onAction: function () {
                    if (!pendingTimer) return;   // 已经提交，来不及撤销
                    clearTimeout(pendingTimer);
                    pendingTimer = null;
                    tellFrame("gm:compose:undo");
                    setState("normal");
                },
            });
        }
    }

    window.addEventListener("beforeunload", function (ev) {
        if (isDirty()) { ev.preventDefault(); ev.returnValue = ""; }
    });

    window.GM = window.GM || {};
    // 写邮件窗口打开时，页面不要自动整页刷新（会丢失正在编写的内容）
    window.GM.composeOpen = function () { return !!win; };
})();

// 星标按钮（列表行、邮件详情）：先在界面上切换，再后台提交；失败时恢复
document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest("[data-star-url]");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    var tokenInput = document.querySelector('input[name="csrf_token"]');
    var starred = !btn.classList.contains("is-starred");
    function apply(value) {
        btn.classList.toggle("is-starred", value);
        btn.setAttribute("aria-pressed", value ? "true" : "false");
        btn.title = value ? "已加星标" : "未加星标";
        var iconEl = btn.querySelector(".ms");
        if (iconEl) iconEl.classList.toggle("ms-fill", value);
    }
    apply(starred);
    var body = new FormData();
    body.append("starred", starred ? "1" : "0");
    fetch(btn.getAttribute("data-star-url"), {
        method: "POST",
        body: body,
        credentials: "same-origin",
        headers: {
            "X-Requested-With": "fetch",
            "X-CSRF-Token": tokenInput ? tokenInput.value : "",
        },
    }).then(function (resp) {
        if (!resp.ok) throw new Error("bad status");
    }).catch(function () {
        apply(!starred);
        if (window.GM && window.GM.toast) window.GM.toast("星标操作失败，请稍后重试");
    });
});

// 会话视图：点击邮件头部折叠 / 展开；折叠的邮件第一次展开时才加载正文和图片
document.addEventListener("click", function (ev) {
    var head = ev.target.closest && ev.target.closest("[data-msg-toggle]");
    if (!head) return;
    if (ev.target.closest("a, button, .dropdown, .dropdown-menu")) return;
    var msg = head.closest(".gm-msg");
    if (!msg) return;
    var expand = !msg.classList.contains("is-expanded");
    msg.classList.toggle("is-expanded", expand);
    if (expand) {
        msg.querySelectorAll("[data-src]").forEach(function (el) {
            el.setAttribute("src", el.getAttribute("data-src"));
            el.removeAttribute("data-src");
        });
    }
});
