#!/bin/sh
# HTTPS 证书续订（Let's Encrypt）。在宿主机上以 root 运行，不在容器里。
#
# 用法（服务器上，项目目录内）：
#   sh tools/cert_renew.sh install          安装定时任务（root 的 crontab），只需执行一次：
#                                           每分钟处理网页「设置 › 安全」里点的「立即续订」，
#                                           每天自动检查一次，剩余 30 天以内才真正续订
#   sh tools/cert_renew.sh uninstall        删除定时任务
#   sh tools/cert_renew.sh renew [--force]  立即续订；--force 未到期也重新签发
#                                           （Let's Encrypt 限制同一证书每周最多签发 5 次）
#   sh tools/cert_renew.sh status           查看证书剩余天数和定时任务
#
# 两种环境（自动判断，也可用 CERT_MODE=web / certbot 指定）：
#   web      「科技lion」脚本搭的环境：nginx 容器使用 /home/web/certs/<域名>_cert.pem / _key.pem，
#            宿主机没有安装 certbot。用 docker 运行 certbot/certbot 签发：nginx 配置里有
#            letsencrypt 验证目录时用 webroot（不中断网站），否则暂停 nginx 用 standalone；
#            签发成功后复制到 /home/web/certs 并重新加载 nginx。
#   certbot  宿主机安装了 certbot：执行 certbot renew 后重新加载 nginx。
#
# 可用环境变量（install 时会一并写进定时任务）：
#   RENEW_DAYS        自动续订的剩余天数阈值，默认 30
#   web 模式：WEB_CERTS_DIR（/home/web/certs）、WEB_ACME_DIR（/home/web/letsencrypt）、
#             LETSENCRYPT_DIR（/etc/letsencrypt）、NGINX_CONTAINER（nginx）、CERTBOT_IMAGE（certbot/certbot）
#   certbot 模式：CERTBOT_CMD（certbot）、NGINX_RELOAD_CMD（默认有 nginx 容器时
#             docker exec nginx nginx -s reload，否则 systemctl reload nginx）
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
CRON_MARK="tools/cert_renew.sh"

RENEW_DAYS=${RENEW_DAYS:-30}
CERTBOT_CMD=${CERTBOT_CMD:-certbot}
WEB_CERTS_DIR=${WEB_CERTS_DIR:-/home/web/certs}
WEB_ACME_DIR=${WEB_ACME_DIR:-/home/web/letsencrypt}
LETSENCRYPT_DIR=${LETSENCRYPT_DIR:-/etc/letsencrypt}
NGINX_CONTAINER=${NGINX_CONTAINER:-nginx}
CERTBOT_IMAGE=${CERTBOT_IMAGE:-certbot/certbot}

has_nginx_container() {
    command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$NGINX_CONTAINER"
}

detect_mode() {
    if [ -n "${CERT_MODE:-}" ]; then
        echo "$CERT_MODE"
    elif [ -d "$WEB_CERTS_DIR" ] && ! command -v "${CERTBOT_CMD%% *}" >/dev/null 2>&1 && has_nginx_container; then
        echo web
    else
        echo certbot
    fi
}

nginx_reload_cmd() {
    if [ -n "${NGINX_RELOAD_CMD:-}" ]; then
        echo "$NGINX_RELOAD_CMD"
    elif has_nginx_container; then
        echo "docker exec $NGINX_CONTAINER nginx -s reload"
    else
        echo "systemctl reload nginx"
    fi
}

prepare_dir() {
    mkdir -p "$CERT_DIR"
    # 目录要让容器里的应用（data/ 的所有者）可写，网页才能提交续订请求
    chown "$(stat -c %u:%g "$PROJECT/data")" "$CERT_DIR" 2>/dev/null || true
}

# 证书到期时间（时间戳）和剩余天数
end_ts() {
    date -d "$(openssl x509 -enddate -noout -in "$1" | cut -d= -f2-)" +%s
}

days_left() {
    echo $(( ($(end_ts "$1") - $(date +%s)) / 86400 ))
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

# ---- web 模式 ----------------------------------------------------------------------
# 续订一个域名。返回 0 已续订、1 失败、2 未到期跳过
web_renew_domain() {  # domain force
    d=$1
    current="$WEB_CERTS_DIR/${d}_cert.pem"
    left=$(days_left "$current")
    if [ "$2" != 1 ] && [ "$left" -gt "$RENEW_DAYS" ]; then
        echo "$d：还有 $left 天到期，无需续订（剩余 $RENEW_DAYS 天以内才续订）"
        return 2
    fi
    echo "$d：还有 $left 天到期，开始续订"
    # 不删除旧证书：签发失败时原来的证书仍然可用
    set -- certonly --non-interactive --agree-tos --no-eff-email --register-unsafely-without-email \
        --key-type ecdsa --force-renewal --cert-name "$d" -d "$d"
    if docker exec "$NGINX_CONTAINER" test -d /var/www/letsencrypt 2>/dev/null \
        && docker exec "$NGINX_CONTAINER" grep -q letsencrypt "/etc/nginx/conf.d/$d.conf" 2>/dev/null; then
        echo "==> webroot 方式签发（网站不中断）"
        docker run --rm -v "$LETSENCRYPT_DIR:/etc/letsencrypt" -v "$WEB_ACME_DIR:/var/www/letsencrypt" \
            "$CERTBOT_IMAGE" "$@" --webroot -w /var/www/letsencrypt || return 1
    else
        echo "==> standalone 方式签发（nginx 配置里没有 letsencrypt 验证目录，暂停 nginx 释放 80 端口，网站中断约半分钟）"
        docker stop "$NGINX_CONTAINER" >/dev/null
        rc=0
        docker run --rm -p 80:80 -v "$LETSENCRYPT_DIR:/etc/letsencrypt" "$CERTBOT_IMAGE" "$@" --standalone || rc=$?
        docker start "$NGINX_CONTAINER" >/dev/null
        [ "$rc" -eq 0 ] || return 1
    fi
    issued="$LETSENCRYPT_DIR/live/$d"
    if [ ! -f "$issued/fullchain.pem" ] || [ ! -f "$issued/privkey.pem" ]; then
        echo "签发后找不到 $issued/fullchain.pem"
        return 1
    fi
    new_left=$(days_left "$issued/fullchain.pem")
    if [ "$(end_ts "$issued/fullchain.pem")" -le "$(end_ts "$current")" ]; then
        echo "签发出的证书（剩 $new_left 天）并不比现有的新，不替换"
        return 1
    fi
    cp -p "$current" "$current.bak"
    cp -p "$WEB_CERTS_DIR/${d}_key.pem" "$WEB_CERTS_DIR/${d}_key.pem.bak"
    cp "$issued/fullchain.pem" "$current"
    cp "$issued/privkey.pem" "$WEB_CERTS_DIR/${d}_key.pem"
    chmod 600 "$WEB_CERTS_DIR/${d}_key.pem"
    echo "$d：已更新为新证书，还有 $new_left 天到期（旧证书备份为 ${d}_cert.pem.bak）"
    return 0
}

web_renew() {  # force log
    renewed=0
    failed=0
    for cert in "$WEB_CERTS_DIR"/*_cert.pem; do
        [ -f "$cert" ] || continue
        r=0
        web_renew_domain "$(basename "$cert" _cert.pem)" "$1" >> "$2" 2>&1 || r=$?
        case $r in
            0) renewed=1 ;;
            2) ;;
            *) failed=1 ;;
        esac
    done
    if [ "$renewed" = 1 ]; then
        echo "==> 检查配置并重新加载 nginx" >> "$2"
        { docker exec "$NGINX_CONTAINER" nginx -t && docker exec "$NGINX_CONTAINER" nginx -s reload; } >> "$2" 2>&1 || failed=1
    fi
    if [ "$failed" = 1 ]; then
        echo failed
    elif [ "$renewed" = 1 ]; then
        echo renewed
    else
        echo not_due
    fi
}

# ---- certbot 模式 ------------------------------------------------------------------
certbot_renew() {  # force log
    if [ "$1" = 1 ]; then
        set -- "$2" renew --non-interactive --force-renewal
    else
        set -- "$2" renew --non-interactive
    fi
    log=$1
    shift
    echo "==> $CERTBOT_CMD $*" >> "$log"
    rc=0
    # CERTBOT_CMD 可能带参数（如 docker run ...），故意不加引号
    $CERTBOT_CMD "$@" >> "$log" 2>&1 || rc=$?
    if [ "$rc" -eq 0 ]; then
        reload=$(nginx_reload_cmd)
        echo "==> $reload" >> "$log"
        sh -c "$reload" >> "$log" 2>&1 || rc=$?
    fi
    if [ "$rc" -ne 0 ]; then
        echo failed
    elif grep -q "No renewals were attempted" "$log"; then
        echo not_due
    else
        echo renewed
    fi
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

    mode=$(detect_mode)
    echo "==> $(date '+%F %T') $mode 模式续订$([ "$force" = 1 ] && echo "（强制）")" > "$log"
    if [ "$mode" = web ]; then
        status=$(web_renew "$force" "$log")
    else
        status=$(certbot_renew "$force" "$log")
    fi
    cat "$log"
    write_result "$status" "$trigger" "$force" "$log"
    [ "$status" != failed ]
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
        echo "请用 root 执行（签发证书和重新加载 nginx 都需要 root）" >&2
        exit 1
    fi
    mode=$(detect_mode)
    if [ "$mode" = web ]; then
        if ! command -v openssl >/dev/null 2>&1; then
            echo "找不到 openssl 命令" >&2
            exit 1
        fi
        echo "web 模式：证书目录 $WEB_CERTS_DIR，用 docker 运行 $CERTBOT_IMAGE 签发"
        docker pull -q "$CERTBOT_IMAGE" >/dev/null || echo "（暂时拉取不到 $CERTBOT_IMAGE，续订时会再试）"
    elif ! command -v "${CERTBOT_CMD%% *}" >/dev/null 2>&1; then
        echo "找不到 certbot 命令：$CERTBOT_CMD（可用 CERTBOT_CMD=... 指定）" >&2
        exit 1
    else
        echo "certbot 模式：续订后执行 $(nginx_reload_cmd)"
    fi
    # cron 的 PATH 很短（snap 安装的 certbot 在 /snap/bin），沿用当前 shell 的 PATH；
    # 执行 install 时指定过的变量一并写进定时任务
    env_part="PATH='$PATH' "
    for name in CERT_MODE RENEW_DAYS CERTBOT_CMD NGINX_RELOAD_CMD WEB_CERTS_DIR WEB_ACME_DIR LETSENCRYPT_DIR NGINX_CONTAINER CERTBOT_IMAGE; do
        if value=$(printenv "$name"); then
            env_part="${env_part}$name='$value' "
        fi
    done
    run="cd '$PROJECT' && ${env_part}sh $CRON_MARK"
    {
        crontab -l 2>/dev/null | grep -vF "$CRON_MARK" || true
        echo "* * * * * $run watch >/dev/null 2>&1"
        echo "23 3 * * * $run auto >/dev/null 2>&1"
    } | crontab -
    watch
    echo "已安装定时任务："
    crontab -l | grep -F "$CRON_MARK"
    echo
    status
    echo
    echo "现在可以在网页「设置 › 安全」中查看证书状态、点「立即续订」。"
}

uninstall() {
    { crontab -l 2>/dev/null | grep -vF "$CRON_MARK" || true; } | crontab -
    rm -f "$HEARTBEAT"
    echo "已删除定时任务"
}

status() {
    if [ "$(detect_mode)" = web ]; then
        for cert in "$WEB_CERTS_DIR"/*_cert.pem; do
            [ -f "$cert" ] || continue
            echo "$(basename "$cert" _cert.pem)：还有 $(days_left "$cert") 天到期"
        done
    else
        $CERTBOT_CMD certificates
    fi
    crontab -l 2>/dev/null | grep -qF "$CRON_MARK" && echo "定时任务：已安装" || echo "定时任务：未安装（sh tools/cert_renew.sh install）"
}

case "${1:-}" in
    install) install ;;
    uninstall) uninstall ;;
    watch) watch ;;
    auto) do_renew auto 0 ;;
    renew)
        if [ "${2:-}" = "--force" ]; then do_renew manual 1; else do_renew manual 0; fi
        ;;
    status) status ;;
    *)
        sed -n '2,30p' "$0"
        exit 1
        ;;
esac
