(function () {
    "use strict";
    var form = document.getElementById("gc-compose");
    var data = JSON.parse(document.getElementById("gc-compose-data").textContent);
    var contacts = data.contacts || [];
    var editor = document.getElementById("gc-editor");
    var plain = document.getElementById("gc-plain");
    var plainToggle = document.getElementById("gc-plain-mode");
    var formatBar = document.getElementById("gc-format-bar");
    var formatToggle = document.getElementById("gc-toggle-format");
    var errorBox = document.getElementById("gc-error");
    var errorText = document.getElementById("gc-error-text");
    var sendBtn = document.getElementById("gc-send");
    var suggestBox = document.getElementById("gc-suggest");
    var submitting = false;
    var dirty = false;

    function markDirty() { dirty = true; }

    function showError(msg) {
        errorText.textContent = msg;
        errorBox.hidden = false;
    }

    // ---------------- 地址解析 ----------------
    var EMAIL_RE = /^[^\s@<>",;]+@[^\s@<>",;]+\.[^\s@<>",;]+$/;

    // 按逗号/分号拆分地址，忽略引号和尖括号内的分隔符。
    function splitAddresses(str) {
        var parts = [], buf = "", quote = false, angle = false;
        for (var i = 0; i < str.length; i++) {
            var ch = str[i];
            if (ch === '"') quote = !quote;
            else if (ch === "<") angle = true;
            else if (ch === ">") angle = false;
            if ((ch === "," || ch === ";") && !quote && !angle) {
                if (buf.trim()) parts.push(buf.trim());
                buf = "";
            } else {
                buf += ch;
            }
        }
        if (buf.trim()) parts.push(buf.trim());
        return parts;
    }

    function parseAddress(token) {
        var m = token.match(/^(.*)<([^>]+)>\s*$/);
        if (m) {
            return { name: m[1].trim().replace(/^"|"$/g, "").trim(), email: m[2].trim() };
        }
        return { name: "", email: token.replace(/^mailto:/i, "").trim() };
    }

    function formatAddress(a) {
        return a.name ? '"' + a.name.replace(/"/g, "") + '" <' + a.email + ">" : a.email;
    }

    // ---------------- 收件人输入框（地址块） ----------------
    var fields = [];

    function ChipField(container) {
        this.container = container;
        this.input = container.querySelector(".gc-chip-input");
        this.hidden = document.getElementById(container.dataset.target);
        this.addresses = [];
        var self = this;

        container.addEventListener("mousedown", function (ev) {
            if (ev.target === container) {
                ev.preventDefault();
                self.input.focus();
            }
        });
        this.input.addEventListener("keydown", function (ev) { self.onKeyDown(ev); });
        this.input.addEventListener("input", function () {
            markDirty();
            if (/[,;]/.test(self.input.value)) {
                self.commit();
            } else {
                suggest.update(self);
            }
        });
        this.input.addEventListener("focus", function () { suggest.update(self); });
        this.input.addEventListener("blur", function () {
            self.commit();
            suggest.close();
        });
    }

    ChipField.prototype.add = function (addr) {
        if (!addr.email) return;
        var key = addr.email.toLowerCase();
        for (var i = 0; i < this.addresses.length; i++) {
            if (this.addresses[i].email.toLowerCase() === key) return;
        }
        this.addresses.push(addr);
    };

    ChipField.prototype.commit = function () {
        var raw = this.input.value;
        if (!raw.trim()) {
            this.input.value = "";
            return;
        }
        var self = this;
        splitAddresses(raw).forEach(function (token) { self.add(parseAddress(token)); });
        this.input.value = "";
        this.render();
    };

    ChipField.prototype.remove = function (index) {
        this.addresses.splice(index, 1);
        markDirty();
        this.render();
    };

    ChipField.prototype.render = function () {
        var self = this;
        this.container.querySelectorAll(".gc-chip").forEach(function (el) { el.remove(); });
        this.addresses.forEach(function (addr, index) {
            var chip = document.createElement("span");
            chip.className = "gc-chip" + (EMAIL_RE.test(addr.email) ? "" : " is-invalid");
            chip.title = EMAIL_RE.test(addr.email) ? addr.email : "无效的邮箱地址：" + addr.email;
            var avatar = document.createElement("span");
            avatar.className = "gc-chip-avatar";
            avatar.textContent = (addr.name || addr.email).charAt(0).toUpperCase();
            var label = document.createElement("span");
            label.className = "gc-chip-label";
            label.textContent = addr.name || addr.email;
            var close = document.createElement("button");
            close.type = "button";
            close.className = "gc-chip-remove";
            close.setAttribute("aria-label", "移除 " + addr.email);
            close.innerHTML = '<span class="ms" aria-hidden="true">close</span>';
            close.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
            close.addEventListener("click", function () {
                self.remove(index);
                self.input.focus();
            });
            chip.appendChild(avatar);
            chip.appendChild(label);
            chip.appendChild(close);
            self.container.insertBefore(chip, self.input);
        });
        this.hidden.value = this.addresses.map(formatAddress).join(", ");
    };

    ChipField.prototype.onKeyDown = function (ev) {
        if (suggest.isOpenFor(this)) {
            if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
                ev.preventDefault();
                suggest.move(ev.key === "ArrowDown" ? 1 : -1);
                return;
            }
            if ((ev.key === "Enter" || ev.key === "Tab") && suggest.pick()) {
                ev.preventDefault();
                return;
            }
            if (ev.key === "Escape") {
                suggest.close();
                return;
            }
        }
        if (ev.key === "Enter" || (ev.key === "Tab" && this.input.value.trim())) {
            if (this.input.value.trim()) {
                ev.preventDefault();
                this.commit();
            } else if (ev.key === "Enter") {
                ev.preventDefault();
            }
        } else if (ev.key === "Backspace" && !this.input.value && this.addresses.length) {
            this.remove(this.addresses.length - 1);
        }
    };

    ChipField.prototype.hasInvalid = function () {
        return this.addresses.some(function (a) { return !EMAIL_RE.test(a.email); });
    };

    // ---------------- 联系人自动补全 ----------------
    var suggest = {
        field: null,
        items: [],
        active: 0,
        isOpenFor: function (field) { return !suggestBox.hidden && this.field === field; },
        update: function (field) {
            var q = field.input.value.trim().toLowerCase();
            if (!q || !contacts.length) {
                this.close();
                return;
            }
            var taken = {};
            field.addresses.forEach(function (a) { taken[a.email.toLowerCase()] = true; });
            this.items = contacts.filter(function (c) {
                return !taken[c.email] &&
                    (c.email.indexOf(q) !== -1 || (c.name && c.name.toLowerCase().indexOf(q) !== -1));
            }).slice(0, 8);
            if (!this.items.length) {
                this.close();
                return;
            }
            this.field = field;
            this.active = 0;
            this.render();
            var fieldRect = field.container.getBoundingClientRect();
            var formRect = form.getBoundingClientRect();
            suggestBox.style.left = (fieldRect.left - formRect.left) + "px";
            suggestBox.style.top = (fieldRect.bottom - formRect.top + 2) + "px";
            suggestBox.style.width = Math.min(fieldRect.width, 440) + "px";
            suggestBox.hidden = false;
        },
        render: function () {
            var self = this;
            suggestBox.innerHTML = "";
            this.items.forEach(function (c, i) {
                var row = document.createElement("div");
                row.className = "gc-suggest-item" + (i === self.active ? " is-active" : "");
                row.setAttribute("role", "option");
                var avatar = document.createElement("span");
                avatar.className = "gc-chip-avatar gc-suggest-avatar";
                avatar.textContent = (c.name || c.email).charAt(0).toUpperCase();
                var text = document.createElement("span");
                text.className = "gc-suggest-text";
                var name = document.createElement("span");
                name.className = "gc-suggest-name";
                name.textContent = c.name || c.email;
                text.appendChild(name);
                if (c.name) {
                    var email = document.createElement("span");
                    email.className = "gc-suggest-email";
                    email.textContent = c.email;
                    text.appendChild(email);
                }
                row.appendChild(avatar);
                row.appendChild(text);
                // mousedown 时就选中：避免输入框先失焦把半截文字提交成地址块。
                row.addEventListener("mousedown", function (ev) {
                    ev.preventDefault();
                    self.active = i;
                    self.pick();
                });
                suggestBox.appendChild(row);
            });
        },
        move: function (delta) {
            this.active = (this.active + delta + this.items.length) % this.items.length;
            this.render();
        },
        pick: function () {
            var c = this.items[this.active];
            if (!c || !this.field) return false;
            var field = this.field;
            field.input.value = "";
            field.add({ name: c.name, email: c.email });
            field.render();
            markDirty();
            this.close();
            field.input.focus();
            return true;
        },
        close: function () {
            suggestBox.hidden = true;
            this.field = null;
        },
    };

    document.querySelectorAll(".gc-chips").forEach(function (el) {
        fields.push(new ChipField(el));
    });
    [data.to, data.cc, data.bcc].forEach(function (value, i) {
        if (!value) return;
        splitAddresses(value).forEach(function (token) { fields[i].add(parseAddress(token)); });
        fields[i].render();
    });

    // 抄送 / 密送：有预填内容时直接展开，否则点击后展开。
    function revealField(name, focus) {
        var row = form.querySelector('[data-field="' + name + '"]');
        row.hidden = false;
        var btn = form.querySelector('[data-reveal="' + name + '"]');
        if (btn) btn.hidden = true;
        if (focus) row.querySelector(".gc-chip-input").focus();
    }
    if (data.cc) revealField("cc", false);
    if (data.bcc) revealField("bcc", false);
    form.querySelectorAll("[data-reveal]").forEach(function (btn) {
        btn.addEventListener("click", function () { revealField(btn.dataset.reveal, true); });
    });

    // ---------------- 正文编辑器 ----------------
    if (data.body_html) {
        editor.innerHTML = data.body_html;  // 服务端生成，内容已转义
    } else if (data.body_text) {
        editor.innerText = data.body_text;
    }
    if (data.is_reply) {
        // 回复时光标放在引用内容上方。
        editor.focus();
        var range = document.createRange();
        range.setStart(editor, 0);
        range.collapse(true);
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
    }
    editor.addEventListener("input", markDirty);
    plain.addEventListener("input", markDirty);
    form.querySelector(".gc-subject").addEventListener("input", markDirty);

    function isEditorEmpty() {
        return !editor.textContent.trim() && !editor.querySelector("img");
    }
    function syncPlaceholder() {
        editor.classList.toggle("is-empty", isEditorEmpty());
    }
    editor.addEventListener("input", syncPlaceholder);
    syncPlaceholder();

    function exec(cmd, value) {
        editor.focus();
        document.execCommand(cmd, false, value || null);
        markDirty();
        syncPlaceholder();
        updateFormatState();
    }

    function updateFormatState() {
        formatBar.querySelectorAll("[data-cmd]").forEach(function (btn) {
            var cmd = btn.dataset.cmd;
            if (cmd === "removeFormat" || cmd === "formatBlock") return;
            var on = false;
            try { on = document.queryCommandState(cmd); } catch (e) { on = false; }
            btn.classList.toggle("is-active", on);
        });
    }
    document.addEventListener("selectionchange", function () {
        if (document.activeElement === editor) updateFormatState();
    });

    formatBar.querySelectorAll("[data-cmd]").forEach(function (btn) {
        btn.addEventListener("mousedown", function (ev) { ev.preventDefault(); });  // 保留编辑器中的选区
        btn.addEventListener("click", function () { exec(btn.dataset.cmd, btn.dataset.value); });
    });
    document.getElementById("gc-color").addEventListener("input", function (ev) {
        exec("foreColor", ev.target.value);
    });
    formatToggle.addEventListener("click", function () {
        formatBar.hidden = !formatBar.hidden;
        formatToggle.classList.toggle("is-active", !formatBar.hidden);
        formatToggle.setAttribute("aria-pressed", String(!formatBar.hidden));
    });

    document.getElementById("gc-link").addEventListener("mousedown", function (ev) { ev.preventDefault(); });
    document.getElementById("gc-link").addEventListener("click", function () {
        if (plainToggle.checked) return;
        var url = window.prompt("链接地址", "https://");
        if (!url || url === "https://") return;
        if (!/^(https?:|mailto:)/i.test(url)) url = "https://" + url;
        var selection = window.getSelection();
        if (selection && selection.toString()) {
            exec("createLink", url);
        } else {
            exec("insertHTML", '<a href="' + url.replace(/"/g, "&quot;") + '">' +
                url.replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</a>");
        }
    });

    // 纯文本模式：正文切换为普通文本框，只发送纯文本。
    plainToggle.addEventListener("change", function () {
        if (plainToggle.checked) {
            plain.value = editor.innerText.replace(/\n{3,}/g, "\n\n");
            editor.hidden = true;
            plain.hidden = false;
            formatBar.hidden = true;
            formatToggle.disabled = true;
            plain.focus();
        } else {
            editor.innerText = plain.value;
            plain.hidden = true;
            editor.hidden = false;
            formatToggle.disabled = false;
            syncPlaceholder();
            editor.focus();
        }
    });

    // ---------------- 附件 ----------------
    var fileInput = document.getElementById("gc-files");
    var attachBox = document.getElementById("gc-attachments");
    var sizeWarning = document.getElementById("gc-size-warning");
    var MAX_TOTAL = 25 * 1024 * 1024;
    var files = [];
    var canEditFiles = typeof DataTransfer !== "undefined";

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
        return (bytes / 1024 / 1024).toFixed(1) + " MB";
    }

    function syncFiles() {
        if (canEditFiles) {
            var dt = new DataTransfer();
            files.forEach(function (f) { dt.items.add(f); });
            fileInput.files = dt.files;
        }
        attachBox.querySelectorAll(".gc-attachment:not(.is-fixed)").forEach(function (el) { el.remove(); });
        var total = 0;
        files.forEach(function (f, index) {
            total += f.size;
            var chip = document.createElement("span");
            chip.className = "gc-attachment";
            chip.title = f.name;
            chip.innerHTML = '<span class="ms" aria-hidden="true">attach_file</span>';
            var name = document.createElement("span");
            name.className = "gc-attachment-name";
            name.textContent = f.name;
            var meta = document.createElement("span");
            meta.className = "gc-attachment-meta";
            meta.textContent = formatSize(f.size);
            chip.appendChild(name);
            chip.appendChild(meta);
            if (canEditFiles) {
                var close = document.createElement("button");
                close.type = "button";
                close.className = "gc-chip-remove";
                close.setAttribute("aria-label", "移除附件 " + f.name);
                close.innerHTML = '<span class="ms" aria-hidden="true">close</span>';
                close.addEventListener("click", function () {
                    files.splice(index, 1);
                    syncFiles();
                });
                chip.appendChild(close);
            }
            attachBox.appendChild(chip);
        });
        sizeWarning.hidden = total <= MAX_TOTAL;
    }

    function addFiles(list) {
        if (!list || !list.length) return;
        if (!canEditFiles) {
            files = Array.prototype.slice.call(fileInput.files);
        } else {
            Array.prototype.forEach.call(list, function (f) { files.push(f); });
        }
        markDirty();
        syncFiles();
    }

    fileInput.addEventListener("change", function () {
        // 先复制出来：syncFiles 会重设 fileInput.files。
        var picked = Array.prototype.slice.call(fileInput.files);
        if (canEditFiles) {
            files = files.concat(picked);
            markDirty();
            syncFiles();
        } else {
            addFiles(picked);
        }
    });

    var dropOverlay = document.getElementById("gc-drop");
    var dragDepth = 0;
    function hasFiles(ev) {
        return ev.dataTransfer && Array.prototype.indexOf.call(ev.dataTransfer.types || [], "Files") !== -1;
    }
    form.addEventListener("dragenter", function (ev) {
        if (!hasFiles(ev) || !canEditFiles) return;
        ev.preventDefault();
        dragDepth++;
        dropOverlay.classList.add("is-visible");
    });
    form.addEventListener("dragover", function (ev) {
        if (hasFiles(ev) && canEditFiles) ev.preventDefault();
    });
    form.addEventListener("dragleave", function () {
        dragDepth = Math.max(0, dragDepth - 1);
        if (!dragDepth) dropOverlay.classList.remove("is-visible");
    });
    form.addEventListener("drop", function (ev) {
        if (!hasFiles(ev) || !canEditFiles) return;
        ev.preventDefault();
        dragDepth = 0;
        dropOverlay.classList.remove("is-visible");
        addFiles(ev.dataTransfer.files);
    });

    // ---------------- 发送 ----------------
    form.addEventListener("submit", function (ev) {
        if (submitting) {
            ev.preventDefault();
            return;
        }
        fields.forEach(function (f) { f.commit(); });
        var total = fields.reduce(function (n, f) { return n + f.addresses.length; }, 0);
        if (!total) {
            ev.preventDefault();
            showError("请至少填写一个收件人。");
            fields[0].input.focus();
            return;
        }
        if (fields.some(function (f) { return f.hasInvalid(); })) {
            ev.preventDefault();
            showError("有无效的邮箱地址（红色标记），请修改后再发送。");
            return;
        }
        if (plainToggle.checked) {
            document.getElementById("gc-body-text").value = plain.value;
            document.getElementById("gc-body-html").value = "";
        } else {
            document.getElementById("gc-body-text").value = editor.innerText;
            document.getElementById("gc-body-html").value =
                isEditorEmpty() ? "" : '<div dir="ltr">' + editor.innerHTML + "</div>";
        }
        submitting = true;
        sendBtn.disabled = true;
        sendBtn.querySelector(".gc-send-label").textContent = "正在发送…";
    });

    // Ctrl/Cmd + Enter 发送
    form.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
            ev.preventDefault();
            if (form.requestSubmit) form.requestSubmit(sendBtn); else sendBtn.click();
        }
    });

    // ---------------- 悬浮窗（embed）----------------
    // 在父页面的悬浮窗口（iframe）中打开时，标题栏按钮通知父页面最小化 / 全屏 / 关闭。
    var isEmbed = !!data.embed && window.parent && window.parent !== window;
    function tellParent(type) {
        if (isEmbed) window.parent.postMessage({ type: "gm:compose:" + type }, window.location.origin);
    }
    form.querySelectorAll("[data-win]").forEach(function (el) {
        el.addEventListener("click", function (ev) {
            if (ev.target.closest("[data-discard]") && el.tagName !== "BUTTON") return;
            ev.preventDefault();
            ev.stopPropagation();
            tellParent(el.getAttribute("data-win"));
        });
    });

    // 标题栏显示主题（与 Gmail 一致），未填写时显示「新邮件」/「回复」等
    var titleEl = document.getElementById("gc-title");
    var subjectInput = document.getElementById("gc-subject");
    if (titleEl && subjectInput) {
        subjectInput.addEventListener("input", function () {
            titleEl.textContent = subjectInput.value.trim() || titleEl.getAttribute("data-default");
        });
    }

    // 父页面告知窗口状态（normal / min / max），用于切换标题栏按钮图标
    window.addEventListener("message", function (ev) {
        if (ev.origin !== window.location.origin || !ev.data || ev.data.type !== "gm:compose:state") return;
        document.body.setAttribute("data-win-state", ev.data.state);
    });

    // 供父页面查询：关闭窗口 / 离开页面前是否需要确认
    window.GCCompose = {
        isDirty: function () { return dirty && !submitting; },
    };

    // 舍弃 / 离开页面前确认，避免误丢内容。
    form.querySelectorAll("[data-discard]").forEach(function (el) {
        el.addEventListener("click", function (ev) {
            ev.stopPropagation();
            if (dirty && !window.confirm("舍弃这封邮件？已填写的内容将不会保存。")) {
                ev.preventDefault();
                return;
            }
            dirty = false;
            if (isEmbed) {
                ev.preventDefault();
                tellParent("close");
            } else if (el.tagName === "BUTTON") {
                window.location.href = data.list_url || "/messages/";
            }
        });
    });
    window.addEventListener("beforeunload", function (ev) {
        if (dirty && !submitting && !isEmbed) {
            ev.preventDefault();
            ev.returnValue = "";
        }
    });
})();
