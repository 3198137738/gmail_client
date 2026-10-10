(function () {
    // ---- List interactions (re-bound after every live refresh) ----------
    var hasCheckedRows = false;
    var selectionStorageKey = "gc:selected:" + window.location.pathname + window.location.search;

    function initMessageList() {
        document.querySelectorAll(".rcm-row[data-href]").forEach(function (row) {
            row.addEventListener("click", function (ev) {
                if (ev.target.closest("a, button, form, input, label")) return;
                if (hasCheckedRows) return;
                var selectedText = "";
                try { selectedText = String(window.getSelection() || ""); } catch (e) {}
                if ((selectedText || "").trim()) return;
                window.location.href = row.dataset.href;
            });
        });

        var selectAll = document.querySelector(".rcm-check-all");
        var rowChecks = Array.prototype.slice.call(document.querySelectorAll(".rcm-row-check"));
        var bulkButtons = Array.prototype.slice.call(document.querySelectorAll(".rcm-bulk-action"));

        function saveSelection() {
            try {
                var ids = rowChecks.filter(function (cb) { return cb.checked; }).map(function (cb) { return cb.value; });
                if (!ids.length) {
                    sessionStorage.removeItem(selectionStorageKey);
                    return;
                }
                sessionStorage.setItem(selectionStorageKey, JSON.stringify(ids));
            } catch (e) {
                /* ignore storage errors */
            }
        }

        function restoreSelection() {
            var raw = null;
            try {
                raw = sessionStorage.getItem(selectionStorageKey);
            } catch (e) {
                return;
            }
            if (!raw) return;
            var wanted = [];
            try {
                wanted = JSON.parse(raw) || [];
            } catch (e) {
                return;
            }
            if (!Array.isArray(wanted) || !wanted.length) return;
            var wantedSet = new Set(wanted.map(function (v) { return String(v); }));
            rowChecks.forEach(function (cb) {
                cb.checked = wantedSet.has(String(cb.value));
            });
        }

        function updateBulkState() {
            var checkedCount = rowChecks.filter(function (cb) { return cb.checked; }).length;
            hasCheckedRows = checkedCount > 0;
            bulkButtons.forEach(function (button) { button.disabled = checkedCount === 0; });
            if (!selectAll) return;
            selectAll.checked = checkedCount > 0 && checkedCount === rowChecks.length;
            selectAll.indeterminate = checkedCount > 0 && checkedCount < rowChecks.length;
            saveSelection();
        }

        if (selectAll) {
            selectAll.addEventListener("change", function () {
                rowChecks.forEach(function (cb) { cb.checked = selectAll.checked; });
                updateBulkState();
            });
        }
        restoreSelection();
        rowChecks.forEach(function (cb) { cb.addEventListener("change", updateBulkState); });
        updateBulkState();
    }

    initMessageList();

    // ---- Live updates via Server-Sent Events -----------------------------
    var ctxEl = document.getElementById("rcm-view-context");
    var ctx = {};
    try { ctx = JSON.parse(ctxEl.textContent); } catch (e) { ctx = {}; }

    // Page 1 with no active search shows new mail at the top, so we can swap
    // the list silently. Otherwise we surface a banner the user can click.
    var canAutoRefresh = (ctx.page === 1) && !ctx.hasQuery;

    function eventMatchesView(ev) {
        if (ctx.accountId && ev.account_id !== ctx.accountId) return false;
        if (ctx.kind === "inbox") return ev.inbox > 0;
        if (ctx.kind === "sent") return ev.sent > 0;
        if (ctx.kind === "junk") return ev.junk > 0;
        return (ev.inbox > 0) || (ev.sent > 0); // "all"（不含垃圾邮件）
    }

    // 重新拉取当前页面，替换 selector 指定的区域。
    // - 收到事件后立即发起请求，不用 setTimeout：浏览器会把后台标签页的定时器
    //   限速到最多每分钟执行一次，而网络请求的回调不受此限制。
    // - 刷新进行中又收到新事件时只做标记，结束后立刻再刷新一次，既不丢失更新，
    //   也能把短时间内的多个事件合并成一次请求。
    function makeRefresher(selector, afterSwap) {
        var running = false;
        var pending = false;
        function run() {
            if (running) {
                pending = true;
                return;
            }
            running = true;
            pending = false;
            fetch(window.location.href, {
                credentials: "same-origin",
                headers: { "X-Requested-With": "fetch" },
            }).then(function (resp) {
                if (!resp.ok) throw new Error("bad status");
                return resp.text();
            }).then(function (text) {
                var doc = new DOMParser().parseFromString(text, "text/html");
                var fresh = doc.querySelector(selector);
                var current = document.querySelector(selector);
                if (fresh && current) {
                    var sidebarScroll = getSidebarScroll();
                    current.replaceWith(fresh);
                    setSidebarScroll(sidebarScroll);
                    if (afterSwap) afterSwap();
                }
            }).catch(function () {
                /* leave the page as-is on transient errors */
            }).finally(function () {
                running = false;
                if (pending) run();
            });
        }
        return run;
    }

    var refreshList = makeRefresher(".rcm-body", function () {
        initMessageList();
        hideBanner();
    });

    // 只刷新侧边栏（未读数），用于不属于当前列表的新邮件，
    // 例如正在看收件箱时收到垃圾邮件，或其他账号收到新邮件。
    var refreshSidebar = makeRefresher(".rcm-sidebar");

    function getSidebarScroll() {
        var el = document.querySelector(".rcm-sidebar");
        return el ? el.scrollTop : 0;
    }

    function setSidebarScroll(top) {
        var el = document.querySelector(".rcm-sidebar");
        if (el) el.scrollTop = top;
    }

    // ---- "New mail" banner (used when an auto-swap would be disruptive) ---
    var banner = null;
    var pendingCount = 0;

    function hideBanner() {
        pendingCount = 0;
        if (banner) { banner.remove(); banner = null; }
    }

    function showBanner() {
        if (!banner) {
            banner = document.createElement("button");
            banner.type = "button";
            banner.className = "rcm-newmail-banner";
            banner.addEventListener("click", function () {
                if (canAutoRefresh) {
                    refreshList();
                } else {
                    window.location.href = "/messages/?kind=" + encodeURIComponent(ctx.kind || "inbox") +
                        (ctx.accountId ? "&account_id=" + ctx.accountId : "");
                }
            });
            document.body.appendChild(banner);
        }
        banner.innerHTML = '<i class="bi bi-envelope-arrow-down"></i> ' +
            pendingCount + ' 封新邮件 · 点击查看';
    }

    function onNewMail(ev) {
        if (!eventMatchesView(ev)) {
            refreshSidebar();
            return;
        }
        var n = ctx.kind === "junk" ? (ev.junk || 0) : (ev.inbox || 0) + (ev.sent || 0);
        pendingCount += n;
        // 勾选状态存在 sessionStorage 中，刷新后 initMessageList 会恢复勾选，
        // 因此有勾选时也可以安全地自动刷新。
        if (canAutoRefresh) {
            refreshList();
        } else {
            showBanner();
            refreshSidebar();
        }
    }

    // The shared SSE connection lives in base.html and re-broadcasts events.
    window.addEventListener("rcm:newmail", function (e) {
        if (e.detail) onNewMail(e.detail);
    });
    window.addEventListener("rcm:sseopen", function (e) {
        // On reconnect we may have missed events while offline; quietly catch
        // up (skip the very first connection, the page is already fresh).
        var detail = e.detail || {};
        if (!detail.first && canAutoRefresh) {
            refreshList();
        }
    });

    // 标签页在后台超过 10 秒后切回前台时补刷新一次：浏览器可能冻结了长时间
    // 处于后台的标签页，期间的事件会丢失。
    var hiddenAt = document.hidden ? Date.now() : null;
    document.addEventListener("visibilitychange", function () {
        if (document.hidden) {
            hiddenAt = Date.now();
            return;
        }
        if (hiddenAt !== null && Date.now() - hiddenAt > 10000) {
            if (canAutoRefresh) refreshList(); else refreshSidebar();
        }
        hiddenAt = null;
    });

    // ---- Fallback: keep the legacy auto-reload while a sync is running ----
    var anyBusy = !!ctx.anyBusy;
    if (anyBusy) {
        setTimeout(function () { window.location.reload(); }, 5000);
    }
})();
