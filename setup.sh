#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

command -v docker >/dev/null 2>&1 || fail '未找到 Docker。请先安装 Docker Engine 和 Docker Compose 插件。'
compose_version=$(docker compose version --short 2>/dev/null) || fail '无法运行 docker compose。请安装 Docker Compose 插件（至少 2.24.0）。'
compose_version=${compose_version#v}
compose_major=${compose_version%%.*}
compose_tail=${compose_version#*.}
compose_minor=${compose_tail%%.*}
case "$compose_major" in ''|*[!0-9]*) fail '无法识别 Compose 版本，请安装 Docker Compose 2.24.0 或更新版本。' ;; esac
case "$compose_minor" in ''|*[!0-9]*) fail '无法识别 Compose 版本，请安装 Docker Compose 2.24.0 或更新版本。' ;; esac
if [ "$compose_major" -lt 2 ] || { [ "$compose_major" -eq 2 ] && [ "$compose_minor" -lt 24 ]; }; then
    fail '此配置使用可选 env_file，需要 Docker Compose 2.24.0 或更新版本。请先更新 Compose 插件。'
fi
docker info >/dev/null 2>&1 || fail '无法连接 Docker 服务。请启动 Docker，并确认当前用户有访问 Docker 的权限。'

docker compose build

if [ ! -e .env ]; then
    umask 077
    env_temp=$(mktemp .env.tmp.XXXXXX)
    trap 'rm -f "$env_temp"' 0
    trap 'exit 1' HUP INT TERM
    docker compose run --rm --no-deps app python init_config.py > "$env_temp"
    [ -s "$env_temp" ] || fail '配置生成失败，未启动服务。'
    mv -n "$env_temp" .env
    if [ -e "$env_temp" ]; then
        printf '%s\n' '.env 已由其他进程创建，保留现有配置。'
        rm -f "$env_temp"
    else
        printf '%s\n' '已生成 .env（权限 600）。请妥善保存其中的管理员密码和数据密钥。'
    fi
else
    printf '%s\n' '保留已有 .env。'
fi

if ! docker compose up -d --wait --wait-timeout 90; then
    fail '服务未通过健康检查。请运行 docker compose logs --tail=100 app 查看原因。'
fi
printf '%s\n' '服务已启动并通过健康检查。'
printf '%s\n' '管理员密码保存在 .env 的 AUTOSIGN_ADMIN_PASSWORD。'
printf '%s\n' '默认仅监听服务器本机；在你的电脑运行：ssh -L 8080:127.0.0.1:8080 用户@服务器IP'
printf '%s\n' '保持 SSH 连接，再用浏览器访问 http://localhost:8080。'
