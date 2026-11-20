# 多站点签到助手

一个部署在自己服务器上的单用户签到服务：网页管理网站与账号，每天按计划运行，保存执行结果。支持站点适配器、浏览器页面流程，以及高级 HTTP 请求三种方式。一个网站账号对应一条任务，用任务名称区分账号。

本项目采用 MIT 许可证，详见 [LICENSE](LICENSE)。第三方软件和引用代码的来源见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 快速流程

1. 在 Linux 服务器安装 Docker Engine 和 Docker Compose 插件（2.24.0 或更新版本）。
2. 克隆仓库并启动服务：

       git clone https://github.com/mini3535/multi-site-checkin.git
       cd multi-site-checkin
       sh setup.sh

3. 在服务器的 .env 中查看首次生成的管理员密码；从自己的电脑用 SSH 隧道安全打开管理页面并登录。
4. 在自己的电脑正常登录目标网站，导出浏览器会话；在管理页面添加站点账号、上传会话，并按网站当前页面填写签到步骤与成功提示。
5. 先保持任务暂停，点击“立即执行”，去网站核实签到成功后，再启用每日计划。
6. 任务遇到登录失效或人工验证时会暂停；人工完成网站验证、更新会话后，再手动测试并启用。

完整命令、每项配置含义、升级与备份流程见下方章节。NodeSeek 和 hostloc 尚未提供经真实账号验证的签到规则；LINUX DO 目前只作为社区链接展示。

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

服务器先安装 Docker Engine 和 Docker Compose 插件 **2.24.0 或更新版本**。按“快速流程”从 GitHub 克隆仓库后，在项目目录运行：

```sh
sh setup.sh
docker compose ps
```

镜像包含 Python 3.12、Playwright、Chromium 及其运行依赖，首次构建需要联网下载。脚本首次运行会生成随机管理员密码和数据密钥，保存至权限为 `600` 的 `.env`，随后启动服务并等待健康检查通过。已有 `.env` 会保留。管理员密码是 `.env` 中 `AUTOSIGN_ADMIN_PASSWORD` 的值；在服务器上用文本编辑器查看。

Linux Compose 部署使用仓库内的 Playwright seccomp 配置并以非 root 用户运行 Chromium 沙箱。该配置允许创建用户命名空间，但不会添加 `SYS_ADMIN` capability，也不会关闭 seccomp。升级 Docker、内核或 Playwright 后，请在目标 Linux 服务器确认浏览器能启动：

```sh
docker compose run --rm --no-deps app python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True, chromium_sandbox=True); b.close(); p.stop()"
```

此启动检查必须在真实 Linux Docker 环境运行；macOS 上的测试不能验证 Linux seccomp 和 user namespace 配置。seccomp 配置来源、版本和许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。

默认服务仅监听服务器的 `127.0.0.1:8080`。在你自己的电脑运行（替换用户名和 IP）：

```sh
ssh -L 8080:127.0.0.1:8080 用户@服务器IP
```

保持 SSH 连接，在电脑浏览器打开 `http://localhost:8080`，输入管理员密码。若本机 8080 被占用，可改用 `ssh -L 18080:127.0.0.1:8080 用户@服务器IP`，然后访问 `http://localhost:18080`。

也可自行配置 Nginx、Caddy 等反向代理与 HTTPS。反代和服务在同一宿主机时，将上游指向 `127.0.0.1:8080`；代理在其他容器时需自行配置容器网络。全程通过 HTTPS 访问后，可将 `.env` 中 `AUTOSIGN_COOKIE_SECURE` 设为 `true`，执行 `docker compose up -d` 应用。

## 添加网站与账号

登录后打开“添加网站”，也可直接访问 `/sites`。先选网站，为账号起一个名称，例如“NodeSeek · 主账号”。同一网站的其他账号另建任务。建议先暂停保存，手动执行并核对网站自己的签到状态，确认后再启用每日计划。

### NodeSeek、hostloc 和其他网页

先在你自己的电脑导出一个新浏览器会话，再在服务器管理页上传。不要把登录 Cookie 或会话文件发到聊天中。工具使用独立的新浏览器窗口，不会读取现有浏览器个人资料。

本机需要 Python 3.12 或更新版本。在项目目录运行：

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

默认所有执行器只允许公网目标。需要访问自己的内网网站时，才设置 `AUTOSIGN_ALLOW_PRIVATE_TARGETS=true` 并执行 `docker compose up -d`。

## 执行规则与数据

默认时区为 `Asia/Shanghai`，每个任务可选择自己的时区。每天到时执行；停机恢复后只补一次，不重放停机期间的每一天。任务当天已有成功记录或“今日已签到”记录时，当天不再自动执行。手动执行仍由你主动触发。

站点适配器或浏览器流程识别到登录失效、人工验证时会自动暂停该任务。登录 Cookie 或浏览器会话过期后，需要重新登录、更新配置，再手动测试和启用。

失败不自动重试，避免网站已处理请求但响应丢失时重复提交。重启时中断的执行会标记为结果未知；先核对网站状态，再决定是否手动执行。日志只保存结果类别、固定说明、HTTP 状态与耗时，不保存网页正文、截图或登录凭据。

浏览器、HTTP 和站点适配任务都在独立进程中执行，整次执行最多 90 秒，包含 DNS、连接和响应等待。超时后终止本次进程并记录结果未知，不会重新发送请求；请先核对网站签到状态。

SQLite 数据保存在 Compose 数据卷 `data` 的 `/data/autosign.db`。任务、Cookie 与浏览器会话使用 `.env` 中的 `AUTOSIGN_SECRET_KEY` 加密；必须同时保留数据库和密钥。只运行一个实例，不要扩容或同时运行多个调度进程。

## 日常管理

在项目目录运行：

```sh
# 查看状态和日志
docker compose ps
docker compose logs --tail=100 -f app

# 停止 / 再次启动
docker compose stop
docker compose start

# 修改 .env 后应用
docker compose up -d

# 更新项目后重新构建并启动
docker compose build
docker compose up -d
```

升级时保留原 `.env`，尤其是 `AUTOSIGN_SECRET_KEY`。`docker compose down` 不删除名卷；`docker compose down -v` 会永久删除全部任务与记录，不要用于普通升级。

## 备份与恢复

先停服务取得一致的 SQLite 备份，再打包整个数据目录，最后启动。备份文件包含凭据，应限制访问。以下命令在项目目录运行：

```sh
umask 077
mkdir -p backups
docker compose stop app
docker compose run --rm --no-deps -T app tar -C /data -czf - . > backups/autosign-data.tar.gz
cp .env backups/autosign.env
docker compose start app
```

将 `autosign-data.tar.gz` 和 `autosign.env` 一起保存在可信位置。固定文件名会覆盖旧备份，长期保留时先改成带日期的名称。备份失败时先排查原因，再运行 `docker compose start app` 恢复服务。

新服务器恢复时，先放好相同项目文件与备份。以下命令会覆盖配置，只应在确定要恢复的部署上执行：

```sh
docker compose stop app
cp backups/autosign.env .env
chmod 600 .env
docker compose build
docker compose run --rm --no-deps app python -c "from pathlib import Path; assert not any(Path('/data').iterdir()), '恢复需要空数据卷；请先备份并确认恢复目标。'"
docker compose run --rm --no-deps -T app tar -C /data -xzf - < backups/autosign-data.tar.gz
docker compose up -d
```

检查命令会拒绝向非空数据卷恢复，避免与已有 SQLite 文件混合。新服务器使用空名卷即可；已有部署请先备份现有数据，再选择空卷恢复。恢复必须使用备份对应的原数据密钥。

## 配置、测试与验证状态

配置说明见 `.env.example`。管理员密码至少 12 字符，首次初始化自动生成；修改绑定地址和端口使用 `BIND_ADDRESS`、`PORT`。Docker 已包含 Chromium，无需在服务器另外安装桌面浏览器。

安装依赖后可运行：

```sh
python -m unittest discover -s tests -v
```

本地模拟站点用于验证任务流程，不代表 NodeSeek、hostloc 或 LINUX DO 的真实账号已测试通过。当前开发环境没有 Docker 可执行文件，实际镜像构建与 Compose 启动仍需在目标服务器验证；上线前请先检查健康状态，再对每个真实账号手动测试。升级前创建的恩山签到任务仍使用原有站点适配器。
