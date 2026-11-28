#!/bin/sh
# Remove this deployment; --purge also removes its images and installation.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
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
            '--purge 另外删除本项目数据卷、未被其他容器使用的项目镜像，以及整个安装目录（包含源码、.env 和目录内备份）。' \
            '彻底卸载必须在终端输入 DELETE 确认；共享镜像会保留并列出，目录外文件和其他项目不清理。'
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
    # Never recursively remove a home directory, shared base directory or mount.
    case "$script_dir" in
        /|/root|/home|/Users|/tmp|/private/tmp|/var|/var/tmp|/opt|/srv|/etc|/usr|/usr/local|/app)
            fail '安装位置是系统或共享目录，拒绝递归删除。请将项目放在独立目录后处理。' ;;
    esac
    [ "$script_dir" != "${HOME:-/}" ] || fail '安装位置是用户主目录，拒绝递归删除。'
    [ -f setup.sh ] || fail '未找到 setup.sh，无法确认独立的项目安装目录，未执行彻底卸载。'
    if [ -r /proc/self/mountinfo ] && awk -v root="$script_dir" '
        { path = $5; gsub(/\\040/, " ", path); gsub(/\\011/, "\t", path);
          if (path == root || index(path, root "/") == 1) found = 1 }
        END { exit !found }' /proc/self/mountinfo; then
        fail '安装目录自身或内部存在挂载点，未执行卸载。请先将挂载数据移出安装目录。'
    fi
    printf '%s\n' '警告：将永久删除本项目容器、网络、全部数据卷、任务、密码、站点会话和 HTTPS 证书。' \
        '同时删除项目镜像及上方安装目录的全部内容，包括源码、.env、目录内备份和个人修改。' \
        '其他容器正在使用的镜像会保留并列出；目录外的备份、外部反代、DNS、防火墙和 Docker 本身不会删除。'
    [ -t 0 ] || fail '彻底卸载需要在交互终端输入 DELETE 确认；未执行卸载。'
    printf '确认永久删除请输入 DELETE，其他输入取消：'
    read -r confirmation || fail '未读取到确认，已取消卸载。'
    [ "$confirmation" = DELETE ] || fail '确认不匹配，已取消卸载。'
    # Snapshot images before Compose containers disappear. Never prune globally.
    configured_images=$(docker compose -f compose.yaml --profile web --profile tunnel config --images) || fail '无法读取部署镜像，未执行卸载。'
    container_images=$(docker ps -a --filter label=com.docker.compose.project=autosign --format '{{.Image}}') || fail '无法读取项目容器，未执行卸载。'
    app_images=$(docker image ls --filter 'reference=ghcr.io/mini3535/multi-site-checkin:*' --format '{{.Repository}}:{{.Tag}}') || fail '无法读取项目镜像，未执行卸载。'
    dangling_images=$(docker image ls --filter dangling=true --filter 'label=org.opencontainers.image.source=https://github.com/mini3535/multi-site-checkin' --format '{{.ID}}') || fail '无法读取旧项目镜像，未执行卸载。'
    images=$(printf '%s\n' "$configured_images" "$container_images" "$app_images" "$dangling_images" | LC_ALL=C sort -u)
    if ! docker compose -f compose.yaml --profile web --profile tunnel down --volumes --remove-orphans; then
        fail '卸载未完成，已保留 .env。请检查上方错误，不要手动删除数据密钥。'
    fi
    retained_images=
    set -f
    for image in $images; do
        if ! docker image inspect "$image" >/dev/null 2>&1; then
            docker info >/dev/null 2>&1 || fail 'Docker 连接中断，镜像清理未完成；安装目录已保留。'
            continue
        fi
        users=$(docker ps -aq --filter "ancestor=$image") || fail '无法检查镜像使用状态；安装目录已保留。'
        if [ -n "$users" ]; then
            printf '%s\n' "保留共享镜像（其他容器正在使用）：$image"
            retained_images=true
            continue
        fi
        docker image rm "$image" || fail '镜像清理未完成；安装目录已保留，请检查错误后重试。'
    done
    # find does not follow symlinks or traverse another filesystem.
    cd -- "$(dirname -- "$script_dir")"
    find "$script_dir" -xdev -depth -delete || fail '安装目录清理未完成，请检查上方文件权限或挂载错误。'
    if [ -n "$retained_images" ]; then
        printf '%s\n' '卸载完成：项目数据和安装目录已删除；上方列出的共享镜像仍保留。'
    else
        printf '%s\n' '彻底卸载完成：项目容器、网络、数据卷、镜像和整个安装目录已清理。'
    fi
    printf '%s\n' '原安装目录已删除，请在终端运行 cd ~ 后继续操作。'
else
    if ! docker compose -f compose.yaml --profile web --profile tunnel down; then
        fail '卸载未完成。请检查上方错误；配置和数据卷保留。'
    fi
    printf '%s\n' '卸载完成：本项目容器和网络已移除，.env、任务、密码、会话及证书数据保留。' \
        '重新运行 sh setup.sh 可使用原数据部署；请保留 .env 中的数据密钥。'
fi
printf '%s\n' '未修改其他项目、外部反代、DNS 或防火墙。'
