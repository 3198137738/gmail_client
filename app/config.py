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
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
STATIC_DIR = BASE_DIR / "app" / "static"

for _d in (DATA_DIR, ATTACHMENTS_DIR, SECRETS_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{(DATA_DIR / 'gmail_client.db').as_posix()}"

FERNET_KEY_PATH = SECRETS_DIR / "fernet.key"

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.gmail.com"
SMTP_SSL_PORT = 465
SMTP_STARTTLS_PORT = 587
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
    raw = os.getenv(name, "").strip()
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
BACKGROUND_MAX_WORKERS = _get_positive_int("BACKGROUND_MAX_WORKERS", 4)
SYNC_SENT_ENABLED = _get_bool("SYNC_SENT_ENABLED", True)
# 同步垃圾邮件文件夹（带 \Junk 标记的文件夹）。默认关闭；垃圾邮件不发送 Telegram 通知。
SYNC_JUNK_ENABLED = _get_bool("SYNC_JUNK_ENABLED", False)

# Real-time delivery via IMAP IDLE + SSE.
# When enabled, a long-lived IMAP connection is kept per account/folder so new
# mail triggers an incremental sync within seconds, and the change is pushed to
# the browser over Server-Sent Events (no manual refresh needed).
REALTIME_ENABLED = _get_bool("REALTIME_ENABLED", True)
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
WEB_AUTH_MAX_FAILURES = _get_positive_int("WEB_AUTH_MAX_FAILURES", 3)
WEB_AUTH_LOCKOUT_MINUTES = _get_positive_int("WEB_AUTH_LOCKOUT_MINUTES", 30)
WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES = _get_bool(
    "WEB_AUTH_PERMANENT_BLOCK_AFTER_FAILURES",
    False,
)
WEB_AUTH_BLOCKED_IPS = _get_csv_set("WEB_AUTH_BLOCKED_IPS")
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


def load_session_secret() -> str:
    """Return the session signing secret, creating one on first run."""
    env_secret = os.getenv("SESSION_SECRET", "").strip()
    if env_secret:
        return env_secret
    if SESSION_SECRET_PATH.exists():
        return SESSION_SECRET_PATH.read_text(encoding="utf-8").strip()

    secret = secrets.token_urlsafe(48)
    SESSION_SECRET_PATH.write_text(secret, encoding="utf-8")
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
