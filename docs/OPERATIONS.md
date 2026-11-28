# 安装、更新与维护

更新日期：2026-11-28。日常添加网站请先看 [README](../README.md)；本页说明当前部署、完整更新、连接与排障流程。

部署在自己服务器上的自动签到服务，支持多个网站和账号，按计划执行并记录结果。

本项目采用 MIT 许可证，详见 [LICENSE](../LICENSE)。第三方软件和引用代码的来源见 [第三方声明](../THIRD_PARTY_NOTICES.md)。

快速目录：[首次安装](#快速开始) · [更新服务器](#更新已有服务器) · [选择访问方式](#选择访问方式) · [卸载服务](#卸载服务) · [检查卸载结果](#检查卸载结果) · [备份与恢复](#备份与恢复)

## 快速开始

首次使用：通过 SSH 登录已安装 Docker Engine、Docker Compose 插件（2.24.0+）和 `curl` 的 Linux 服务器，然后运行：

   ```sh
   curl -fsSL \
     https://raw.githubusercontent.com/mini3535/multi-site-checkin/main/install.sh \
     -o autosign-install.sh &&
     sh autosign-install.sh /root/multi-site-checkin
   ```

安装完成后，先确认通过受支持的入口打开助手、在自己的电脑 Chrome 安装一次扩展，再按 **网站登录 → 扩展识别并添加 → 验证签到 → 开启每日计划** 操作。没有域名或 80/443 已占用时，可选免自有域名 HTTPS；已有 HTTPS 反代也可复用。已有平台接口可在目录中选择专用表单。保存安装时显示的网页链接和初始密码；登录后可修改密码。

登录后，顶部“使用指引”提供连接设置、扩展安装、自动添加、验证与计划、更新与排障、卸载与清理。网站目录有“首次使用指引”，底部有更新与卸载入口，账号表单和四步进度也有指引链接；HTTPS 用户同样可以随时打开。常规更新从网页的「系统更新」操作；首次部署、旧版首次启用网页更新、完整升级及卸载在服务器终端操作。扩展安装和网站登录在自己电脑的 Chrome 中完成。

每个网站账号单独建任务。验证会实际执行一次签到；验证成功后还需设置时间，才会开启每日执行。已安装用户请看[更新已有服务器](#更新已有服务器)，访问方式见[访问配置](#选择访问方式)。

### 更新已有服务器

#### 日常网页更新

**新版提醒 → 系统更新 → 立即更新 → 刷新使用新版。**

1. 登录签到助手。系统每小时检查官方 `latest`，已发布且版本不同才在页面顶部提醒。没有提醒时，可打开「系统更新」点击「检查更新」；显示「当前已是最新版本」时无需更新。GitHub 代码提交后，仍需等待镜像构建、测试和发布全部完成。
2. 看到新版本后点击「立即更新」，核对提示并确认。系统依次下载目标摘要镜像、校验版本、检查 Chromium、暂停新操作和计划、等待运行中任务结束，备份数据库与原 `.env`，再重启并检查健康状态。
3. 保持页面打开，等待更新状态恢复。网页在重启期间短暂断开，无需重复点击或重新安装；看到「更新完成」后点击「刷新使用新版」，或手动刷新网页。
4. 查看页脚「构建版本」，确认当前运行版本。扩展版本有变化时，在新版「网站目录」下载扩展 ZIP，覆盖自己电脑上的原扩展文件夹，再到 `chrome://extensions` 点击刷新。服务器更新只更新下载包，不会替电脑安装扩展；只刷新旧文件夹也不会下载新版。

网页更新保留任务、密码、账号会话和原访问方式。Tailscale 模式会随应用重建网络并恢复 Funnel，原身份数据保留。新版启动失败时会尝试恢复旧镜像和更新前数据库，结果显示在更新页面。恢复成功后继续使用旧版；显示「需要人工恢复」时按下方异常处理步骤操作。

#### 旧版首次启用或完整更新

旧版没有「系统更新」入口，或提示「网页更新服务尚未就绪」时，先执行一次下面的完整更新，装入 `updater` 服务与控制卷。**只拉取 app 镜像无法启用网页更新。** 完成后，常规版本从网页点击更新即可。

同一流程也用于更新服务自身、部署配置变化或旧脚本不支持新参数。先[备份](#备份与恢复)到安装目录外；自行改过 Compose 时先保存自己的修改。命令在服务器终端执行，将 `/root/multi-site-checkin` 替换为自己的原安装目录，避免创建另一个空安装：

```sh
cd /root/multi-site-checkin &&
  if [ -f .env ]; then
    curl -fsSL https://raw.githubusercontent.com/mini3535/multi-site-checkin/main/install.sh -o /tmp/autosign-install.sh &&
    sh /tmp/autosign-install.sh "$PWD" < /dev/null
  else
    echo '未找到原 .env，请确认原安装目录，已停止更新。'
  fi
```

等终端检查通过后，用原地址刷新网页，打开「系统更新」并点击「检查更新」。`< /dev/null` 让更新沿用已有域名、IP、免自有域名 HTTPS 或 SSH 私有访问配置；任务、密码和账号会话保留。若要重新选择访问方式，在原目录运行 `sh setup.sh`；改为免自有域名 HTTPS 可用 `sh setup.sh --tunnel`。

网页安装只支持官方 `latest`，且只更新应用和随附的扩展下载包。固定标签、自定义镜像、本地构建仍在终端处理；源码部署见[日常管理](#日常管理)。`.env` 中的 `AUTOSIGN_IMAGE` 固定为旧版本标签时，需按自己的部署选择更新标签，或改为 `ghcr.io/mini3535/multi-site-checkin:latest`。部署方式变化会通过更新协议提示终端升级；独立更新服务自身只能通过完整终端更新升级。

#### 终端只更新镜像（备用）

已装入新版部署文件、需要在终端只更新镜像时，在原安装目录运行：

```sh
cd /root/multi-site-checkin &&
  if [ -f .env ]; then
    sh setup.sh < /dev/null
  else
    echo '未找到原 .env，请确认原安装目录，已停止更新。'
  fi
```

更新会拉取预构建镜像、检查 Chromium 沙箱、重建服务并检查健康状态。保留原 `.env`、数据密钥和数据卷，数据库会自动升级。普通升级不要使用 `docker compose down -v`。

#### 更新异常与备份

检查更新失败时，按页面提示检查网络和镜像仓库可达性后重试。下载或环境检查失败时，原服务继续运行；新版启动失败时，系统会尝试自动恢复旧版和数据库。

显示「需要人工恢复」时，计划任务保持暂停。先在原安装目录检查磁盘空间、`docker compose ps` 和 `docker compose logs --tail=100 updater`。排除问题后执行：

```sh
docker compose exec -T updater python -m autosign.updater --recover
```

命令会重试恢复旧镜像和更新前数据库。完成后回到更新页面刷新查看结果；仍失败时保留日志和备份继续定位。不要删除数据卷、重新生成密钥或手动移除暂停标记。

备份在数据卷 `backups/web-updates/`，包含数据库和原 `.env`，权限仅限服务端；普通卸载保留，彻底卸载删除。请按需备份到服务器之外并清理不再需要的旧备份。

更新服务单独挂载 Docker socket，仅通过本项目控制卷的 Unix socket 接受固定操作，不监听 TCP；应用容器不挂载 Docker socket，也不能传入镜像、路径或命令。Docker socket 等同于服务器管理权限，应只运行可信的官方镜像。更新检查只访问官方公开 GHCR，不需要填写 GitHub Token。

页脚「构建版本」显示镜像提交号前 12 位，可与 GitHub Actions 对应运行的完整提交号核对。它只在登录后的页面显示，公开健康接口不包含构建元数据。本地 `--local` 构建默认标为 `local`，也可通过 `AUTOSIGN_BUILD_REVISION` 设置自定义标识。GitHub 代码或镜像发布不会自行安装到服务器，需要你在网页点击更新，或在终端执行更新。

### 卸载入口

**获取最新版卸载器 → 核对原安装目录 → 输入 DELETE → 检查残留。** 不需要先升级镜像或重装。在服务器的交互终端运行，以下目录同样需要按实际安装位置替换：

```sh
cd /root/multi-site-checkin &&
  curl -fsSL https://raw.githubusercontent.com/mini3535/multi-site-checkin/main/install.sh -o /tmp/autosign-install.sh &&
  sh /tmp/autosign-install.sh "$PWD" --uninstall --purge
```

彻底卸载会删除数据、未共享的项目镜像和整个安装目录。需要的备份先移到目录外，核对显示路径后输入大写 `DELETE`，不能添加 `< /dev/null`。只想移除服务并保留任务、密码、会话和配置时，去掉最后的 `--purge`。旧版本也使用同一入口获取新版卸载器；完整范围和失败处理见[卸载服务](#卸载服务)，结束后按[检查卸载结果](#检查卸载结果)核对。

**镜像发布状态：** 请在 [GitHub Actions](https://github.com/mini3535/multi-site-checkin/actions/workflows/container.yml) 核对目标提交。Linux `amd64` 和 `arm64` 均构建、测试成功后，发布步骤才会更新 `latest`。代码推送成功不表示镜像已发布，也不会自动更新现有服务器。当前镜像与安装流程以该提交的检查结果为准。

## 当前能做什么

网站目录提供九个平台接口适配，以及豆包官网说明入口。WorkBuddy、百度千帆（DuMate）、MiniMax Code、
Qoder 国内版、Link AI、WPS 灵犀、Trae Work、Coze 和华为码道已实现接口适配与专用账号表单，
无需配置请求 JSON；豆包暂提供受限说明和官网入口。所有新适配均待真实账号验证，
Qoder 需要账号有开放活动，Link AI 遇验证码需人工处理。
平台接口凭据仍需按页面说明手动提供；浏览器网站可用扩展自动读取当前网页会话与签到线索。所有可运行的网站入口共用四步向导：新任务暂停保存，验证通过后单独开启每日计划；更换凭据或规则需要重新验证。
接入条件、实现来源与限制详见 [平台说明](../PLATFORMS.md)。

| 入口 | 已实现的能力 | 使用前还需要什么 |
| --- | --- | --- |
| NodeSeek | 浏览器会话导入与页面步骤执行入口 | 确认当前签到页面、操作步骤和结果文字；尚未实测 |
| hostloc | 浏览器会话导入与页面步骤执行入口（`https://hostloc.com/forum.php`） | 先确认该站当前是否提供每日签到，再配置规则；尚未实测 |
| LINUX DO | 目录中的社区信息卡片 | 未确认有独立每日签到按钮，因此不提供自动签到任务 |
| 其他论坛或网站 | 扩展识别当前网页、导入会话并新建暂停任务；也可手动填网址 | 核对识别结果，缺少按钮或提示时补充规则，再验证 |
| 高级 HTTP | 每天发送一个 GET/POST 请求并验证响应 | 已知的真实签到接口、请求凭据和成功条件 |

NodeSeek (`https://www.nodeseek.com/board`) 和 hostloc (`https://hostloc.com/forum.php`) 的预填地址只是浏览器入口，不代表已确认签到页或可用规则；Hostloc 使用官方 `.com` 域名。LINUX DO 当前没有已确认的独立每日签到操作；每日访问或浏览不等同于签到成功，不能用普通页面文字冒充成功条件。扩展根据当前可见页面提供按钮和结果提示建议，无法保证适配所有网站或发现全部奖励任务；建议需要核对并验证。遇到验证码、人机验证或登录失效时，需要人工完成正常验证并更新会话。

## 安装与访问

### 默认方式：下载预构建镜像

服务器需要 Docker Engine、Docker Compose **2.24.0 或更新版本**、`curl`，并且当前用户可以连接 Docker 服务。运行上面的快速安装命令即可。安装脚本只下载 Compose、沙箱配置、初始化脚本等少量部署文件，然后拉取 `ghcr.io/mini3535/multi-site-checkin:latest`。

指定安装目录时，将一个目录参数传给安装脚本：

```sh
sh autosign-install.sh /opt/multi-site-checkin
```

安装目录可写即可。以后进入该目录管理服务：

```sh
cd /opt/multi-site-checkin
sh setup.sh
docker compose ps
```

`sh setup.sh` 默认拉取镜像。镜像包含 Python 3.12、Playwright、Chromium 及运行依赖，服务器无需单独安装这些组件。终端交互运行且未传入访问方式参数时，每次都会显示“域名 HTTPS”“无域名 IP＋端口”“免自有域名 HTTPS”“使用已有 HTTPS 反代”四项选择，已有 `.env` 时也会询问；选择方法见[选择访问方式](#选择访问方式)。首次运行生成随机管理员密码和数据密钥，保存至权限为 `600` 的 `.env`，检查 Chromium 沙箱，然后启动服务并等待健康检查。安装结束会显示网页链接和初始登录密码。已有密码、数据密钥和数据卷会保留。

如需指定其他镜像地址或版本，在 `.env` 中添加 `AUTOSIGN_IMAGE=镜像地址:标签`，再运行 `sh setup.sh`。只使用自己信任的镜像；不存在的标签会导致拉取失败。

### 从源码构建

需要修改代码，或希望自行构建时，克隆完整仓库并显式选择本地构建：

```sh
git clone https://github.com/mini3535/multi-site-checkin.git
cd multi-site-checkin
sh setup.sh --local
```

`--local` 使用 `compose.build.yaml`，需要联网下载 Python 基础镜像、系统依赖、Python 包和 Chromium。快速安装目录只有部署文件，不能直接执行 `--local`。

如果构建日志出现 `Temporary failure in name resolution`、`Could not resolve host` 或 pip 显示 `from versions: none`，先检查服务器和 Docker 构建环境的 DNS 与出站网络；包索引不可达时，也可能出现 `from versions: none`，不能仅凭最后一行判断版本号无效。可分别检查服务器与容器：

```sh
# 在服务器上检查
getent hosts pypi.org
curl -I --max-time 10 https://pypi.org/simple/flask/

# 检查容器里的 DNS 和 HTTPS 请求
docker run --rm python:3.12-slim-bookworm python -c "import urllib.request; print(urllib.request.urlopen('https://pypi.org/simple/flask/', timeout=10).status)"
```

服务器本身无法访问时，先修复服务器 DNS、出站网络或代理；服务器正常而容器失败时，检查 Docker daemon 的 DNS、代理和构建网络配置。上面的容器检查也需要能够拉取基础镜像；拉取失败时先处理镜像仓库连通性。

若仅默认 PyPI 不可达，可指定网络可访问的可信 PyPI 镜像重试：

```sh
PIP_INDEX_URL='https://<镜像地址>/simple' sh setup.sh --local
```

`PIP_INDEX_URL` 是构建期参数，不需要写入应用运行配置。它只影响 Python 依赖安装，不会切换 Debian 软件源或 Playwright 浏览器下载源。只使用可信的 HTTPS 公共索引，不要把账号、密码或令牌放入 URL。默认预构建镜像安装不运行 pip，此参数对拉取镜像无效。

### 镜像下载失败或离线安装

先检查镜像是否已发布且公开、地址和标签是否正确，再检查网络：

```sh
getent hosts ghcr.io
curl -I --max-time 10 https://ghcr.io/v2/
docker compose pull app
```

访问 `/v2/` 返回 **HTTP 401 是正常的认证挑战，表示已连通**；它不能证明特定镜像已公开。镜像是否可以匿名拉取，以 `docker compose pull app` 的实际结果为准。`denied`、`unauthorized` 或 `manifest unknown` 需要核对镜像发布和公开状态；`no matching manifest` 需要核对服务器 CPU 架构。若 curl 正常而 Docker 拉取超时，检查 Docker 服务自身的代理和出站网络配置。

服务器无法访问 GHCR 时，可在能访问镜像仓库、与服务器 CPU 架构相同的电脑导出镜像：

```sh
docker pull ghcr.io/mini3535/multi-site-checkin:latest
docker save -o autosign-image.tar ghcr.io/mini3535/multi-site-checkin:latest
scp autosign-image.tar 用户@服务器IP:/tmp/
```

先把部署文件放到服务器安装目录，在服务器导入并启动：

```sh
docker load -i /tmp/autosign-image.tar
cd /你的安装目录
sh setup.sh --loaded
```

`--loaded` 只使用本机已有镜像，不执行下载或构建；镜像标签必须与 Compose 默认地址或 `.env` 中的 `AUTOSIGN_IMAGE` 一致。离线服务器还需要提前传入 `compose.yaml`、`seccomp_profile.json`、`setup.sh` 等部署文件。

### 选择访问方式

在终端运行安装器或 `sh setup.sh`，未显式传入访问方式时，都会显示以下菜单，即使安装目录已有 `.env` 也不会跳过：

```text
1) 域名 HTTPS
2) 无域名 IP＋端口（HTTP，仅可信网络；不支持扩展自动连接）
3) 免自有域名 HTTPS（Tailscale Funnel，需账号授权，不占 80/443）
4) 使用已有 HTTPS 反代（适合宝塔、Nginx 已占用 80/443）
```

没有域名且希望使用扩展时选择 `3`；已有域名反代选择 `4`。选择 `1` 或 `4` 后填写域名，选择 `2` 后填写服务器 IP。没有默认选项，直接回车会取消，不会自动切换到 SSH 私有访问。也可直接传参跳过菜单：

```sh
# 有域名：先将 A/AAAA 记录解析到本机，确保 80/443 空闲且公网可达
sh setup.sh --domain sign.example.com

# 无域名：填写服务器实际 IP，以 http://服务器IP:8080/login 访问
sh setup.sh --no-domain 203.0.113.8

# 无自有域名、需要扩展连接：通过自己的账号授权 Tailscale Funnel
sh setup.sh --tunnel

# 已有 HTTPS 反代：不启动 Caddy，不占用额外的 80/443
sh setup.sh --domain sign.example.com --external-proxy

# SSH 私有访问能力保留，不在上述四项菜单中显示；显式使用以下参数
sh setup.sh --private
```

上面的域名和 IP 均为示例，请替换。快速安装器也支持传参，例如 `sh autosign-install.sh /opt/multi-site-checkin --no-domain 你的服务器IP`。重复运行快速安装器也会显示菜单。非交互运行且未指定访问方式时，新安装默认使用 SSH 私有访问；已有部署保留原配置。自动化部署建议显式指定访问方式。之后可重新运行上述命令切换方式，数据密钥、任务和密码不会重置。

域名模式使用独立的 Caddy 容器自动管理公开可信的 HTTPS 证书；需能访问证书服务，并遵守所用证书机构的服务条款。`--loaded` 离线安装域名模式时，还需事先导入 `caddy:2-alpine` 镜像。域名 HTTPS 检查未通过时脚本报错。

已有 Nginx、Caddy 或其他 HTTPS 代理占用 80/443 时，保留现有代理，配置域名证书并将上游指向 `127.0.0.1:8080`（同一宿主机），然后运行 `sh setup.sh --domain sign.example.com --external-proxy`。此选项不启动 Caddy，仍检查域名 HTTPS。代理位于其他容器时需自行配置可达的容器网络。脚本不会修改现有代理、DNS、服务器防火墙或云安全组。

### 免自有域名 HTTPS：安装时授权，之后直接访问

选择菜单 `3`，或在原安装目录运行 `sh setup.sh --tunnel`。该方式使用第三方 [Tailscale Funnel](https://tailscale.com/kb/1223/funnel)，无需购买域名、不占服务器 80/443，也不需要访问者安装 Tailscale 客户端或建立 SSH 隧道。服务器必须能访问 Tailscale 控制面、转发服务和证书服务；该账号策略需要允许 Funnel。使用前需自行了解服务条款、地区可用性和服务限制，不保证任意网络都可用。

1. 首次安装，终端显示设备授权链接。在电脑浏览器打开，以自己的 Tailscale 账号登录并批准设备。若有设备审批策略，还需在账号后台批准。
2. 如命令提示启用 HTTPS 或 Funnel，打开显示的链接批准。安装器等待授权并检查公网 HTTPS；超时或失败不会显示安装完成信息，也不会降级成公网 HTTP。
3. 检查通过后，使用安装器显示的 `https://设备名.你的网络.ts.net/login` 地址登录助手。回到网站目录生成新的识别链接，再连接扩展。每台电脑无需重复设置隧道。
4. 重启、升级时保留 `.env` 和 `tailscale_data` 数据卷，继续使用同一设备身份。更改设备名称、tailnet 域名或删除设备可能改变地址；账号撤销授权或节点密钥到期时需重新授权。不要依赖每次启动都会换域名的临时隧道。

Funnel 是公网入口，仍需助手管理员密码；勿将初始密码或识别链接分享给他人。任务和网站账号保存在自己的服务器，不在扩展作者的公共服务器中托管；公网入口仍依赖 Tailscale 服务，不能宣传为完全无第三方依赖。

代理以 userspace 模式运行，不需要宿主机安装 Tailscale、TUN 设备、特权容器或额外的网络管理权限。`--loaded --tunnel` 需事先导入 Compose 指定版本的 `tailscale/tailscale` 镜像；离线授权和公网访问不受支持。普通卸载保留设备身份卷；彻底卸载删除本地身份卷和未共享的隧道镜像。Tailscale 账号中的设备记录需在自己的账号后台删除，卸载器不会删除第三方账号或修改账号策略。切换到其他访问方式时，安装器先停止原隧道入口。

### IP＋端口：仅可信网络的基础访问

**IP 模式会把应用端口发布到服务器网络，使用 HTTP 明文传输密码和会话，且不能自动连接浏览器扩展。仅在可信网络使用；公网使用域名 HTTPS、免自有域名 HTTPS 或 SSH 隧道。** 防火墙和云安全组需允许所选端口。默认端口为 8080，可先修改 `.env` 中 `PORT` 再运行安装器。IPv6 地址会在链接中自动加方括号。

### 打开管理页面与修改密码

安装完成会显示对应的 `/login` 网页链接和初始登录密码。无需用户名，输入密码即可登录；登录后点击顶部 **“修改密码”**，输入当前密码、新密码和确认密码。新密码需为 12 至 256 字符。修改成功后所有设备需使用新密码重新登录。

密码哈希保存在数据卷的 SQLite 数据库，重启和升级后继续有效；`.env` 中的 `AUTOSIGN_ADMIN_PASSWORD` 仅用于首次初始化。后台修改后，初始密码立即失效，再次运行安装器会提示使用后台修改后的密码，不再显示无效的初始密码。请自行保存新密码，服务不提供明文查询。修改登录密码不会更换 `AUTOSIGN_SECRET_KEY`，也不会影响已加密的站点会话。

选择 SSH 私有访问时，服务仅监听服务器的 `127.0.0.1:8080`。在你自己的电脑运行（替换用户名和 IP）：

```sh
ssh -L 8080:127.0.0.1:8080 用户@服务器IP
```

保持 SSH 连接，在电脑浏览器打开 `http://localhost:8080`，输入管理员密码。若本机 8080 被占用，可改用 `ssh -L 18080:127.0.0.1:8080 用户@服务器IP`，然后访问 `http://localhost:18080`。

Netcatty 的主机浏览器也可通过当前 SSH 会话打开服务器本地链接。普通 Chrome 中的 `localhost` 指自己的电脑，需先建立上述隧道。

Linux Compose 部署使用仓库内的 Playwright seccomp 配置并以非 root 用户运行 Chromium 沙箱。`setup.sh` 每次启动前都会检查沙箱；启动失败时应检查服务器的 user namespace 和 seccomp 配置。该配置不会添加 `SYS_ADMIN` capability，也不会关闭 seccomp。真实 Linux Docker 环境的检查结果才可用于判断部署是否可用；macOS 上的测试不能验证这些内核条件。配置来源、版本和许可见 [第三方声明](../THIRD_PARTY_NOTICES.md)。

## 添加网站与账号

普通网页推荐按以下顺序操作：

1. 从安装器检查通过后显示的 HTTPS 地址登录助手。公网 IP＋端口 HTTP 无法自动连接扩展，先从“使用指引 → 连接设置”选择免自有域名 HTTPS 或已有 HTTPS 反代；也可按[选择访问方式](#选择访问方式)在服务器原安装目录配置。
2. 在自己电脑的 Chrome 打开“网站目录”，展开“首次安装或更新浏览器扩展”，从当前部署下载配套 ZIP。解压后在 `chrome://extensions` 开启开发者模式并加载含 `manifest.json` 的解压文件夹，固定到工具栏。添加其他网站无需重复安装。
3. 在另一个 Chrome 标签页登录目标网站，打开实际 HTTPS 签到页。
4. 回到目录，点击“生成识别链接”并复制，切回目标网站标签页，在扩展中粘贴链接，核对助手地址，点击“识别网页并连接账号”。按 Chrome 提示授予当前网站及助手站点权限。
5. 回到目录，点击“打开任务并验证”。任务默认暂停。未找到按钮时，先返回规则设置补充实际按钮或步骤；否则核对规则并开始验证。
6. 验证成功或今日已签到后，选择时间和时区，开启每日计划。同一网站的其他账号分别添加任务。

目录与表单会根据当前浏览器地址显示“当前地址支持扩展连接”或“查看连接设置”；它只判断地址格式是否受支持，不代表已测试扩展权限或服务器连通性。顶部“使用指引”与向导中的帮助链接始终可访问。识别链接是扩展连接码，5 分钟失效且只能成功使用一次，不是安装包；过期或更换助手地址后重新生成。

网站表单中的“生成一次性连接链接”只连接当前表单，不会另建任务。手动粘贴网址会预填网站名称和域名，再使用同一个扩展连接。现有平台接口则走下方专用表单。

### 已适配平台

WorkBuddy、百度千帆、MiniMax Code、Qoder 国内版、Link AI、WPS 灵犀、Trae Work、Coze 和华为码道使用专用表单。按页面提示提供自己的账号凭据，无需设置接口地址、请求 JSON 或成功条件。具体字段与各平台限制见[平台说明](../PLATFORMS.md)。

### NodeSeek、hostloc 和其他网页

在“连接账号”页填写实际签到页。普通使用推荐浏览器扩展：先在 Chrome 登录目标网站，回到助手生成一次性链接，切回目标网站标签页，在扩展中粘贴并点击“识别网页并连接账号”。配置页连接会把会话和识别到的按钮填回当前表单；网站目录中的“从当前网页自动识别并添加”会直接新建一个暂停任务。两种入口使用同一个扩展。首次从当前部署页面下载配套 ZIP 安装，详见[扩展指南](../browser-extension/README.md)。若旧扩展只显示“导出并下载”，先下载新包并覆盖原扩展文件夹，再在 `chrome://extensions` 刷新；也可移除旧版本并加载新版文件夹。

目标网站必须使用 HTTPS。签到助手可使用自己的 HTTPS 域名、免自有域名 HTTPS 或已有 HTTPS 反代；通过 SSH 隧道在本机访问时，也可使用 `http://localhost` 或 `http://127.0.0.1`。公网 HTTP 不支持扩展直连。没有域名或 80/443 已被占用不必改用 Cookie、Token，可先按页面“使用指引 → 连接设置”配置 HTTPS 入口。Cookie、Token 和会话文件仅作为备用方式：

- **粘贴登录 Cookie：** 从自己已登录网站请求的 Cookie 头中复制值，只粘贴内容，无需 `Cookie:` 前缀。此方式只连接签到页所在主机，适合只依赖 Cookie 的网站。
- **上传登录会话文件：** 在扩展弹窗展开“改用本机文件导出”，下载 JSON 后回到助手上传。备用方式也可粘贴完整 JSON。
- **粘贴登录 Token：** 在“账号连接方式”中选择“手动粘贴 Token”，粘贴已登录请求的 Authorization 值，可带 Bearer 前缀，不含 Authorization: 字段名。不是模型 API Key，无需 JSON。Token 默认写入当前来源的 localStorage.access_token，可在高级设置修改存储键名。Token 只写入签到页面的准确来源，不会成为所有资源请求的全局请求头。仅使用请求头、sessionStorage 或还需要额外登录资料的网站，仍需专用适配或完整会话；Token 过期需重新登录更新。

不要同时提供 Cookie、Token 和会话文件，也不要把凭据发到聊天或 GitHub。编辑时保持原连接方式、凭据留空会保留已保存的登录状态；切换方式需提供新凭据，旧账号的 Cookie、本地存储和刷新令牌不会混入。

### 用 Chrome 扩展导出会话（备用）

扩展主流程是通过一次性链接直连助手；用户也可在弹窗展开“改用本机文件导出”，把 Cookie 与 localStorage 导出到本机后上传。账号密码和验证码由用户直接在网站输入，扩展不会读取密码。Chrome 网上应用店发布前，首次安装仍需开启开发者模式。

扩展不读取 sessionStorage、IndexedDB 或其他域名的本地存储。使用这些存储方式或第三方登录的网站可能需要单独适配。上传的会话文件等同登录凭据，请只上传到自己信任的服务并在导入后删除。

文件导入最多 **512 KiB**。保存后会加密，编辑页不回显；勾选清空会移除凭据并暂停任务。成功执行后会保存更新后的浏览器会话。文件导出统一使用扩展的备用入口，无需本机安装 Python 或重新打开独立浏览器。

接着配置实际签到页、允许的域名与页面规则：

- 允许的域名一行一个，包含页面、登录、接口及必要图片或脚本资源所在域名；不包含协议、端口或路径，不支持通配符。未列出的主机、WebSocket 和 Service Worker 会被阻止，页面可能因此需要补充必要域名。
- 简单签到只需填写“签到按钮文字”，系统生成点击规则。多步操作再展开高级设置：步骤是最多 20 项的 JSON 数组，只支持 `click`、`fill` 和 `wait`。每项填写唯一的 `selector`，`fill` 另填 `value`；`timeout_ms` 可设为 500 至 30000 毫秒。起始页面会自动打开，不需要另外配置打开页面。
- 内嵌签到页（iframe）也会自动检查。扩展 v1.3.1 起识别可见同源内嵌页面中的按钮和提示；服务器仅在已允许域名内的可见页面执行，隐藏页面不参与判断，多个可见元素匹配时停止。ChatflowsAI 等网站使用正常控制台签到地址，不要把含临时登录参数的内嵌链接保存为每日任务地址。旧版识别漏掉按钮时，更新服务器和配套扩展后，在原任务编辑页重新连接、保存并验证。
- 新建浏览器网站默认启用常见结果自动识别；可选填准确的“签到成功文字”和“今日已签到文字”提高识别准确度。手工提示按完整可见元素精确匹配，两种提示需不同。关闭自动识别后必须提供成功文字。
- “需要重新登录”和“需要人工验证”选择器应只在对应状态出现。匹配后停止流程并暂停任务，更新会话后重新验证，再开启每日计划。

验证失败时会区分“没有找到可见的签到操作”“匹配到多个可见元素”“操作等待超时”和“操作完成但没有明确结果”。最后一种情况应先到官网核对是否已经签到，再修改成功提示；不要仅因未识别结果就重复领取。

NodeSeek 和 hostloc 的签到规则需要按当前页面自行确认；本项目没有为它们预置经过真实账号验证的选择器或成功提示。LINUX DO 当前不提供自动签到任务，因为没有已确认的独立签到操作。整次浏览器任务最多运行 90 秒，不会自动重试。步骤格式见[源码示例](https://github.com/mini3535/multi-site-checkin/tree/main/examples)。示例选择器只说明格式，不能直接用于这些社区。

### 高级 HTTP 请求

适合已经知道真实签到接口的网站。先在浏览器正常登录，在 Network 中找到手动签到产生的请求，再按真实请求填写：

- 请求方法支持 GET/POST。请求头使用 JSON 对象，例如 `{"Cookie":"session=你的值","Content-Type":"application/json"}`。
- POST 请求体是原样发送的字符串；GET 不能有请求体。JSON 或普通表单需要设置对应的 Content-Type。
- 成功条件必须填写，且要求 HTTP 状态为 2xx。文本模式检查指定字符串；JSON 模式比较指定字段与期望 JSON 值。
- JSON 路径如 `data.checked`、`items.0.ok`。期望值填写 JSON 字面量：`true`、`0`，或带双引号的 `"成功"`。路径留空表示比较整个 JSON。

编辑时，请求头与请求体不回显，留空保留已有值；清空选项会同时清空两项。HTTP 请求不跟随重定向，不自动登录，也不生成动态签名或一次性参数。

默认所有执行器只允许公网目标。需要访问自己的内网网站时，才设置 `AUTOSIGN_ALLOW_PRIVATE_TARGETS=true` 并执行 `docker compose up -d --pull never`。

## 执行规则与数据

默认时区为 `Asia/Shanghai`，每个任务可选择自己的时区。每天到时执行；停机恢复后只补一次，不重放停机期间的每一天。任务当天已有成功记录或“今日已签到”记录时，当天不再自动执行。手动执行仍由你主动触发。

站点适配器或浏览器流程识别到登录失效、人工验证时会自动暂停该任务。登录 Cookie 或浏览器会话过期后，需要重新登录、更新配置，按“验证签到 → 每天自动执行”恢复任务。

失败不自动重试，避免网站已处理请求但响应丢失时重复提交。重启时中断的执行会标记为结果未知；先核对网站状态，再决定是否手动执行。日志只保存结果类别、固定说明、HTTP 状态与耗时，不保存网页正文、截图或登录凭据。

浏览器、HTTP 和站点适配任务都在独立进程中执行，整次执行最多 90 秒，包含 DNS、连接和响应等待。超时后终止本次进程并记录结果未知，不会重新发送请求；请先核对网站签到状态。

SQLite 数据保存在 Compose 数据卷 `data` 的 `/data/autosign.db`。任务、Cookie 与浏览器会话使用 `.env` 中的 `AUTOSIGN_SECRET_KEY` 加密；必须同时保留数据库和密钥。只运行一个实例，不要扩容或同时运行多个调度进程。

## 日常管理

在安装目录运行：

```sh
# 查看状态和日志
docker compose ps
docker compose logs --tail=100 -f app

# 停止 / 再次启动
docker compose stop
docker compose start

# 修改 .env 后应用当前镜像
docker compose up -d --pull never

# 拉取最新预构建镜像、检查沙箱并启动
sh setup.sh
```

源码部署需要先 `git pull --ff-only` 获取最新文件，再选择 `sh setup.sh` 切换至预构建镜像，或 `sh setup.sh --local` 重新构建。快速安装用户如需更新部署脚本和配置，先下载最新安装器，再指定原安装目录执行，命令见[更新已有服务器](#更新已有服务器)。它保留 `.env` 和数据卷，但会更新项目提供的部署文件，因此自行修改过 Compose 时请先备份。

升级时保留原 `.env`，尤其是 `AUTOSIGN_SECRET_KEY`。保持 Compose 项目名 `autosign` 和数据卷配置不变，旧部署才能继续使用同一名卷。`docker compose down` 不删除名卷；`docker compose down -v` 会永久删除全部任务与记录，不要用于普通升级。只运行一个服务实例。

### 卸载服务

以下操作在服务器的交互终端完成。先核对原安装目录，将需要的数据库、密钥和备份移到安装目录外，再执行。以下以 `/root/multi-site-checkin` 为例，自定义安装时请替换为实际目录。

**最新版和旧版本统一使用下面的入口**，先下载最新卸载器，再沿用原部署配置清理，无需先升级镜像或重装服务：

```sh
cd /root/multi-site-checkin &&
  curl -fsSL https://raw.githubusercontent.com/mini3535/multi-site-checkin/main/install.sh -o /tmp/autosign-install.sh &&
  sh /tmp/autosign-install.sh "$PWD" --uninstall --purge
```

核对显示的“卸载目录”和删除范围，输入大写 `DELETE` 确认，其他输入取消。彻底卸载需要交互终端；不能像更新命令一样添加 `< /dev/null`，也不要通过管道代替人工确认。命令只更新 `uninstall.sh`，保留原 Compose 配置，不拉取镜像、不构建、不启动应用，也不会进入域名/IP 选择。GitHub 更新不会自动修改服务器上的旧脚本。

**只想停用并保留数据**，使用下面这组命令代替彻底卸载：

```sh
cd /root/multi-site-checkin &&
  curl -fsSL https://raw.githubusercontent.com/mini3535/multi-site-checkin/main/install.sh -o /tmp/autosign-install.sh &&
  sh /tmp/autosign-install.sh "$PWD" --uninstall
```

普通卸载移除项目容器和网络，保留数据卷、任务、密码、会话、证书、隧道身份、镜像、`.env` 和安装目录；之后在原目录运行 `sh setup.sh` 可使用原数据恢复部署。确认已取得最新版脚本的用户也可直接运行 `sh uninstall.sh` 或 `sh uninstall.sh --purge`。

彻底卸载输入 `DELETE` 后会删除：

- 本项目容器、网络和 Compose 数据卷，包括任务、记录、密码、会话、HTTPS 证书及本地 Tailscale 隧道身份。
- 当前配置与容器使用的项目镜像、官方仓库的旧版本标签，以及带本项目来源标签的悬空旧镜像。
- 整个安装目录，包括源码、`.env`、`backups/`、目录内的自定义文件和个人修改。需要的备份必须先移到安装目录外。

其他容器仍在使用的镜像会保留并列出；不会强制删除，也不执行全局 Docker 清理。以下内容需要自己按需收尾：

- 安装目录外下载的安装器、ZIP、镜像压缩包、备份和自定义绑定挂载数据。
- 现有宝塔、Nginx 等外部反代中的项目域名配置，以及单独为本项目添加的 DNS、防火墙或云安全组规则。
- Tailscale 账号后台的设备记录；清理服务器上的本地身份卷不会删除第三方账号记录。
- 自己电脑 Chrome 中的连接器：打开 `chrome://extensions`，找到“签到助手浏览器连接器”并点击“移除”，再删除不需要的解压文件夹、ZIP 和会话导出文件。

Docker 本身、共享镜像、共享构建缓存和其他项目保留。不要用全局 Docker 清理代替项目卸载。

安装目录必须是独立的项目目录；系统目录、用户主目录或包含挂载点的目录会拒绝递归删除，符号链接不会跟随到外部目录。清理出错会报告未完成；镜像删除失败时保留安装文件供重试。删除成功后原终端仍可能停在已经删除的路径，请运行 `cd ~`。

**已经执行过旧版彻底卸载、原目录仍在时**，也使用本节开头的最新版入口补充清理镜像和安装文件。原目录中需仍有 `compose.yaml` 和 `setup.sh`；缺少部署文件时命令会停止，不会建立新部署。先根据原部署信息核对残留，不要重新安装一个空项目用于卸载。镜像清理失败时先修复上方 Docker 或权限错误，再重新运行最新版入口。

### 检查卸载结果

先确认卸载输出没有“卸载未完成”或其他错误。彻底卸载成功后，原目录应不存在，项目容器、数据卷和网络应为空。先执行 `cd ~` 离开已删除的路径，再运行以下只读检查；两处安装路径都需替换为自己的原目录：

```sh
cd ~
if [ -e /root/multi-site-checkin ] || [ -L /root/multi-site-checkin ]; then
  echo '原安装目录仍存在，请检查卸载输出。'
else
  echo '原安装目录已删除。'
fi
docker ps -a --filter label=com.docker.compose.project=autosign
docker volume ls --filter label=com.docker.compose.project=autosign
docker network ls --filter label=com.docker.compose.project=autosign
docker image ls ghcr.io/mini3535/multi-site-checkin
docker image ls --filter label=org.opencontainers.image.source=https://github.com/mini3535/multi-site-checkin
```

容器、数据卷和网络列表只剩表头表示已清理。镜像查询包含官方标签和带项目来源标签的本地镜像；若仍有内容，先对照卸载器输出的“保留共享镜像”，其他容器仍在使用时会保留并列出。自定义镜像及 Caddy、Tailscale 等部署镜像按原 Compose 和卸载输出核对，不能仅凭官方镜像列表判断。检查命令报错时，先恢复 Docker 连接或权限，再检查，不能将报错当作空列表。

普通卸载后目录、数据卷和镜像仍在是预期结果。外部反代、账号设备记录和电脑扩展按上一节单独核对。以上命令不执行删除，也不会影响其他项目。

## 备份与恢复

先停服务取得一致的 SQLite 备份，再打包整个数据目录，最后启动。备份文件包含凭据，应限制访问。以下命令在项目目录运行：

```sh
umask 077
mkdir -p backups
docker compose stop app
docker compose run --rm --no-deps --pull never -T app tar -C /data -czf - . > backups/autosign-data.tar.gz
cp .env backups/autosign.env
docker compose start app
```

将 `autosign-data.tar.gz` 和 `autosign.env` 一起保存在可信位置。固定文件名会覆盖旧备份，长期保留时先改成带日期的名称。备份失败时先排查原因，再运行 `docker compose start app` 恢复服务。

上面的应用备份不包含 `tailscale_data` 设备身份卷。使用免自有域名模式且需要迁移后沿用同一设备地址时，还需停止 `tunnel` 并通过可信的 Docker 数据卷备份方式保存该卷；迁移时先停止旧设备，恢复身份卷后再启动新设备。不要让两台服务器同时使用同一份设备身份。仅恢复应用备份时，可以重新授权，但生成的访问地址可能变化。

新服务器恢复时，先放好相同部署文件与备份，暂不运行安装脚本，避免先启动空数据库。以下命令在恢复目录运行，会覆盖 `.env`，只应在确定要恢复的部署上执行：

```sh
docker compose stop app
cp backups/autosign.env .env
chmod 600 .env
# 只准备镜像，此时不启动应用
docker compose pull app
docker compose run --rm --no-deps --pull never app python -c "from pathlib import Path; assert not any(Path('/data').iterdir()), '恢复需要空数据卷；请先备份并确认恢复目标。'"
docker compose run --rm --no-deps --pull never -T app tar -C /data -xzf - < backups/autosign-data.tar.gz
# 使用已准备的镜像检查沙箱、启动并检查健康状态
sh setup.sh --loaded
```

从完整源码恢复且选择本地构建时，将上面的 `docker compose pull app` 替换为 `docker compose -f compose.yaml -f compose.build.yaml build`；离线恢复则先 `docker load -i 镜像文件.tar`。两种方式都可在恢复数据后运行 `sh setup.sh --loaded`。不要先对空数据卷运行 `sh setup.sh` 或 `sh setup.sh --local`，因为它们会直接启动应用。

检查命令会拒绝向非空数据卷恢复，避免与已有 SQLite 文件混合。新服务器使用空名卷即可；已有部署请先备份现有数据，再选择空卷恢复。恢复必须使用备份对应的原数据密钥，并保持 Compose 项目名和卷配置一致。

## 发布镜像（仓库维护者）

默认安装依赖 GHCR 上已经公开的成品镜像。首次发布需完成以下步骤：

1. 将镜像发布工作流合入 `main`，在仓库 **Actions** 中运行工作流。工作流需要仓库内容读取权限与 Packages 写入权限，目标平台为 `linux/amd64`、`linux/arm64`。
2. 构建完成后，进入账号或仓库的 **Packages**，打开 `multi-site-checkin` 包的 **Package settings**，将可见性设为 **Public**。源码仓库公开不代表包会自动公开。
3. 在没有 GHCR 登录凭据的环境验证匿名下载：`docker pull ghcr.io/mini3535/multi-site-checkin:latest`。在目标 Linux 服务器运行 `sh setup.sh`，确认 Chromium 沙箱检查与服务健康检查通过。
4. 核对当前提交的 `validate`、`build` 和 `publish` 全部成功。扩展 ZIP、JS 测试或任一架构检查失败时，不会更新 `latest`。构建成功与真实账号签到成功分别验证。

如果在自己的分叉仓库发布，首次构建和包公开需要单独完成。GHCR 的权限或标签报错应先核对仓库与包状态。

## 配置、测试与验证状态

配置说明见 `.env.example`。管理员密码为 12 至 256 字符，首次初始化自动生成，登录后可在后台修改；访问方式使用安装器参数，端口使用 `PORT`。Docker 已包含 Chromium，无需在服务器另外安装桌面浏览器。

源码开发需要 Python 3.12+，在完整仓库安装依赖与 Chromium；Node.js 22+ 用于浏览器扩展测试。发布前运行：

```sh
python -m unittest discover -s tests -v
node --test browser-extension/popup.test.cjs
python tools/package_extension.py --check
```

改过扩展时，先执行 `python tools/package_extension.py` 更新仓库内唯一的 ZIP，再提交源码和 ZIP。打包只包含运行所需的六个文件，不含测试、旧版本或个人会话；同一源码生成相同 ZIP。CI 会校验 ZIP 与源码、弹窗与 manifest 版本以及浏览器脚本语法和交互，全部通过后才构建镜像。

双架构构建会在实际 Linux 镜像中验证 Python 测试、Chromium 沙箱、Compose 启动、健康接口、登录页、卸载保留数据与重新部署。当前提交的结果以 Actions 为准。本机测试或历史部署成功均不代表服务器已运行最新代码；更新后应核对页脚构建版本，再重新下载配套扩展。

平台与论坛仍需要用自己的账号验证；扩展建议不能替代真实签到检查。LINUX DO 和豆包目前只提供信息入口。
