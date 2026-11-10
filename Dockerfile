# syntax=docker/dockerfile:1.7

# 基础镜像按 digest 固定：标签可以被重新指向，digest 不会。升级时取新 digest：
#   docker pull python:3.12-slim && docker inspect --format '{{index .RepoDigests 0}}' python:3.12-slim
# （tools/deploy.sh 取第一个 FROM 作为检查用的 Python 镜像，Python 镜像必须写在最前面）
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS base

# ---- 前端构建（Vue 3 + Naive UI）：产物只有静态文件，Node 不进入最终镜像 ----
#   docker pull node:24-alpine && docker inspect --format '{{index .RepoDigests 0}}' node:24-alpine
FROM node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS frontend
WORKDIR /frontend
# 依赖按 package-lock.json 安装：每个包都锁定版本和 sha512，被替换或投毒时校验失败；
# --ignore-scripts：不执行任何包的安装脚本
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
# 输出到 /app/static/dist（见 vite.config.js 的 outDir）
RUN npm run build

# ---- 原生 MIME 解析扩展（C++ / pybind11）：只在这一阶段编译，编译器不进入最终镜像 ----
# 源码在 native/fastmime/；构建依赖按 build-requirements.lock 的 hash 安装。
# 编译失败会让整个构建失败（不静默退回），运行时则由 fastmime_check 自检决定是否启用。
FROM base AS native
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ make libssl-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY native/fastmime/build-requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --no-deps -r build-requirements.lock
# fastmime：MIME 结构解析；imapfetch：新邮件下载引擎（常驻连接、头部先行、并发取正文）
COPY native/fastmime/ ./fastmime/
COPY native/imapfetch/ ./imapfetch/
RUN cd fastmime && python setup.py build_ext --inplace && ls fastmime*.so \
 && cd ../imapfetch && python setup.py build_ext --inplace && ls imapfetch*.so

FROM base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

# uvicorn 只信任本机与 Docker/内网段（nginx 容器）发来的 X-Forwarded-For；
# 不能用 "*"，否则任何人都能伪造来源 IP，绕过登录失败封禁。
ENV FORWARDED_ALLOW_IPS="127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        curl \
        gosu \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo $TZ > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖按 requirements.lock 安装：每个包（含间接依赖）都锁定版本和 sha256，
# PyPI 上的包被替换或投毒时 hash 对不上，构建直接失败
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock  && pip check

COPY app ./app
COPY --from=frontend /app/static/dist ./app/static/dist
COPY --from=native /build/fastmime/fastmime*.so /build/imapfetch/imapfetch*.so ./app/native/
COPY run.py ./run.py
COPY docker-entrypoint.sh /docker-entrypoint.sh
# Windows CRLF in the script makes the kernel look for "/bin/sh\r" → "no such file or directory"
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh

RUN groupadd --system --gid 1000 app \
 && useradd  --system --uid 1000 --gid app --home /app --shell /sbin/nologin app \
 && mkdir -p /app/data /app/attachments /app/secrets /app/logs /app/backups \
 && chown -R app:app /app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz >/dev/null || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
