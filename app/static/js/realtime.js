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

    // 浏览器只在网络断开时自动重连；服务器返回错误（会话过期 401/303、部署期间 502）时
    // EventSource 会彻底关闭，需要自己退避重建，否则状态灯一直停在「正在重连」。
    var RETRY_MIN_MS = 5000;
    var RETRY_MAX_MS = 60000;
    var retryMs = RETRY_MIN_MS;
    var retryTimer = null;
    var firstOpen = true;
    var es = null;
    window.RCM = window.RCM || {};

    function onMessage(e) {
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
    }

    function scheduleReconnect() {
        if (retryTimer) return;
        setState("offline", "实时连接已断开，" + Math.round(retryMs / 1000) + " 秒后重试…");
        retryTimer = setTimeout(function () {
            retryTimer = null;
            connect();
        }, retryMs);
        retryMs = Math.min(retryMs * 2, RETRY_MAX_MS);
    }

    function connect() {
        if (es) {
            try { es.close(); } catch (err) {}
        }
        es = new EventSource("/events/stream");
        window.RCM.sse = es;
        es.onopen = function () {
            retryMs = RETRY_MIN_MS;
            setState("connected", "实时已连接");
            window.dispatchEvent(new CustomEvent("rcm:sseopen", { detail: { first: firstOpen } }));
            firstOpen = false;
        };
        es.onerror = function () {
            if (es.readyState === EventSource.CLOSED) {
                scheduleReconnect();
            } else {
                // 网络中断：EventSource 会自动重连
                setState("connecting", "实时连接已断开，正在重连…");
            }
        };
        es.onmessage = onMessage;
    }

    function disconnect() {
        clearTimeout(retryTimer);
        retryTimer = null;
        if (es) {
            try { es.close(); } catch (err) {}
        }
    }

    connect();

    // 用 pagehide / pageshow 而不是 beforeunload：beforeunload 会让页面无法进入往返缓存，
    // 且从缓存恢复时已关闭的连接不会重开。
    window.addEventListener("pagehide", disconnect);
    window.addEventListener("pageshow", function (e) {
        if (e.persisted) {
            retryMs = RETRY_MIN_MS;
            connect();
        }
    });
})();
