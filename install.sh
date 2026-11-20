#!/bin/sh
# Download deployment files only. Docker builds run in GitHub Actions.
set -eu

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

case "${1:-}" in
    -h|--help)
        printf '%s\n' '用法：sh install.sh [安装目录]' \
            '默认安装到当前目录下的 multi-site-checkin。需提前安装 Docker Engine、Compose 2.24.0+ 和 curl。'
        exit 0
        ;;
    -*) fail '安装目录不能以 - 开头；可使用 ./目录名。' ;;
esac
[ "$#" -le 1 ] || fail '用法：sh install.sh [安装目录]'

command -v curl >/dev/null 2>&1 || fail '未找到 curl，请先安装。'
command -v docker >/dev/null 2>&1 || fail '未找到 Docker，请先安装 Docker Engine 和 Docker Compose 插件。'

install_dir=${1:-./multi-site-checkin}
umask 077
mkdir -p -- "$install_dir"
cd -- "$install_dir"
install_dir=$(pwd -P)
staging_dir=$(mktemp -d .autosign-install.XXXXXX)
trap 'rm -rf -- "$staging_dir"' 0
trap 'exit 1' HUP INT TERM

download_root=https://raw.githubusercontent.com/mini3535/multi-site-checkin/main
printf '%s\n' "正在下载部署文件到 $install_dir"
for file in compose.yaml seccomp_profile.json setup.sh .env.example README.md; do
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
        --connect-timeout 15 --max-time 120 --retry 2 \
        "$download_root/$file" -o "$staging_dir/$file" || fail "下载 $file 失败，部署文件尚未更新。请检查 GitHub 连通性。"
    [ -s "$staging_dir/$file" ] || fail "下载的 $file 为空，部署文件尚未更新。"
done
for file in compose.yaml seccomp_profile.json setup.sh .env.example README.md; do
    [ ! -d "$file" ] || fail "$file 是目录，无法写入部署文件。"
done
for file in compose.yaml seccomp_profile.json setup.sh .env.example README.md; do
    chmod 644 "$staging_dir/$file"
    mv -f -- "$staging_dir/$file" "$file"
done

printf '%s\n' "部署目录：$install_dir" '以后在此目录运行 sh setup.sh 即可更新镜像。'
sh ./setup.sh
