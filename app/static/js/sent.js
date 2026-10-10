(function () {
    document.querySelectorAll(".sent-row[data-href]").forEach(function (row) {
        row.addEventListener("click", function (ev) {
            if (ev.target.closest("a, button, form, input")) return;
            window.location = row.dataset.href;
        });
    });
})();
