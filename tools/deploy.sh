#!/bin/sh
# 安全部署：拉取代码 → 完整性检查 → 依赖漏洞扫描 → 构建并重启。任何一步失败都不会部署。
#
# 用法（服务器上，项目目录内）：
#   sh tools/deploy.sh
# PyPI / 漏洞库暂时连不上、确需先部署时：SKIP_AUDIT=1 sh tools/deploy.sh（不推荐）
#
# 修改依赖（requirements.txt）后需要在开发机重新生成锁文件并提交：
#   uv pip compile requirements.txt --generate-hashes --python-version 3.12 \
#       --python-platform x86_64-manylinux_2_28 --no-header -o requirements.lock
# 前端依赖（frontend/package.json）修改后用 npm install 更新 package-lock.json 并提交；
# 前端在 docker compose build 时构建，服务器上不需要安装 Node。
set -eu

cd "$(dirname "$0")/.."

echo "==> 拉取代码"
# 工作区有未提交的改动说明文件在服务器上被改过（可能被篡改），拒绝部署
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "工作区有未提交的改动，拒绝部署：" >&2
    git status --short --untracked-files=no >&2
    exit 1
fi
git fetch --quiet origin main
git merge --ff-only origin/main

# 检查与扫描都在固定 digest 的基础镜像里运行，不依赖宿主机的 Python / Node
IMAGE=$(sed -n 's/^FROM \([^ ]*\).*/\1/p' Dockerfile | head -n 1)
NODE_IMAGE=$(sed -n 's/^FROM \(node:[^ ]*\).*/\1/p' Dockerfile | head -n 1)

echo "==> 完整性检查（恶意注入特征、依赖锁文件）"
docker run --rm --network none -v "$PWD:/src:ro" -w /src "$IMAGE" python tools/check_integrity.py /src

if [ "${SKIP_AUDIT:-}" = "1" ]; then
    echo "!!! 已跳过依赖漏洞扫描（SKIP_AUDIT=1）" >&2
else
    echo "==> 依赖漏洞扫描（pip-audit，工具本身也按 hash 安装）"
    docker run --rm -v "$PWD:/src:ro" -w /src "$IMAGE" sh -c '
        pip install --quiet --no-cache-dir --root-user-action=ignore --require-hashes -r tools/audit-requirements.lock \
        && pip-audit --strict --progress-spinner off -r requirements.lock --require-hashes --disable-pip'
    echo "==> 前端依赖漏洞扫描（npm audit，按 package-lock.json 检查打包进页面的依赖）"
    docker run --rm -v "$PWD/frontend:/src:ro" -w /src "$NODE_IMAGE" npm audit --omit=dev
fi

echo "==> 构建并重启"
docker compose build
docker compose up -d
docker compose ps
