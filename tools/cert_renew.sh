#!/bin/sh
# HTTPS 证书续订（certbot / Let's Encrypt）。在宿主机上以 root 运行，不在容器里。
#
# 用法（服务器上，项目目录内）：
#   sh tools/cert_renew.sh install          安装定时任务（root 的 crontab），只需执行一次：
#                                           每分钟处理网页「设置 › 安全」里点的「立即续订」，
#                                           每天自动执行一次 certbot renew（剩余 30 天内才会真正续订）
#   sh tools/cert_renew.sh uninstall        删除定时任务
#   sh tools/cert_renew.sh renew [--force]  立即续订；--force 未到期也重新签发
#                                           （Let's Encrypt 限制同一证书每周最多签发 5 次）
#   sh tools/cert_renew.sh status           查看证书有效期和定时任务
#
# 续订成功后重新加载 nginx，网站才会用上新证书。可用环境变量调整（install 时会一并写进定时任务）：
#   CERTBOT_CMD       certbot 命令，默认 certbot
#   NGINX_RELOAD_CMD  重新加载 nginx 的命令；默认有名为 nginx 的容器时用
#                     docker exec nginx nginx -s reload，否则用 systemctl reload nginx
#
# 与网页的交互通过 data/cert/ 目录（已挂载进容器）：网页写 renew-request，本脚本执行后写
# renew-result.txt；agent-heartbeat 表示本脚本在运行。
set -eu

cd "$(dirname "$0")/.."
PROJECT=$(pwd)
CERT_DIR="$PROJECT/data/cert"
REQUEST="$CERT_DIR/renew-request"
RESULT="$CERT_DIR/renew-result.txt"
HEARTBEAT="$CERT_DIR/agent-heartbeat"
LOCK="$CERT_DIR/.renew.lock"
CERTBOT_CMD=${CERTBOT_CMD:-certbot}
CRON_MARK="tools/cert_renew.sh"

nginx_reload_cmd() {
    if [ -n "${NGINX_RELOAD_CMD:-}" ]; then
        echo "$NGINX_RELOAD_CMD"
    elif command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx nginx; then
        echo "docker exec nginx nginx -s reload"
    else
        echo "systemctl reload nginx"
    fi
}

prepare_dir() {
    mkdir -p "$CERT_DIR"
    # 目录要让容器里的应用（data/ 的所有者）可写，网页才能提交续订请求
    chown "$(stat -c %u:%g "$PROJECT/data")" "$CERT_DIR" 2>/dev/null || true
}

# 写结果：先写临时文件再改名，网页不会读到写了一半的内容
write_result() {  # status trigger forced log
    tmp="$RESULT.tmp"
    {
        echo "status=$1"
        echo "finished_at=$(date -Iseconds)"
        echo "trigger=$2"
        echo "forced=$3"
        echo "---"
        tail -c 4000 "$4"
    } > "$tmp"
    chmod 644 "$tmp"
    mv -f "$tmp" "$RESULT"
}

do_renew() {  # trigger(manual/auto) force(0/1)
    trigger=$1
    force=$2
    prepare_dir
    # 上次异常退出留下的锁：超过 30 分钟视为失效
    find "$LOCK" -maxdepth 0 -mmin +30 -exec rmdir {} \; 2>/dev/null || true
    if ! mkdir "$LOCK" 2>/dev/null; then
        echo "已有续订在进行中" >&2
        return 1
    fi
    log=$(mktemp)
    trap 'rmdir "$LOCK" 2>/dev/null; rm -f "$log"' EXIT

    if [ "$force" = 1 ]; then
        set -- renew --non-interactive --force-renewal
    else
        set -- renew --non-interactive
    fi
    echo "==> $CERTBOT_CMD $*" > "$log"
    rc=0
    # CERTBOT_CMD 可能带参数（如 docker run ...），故意不加引号
    $CERTBOT_CMD "$@" >> "$log" 2>&1 || rc=$?
    if [ "$rc" -eq 0 ]; then
        reload=$(nginx_reload_cmd)
        echo "==> $reload" >> "$log"
        sh -c "$reload" >> "$log" 2>&1 || rc=$?
    fi

    if [ "$rc" -ne 0 ]; then
        status=failed
    elif grep -q "No renewals were attempted" "$log"; then
        status=not_due
    else
        status=renewed
    fi
    cat "$log"
    write_result "$status" "$trigger" "$force" "$log"
    [ "$rc" -eq 0 ]
}

# 定时任务每分钟调用：更新心跳，有网页提交的续订请求时执行
watch() {
    prepare_dir
    touch "$HEARTBEAT"
    [ -f "$REQUEST" ] || return 0
    force=0
    grep -qx "force=1" "$REQUEST" && force=1
    rm -f "$REQUEST"
    do_renew manual "$force"
}

install() {
    if [ "$(id -u)" != 0 ]; then
        echo "请用 root 执行（certbot 和重新加载 nginx 都需要 root）" >&2
        exit 1
    fi
    if ! command -v "${CERTBOT_CMD%% *}" >/dev/null 2>&1; then
        echo "找不到 certbot 命令：$CERTBOT_CMD（可用 CERTBOT_CMD=... 指定）" >&2
        exit 1
    fi
    # cron 的 PATH 很短（snap 安装的 certbot 在 /snap/bin），沿用当前 shell 的 PATH
    env_part="PATH='$PATH' "
    [ "$CERTBOT_CMD" = certbot ] || env_part="${env_part}CERTBOT_CMD='$CERTBOT_CMD' "
    [ -z "${NGINX_RELOAD_CMD:-}" ] || env_part="${env_part}NGINX_RELOAD_CMD='$NGINX_RELOAD_CMD' "
    run="cd '$PROJECT' && ${env_part}sh $CRON_MARK"
    {
        crontab -l 2>/dev/null | grep -vF "$CRON_MARK" || true
        echo "* * * * * $run watch >/dev/null 2>&1"
        echo "23 3 * * * $run auto >/dev/null 2>&1"
    } | crontab -
    watch
    echo "已安装定时任务："
    crontab -l | grep -F "$CRON_MARK"
    echo "续订成功后执行：$(nginx_reload_cmd)"
    echo "现在可以在网页「设置 › 安全」中查看证书状态、点「立即续订」。"
}

uninstall() {
    { crontab -l 2>/dev/null | grep -vF "$CRON_MARK" || true; } | crontab -
    rm -f "$HEARTBEAT"
    echo "已删除定时任务"
}

case "${1:-}" in
    install) install ;;
    uninstall) uninstall ;;
    watch) watch ;;
    auto) do_renew auto 0 ;;
    renew)
        if [ "${2:-}" = "--force" ]; then do_renew manual 1; else do_renew manual 0; fi
        ;;
    status)
        $CERTBOT_CMD certificates
        echo
        crontab -l 2>/dev/null | grep -F "$CRON_MARK" || echo "未安装定时任务（sh tools/cert_renew.sh install）"
        ;;
    *)
        sed -n '2,20p' "$0"
        exit 1
        ;;
esac
