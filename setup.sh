#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

mode=pull
access=
host=
external_proxy=
action=install
purge=false
while [ "$#" -gt 0 ]; do
case "$1" in
    --local|--loaded)
        [ "$mode" = pull ] || fail '一次只能选择一种镜像方式。'
        mode=${1#--} ;;
    --domain|--no-domain)
        [ -z "$access" ] || fail '一次只能选择一种访问方式。'
        [ "$#" -ge 2 ] || fail '请在访问方式后填写域名或服务器 IP。'
        if [ "$1" = --domain ]; then access=domain; else access=ip; fi
        host=$2
        shift ;;
    --private)
        [ -z "$access" ] || fail '一次只能选择一种访问方式。'
        access=private ;;
    --external-proxy) external_proxy=--external-proxy ;;
    --uninstall) action=uninstall ;;
    --purge) purge=true ;;
    -h|--help)
        printf '%s\n' '用法：sh setup.sh [--local | --loaded] [--domain 域名 | --no-domain 服务器IP | --private]' \
            '默认下载预构建镜像；终端交互运行会选择域名 HTTPS 或无域名 IP＋端口，已有配置也会询问。' \
            'SSH 私有访问仍可显式使用 --private，不在交互菜单中显示。' \
            '无交互且未指定访问方式时，新安装仅监听本机；升级保留原配置。无域名 HTTP 未加密。' \
            '已有 HTTPS 反代时使用 --domain 域名 --external-proxy，上游为 127.0.0.1:8080。' \
            '卸载：sh setup.sh --uninstall（保留数据）；彻底卸载：sh setup.sh --uninstall --purge（需输入 DELETE 确认）。'
        exit 0
        ;;
    *) fail '未知参数，请运行 sh setup.sh --help。' ;;
esac
shift
done
if [ "$action" = uninstall ]; then
    [ "$mode" = pull ] && [ -z "$access" ] && [ -z "$external_proxy" ] || fail '卸载不能与镜像或访问配置参数混用。'
    [ -f ./uninstall.sh ] || fail '未找到 uninstall.sh，请更新部署文件后重试。'
    if [ "$purge" = true ]; then
        exec sh ./uninstall.sh --purge
    fi
    exec sh ./uninstall.sh
fi
[ "$purge" = false ] || fail '--purge 必须与 --uninstall 一起使用。'
[ -z "$external_proxy" ] || [ "$access" = domain ] || fail '--external-proxy 必须与 --domain 一起使用。'

if [ -z "$access" ] && [ -t 0 ]; then
    printf '%s\n' '请选择访问方式（已有密码、数据密钥和任务会保留）：' \
        '  1) 域名 HTTPS（域名需已解析到本机，80/443 空闲并开放）' \
        '  2) 无域名 IP＋端口（HTTP 明文，仅限可信网络）'
    printf '请输入 [1/2]：'
    read -r choice || fail '未读取到访问方式，已取消部署。'
    case "$choice" in
        1) access=domain; printf '请输入域名（不含 https://）：'; read -r host || fail '未读取到域名，已取消部署。' ;;
        2) access=ip; printf '请输入服务器 IP：'; read -r host || fail '未读取到服务器 IP，已取消部署。' ;;
        *) fail '请选择 1（域名 HTTPS）或 2（无域名 IP＋端口），已取消部署。' ;;
    esac
fi

compose() {
    if [ "$mode" = local ]; then
        docker compose -f compose.yaml -f compose.build.yaml "$@"
    else
        docker compose -f compose.yaml "$@"
    fi
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

if [ "$mode" = local ]; then
    [ -f Dockerfile ] && [ -f compose.build.yaml ] || fail '--local 需要完整的 Git 仓库源码；快速安装目录请直接运行 sh setup.sh 下载成品镜像。'
    if ! compose build; then
    cat >&2 <<'EOF'
镜像构建失败。若构建日志包含 “Temporary failure in name resolution” 或 “Could not resolve host”，请检查服务器 DNS、出站网络或 Docker 构建环境的 DNS。
可先检查服务器能否访问 PyPI：getent hosts pypi.org；curl -I --max-time 10 https://pypi.org/simple/flask/
如只是默认 PyPI 不可达，可设置可信的 PyPI 镜像地址后重试：PIP_INDEX_URL='https://<镜像地址>/simple' sh setup.sh --local
PIP_INDEX_URL 只切换 Python 包索引，不会修复通用 DNS 问题，也不会切换 Debian 软件源或 Playwright 浏览器下载源。请勿在该地址中放入账号、密码或令牌。
EOF
        exit 1
    fi
elif [ "$mode" = pull ]; then
    printf '%s\n' '正在下载预构建镜像，服务器无需安装 Python 或下载浏览器依赖。'
    if ! compose pull app; then
        cat >&2 <<'EOF'
镜像下载失败，尚未启动服务。请检查上方错误：
  域名解析或超时：检查服务器 DNS、出站网络和 Docker 服务的代理配置。
  denied / unauthorized / manifest unknown：确认镜像已发布且公开，或 AUTOSIGN_IMAGE 指向正确的镜像。
  no matching manifest：当前服务器架构不在此镜像支持范围内。
可运行 curl -I --max-time 10 https://ghcr.io/v2/ 检查仓库连通性；HTTP 401 说明已连通，镜像能否匿名下载仍以 docker compose pull app 为准。
也可在能下载镜像的同架构电脑 docker save，传到服务器 docker load 后，运行 sh setup.sh --loaded。
EOF
        exit 1
    fi
else
    printf '%s\n' '使用已存在的本地镜像，不下载或构建。'
fi

if [ ! -e .env ]; then
    umask 077
    env_temp=$(mktemp .env.tmp.XXXXXX)
    trap 'rm -f "$env_temp"' 0
    trap 'exit 1' HUP INT TERM
    compose run --rm --no-deps --pull never -T app python init_config.py > "$env_temp"
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

if [ -n "$access" ]; then
    umask 077
    env_temp=$(mktemp .env.tmp.XXXXXX)
    trap 'rm -f "$env_temp"' 0
    trap 'exit 1' HUP INT TERM
    compose run --rm --no-deps --pull never -T app python deployment.py configure "$access" "$host" $external_proxy < .env > "$env_temp"
    [ -s "$env_temp" ] || fail '访问配置生成失败。'
    mv -f "$env_temp" .env
fi

if grep -q '^AUTOSIGN_ACCESS_MODE=domain$' .env; then
    if grep -q '^AUTOSIGN_EXTERNAL_PROXY=true$' .env; then
        printf '%s\n' '使用现有 HTTPS 反代，请确认域名已配置，上游指向 127.0.0.1:所选端口。'
    else
        if [ "$mode" = pull ]; then compose pull web; fi
        printf '%s\n' '自动 HTTPS 需要独占 80/443；已有反代时使用 --domain 域名 --external-proxy。'
    fi
fi

printf '%s\n' '正在检查 Chromium 沙箱能否启动。'
if ! compose run --rm --no-deps --pull never -T app python -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True, chromium_sandbox=True); b.close(); p.stop()'; then
    fail 'Chromium 沙箱启动失败，未启动新服务。请检查上方错误及服务器的 user namespace、seccomp 配置。已有 .env 与数据卷保留。'
fi

if ! compose up -d --no-build --pull never --wait --wait-timeout 90; then
    fail '服务未通过健康检查。请运行 docker compose logs --tail=100 app 查看原因。'
fi
printf '%s\n' '服务已启动并通过健康检查。'
if ! grep -q '^AUTOSIGN_ACCESS_MODE=domain$' .env || grep -q '^AUTOSIGN_EXTERNAL_PROXY=true$' .env; then
    # A previously enabled HTTPS sidecar must not keep serving after switching modes.
    compose --profile web stop web
fi
if grep -q '^AUTOSIGN_ACCESS_MODE=domain$' .env; then
    compose exec -T app python deployment.py verify-domain
fi
compose exec -T app python deployment.py summary
printf '%s\n' '卸载服务（保留数据）：sh setup.sh --uninstall' \
    '彻底卸载（需确认永久删除数据）：sh setup.sh --uninstall --purge'
