// 账号管理页：Bootstrap 弹出提示、批量删除的全选/计数。
document.querySelectorAll('[data-bs-toggle="popover"]').forEach(function (el) {
    new bootstrap.Popover(el, { html: false, sanitize: true });
});

(function () {
    var selectAll = document.getElementById("select-all-accounts");
    var checkboxes = Array.prototype.slice.call(document.querySelectorAll(".account-checkbox"));
    var deleteBtn = document.getElementById("batch-delete-btn");
    var countEl = document.getElementById("batch-delete-count");

    function refresh() {
        var checked = checkboxes.filter(function (c) { return c.checked; });
        if (countEl) countEl.textContent = checked.length;
        if (deleteBtn) deleteBtn.disabled = checked.length === 0;
        if (selectAll) {
            selectAll.checked = checkboxes.length > 0 && checked.length === checkboxes.length;
            selectAll.indeterminate = checked.length > 0 && checked.length < checkboxes.length;
        }
    }

    if (selectAll) {
        selectAll.addEventListener("change", function () {
            checkboxes.forEach(function (c) { c.checked = selectAll.checked; });
            refresh();
        });
    }
    checkboxes.forEach(function (c) { c.addEventListener("change", refresh); });
    refresh();
})();
