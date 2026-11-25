# 多站点签到助手

一个部署在自己服务器上的单用户签到服务：网页管理网站与账号，每天按计划运行，保存执行结果。支持站点适配器、浏览器页面流程，以及高级 HTTP 请求三种方式。一个网站账号对应一条任务，用任务名称区分账号。

本项目采用 MIT 许可证，详见 [LICENSE](LICENSE)。第三方软件和引用代码的来源见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 快速流程

1. 在 Linux 服务器安装 Docker Engine、Docker Compose 插件 **2.24.0 或更新版本**以及 `curl`。
2. 下载并运行安装脚本；默认安装到当前目录下的 `multi-site-checkin`，无需 Git、Python 或本地编译：

   ```sh
   curl -fsSL https://raw.githubusercontent.com/mini3535/multi-site-checkin/main/install.sh -o autosign-install.sh && sh autosign-install.sh
   ```

3. 保存安装结束时显示的网页链接和初始登录密码，按所选方式打开网页。登录后可点击顶部“修改密码”。
4. 在自己的电脑正常登录目标网站，导出浏览器会话；在管理页面添加站点账号、上传会话，并按网站当前页面填写签到步骤与成功提示。
5. 先保持任务暂停，点击“立即执行”，去网站核实签到成功后，再启用每日计划。
6. 任务遇到登录失效或人工验证时会暂停；人工完成网站验证、更新会话后，再手动测试并启用。
7. 需要卸载时，先进入**原安装目录**再执行卸载命令。以下以 `/root/multi-site-checkin` 为例；如果安装在 `/opt/multi-site-checkin` 或其他位置，请替换目录。

   **卸载服务，保留任务、密码和配置：**

   ```sh
   cd /root/multi-site-checkin && sh setup.sh --uninstall
   ```

   **彻底卸载，永久删除本项目数据卷和 `.env`：**

   ```sh
   cd /root/multi-site-checkin && sh setup.sh --uninstall --purge
   ```

   彻底卸载需在终端输入 `DELETE` 确认，请先备份。两种方式都会保留安装目录、代码、Docker 镜像和备份文件；完整说明及旧版安装目录的更新方法见[卸载服务](#卸载服务)。

**发布状态（2026-11-25）：预构建镜像已公开发布，支持 Linux `amd64` 和 `arm64`。** GitHub Actions 运行 `45533651799` 已在两个原生架构完成构建、110 项测试、Chromium 沙箱启动与服务健康检查。两个架构的镜像清单均可匿名读取；目标 amd64 服务器已使用空 Docker 凭据目录完成匿名拉取，并通过实际部署与容器内 DNS、HTTPS 检查。arm64 已在 CI 中运行验证，尚未在另一台目标服务器完成部署验收。已遇到的故障、处理办法和验证边界见 [部署问题记录](DEPLOYMENT_ISSUES.md)。

NodeSeek 和 hostloc 尚未提供经真实账号验证的签到规则；LINUX DO 目前只作为社区链接展示。

## 当前能做什么

| 入口 | 已实现的能力 | 使用前还需要什么 |
| --- | --- | --- |
| NodeSeek | 浏览器会话导入与页面步骤执行入口 | 确认当前签到页面、操作步骤和结果文字；尚未实测 |
| hostloc | 浏览器会话导入与页面步骤执行入口（`https://hostloc.com/forum.php`） | 先确认该站当前是否提供每日签到，再配置规则；尚未实测 |
| LINUX DO | 目录中的社区信息卡片 | 未确认有独立每日签到按钮，因此不提供自动签到任务 |
| 其他论坛或网站 | 自定义浏览器页面、允许的域名和操作步骤 | 每个网站单独配置并验证 |
| 高级 HTTP | 每天发送一个 GET/POST 请求并验证响应 | 已知的真实签到接口、请求凭据和成功条件 |

NodeSeek (`https://www.nodeseek.com/board`) 和 hostloc (`https://hostloc.com/forum.php`) 的预填地址只是浏览器入口，不代表已确认签到页或可用规则；Hostloc 使用官方 `.com` 域名。LINUX DO 当前没有已确认的独立每日签到操作；每日访问或浏览不等同于签到成功，不能用普通页面文字冒充成功条件。服务不会自动识别任意网站、发现全部奖励任务或生成网页规则。遇到验证码、人机验证或登录失效时，需要人工完成正常验证并更新会话。

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

`sh setup.sh` 默认拉取镜像。镜像包含 Python 3.12、Playwright、Chromium 及运行依赖，服务器无需单独安装这些组件。终端交互运行且未传入访问方式参数时，每次都会显示“域名 HTTPS”和“无域名 IP＋端口”两项选择，已有 `.env` 时也会询问。首次运行生成随机管理员密码和数据密钥，保存至权限为 `600` 的 `.env`，检查 Chromium 沙箱，然后启动服务并等待健康检查。安装结束会显示网页链接和初始登录密码。已有密码、数据密钥和数据卷会保留。

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

### 选择域名或无域名访问

在终端运行安装器或 `sh setup.sh`，未显式传入访问方式时，都会显示以下两项菜单，即使安装目录已有 `.env` 也不会跳过：

```text
1) 域名 HTTPS
2) 无域名 IP＋端口
```

输入 `1` 后填写域名，或输入 `2` 后填写服务器 IP；没有默认选项，直接回车会取消，不会自动切换到 SSH 私有访问。也可直接传参跳过菜单：

```sh
# 有域名：先将 A/AAAA 记录解析到本机，确保 80/443 空闲且公网可达
sh setup.sh --domain sign.example.com

# 无域名：填写服务器实际 IP，以 http://服务器IP:8080/login 访问
sh setup.sh --no-domain 203.0.113.8

# SSH 私有访问能力保留，不在上述两项菜单中显示；显式使用以下参数
sh setup.sh --private
```

上面的域名和 IP 均为示例，请替换。快速安装器也支持传参，例如 `sh autosign-install.sh /opt/multi-site-checkin --no-domain 你的服务器IP`。重复运行快速安装器也会显示菜单。非交互运行且未指定访问方式时，新安装默认使用 SSH 私有访问；已有部署保留原配置。自动化部署建议显式指定访问方式。之后可重新运行上述命令切换方式，数据密钥、任务和密码不会重置。

域名模式使用独立的 Caddy 容器自动管理公开可信的 HTTPS 证书；需能访问证书服务，并遵守所用证书机构的服务条款。`--loaded` 离线安装域名模式时，还需事先导入 `caddy:2-alpine` 镜像。域名 HTTPS 检查未通过时脚本报错。

已有 Nginx、Caddy 或其他 HTTPS 代理占用 80/443 时，保留现有代理，配置域名证书并将上游指向 `127.0.0.1:8080`（同一宿主机），然后运行 `sh setup.sh --domain sign.example.com --external-proxy`。此选项不启动 Caddy，仍检查域名 HTTPS。代理位于其他容器时需自行配置可达的容器网络。脚本不会修改现有代理、DNS、服务器防火墙或云安全组。

**无域名模式会把应用端口发布到服务器网络，使用 HTTP 明文传输密码和会话。仅在可信网络使用；公网推荐域名 HTTPS 或 SSH 隧道。** 防火墙和云安全组需允许所选端口。默认端口为 8080，可先修改 `.env` 中 `PORT` 再运行安装器。IPv6 地址会在链接中自动加方括号。

### 打开管理页面与修改密码

安装完成会显示对应的 `/login` 网页链接和初始登录密码。无需用户名，输入密码即可登录；登录后点击顶部 **“修改密码”**，输入当前密码、新密码和确认密码。新密码需为 12 至 256 字符。修改成功后所有设备需使用新密码重新登录。

密码哈希保存在数据卷的 SQLite 数据库，重启和升级后继续有效；`.env` 中的 `AUTOSIGN_ADMIN_PASSWORD` 仅用于首次初始化。后台修改后，初始密码立即失效，再次运行安装器会提示使用后台修改后的密码，不再显示无效的初始密码。请自行保存新密码，服务不提供明文查询。修改登录密码不会更换 `AUTOSIGN_SECRET_KEY`，也不会影响已加密的站点会话。

选择 SSH 私有访问时，服务仅监听服务器的 `127.0.0.1:8080`。在你自己的电脑运行（替换用户名和 IP）：

```sh
ssh -L 8080:127.0.0.1:8080 用户@服务器IP
```

保持 SSH 连接，在电脑浏览器打开 `http://localhost:8080`，输入管理员密码。若本机 8080 被占用，可改用 `ssh -L 18080:127.0.0.1:8080 用户@服务器IP`，然后访问 `http://localhost:18080`。

Netcatty 的主机浏览器也可通过当前 SSH 会话打开服务器本地链接。普通 Chrome 中的 `localhost` 指自己的电脑，需先建立上述隧道。

Linux Compose 部署使用仓库内的 Playwright seccomp 配置并以非 root 用户运行 Chromium 沙箱。`setup.sh` 每次启动前都会检查沙箱；启动失败时应检查服务器的 user namespace 和 seccomp 配置。该配置不会添加 `SYS_ADMIN` capability，也不会关闭 seccomp。真实 Linux Docker 环境的检查结果才可用于判断部署是否可用；macOS 上的测试不能验证这些内核条件。配置来源、版本和许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 添加网站与账号

登录后打开“添加网站”，也可直接访问 `/sites`。先选网站，为账号起一个名称，例如“NodeSeek · 主账号”。同一网站的其他账号另建任务。建议先暂停保存，手动执行并核对网站自己的签到状态，确认后再启用每日计划。

### NodeSeek、hostloc 和其他网页

先在你自己的电脑导出一个新浏览器会话，再在服务器管理页上传。不要把登录 Cookie 或会话文件发到聊天中。工具使用独立的新浏览器窗口，不会读取现有浏览器个人资料。

在你自己的电脑克隆或下载完整仓库；服务器快速安装目录不包含导出工具。本机需要 Python 3.12 或更新版本，在源码目录运行：

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python tools/export_session.py --site nodeseek --output nodeseek-session.json --browser chrome
```

`--browser chrome` 使用电脑已安装的 Chrome。打开窗口后，手动登录并完成网站要求的正常验证，回到终端按 Enter 导出。hostloc 使用 `--site hostloc` 并更换输出文件名。工具不会覆盖已有文件；更新会话时选择新文件名，或先自行处理旧文件。

若没有安装 Chrome，先安装项目使用的 Chromium，再省略 `--browser chrome`：

```sh
python -m playwright install chromium
python tools/export_session.py --site hostloc --output hostloc-session.json
```

其他网站使用自己的登录地址：

```sh
python tools/export_session.py --url https://example.com/login --output forum-session.json --browser chrome
```

在任务中上传生成的 JSON，或粘贴完整 JSON。文件最多 **512 KiB**，服务会加密保存；编辑时留空保留原会话，勾选清空才会移除。成功执行后会保存更新后的浏览器会话，页面不回显。浏览器会话 JSON 与旧版恩山适配器接收的 Cookie 文本是不同格式。升级前创建的恩山任务会保留原有适配器；当前新建目录已改为 NodeSeek、hostloc 和 LINUX DO。

接着配置实际签到页、允许的域名与页面规则：

- 允许的域名一行一个，包含页面、登录、接口及必要图片或脚本资源所在域名；不包含协议、端口或路径，不支持通配符。未列出的主机、WebSocket 和 Service Worker 会被阻止，页面可能因此需要补充必要域名。
- 步骤是最多 20 项的 JSON 数组，只支持 `click`、`fill` 和 `wait`。每项填写唯一的 `selector`，`fill` 另填 `value`；`timeout_ms` 可设为 500 至 30000 毫秒。起始页面会自动打开，不需要另外配置打开页面。
- “签到成功文字”和“今日已签到文字”匹配完整、可见元素的确切文字，不能只写网页正文里常见的模糊词。两种提示需要不同。
- “需要重新登录”和“需要人工验证”选择器应只在对应状态出现。匹配后停止流程并暂停任务，更新会话后再手动测试与启用。

NodeSeek 和 hostloc 的签到规则需要按当前页面自行确认；本项目没有为它们预置经过真实账号验证的选择器或成功提示。LINUX DO 当前不提供自动签到任务，因为没有已确认的独立签到操作。整次浏览器任务最多运行 90 秒，不会自动重试。步骤示例见 [examples/README.md](examples/README.md)。示例选择器只说明格式，不能直接用于这些社区。

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

站点适配器或浏览器流程识别到登录失效、人工验证时会自动暂停该任务。登录 Cookie 或浏览器会话过期后，需要重新登录、更新配置，再手动测试和启用。

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

源码部署需要先 `git pull` 获取最新文件，再选择 `sh setup.sh` 切换至预构建镜像，或 `sh setup.sh --local` 重新构建。快速安装用户如需更新部署脚本和配置，在原安装脚本所在目录重新运行 `sh autosign-install.sh 原安装目录`；它保留 `.env` 和数据卷，但会更新项目提供的部署文件，因此自行修改过 Compose 时请先备份。

升级时保留原 `.env`，尤其是 `AUTOSIGN_SECRET_KEY`。保持 Compose 项目名 `autosign` 和数据卷配置不变，旧部署才能继续使用同一名卷。`docker compose down` 不删除名卷；`docker compose down -v` 会永久删除全部任务与记录，不要用于普通升级。只运行一个服务实例。

### 卸载服务

先进入原安装目录再执行。以下以 `/root/multi-site-checkin` 为例，自定义安装时请替换为实际目录；不要直接在 `/root` 等其他目录执行 `sh setup.sh`。

```sh
# 卸载容器和网络，保留 .env、任务、密码、会话与 HTTPS 证书数据
cd /root/multi-site-checkin && sh setup.sh --uninstall

# 永久删除本项目 Compose 数据卷和 .env；需在终端输入 DELETE 确认
cd /root/multi-site-checkin && sh setup.sh --uninstall --purge
```

也可直接运行 `sh uninstall.sh` 或 `sh uninstall.sh --purge`。卸载不会拉取镜像、构建或启动应用，也不会进入域名/IP 选择。默认卸载后，保留安装目录与 `.env`，重新运行 `sh setup.sh` 可使用原数据部署。彻底卸载会清除数据库中的任务、记录、修改后的密码、站点会话与本项目的 Caddy 证书数据；无法撤销，请先按下文备份。非交互环境拒绝彻底删除数据。

源码、Docker 镜像、`backups/` 和自定义绑定挂载目录不会自动删除；脚本也不清理外部反代、域名解析、防火墙、Docker 本身或其他项目。使用现有 Nginx 等反代时，相关域名代理配置需自行移除。

旧版快速安装目录缺少卸载脚本时，可先下载新版安装器，再指定原目录与卸载参数：

```sh
curl -fsSL https://raw.githubusercontent.com/mini3535/multi-site-checkin/main/install.sh -o autosign-install.sh
sh autosign-install.sh /你的原安装目录 --uninstall
```

以上命令会更新项目提供的部署文件；如曾自行修改 Compose，请先备份并在原配置下卸载，避免遗漏自定义资源。

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
4. 核对 `amd64`、`arm64` 的构建和拉取结果后，再更新本文的首次发布状态。构建成功与真实账号签到成功分别验证。

如果首次构建或包公开尚未完成，请使用完整源码的 `sh setup.sh --local`，不要把 GHCR 的权限或不存在的标签报错误判成服务器安装失败。

## 配置、测试与验证状态

配置说明见 `.env.example`。管理员密码为 12 至 256 字符，首次初始化自动生成，登录后可在后台修改；访问方式使用安装器参数，端口使用 `PORT`。Docker 已包含 Chromium，无需在服务器另外安装桌面浏览器。

安装依赖后可运行：

```sh
python -m unittest discover -s tests -v
```

本地模拟站点用于验证任务流程，不代表 NodeSeek、hostloc 或 LINUX DO 的真实账号已测试通过。本地和双架构 CI 均已通过 110 项 Python 测试，其中 10 项使用模拟 Docker 命令验证部署脚本。双架构 CI 另已验证实际镜像、Linux Chromium 沙箱、Compose 启动、健康接口与登录页；目标 amd64 服务器已完成匿名拉取、沙箱、健康状态、登录页与容器出站网络验收。具体边界见 [部署问题记录](DEPLOYMENT_ISSUES.md)。上线前请先检查健康状态，再对每个真实账号手动测试。升级前创建的恩山签到任务仍使用原有站点适配器。
