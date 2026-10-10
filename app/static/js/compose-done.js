// 悬浮写邮件窗口发送成功后的落地页：交给父页面关闭窗口并弹出「邮件已发送」提示；
// 若不是在悬浮窗里打开（直接访问），则跳到发件记录。
(function () {
    var el = document.getElementById("gc-done");
    var url = el ? el.getAttribute("data-sent-url") : "/compose/sent";
    if (window.parent && window.parent !== window) {
        window.parent.postMessage({ type: "gm:compose:sent", url: url }, window.location.origin);
    } else {
        window.location.replace(url);
    }
})();
