// 键盘快捷键（与 Gmail 一致）。按 ? 查看全部。
// 在输入框 / 编辑区里打字时不触发；带 Ctrl / Alt / ⌘ 的组合键交给浏览器。
(function () {
    var HELP = [
        ["通用", [
            ["c", "写邮件"],
            ["/", "搜索邮件"],
            ["?", "显示快捷键帮助"],
            ["g 然后 i", "转到收件箱"],
            ["g 然后 s", "转到已加星标"],
            ["g 然后 t", "转到已发送"],
            ["g 然后 a", "转到所有邮件"],
        ]],
        ["邮件列表", [
            ["j / k", "下一封 / 上一封"],
            ["o 或 Enter", "打开邮件"],
            ["x", "选中 / 取消选中"],
            ["s", "加星标 / 取消星标"],
            ["#", "删除"],
            ["Shift + I", "标记为已读"],
            ["Shift + U", "标记为未读"],
            ["Esc", "取消选择"],
        ]],
        ["阅读邮件", [
            ["u", "返回列表"],
            ["k / j", "较新 / 较旧的一封"],
            ["r", "回复"],
            ["a", "回复全部"],
            ["f", "转发"],
            ["s", "加星标 / 取消星标"],
            ["#", "删除"],
            ["Shift + U", "标记为未读"],
        ]],
    ];

    function isTyping(target) {
        if (!target || !target.closest) return false;
        if (target.isContentEditable) return true;
        return !!target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']");
    }

    function clickIfPresent(selector) {
        var el = typeof selector === "string" ? document.querySelector(selector) : selector;
        if (!el || el.disabled || el.getAttribute("aria-disabled") === "true") return false;
        el.click();
        return true;
    }

    // ---- 帮助浮层 ----------------------------------------------------------
    var help = null;
    function toggleHelp(show) {
        if (show === undefined) show = !help;
        if (!show) {
            if (help) { help.remove(); help = null; }
            return;
        }
        if (help) return;
        help = document.createElement("div");
        help.className = "gm-kbd-help";
        help.setAttribute("role", "dialog");
        help.setAttribute("aria-label", "键盘快捷键");
        var panel = document.createElement("div");
        panel.className = "gm-kbd-panel";
        var title = document.createElement("div");
        title.className = "gm-kbd-title";
        title.textContent = "键盘快捷键";
        panel.appendChild(title);
        var grid = document.createElement("div");
        grid.className = "gm-kbd-grid";
        HELP.forEach(function (group) {
            var section = document.createElement("section");
            var h = document.createElement("h3");
            h.textContent = group[0];
            section.appendChild(h);
            group[1].forEach(function (item) {
                var row = document.createElement("div");
                row.className = "gm-kbd-row";
                var key = document.createElement("kbd");
                key.textContent = item[0];
                var label = document.createElement("span");
                label.textContent = item[1];
                row.appendChild(key);
                row.appendChild(label);
                section.appendChild(row);
            });
            grid.appendChild(section);
        });
        panel.appendChild(grid);
        help.appendChild(panel);
        help.addEventListener("click", function (ev) {
            if (ev.target === help) toggleHelp(false);
        });
        document.body.appendChild(help);
    }

    // ---- 邮件列表：当前行 ---------------------------------------------------
    var focusedId = null;

    function rows() {
        return Array.prototype.slice.call(document.querySelectorAll(".rcm-row:not([hidden])"));
    }

    function rowId(row) {
        var cb = row && row.querySelector(".rcm-row-check");
        return cb ? cb.value : null;
    }

    function currentRow() {
        if (!focusedId) return null;
        var cb = document.getElementById("rcm-check-" + focusedId);
        var row = cb && cb.closest(".rcm-row");
        return row && !row.hidden ? row : null;
    }

    function focusRow(row) {
        document.querySelectorAll(".rcm-row.is-focused").forEach(function (r) {
            r.classList.remove("is-focused");
        });
        if (!row) { focusedId = null; return; }
        focusedId = rowId(row);
        row.classList.add("is-focused");
        row.scrollIntoView({ block: "nearest" });
    }

    function moveFocus(step) {
        var list = rows();
        if (!list.length) return;
        var index = list.indexOf(currentRow());
        if (index < 0) index = step > 0 ? -1 : list.length;
        focusRow(list[Math.max(0, Math.min(list.length - 1, index + step))]);
    }

    window.addEventListener("gm:listrefreshed", function () {
        var row = currentRow();
        if (row) row.classList.add("is-focused");
    });

    function hasSelection() {
        return !!document.querySelector(".rcm-row-check:checked");
    }

    // 有勾选时作用于勾选的邮件（工具栏批量按钮），否则作用于当前行
    function rowOrBulk(rowSelector, bulkSelector) {
        if (hasSelection()) return clickIfPresent(bulkSelector);
        var row = currentRow();
        return row ? clickIfPresent(row.querySelector(rowSelector)) : false;
    }

    function listKey(key) {
        switch (key) {
            case "j": moveFocus(1); return true;
            case "k": moveFocus(-1); return true;
            case "o":
            case "Enter": {
                var row = currentRow();
                return row ? clickIfPresent(row.querySelector(".rcm-subject-link")) : false;
            }
            case "x": {
                var r = currentRow();
                return r ? clickIfPresent(r.querySelector(".rcm-row-check")) : false;
            }
            case "s": {
                var sr = currentRow();
                return sr ? clickIfPresent(sr.querySelector("[data-star-url]")) : false;
            }
            case "#":
                return rowOrBulk('[data-row-action$="batch-delete"]', '.rcm-bulk-action[formaction$="batch-delete"]');
            case "I":
                return rowOrBulk('[data-row-action$="batch-mark-read"]', '.rcm-bulk-action[formaction$="batch-mark-read"]');
            case "U":
                return rowOrBulk('[data-row-action$="batch-mark-unread"]', '.rcm-bulk-action[formaction$="batch-mark-unread"]');
            case "Escape":
                if (hasSelection()) return clickIfPresent('[data-select="none"]');
                focusRow(null);
                return true;
        }
        return false;
    }

    function detailKey(key) {
        var map = {
            u: '[data-shortcut="back"]',
            k: '[data-shortcut="newer"]',
            j: '[data-shortcut="older"]',
            r: '[data-shortcut="reply"]',
            a: '[data-shortcut="reply-all"]',
            f: '[data-shortcut="forward"]',
            s: ".gm-star-btn",
            "#": '[data-shortcut="delete"]',
            U: '[data-shortcut="unread"]',
        };
        return map[key] ? clickIfPresent(map[key]) : false;
    }

    // ---- g 前缀导航 ----------------------------------------------------------
    var gPressedAt = 0;
    var GO = { i: "inbox", s: "starred", t: "sent", a: "all" };

    function goTo(kind) {
        var link = document.querySelector('.gm-nav-item[href*="kind=' + kind + '&"]');
        if (link) { link.click(); return true; }
        window.location.href = "/messages/?kind=" + kind;
        return true;
    }

    document.addEventListener("keydown", function (ev) {
        if (ev.defaultPrevented || ev.ctrlKey || ev.metaKey || ev.altKey) return;
        if (ev.key === "Escape" && help) { toggleHelp(false); ev.preventDefault(); return; }
        if (isTyping(ev.target)) return;
        if (document.querySelector(".modal.show, .dropdown-menu.show")) return;

        var key = ev.key;
        var handled = false;
        if (gPressedAt && Date.now() - gPressedAt < 1500 && GO[key]) {
            gPressedAt = 0;
            handled = goTo(GO[key]);
        } else if (key === "g") {
            gPressedAt = Date.now();
            handled = true;
        } else if (key === "?") {
            toggleHelp();
            handled = true;
        } else if (key === "/") {
            var search = document.querySelector(".gm-search-input");
            if (search) { search.focus(); search.select(); handled = true; }
        } else if (key === "c") {
            handled = clickIfPresent('[data-shortcut="compose"]') || clickIfPresent("[data-compose]");
        } else if (document.querySelector(".rcm-list")) {
            handled = listKey(key);
        } else if (document.querySelector(".gm-detail")) {
            handled = detailKey(key);
        }
        if (handled) ev.preventDefault();
    });
})();
