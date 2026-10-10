"""日志文件按大小轮转。"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app import config


def test_file_log_handler_rotates():
    config.setup_logging()
    handlers = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert handlers, "app.log 应使用 RotatingFileHandler"
    assert handlers[0].maxBytes == config.LOG_MAX_MB * 1024 * 1024
    assert handlers[0].backupCount == config.LOG_BACKUP_COUNT
