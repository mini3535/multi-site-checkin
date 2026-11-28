# 多站点签到助手

部署在自己服务器上的自动签到服务，支持多个网站和账号，按计划运行并记录结果。

MIT 许可证：[LICENSE](LICENSE) · [第三方软件声明](THIRD_PARTY_NOTICES.md)

## 快速开始

服务器需安装 Docker Engine、Docker Compose 插件 2.24.0+ 和 curl。通过 SSH 登录后运行：

    curl -fsSL https://raw.githubusercontent.com/mini3535/multi-site-checkin/main/install.sh -o autosign-install.sh
    sh autosign-install.sh /root/multi-site-checkin

按提示选择域名 HTTPS 或 IP＋端口。安装结束会显示管理页面链接和初始密码；登录后可修改密码。SSH 私有访问和其他安装选项见[访问与安装说明](docs/OPERATIONS.md#安装与访问)。

## 添加签到

**在网站登录 → 扩展识别并添加 → 验证签到 → 开启每日计划**

1. 在 Chrome 登录目标网站，打开实际签到页面。
2. 回到签到助手的“网站目录”，展开安装说明，下载**与当前助手配套的扩展 ZIP**。首次安装一次即可。
3. 点击“生成识别链接”并复制，切回目标网站标签页，打开扩展，粘贴链接，点击“识别网页并连接账号”。
4. 回到助手，点击“打开任务并验证”。系统会新建暂停任务，预填网页标题、登录状态和识别到的签到规则；未找到按钮时，先返回规则设置补充。
5. 点击“开始验证签到”，成功或显示今日已签到后，选择每天执行时间和时区，完成设置。

**已有平台接口**：在网站目录选择 WorkBuddy 等平台，按其专用表单填写所需凭据，再验证和开启计划。
**手动配置网站**：粘贴 HTTPS 地址，进入表单后使用同一个扩展连接当前账号；Cookie、Token、账号密码和会话文件放在备用方式中。

一个网站账号对应一个任务。验证会真实执行一次签到。识别失败或网站提示不常见时，需要补充按钮或结果文字；更换账号、网址或规则后需重新验证。仅能在原生 iOS 或 Android App 中操作的签到暂不支持。

### 浏览器连接扩展

推荐从部署后的助手页面下载扩展，确保与服务器版本一致。扩展尚未上架 Chrome 网上应用店：解压 ZIP，在 `chrome://extensions` 开启“开发者模式”，点击“加载已解压的扩展程序”并选择解压文件夹。更新时用新包覆盖原文件夹，再在扩展管理页刷新。弹窗应有链接输入框和“识别网页并连接账号”按钮。

源码使用者也可下载仓库中的[当前扩展 ZIP](https://github.com/mini3535/multi-site-checkin/raw/refs/heads/main/signin-session-exporter.zip)。完整步骤见[扩展指南](browser-extension/README.md)。

目标网站必须使用 HTTPS；签到助手使用 HTTPS，或仅在本机通过 `localhost` / `127.0.0.1` 访问（例如 SSH 隧道）。公网 HTTP 不支持浏览器连接。连接链接五分钟过期且只能使用一次。详细说明见[扩展指南](browser-extension/README.md)。

## 支持范围

- 已适配平台：WorkBuddy、百度千帆、MiniMax Code、Qoder 国内版、Link AI、WPS 灵犀、Trae Work、Coze、华为码道。
- NodeSeek、hostloc 和其他网站：需自行确认签到页面并配置规则；尚未完成真实账号通用验证。
- LINUX DO 和豆包目前仅提供信息入口，不提供自动签到任务。
- 复杂登录、第三方登录或依赖 sessionStorage 的网站可能需要单独适配。

查看[平台能力与限制](PLATFORMS.md)。高级自定义规则见[源码示例](https://github.com/mini3535/multi-site-checkin/tree/main/examples)。

## 更新与维护

在原安装目录运行以下命令，仅更新镜像并沿用已有访问配置，保留任务、密码和会话：

    cd /root/multi-site-checkin
    sh setup.sh < /dev/null

需要连部署脚本一起更新时，按[完整更新流程](docs/OPERATIONS.md#更新已有服务器)操作。源码部署先执行 `git pull --ff-only`。
代码或镜像发布不会自动更新服务器。镜像发布成功后再执行更新；扩展也需从更新后的助手页面重新下载。
登录后，页面页脚会显示当前镜像的短构建版本，便于核对服务器实际运行的提交；本地构建默认显示 local。

查看运行状态和日志：

    docker compose ps
    docker compose logs --tail=100 -f app

停止或启动：

    docker compose stop
    docker compose start

安全卸载（保留数据）：

    cd /root/multi-site-checkin && sh setup.sh --uninstall

彻底卸载（删除数据、项目镜像和整个安装目录）：

    cd /root/multi-site-checkin && sh setup.sh --uninstall --purge

彻底卸载需输入 `DELETE` 确认，会删除任务、会话、记录、证书、项目镜像及安装目录内的全部文件，包括源码、配置和备份。需要的备份请先移到安装目录外。其他容器仍在使用的镜像会保留并列出，不会强制删除。旧服务器需先获取新版卸载器，命令见[卸载与清理旧版本](docs/OPERATIONS.md#卸载服务)；备份、恢复和排障也见该指南。

## 数据与安全

任务和执行记录存于 SQLite 数据卷；账号凭据及浏览器会话使用安装目录中的密钥加密。请备份数据库和密钥，并限制备份文件访问。公网部署建议使用域名 HTTPS；IP＋端口模式使用 HTTP，不适合不可信网络。

## 开发与贡献

使用 Python 3.12+ 安装 `requirements.txt`，并准备 Playwright Chromium 后运行 Python 测试；Node.js 22+ 用于扩展测试。发布前检查：

    python -m unittest discover -s tests -v
    node --test browser-extension/popup.test.cjs
    python tools/package_extension.py --check

修改扩展后，运行 `python tools/package_extension.py` 更新唯一的 ZIP，再提交源码和 ZIP。CI 会先检查脚本和扩展，再构建、验证并发布双架构镜像。

镜像构建与发布状态见 [GitHub Actions](https://github.com/mini3535/multi-site-checkin/actions/workflows/container.yml)。代码推送成功不代表镜像已发布，也不会自动更新服务器。
