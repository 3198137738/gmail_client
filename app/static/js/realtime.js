// Single shared SSE connection: drives the top-right "实时" status light and
// re-broadcasts mailbox events as window events that page scripts can consume.
(function () {
    var rt = document.getElementById("rcm-rt");
    function setState(state, title) {
        if (!rt) return;
        rt.dataset.state = state;
        if (title) rt.title = title;
    }

    if (!window.EventSource) {
        setState("offline", "浏览器不支持实时推送");
        return;
    }

    var firstOpen = true;
    var es = new EventSource("/events/stream");
    window.RCM = window.RCM || {};
    window.RCM.sse = es;

    es.onopen = function () {
        setState("connected", "实时已连接");
        window.dispatchEvent(new CustomEvent("rcm:sseopen", { detail: { first: firstOpen } }));
        firstOpen = false;
    };
    es.onerror = function () {
        // EventSource reconnects automatically; reflect the transient state.
        setState("connecting", "实时连接已断开，正在重连…");
    };
    es.onmessage = function (e) {
        var data;
        try { data = JSON.parse(e.data); } catch (err) { return; }
        if (data && data.type === "new_mail") {
            window.dispatchEvent(new CustomEvent("rcm:newmail", { detail: data }));
        } else if (data && data.type === "mail_changed") {
            // 在手机 / Gmail 网页上读过、删除或归档的邮件已同步到本地
            window.dispatchEvent(new CustomEvent("rcm:mailchanged", { detail: data }));
        } else if (data && data.type === "sync_done") {
            window.dispatchEvent(new CustomEvent("rcm:syncdone", { detail: data }));
        }
    };

    window.addEventListener("beforeunload", function () {
        try { es.close(); } catch (err) {}
    });
})();
