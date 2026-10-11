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
                saveListScroll();
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
            rowChecks.forEach(function (cb) {
                var row = cb.closest(".rcm-row");
                if (row) row.classList.toggle("is-selected", cb.checked);
            });
            var card = document.querySelector(".gm-list-card");
            if (card) card.classList.toggle("has-selection", hasCheckedRows);
            var countEl = document.querySelector(".gm-sel-count");
            if (countEl) countEl.textContent = checkedCount ? String(checkedCount) : "";
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
        document.querySelectorAll("[data-select]").forEach(function (item) {
            item.addEventListener("click", function () {
                var mode = item.getAttribute("data-select");
                rowChecks.forEach(function (cb) {
                    var row = cb.closest(".rcm-row");
                    var unread = row && row.classList.contains("is-unread");
                    cb.checked = mode === "all" || (mode === "unread" && unread) || (mode === "read" && !unread);
                });
                updateBulkState();
            });
        });

        // 行内悬停按钮：把邮件 id 填进共用表单后提交（删除需确认）
        var rowForm = document.getElementById("gm-row-form");
        document.querySelectorAll("[data-row-action]").forEach(function (btn) {
            btn.addEventListener("click", function (ev) {
                ev.stopPropagation();
                if (!rowForm) return;
                var message = btn.getAttribute("data-confirm");
                if (message && !window.confirm(message)) return;
                rowForm.action = btn.getAttribute("data-row-action");
                document.getElementById("gm-row-id").value = btn.getAttribute("data-id");
                saveListScroll();
                rowForm.submit();
            });
        });

        restoreSelection();
        rowChecks.forEach(function (cb) { cb.addEventListener("change", updateBulkState); });
        updateBulkState();

        document.querySelectorAll(".rcm-row").forEach(function (row) {
            // 列表刷新后，仍在「可撤销」期内的邮件继续隐藏
            if (swipeDeleted.has(rowId(row))) row.hidden = true;
            bindSwipe(row);
        });
    }

    // ---- 触屏左滑 / 右滑删除（与 Gmail App 一致） --------------------------
    // 滑过一定距离或快速甩动即删除：先在界面上移除并提示「撤销」，
    // 几秒后（或离开页面时）才真正提交删除请求。
    var SWIPE_UNDO_MS = 5000;
    var swipeDeleted = new Set();   // 已滑走、尚未提交到服务器的邮件 id
    var swipeTimer = null;
    var lastSwipeAt = 0;

    function rowId(row) {
        var cb = row.querySelector(".rcm-row-check");
        return cb ? String(cb.value) : "";
    }

    function rowById(id) {
        var cb = document.getElementById("rcm-check-" + id);
        return cb ? cb.closest(".rcm-row") : null;
    }

    function deleteFormData(ids) {
        var form = document.getElementById("gm-row-form");
        if (!form) return null;
        var data = new FormData(form);
        data.delete("message_ids");
        ids.forEach(function (id) { data.append("message_ids", id); });
        return { url: form.getAttribute("data-delete-url"), data: data };
    }

    function commitSwipeDeletes(useBeacon) {
        clearTimeout(swipeTimer);
        swipeTimer = null;
        var ids = Array.from(swipeDeleted);
        if (!ids.length) return;
        var req = deleteFormData(ids);
        if (!req || !req.url) return;
        if (useBeacon && navigator.sendBeacon) {
            // 页面即将关闭 / 跳转，普通 fetch 可能被取消
            if (navigator.sendBeacon(req.url, req.data)) swipeDeleted.clear();
            return;
        }
        fetch(req.url, {
            method: "POST",
            body: req.data,
            credentials: "same-origin",
            redirect: "manual",
            keepalive: true,
        }).then(function (resp) {
            if (!resp.ok && resp.type !== "opaqueredirect") throw new Error("bad status");
            ids.forEach(function (id) {
                swipeDeleted.delete(id);
                var row = rowById(id);
                if (row) row.remove();
            });
            // 刷新列表以补齐当前页、更新未读数
            if (canAutoRefresh) refreshList(); else refreshSidebar();
        }).catch(function () {
            ids.forEach(function (id) { swipeDeleted.delete(id); restoreSwipedRow(rowById(id)); });
            if (window.GM && window.GM.toast) window.GM.toast("删除失败，请稍后重试");
        });
    }

    function restoreSwipedRow(row) {
        if (!row) return;
        row.hidden = false;
        row.style.transition = "";
        row.style.transform = "";
        row.style.opacity = "";
        row.style.height = "";
        row.style.minHeight = "";
        row.style.paddingTop = "";
        row.style.paddingBottom = "";
        row.style.overflow = "";
    }

    function undoSwipeDeletes() {
        clearTimeout(swipeTimer);
        swipeTimer = null;
        swipeDeleted.forEach(function (id) { restoreSwipedRow(rowById(id)); });
        swipeDeleted.clear();
    }

    function onRowSwiped(row) {
        swipeDeleted.add(rowId(row));
        clearTimeout(swipeTimer);
        swipeTimer = setTimeout(function () { commitSwipeDeletes(false); }, SWIPE_UNDO_MS);
        if (window.GM && window.GM.toast) {
            window.GM.toast("已删除 " + swipeDeleted.size + " 封邮件", {
                actionText: "撤销",
                onAction: undoSwipeDeletes,
                duration: SWIPE_UNDO_MS,
            });
        }
    }

    // 离开页面（打开邮件、切换文件夹、关闭标签页）时立即提交未完成的删除
    window.addEventListener("pagehide", function () { commitSwipeDeletes(true); });

    function bindSwipe(row) {
        var startX = 0, startY = 0, startT = 0, dx = 0;
        var tracking = false, swiping = false;
        var bg = null;

        function cleanup() {
            row.classList.remove("is-swiping");
            if (bg) { bg.remove(); bg = null; }
        }

        row.addEventListener("touchstart", function (ev) {
            if (hasCheckedRows || ev.touches.length !== 1) { tracking = false; return; }
            if (ev.target.closest("button, input, label, .dropdown-menu")) { tracking = false; return; }
            var t = ev.touches[0];
            startX = t.clientX;
            startY = t.clientY;
            startT = Date.now();
            dx = 0;
            tracking = true;
            swiping = false;
        }, { passive: true });

        row.addEventListener("touchmove", function (ev) {
            if (!tracking) return;
            var t = ev.touches[0];
            var mx = t.clientX - startX;
            var my = t.clientY - startY;
            if (!swiping) {
                if (Math.abs(mx) < 10 && Math.abs(my) < 10) return;
                // 先判断方向：竖向为主则交给页面滚动
                if (Math.abs(my) >= Math.abs(mx)) { tracking = false; return; }
                swiping = true;
                row.classList.add("is-swiping");
                row.style.transition = "none";
                bg = document.createElement("div");
                bg.className = "gm-swipe-bg";
                bg.innerHTML = '<span class="ms" aria-hidden="true">delete</span>';
                bg.style.top = row.offsetTop + "px";
                bg.style.height = row.offsetHeight + "px";
                row.parentNode.insertBefore(bg, row);
            }
            if (ev.cancelable) ev.preventDefault();
            dx = mx;
            row.style.transform = "translateX(" + dx + "px)";
            bg.classList.toggle("is-right", dx < 0);
            bg.classList.toggle("is-armed", Math.abs(dx) > row.offsetWidth * 0.35);
        }, { passive: false });

        function onEnd() {
            if (!tracking) return;
            tracking = false;
            if (!swiping) return;
            swiping = false;
            lastSwipeAt = Date.now();
            var width = row.offsetWidth;
            var velocity = Math.abs(dx) / Math.max(1, Date.now() - startT);  // px/ms
            var commit = Math.abs(dx) > width * 0.35 || (Math.abs(dx) > 48 && velocity > 0.6);
            row.style.transition = "transform .2s ease-out, opacity .2s ease-out";
            if (!commit) {
                row.style.transform = "";
                setTimeout(function () { row.style.transition = ""; cleanup(); }, 200);
                return;
            }
            // 滑出屏幕后收起行高，再隐藏
            row.style.transform = "translateX(" + (dx > 0 ? width : -width) + "px)";
            row.style.opacity = "0";
            var id = rowId(row);
            setTimeout(function () {
                if (!swipeDeleted.has(id)) { cleanup(); return; }  // 动画期间已撤销
                var h = row.offsetHeight;
                row.style.overflow = "hidden";
                row.style.height = h + "px";
                if (bg) {
                    bg.style.transition = "height .2s ease-out";
                    bg.style.height = h + "px";
                }
                // 强制回流，让高度动画从当前值开始
                void row.offsetHeight;
                row.style.transition = "height .2s ease-out, padding .2s ease-out, min-height .2s ease-out";
                row.style.height = "0px";
                row.style.minHeight = "0px";
                row.style.paddingTop = "0px";
                row.style.paddingBottom = "0px";
                if (bg) bg.style.height = "0px";
                setTimeout(function () {
                    if (swipeDeleted.has(id)) row.hidden = true; else restoreSwipedRow(row);
                    cleanup();
                }, 200);
            }, 200);
            onRowSwiped(row);
        }

        row.addEventListener("touchend", onEnd);
        row.addEventListener("touchcancel", function () {
            if (!tracking) return;
            tracking = false;
            swiping = false;
            row.style.transition = "";
            row.style.transform = "";
            cleanup();
        });
        // 滑动结束后浏览器可能补发一次 click，不要当成打开邮件
        row.addEventListener("click", function (ev) {
            if (Date.now() - lastSwipeAt < 400) {
                ev.stopImmediatePropagation();
                ev.preventDefault();
            }
        }, true);
    }

    initMessageList();

    // ---- Live updates via Server-Sent Events -----------------------------
    var ctxEl = document.getElementById("rcm-view-context");
    var ctx = {};
    try { ctx = JSON.parse(ctxEl.textContent); } catch (e) { ctx = {}; }

    // ---- 记住列表滚动位置：打开邮件再返回时回到原来的位置 ----------------
    // 列表在 .rcm-list 内部滚动（手机端是整个页面滚动），浏览器不会自动恢复，
    // 所以打开邮件前按「当前列表地址」存下位置，回到同一列表时恢复。
    function scrollKey() {
        return "gc:scroll:" + (ctx.selfUrl || (window.location.pathname + window.location.search));
    }

    function saveListScroll() {
        var list = document.querySelector(".rcm-list");
        try {
            sessionStorage.setItem(scrollKey(), JSON.stringify({
                list: list ? list.scrollTop : 0,
                win: window.scrollY || 0,
            }));
        } catch (e) { /* ignore storage errors */ }
    }

    function restoreListScroll() {
        var raw = null;
        try {
            raw = sessionStorage.getItem(scrollKey());
            sessionStorage.removeItem(scrollKey());
        } catch (e) { return; }
        if (!raw) return;
        try {
            var pos = JSON.parse(raw) || {};
            var list = document.querySelector(".rcm-list");
            if (list && pos.list) list.scrollTop = pos.list;
            if (pos.win) window.scrollTo(0, pos.win);
        } catch (e) { /* ignore */ }
    }

    // 点主题链接（包括中键/新标签页打开）同样记下位置
    document.addEventListener("click", function (ev) {
        if (ev.target.closest && ev.target.closest(".rcm-subject-link")) saveListScroll();
    }, true);
    restoreListScroll();

    // Page 1 with no active search shows new mail at the top, so we can swap
    // the list silently. Otherwise we surface a banner the user can click.
    var canAutoRefresh = (ctx.page === 1) && !ctx.hasQuery;

    function eventMatchesView(ev) {
        if (ctx.accountId && ev.account_id !== ctx.accountId) return false;
        if (ctx.kind === "inbox") return ev.inbox > 0;
        if (ctx.kind === "sent") return ev.sent > 0;
        if (ctx.kind === "junk") return ev.junk > 0;
        if (ctx.kind === "starred") return false;  // 新邮件不会自带星标
        if (/^label-/.test(ctx.kind || "")) return false;  // 标签视图：新邮件只更新侧栏
        return (ev.inbox > 0) || (ev.sent > 0); // "all"（不含垃圾邮件）
    }

    // 拉取 url，替换 selector 指定的区域。带 X-Requested-With: fetch 时服务端只返回
    // 侧栏和内容区（不含顶栏、脚本等页面框架）。
    // - 收到事件后立即发起请求，不用 setTimeout：浏览器会把后台标签页的定时器
    //   限速到最多每分钟执行一次，而网络请求的回调不受此限制。
    // - 刷新进行中又收到新事件时只做标记，结束后立刻再刷新一次，既不丢失更新，
    //   也能把短时间内的多个事件合并成一次请求。
    function makeRefresher(urlFn, selectors, afterSwap) {
        var running = false;
        var pending = false;
        function run() {
            if (running) {
                pending = true;
                return;
            }
            running = true;
            pending = false;
            fetch(urlFn(), {
                credentials: "same-origin",
                headers: { "X-Requested-With": "fetch" },
            }).then(function (resp) {
                if (!resp.ok) throw new Error("bad status");
                return resp.text();
            }).then(function (text) {
                var doc = new DOMParser().parseFromString(text, "text/html");
                var swapped = false;
                var navScroll = getNavScroll();
                var oldList = document.querySelector(".rcm-list");
                var listScroll = oldList ? oldList.scrollTop : 0;
                selectors.forEach(function (selector) {
                    var fresh = doc.querySelector(selector);
                    var current = document.querySelector(selector);
                    if (fresh && current) {
                        current.replaceWith(fresh);
                        swapped = true;
                    }
                });
                if (swapped) {
                    setNavScroll(navScroll);
                    var newList = document.querySelector(".rcm-list");
                    if (newList && listScroll) newList.scrollTop = listScroll;
                    if (doc.title) document.title = doc.title;
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

    var refreshList = makeRefresher(function () { return window.location.href; }, [".gm-main", ".gm-nav"], function () {
        initMessageList();
        hideBanner();
        // 键盘快捷键据此恢复当前选中的行
        window.dispatchEvent(new CustomEvent("gm:listrefreshed"));
    });

    // 只刷新左侧导航（未读数），用于不属于当前列表的新邮件，
    // 例如正在看收件箱时收到垃圾邮件，或其他账号收到新邮件。
    // 走专门的轻量接口，服务端不必查询和渲染整个邮件列表。
    var refreshSidebar = makeRefresher(function () {
        return "/messages/nav" + window.location.search;
    }, [".gm-nav"]);

    function getNavScroll() {
        var el = document.querySelector(".gm-nav");
        return el ? el.scrollTop : 0;
    }

    function setNavScroll(top) {
        var el = document.querySelector(".gm-nav");
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
        banner.innerHTML = '<span class="ms" aria-hidden="true">arrow_upward</span> ' +
            pendingCount + ' 封新邮件';
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
    // 服务器上的已读 / 删除变化：不会把列表往下挤，直接原地刷新列表和未读数
    window.addEventListener("rcm:mailchanged", function (e) {
        var detail = e.detail || {};
        if (ctx.accountId && detail.account_id !== ctx.accountId) return;
        refreshList();
    });
    window.addEventListener("rcm:sseopen", function (e) {
        // On reconnect we may have missed events while offline; quietly catch
        // up (skip the very first connection, the page is already fresh).
        var detail = e.detail || {};
        if (!detail.first && canAutoRefresh) {
            refreshList();
        } else if (detail.first && ctx.anyBusy) {
            // 页面渲染后、连接建立前同步可能已结束，sync_done 会错过，补刷新一次
            refreshList();
        }
    });
    // 同步结束：刷新列表（去掉「正在同步…」，补上新邮件）
    window.addEventListener("rcm:syncdone", function (e) {
        var detail = e.detail || {};
        if (ctx.accountId && detail.account_id !== ctx.accountId) return;
        if (document.querySelector(".gm-busy")) refreshList();
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

    // 手机端「写邮件」悬浮按钮：列表向下滚动时收起成圆形，向上滚动时展开（与 Gmail App 一致）
    (function () {
        var lastTop = 0;
        document.addEventListener("scroll", function (ev) {
            var list = ev.target;
            if (!list.classList || !list.classList.contains("rcm-list")) return;
            var fab = document.querySelector(".gm-fab");
            if (!fab) return;
            var top = list.scrollTop;
            if (Math.abs(top - lastTop) < 8) return;
            fab.classList.toggle("is-collapsed", top > lastTop && top > 24);
            lastTop = top;
        }, true);
    })();

    // ---- 没有实时连接时的兜底：同步进行中每 5 秒整页重载 ----
    // 有实时连接时由上面的 sync_done 事件刷新，不再整页重载。
    var anyBusy = !!ctx.anyBusy && !(window.RCM && window.RCM.sse);
    if (anyBusy) {
        setTimeout(function reload() {
            // 写邮件窗口打开时推迟刷新，避免丢失正在编写的内容
            if (window.GM && window.GM.composeOpen && window.GM.composeOpen()) { setTimeout(reload, 5000); return; }
            window.location.reload();
        }, 5000);
    }
})();
