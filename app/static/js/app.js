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
    if (ms > 0) setTimeout(function () { window.location.reload(); }, ms);
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
            || btn.classList.contains("rcm-icon-btn")
            || btn.classList.contains("rcm-icon-btn-sm")
            || btn.classList.contains("rcm-tool-btn")) {
            return;
        }
        var original = btn.innerHTML;
        btn.dataset.originalLabel = original;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm align-middle me-1" role="status" aria-hidden="true"></span>处理中…';
        setTimeout(function () {
            if (btn.disabled && btn.innerHTML.indexOf("处理中") !== -1) {
                btn.disabled = false;
                btn.innerHTML = original;
            }
        }, 8000);
    }, true);
})();

// 手机端侧边抽屉：打开状态记在 body 上，实时刷新替换侧边栏时不会被关闭。
(function () {
    var toggle = document.querySelector(".rcm-drawer-toggle");
    var backdrop = document.querySelector(".rcm-drawer-backdrop");
    if (!toggle) return;
    if (!document.querySelector(".rcm-sidebar")) {
        toggle.hidden = true;  // 没有侧边栏的页面不需要菜单按钮
        return;
    }
    function setOpen(open) {
        document.body.classList.toggle("rcm-drawer-open", open);
        toggle.setAttribute("aria-expanded", String(open));
    }
    toggle.addEventListener("click", function () {
        setOpen(!document.body.classList.contains("rcm-drawer-open"));
    });
    if (backdrop) backdrop.addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape") setOpen(false);
    });
    // 点了抽屉里的文件夹链接后自动收起（页面跳转前的视觉反馈）
    document.addEventListener("click", function (ev) {
        if (ev.target.closest(".rcm-sidebar a.rcm-folder")) setOpen(false);
    });
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
