"""Application configuration."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
import secrets
from ipaddress import ip_network
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# 测试时可通过环境变量把 .env 和数据目录指向临时位置，避免读写真实数据。
ENV_FILE_PATH = Path(os.getenv("APP_ENV_FILE") or (BASE_DIR / ".env"))
DATA_ROOT = Path(os.getenv("APP_DATA_ROOT") or BASE_DIR)


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs from ``.env`` without overriding real env."""
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value


_load_dotenv(ENV_FILE_PATH)

DATA_DIR = DATA_ROOT / "data"
ATTACHMENTS_DIR = DATA_ROOT / "attachments"
SECRETS_DIR = DATA_ROOT / "secrets"
LOGS_DIR = DATA_ROOT / "logs"
IMAGE_CACHE_DIR = DATA_ROOT / "data" / "image-cache"
BACKUPS_DIR = DATA_ROOT / "backups"
STATIC_DIR = BASE_DIR / "app" / "static"

for _d in (DATA_DIR, ATTACHMENTS_DIR, SECRETS_DIR, LOGS_DIR, BACKUPS_DIR, IMAGE_CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{(DATA_DIR / 'gmail_client.db').as_posix()}"

FERNET_KEY_PATH = SECRETS_DIR / "fernet.key"

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.gmail.com"
SMTP_SSL_PORT = 465
SMTP_STARTTLS_PORT = 587
# Outlook（Outlook.com / Hotmail / Microsoft 365）：只能用 Microsoft 账号授权（OAuth2）登录，
# SMTP 只提供 587 端口（STARTTLS）
OUTLOOK_IMAP_HOST = "outlook.office365.com"
OUTLOOK_SMTP_HOST = "smtp.office365.com"
IMAP_TIMEOUT_SEC = 30
SMTP_TIMEOUT_SEC = 30

ATTACHMENTS_URL_PREFIX = "/attachments"

ALLOWED_TAGS = {
    "p", "br", "div", "span",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col",
    "a", "img",
    "strong", "em", "b", "i", "u", "s", "small", "sub", "sup", "font", "center",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "hr",
}

# 不再允许 class / id：邮件自带的 <style> 会被整体删除，class 没有用处，
# 反而可能被用来套用本站样式伪造界面；id 可能造成 DOM clobbering。
# <a> 的 rel 由清洗器统一设置为 noopener noreferrer，不能出现在这里。
ALLOWED_ATTRIBUTES = {
    "*": {"style", "title", "align", "dir"},
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan", "valign", "width", "height", "bgcolor"},
    "th": {"colspan", "rowspan", "valign", "width", "height", "bgcolor"},
    "table": {"border", "cellpadding", "cellspacing", "width", "bgcolor"},
    "col": {"span", "width"},
    "colgroup": {"span", "width"},
    "font": {"color", "face", "size"},
}

ALLOWED_PROTOCOLS = {"http", "https", "data", "mailto"}


def _get_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _get_csv_set(name: str) -> set[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def _get_proxy_networks(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    # 未单独配置时沿用 uvicorn 的 FORWARDED_ALLOW_IPS，保证两边信任同一批代理
    raw = (os.getenv(name, "") or os.getenv("FORWARDED_ALLOW_IPS", "")).strip()
    values = [item.strip() for item in raw.split(",") if item.strip()] if raw else list(default)
    valid: list[str] = []
    for item in values:
        try:
            ip_network(item, strict=False)
        except ValueError:
            continue
        valid.append(item)
    return tuple(valid)


DEFAULT_FOLDER = "INBOX"
INITIAL_SYNC_LIMIT = _get_positive_int("INITIAL_SYNC_LIMIT", 100)
SYNC_INTERVAL_MINUTES = _get_positive_int("SYNC_INTERVAL_MINUTES", 5)
IMAP_FETCH_BATCH_SIZE = _get_positive_int("IMAP_FETCH_BATCH_SIZE", 50)
# 同步全部历史邮件：首次同步先取最新 INITIAL_SYNC_LIMIT 封，之后在后台按
# BACKFILL_BATCH_SIZE 一批批回填更早的邮件，直到全部同步完（不发送通知）。
SYNC_FULL_HISTORY = _get_bool("SYNC_FULL_HISTORY", True)
BACKFILL_BATCH_SIZE = _get_positive_int("BACKFILL_BATCH_SIZE", 200)
# 每批之间的间隔（秒），避免长时间占满 IMAP 带宽
BACKFILL_PAUSE_SEC = _get_positive_int("BACKFILL_PAUSE_SEC", 1)
# 同时回填的账号数（独立线程池，不占用常规同步的后台线程）
BACKFILL_MAX_CONCURRENT = _get_positive_int("BACKFILL_MAX_CONCURRENT", 2)
# 每批下载的邮件总大小上限（MB），避免大附件邮件导致内存峰值过高
IMAP_FETCH_MAX_MB = _get_positive_int("IMAP_FETCH_MAX_MB", 20)
IMAP_FETCH_MAX_BYTES = IMAP_FETCH_MAX_MB * 1024 * 1024
BACKGROUND_MAX_WORKERS = _get_positive_int("BACKGROUND_MAX_WORKERS", 4)
SYNC_SENT_ENABLED = _get_bool("SYNC_SENT_ENABLED", True)
# 同步垃圾邮件文件夹（带 \Junk 标记的文件夹）。默认关闭；垃圾邮件不发送 Telegram 通知。
SYNC_JUNK_ENABLED = _get_bool("SYNC_JUNK_ENABLED", False)
# 同步「已删除邮件」文件夹（带 \Trash 标记的文件夹），左侧显示「已删除」。默认开启；不发送通知。
SYNC_TRASH_ENABLED = _get_bool("SYNC_TRASH_ENABLED", True)

# 外部图片代理：邮件里的外部图片改由服务器下载后再显示，浏览器不直接连对方服务器，
# 追踪像素拿不到你的 IP；新邮件同步时就预先下载（见 IMAGE_PROXY_PREFETCH），
# 对方看到的「打开时间」只是收信时间。关闭后恢复浏览器直接加载。
IMAGE_PROXY_ENABLED = _get_bool("IMAGE_PROXY_ENABLED", True)
# 新邮件（收件箱，不含垃圾邮件）同步入库时预先下载其中的外部图片
IMAGE_PROXY_PREFETCH = _get_bool("IMAGE_PROXY_PREFETCH", True)
IMAGE_PROXY_MAX_MB = _get_positive_int("IMAGE_PROXY_MAX_MB", 10)
IMAGE_PROXY_TIMEOUT_SEC = _get_positive_int("IMAGE_PROXY_TIMEOUT_SEC", 10)
# 图片缓存总大小上限与保留天数，每天清理一次
IMAGE_CACHE_MAX_MB = _get_positive_int("IMAGE_CACHE_MAX_MB", 500)
IMAGE_CACHE_DAYS = _get_positive_int("IMAGE_CACHE_DAYS", 30)

# 打开邮件时自动加载外部图片。关闭后外部图片默认被拦截（防止跟踪像素获知
# 打开时间和 IP），需要在邮件中点「显示图片」才加载。
MAIL_LOAD_REMOTE_IMAGES = _get_bool("MAIL_LOAD_REMOTE_IMAGES", True)

# Real-time delivery via IMAP IDLE + SSE.
# When enabled, a long-lived IMAP connection is kept per account/folder so new
# mail triggers an incremental sync within seconds, and the change is pushed to
# the browser over Server-Sent Events (no manual refresh needed).
REALTIME_ENABLED = _get_bool("REALTIME_ENABLED", True)
# 撤销发送：点击发送后等待几秒再真正提交，期间可以撤销（与 Gmail 一致，0 表示关闭，最长 30 秒）
try:
    UNDO_SEND_SECONDS = max(0, min(30, int(os.getenv("UNDO_SEND_SECONDS", "5"))))
except ValueError:
    UNDO_SEND_SECONDS = 5
# How long a single IDLE wait blocks before we loop and re-check the stop flag.
# Keep well under the IMAP socket timeout used for IDLE connections.
IDLE_POLL_SEC = _get_positive_int("IDLE_POLL_SEC", 30)
# 每隔多少秒退出 IDLE 做一次兜底检查（UID SEARCH）后重新进入 IDLE。
# 既能补上 Gmail 偶尔漏发的新邮件通知，也能尽快发现已失效的连接；
# 远小于 Gmail 断开空闲连接的时间（约 10-30 分钟），同时起到保活作用。
IDLE_REFRESH_SEC = _get_positive_int("IDLE_REFRESH_SEC", 60)
# Backoff after an IDLE connection error before reconnecting.
IDLE_RECONNECT_BACKOFF_SEC = _get_positive_int("IDLE_RECONNECT_BACKOFF_SEC", 30)

# --- Telegram new-mail notifications ---------------------------------------
# When a bot token + chat id are set, newly synced inbox mail triggers a push
# notification to the configured Telegram chat. Notifications are best-effort:
# failures are logged but never affect mail sync.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
# Comma-separated list of chat ids (a private chat, group or channel). All
# configured chats receive notifications, and only they may control the bot.
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_CHAT_IDS = tuple(
    c.strip() for c in TELEGRAM_CHAT_ID.split(",") if c.strip()
)
TELEGRAM_NOTIFY_ENABLED = _get_bool(
    "TELEGRAM_NOTIFY_ENABLED", bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS)
)
# Also notify when new messages appear in the Sent folder (off by default).
TELEGRAM_NOTIFY_SENT = _get_bool("TELEGRAM_NOTIFY_SENT", False)
# 垃圾邮件文件夹收到新邮件时也推送通知（需同时开启 SYNC_JUNK_ENABLED）。
TELEGRAM_NOTIFY_JUNK = _get_bool("TELEGRAM_NOTIFY_JUNK", True)
TELEGRAM_API_TIMEOUT_SEC = _get_positive_int("TELEGRAM_API_TIMEOUT_SEC", 10)
# Run a long-polling listener so the bot reacts to inline buttons (一键已读/
# 删除) and commands (/unread, /help). Disable to make notifications one-way.
TELEGRAM_BOT_COMMANDS_ENABLED = _get_bool("TELEGRAM_BOT_COMMANDS_ENABLED", True)
# Long-poll wait passed to getUpdates; the listener loops on this interval.
TELEGRAM_POLL_TIMEOUT_SEC = _get_positive_int("TELEGRAM_POLL_TIMEOUT_SEC", 50)

WEB_AUTH_PASSWORD = os.getenv("WEB_AUTH_PASSWORD", "").strip()
WEB_AUTH_ENABLED = bool(WEB_AUTH_PASSWORD)
# 没有设置访问密码时默认拒绝启动（.env 丢失或变量名写错时不能把整个邮箱公开出去）。
# 只在确实不需要密码的环境（例如仅本机访问）显式设置 ALLOW_NO_AUTH=true。
ALLOW_NO_AUTH = _get_bool("ALLOW_NO_AUTH", False)
# 手机丢失、无法生成验证码时的应急开关：设为 true 并重启后忽略两步验证（只用密码登录），
# 登录后到「设置 → 安全」重新绑定，再删掉此变量。
WEB_AUTH_2FA_RESET = _get_bool("WEB_AUTH_2FA_RESET", False)
# 用 Google 账号授权（OAuth2）添加 Gmail 账号，替代应用专用密码。
# 在 Google Cloud Console 创建「Web 应用」类型的 OAuth 客户端，已获授权的重定向 URI 填
# https://<你的域名>/accounts/oauth/callback
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
# 用 Microsoft 账号授权（OAuth2）添加 Outlook 邮箱（Outlook.com / Hotmail / Microsoft 365）。
# 在 Azure 门户「应用注册」中新建应用：支持的账户类型选「任何组织目录中的帐户和个人 Microsoft 帐户」，
# 平台选「Web」，重定向 URI 填 https://<你的域名>/accounts/oauth/microsoft/callback；
# 在「证书和密码」中新建客户端密码。API 权限无需手动添加（授权时按需申请）。
MICROSOFT_OAUTH_CLIENT_ID = os.getenv("MICROSOFT_OAUTH_CLIENT_ID", "").strip()
MICROSOFT_OAUTH_CLIENT_SECRET = os.getenv("MICROSOFT_OAUTH_CLIENT_SECRET", "").strip()
# 租户：common（个人账号和工作/学校账号都可以）、consumers（只允许个人账号）、organizations 或租户 ID
MICROSOFT_OAUTH_TENANT = os.getenv("MICROSOFT_OAUTH_TENANT", "").strip() or "common"
WEB_AUTH_MAX_FAILURES = _get_positive_int("WEB_AUTH_MAX_FAILURES", 3)
WEB_AUTH_LOCKOUT_MINUTES = _get_positive_int("WEB_AUTH_LOCKOUT_MINUTES", 30)
WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES = _get_bool(
    "WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES",
    False,
)
WEB_AUTH_BLOCKED_IPS = _get_csv_set("WEB_AUTH_BLOCKED_IPS")
# 全局限速：所有来源合计，WEB_AUTH_GLOBAL_WINDOW_MINUTES 分钟内密码错误达到这个次数后，
# 只允许以前登录成功过的 IP（IPv6 按 /64 网段）继续尝试。防止攻击者轮换大量 IP
# 绕过按 IP 的封禁（一个 IPv6 用户就有整段 /64 地址可用）。
WEB_AUTH_GLOBAL_MAX_FAILURES = _get_positive_int("WEB_AUTH_GLOBAL_MAX_FAILURES", 10)
WEB_AUTH_GLOBAL_WINDOW_MINUTES = _get_positive_int("WEB_AUTH_GLOBAL_WINDOW_MINUTES", 60)
TRUST_PROXY_HEADERS = _get_bool("TRUST_PROXY_HEADERS", True)
# 只有来自这些网段的直连请求，才会采信其 X-Forwarded-For / X-Real-IP。
# 默认包含本机和私有网段（nginx 容器通过 Docker 内网转发过来）；公网地址永远不被信任。
TRUSTED_PROXY_NETWORKS = _get_proxy_networks(
    "TRUSTED_PROXY_NETWORKS",
    (
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fc00::/7",
    ),
)
# --- 自动备份 -----------------------------------------------------------------
# 每天定时把数据库快照 + secrets/（含解密账号密码所需的 fernet.key）打包到
# backups/，只保留最近 BACKUP_KEEP 份。备份里有密钥，请妥善保管并定期拷到异地。
BACKUP_ENABLED = _get_bool("BACKUP_ENABLED", True)
try:
    BACKUP_HOUR = int(os.getenv("BACKUP_HOUR", "4"))  # 本地时间几点执行（0-23）
except ValueError:
    BACKUP_HOUR = 4
if not 0 <= BACKUP_HOUR <= 23:
    BACKUP_HOUR = 4
BACKUP_KEEP = _get_positive_int("BACKUP_KEEP", 7)
# 附件目录可能很大且能从 Gmail 重新同步，默认不打包
BACKUP_INCLUDE_ATTACHMENTS = _get_bool("BACKUP_INCLUDE_ATTACHMENTS", False)
# 备份加密公钥（X25519，base64）。设置后备份先加密再落盘，服务器上只有公钥，
# 解密用的私钥由你离线保管：拿到备份文件（或整台服务器）也解不开。生成密钥对：
#   docker compose exec gmail-client python -m app.services.backup_crypto keygen
BACKUP_PUBLIC_KEY = os.getenv("BACKUP_PUBLIC_KEY", "").strip()
# 异地备份：加密后的备份自动上传到服务器以外的地方（只上传加密备份）。
# 1) Telegram：发到 TELEGRAM_CHAT_ID（Bot API 单文件上限 50 MB）
BACKUP_UPLOAD_TELEGRAM = _get_bool("BACKUP_UPLOAD_TELEGRAM", False)
# 2) WebDAV：坚果云、Nextcloud、Alist 等，填目录地址（以 / 结尾），文件直接 PUT 到该目录
BACKUP_WEBDAV_URL = os.getenv("BACKUP_WEBDAV_URL", "").strip()
BACKUP_WEBDAV_USER = os.getenv("BACKUP_WEBDAV_USER", "").strip()
BACKUP_WEBDAV_PASSWORD = os.getenv("BACKUP_WEBDAV_PASSWORD", "").strip()
BACKUP_UPLOAD_TIMEOUT_SEC = _get_positive_int("BACKUP_UPLOAD_TIMEOUT_SEC", 300)

# --- 审计日志与异常告警 ---------------------------------------------------------
# 审计日志保留天数，超过的每天清理一次
AUDIT_RETENTION_DAYS = _get_positive_int("AUDIT_RETENTION_DAYS", 180)
# 从没登录成功过的 IP 登录成功时，用 Telegram 提醒（需配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID）
AUDIT_ALERT_NEW_IP = _get_bool("AUDIT_ALERT_NEW_IP", True)
# IP 因密码错误次数过多被封禁时也提醒
AUDIT_ALERT_IP_BLOCKED = _get_bool("AUDIT_ALERT_IP_BLOCKED", True)
LOCAL_TIMEZONE = os.getenv("TZ", "").strip() or "Asia/Shanghai"

LOG_MAX_MB = _get_positive_int("LOG_MAX_MB", 10)
LOG_BACKUP_COUNT = _get_positive_int("LOG_BACKUP_COUNT", 5)

SESSION_SECRET_PATH = SECRETS_DIR / "session_secret.txt"
# 会话 cookie 只通过 HTTPS 发送。仅用 http:// 访问（如本地调试）时需设为 false，
# 否则浏览器不会回传 cookie，表现为登录后又跳回登录页。
SESSION_COOKIE_SECURE = _get_bool("SESSION_COOKIE_SECURE", True)
# 登录有效期（天）：从登录那一刻起计算的绝对期限，到期必须重新输入密码，
# 不会因为持续访问而无限续期。
SESSION_MAX_AGE_DAYS = _get_positive_int("SESSION_MAX_AGE_DAYS", 7)
SESSION_MAX_AGE_SEC = SESSION_MAX_AGE_DAYS * 24 * 3600


def _restrict_to_owner(path: Path) -> None:
    """密钥文件只允许属主读写（0600）。Windows 不支持 POSIX 权限，跳过。"""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_session_secret() -> str:
    """Return the session signing secret, creating one on first run.

    该密钥能伪造会话 cookie，也是图片代理签名的根密钥，文件权限与 fernet.key 一样设为 0600。
    """
    env_secret = os.getenv("SESSION_SECRET", "").strip()
    if env_secret:
        return env_secret
    if SESSION_SECRET_PATH.exists():
        _restrict_to_owner(SESSION_SECRET_PATH)   # 旧版本创建的文件可能是 0644
        return SESSION_SECRET_PATH.read_text(encoding="utf-8").strip()

    secret = secrets.token_urlsafe(48)
    # 创建时就带上 0600，不留下「先按默认权限创建、再 chmod」的窗口
    fd = os.open(SESSION_SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(secret)
    _restrict_to_owner(SESSION_SECRET_PATH)
    logger = logging.getLogger(__name__)
    logger.info("Generated new session secret at %s", SESSION_SECRET_PATH)
    return secret


def setup_logging() -> None:
    """Configure root logger; never write secrets to logs."""
    log_file = LOGS_DIR / "app.log"
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    # 按大小轮转：单个文件超过 LOG_MAX_MB 后切分，最多保留 LOG_BACKUP_COUNT 个旧文件
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_MB * 1024 * 1024,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[file_handler, logging.StreamHandler()],
        force=True,
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("imapclient").setLevel(logging.WARNING)
