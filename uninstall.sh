#!/bin/sh
# Remove only this Compose deployment; retain data unless explicitly confirmed.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

purge=false
case "${1:-}" in
    '') [ "$#" -eq 0 ] || fail '未知参数，请运行 sh uninstall.sh --help。' ;;
    --purge)
        [ "$#" -eq 1 ] || fail '未知参数，请运行 sh uninstall.sh --help。'
        purge=true ;;
    -h|--help)
        printf '%s\n' '用法：sh uninstall.sh [--purge]' \
            '默认移除本项目容器和网络，保留 .env、任务、密码、会话及 HTTPS 证书数据。' \
            '--purge 另外删除本项目 Compose 数据卷和 .env，必须在终端输入 DELETE 确认。' \
            '镜像、源码、备份目录、外部反代、DNS、防火墙和其他项目均不清理。'
        exit 0 ;;
    *) fail '未知参数，请运行 sh uninstall.sh --help。' ;;
esac

[ -f compose.yaml ] || fail '未找到 compose.yaml，未执行卸载。请将脚本放在原安装目录。'
command -v docker >/dev/null 2>&1 || fail '未找到 Docker，未执行卸载；现有配置和数据保留。'
compose_version=$(docker compose version --short 2>/dev/null) || fail '无法运行 docker compose，未执行卸载。'
compose_version=${compose_version#v}
compose_major=${compose_version%%.*}
compose_tail=${compose_version#*.}
compose_minor=${compose_tail%%.*}
case "$compose_major" in ''|*[!0-9]*) fail '无法识别 Compose 版本，请安装 Docker Compose 2.24.0 或更新版本。' ;; esac
case "$compose_minor" in ''|*[!0-9]*) fail '无法识别 Compose 版本，请安装 Docker Compose 2.24.0 或更新版本。' ;; esac
if [ "$compose_major" -lt 2 ] || { [ "$compose_major" -eq 2 ] && [ "$compose_minor" -lt 24 ]; }; then
    fail '此配置需要 Docker Compose 2.24.0 或更新版本，未执行卸载。'
fi
docker info >/dev/null 2>&1 || fail '无法连接 Docker 服务，未执行卸载；现有配置和数据保留。'

printf '%s\n' "卸载目录：$script_dir"
if [ "$purge" = true ]; then
    printf '%s\n' '警告：将永久删除本项目 Compose 数据卷中的全部任务、执行记录、密码、站点会话及 HTTPS 证书，并删除 .env。' \
        '源码、镜像、备份目录和自定义绑定挂载目录会保留；外部反代、DNS 和防火墙需自行处理。'
    [ -t 0 ] || fail '彻底卸载需要在交互终端输入 DELETE 确认；未执行卸载。'
    printf '确认永久删除请输入 DELETE，其他输入取消：'
    read -r confirmation || fail '未读取到确认，已取消卸载。'
    [ "$confirmation" = DELETE ] || fail '确认不匹配，已取消卸载。'
    if ! docker compose -f compose.yaml --profile web down --volumes; then
        fail '卸载未完成，已保留 .env。请检查上方错误，不要手动删除数据密钥。'
    fi
    rm -f -- .env
    printf '%s\n' '彻底卸载完成：本项目 Compose 数据卷和 .env 已删除；镜像、源码及备份保留。'
else
    if ! docker compose -f compose.yaml --profile web down; then
        fail '卸载未完成。请检查上方错误；配置和数据卷保留。'
    fi
    printf '%s\n' '卸载完成：本项目容器和网络已移除，.env、任务、密码、会话及证书数据保留。' \
        '重新运行 sh setup.sh 可使用原数据部署；请保留 .env 中的数据密钥。'
fi
printf '%s\n' '未修改其他项目、外部反代、DNS 或防火墙。'
