# 多站点签到助手

部署在自己服务器上的自动签到服务，支持多个网站和账号，按计划运行并记录结果。

MIT 许可证：[LICENSE](LICENSE) · [第三方软件声明](THIRD_PARTY_NOTICES.md)

## 快速开始

服务器需安装 Docker Engine、Docker Compose 插件 2.24.0+ 和 curl。通过 SSH 登录后运行：

    curl -fsSL https://raw.githubusercontent.com/mini3535/multi-site-checkin/main/install.sh -o autosign-install.sh
    sh autosign-install.sh /root/multi-site-checkin

按提示选择域名 HTTPS 或 IP＋端口。安装结束会显示管理页面链接和初始密码；登录后可修改密码。SSH 私有访问和其他安装选项见[访问与安装说明](docs/OPERATIONS.md#安装与访问)。

## 添加签到

**选择网站 → 连接账号 → 验证签到 → 设置每日执行**

1. 在网站目录选择平台，或粘贴网站 / Web App 的 HTTPS 地址；已接入的平台会自动识别，其他网站会预填名称和域名。
2. 按页面提示连接账号。自定义网站推荐在自己电脑上登录网站后，使用浏览器扩展和五分钟一次性链接导入登录状态；Cookie、Token、账号密码和会话文件作为备用方式。
3. 验证会实际执行一次签到；成功后继续设置执行时间和时区。
4. 在任务总览查看每日结果。新任务验证通过前不会自动运行。

一个网站账号对应一个任务。更换凭据、网址或规则后需重新验证。网站要求验证码时，由你在登录页自行完成。
快速添加适用于网页登录的站点；扩展可从当前网页建议签到按钮和已出现的结果提示，仍需由用户确认。登录连接完成后，“验证签到”会真实点击一次按钮，不是只读连通性检查。仅能在原生 iOS 或 Android App 中操作的签到暂不支持。

### 浏览器连接扩展

扩展目前尚未上架 Chrome 网上应用店，首次安装需下载[扩展 ZIP](https://github.com/mini3535/multi-site-checkin/raw/refs/heads/main/signin-session-exporter.zip)，解压后在 Chrome 打开 `chrome://extensions`，开启开发者模式并加载解压文件夹。登录目标网站后，在助手生成一次性链接，切回目标网站标签页，粘贴到扩展并点击“识别网页并连接账号”。配置页会等待连接并自动填入识别到的签到按钮；网站目录里的“从当前网页自动识别并添加”则会创建新任务。旧版扩展若只显示“导出并下载”，请下载新包覆盖原扩展文件夹后刷新，或移除旧扩展并加载新文件夹；新版显示 v1.3.0。

目标网站必须使用 HTTPS；签到助手使用 HTTPS，或仅在本机通过 `localhost` / `127.0.0.1` 访问（例如 SSH 隧道）。公网 HTTP 不支持浏览器连接。连接链接五分钟过期且只能使用一次。详细说明见[扩展指南](browser-extension/README.md)。

## 支持范围

- 已适配平台：WorkBuddy、百度千帆、MiniMax Code、Qoder 国内版、Link AI、WPS 灵犀、Trae Work、Coze、华为码道。
- NodeSeek、hostloc 和其他网站：需自行确认签到页面并配置规则；尚未完成真实账号通用验证。
- LINUX DO 和豆包目前仅提供信息入口，不提供自动签到任务。
- 复杂登录、第三方登录或依赖 sessionStorage 的网站可能需要单独适配。

查看[平台能力与限制](PLATFORMS.md)。自定义规则示例见[示例说明](examples/README.md)。

## 更新与维护

在原安装目录运行以下命令，保留任务、密码和会话：

    cd /root/multi-site-checkin
    sh setup.sh

代码或镜像发布不会自动更新服务器。源码部署、旧版安装目录更新和发布状态见[扩展运维指南](docs/OPERATIONS.md#更新已有服务器)。
登录后，页面页脚会显示当前镜像的短构建版本，便于核对服务器实际运行的提交；本地构建默认显示 local。

查看运行状态和日志：

    docker compose ps
    docker compose logs --tail=100 -f app

停止或启动：

    docker compose stop
    docker compose start

安全卸载（保留数据）：

    cd /root/multi-site-checkin && sh setup.sh --uninstall

彻底删除项目数据：

    cd /root/multi-site-checkin && sh setup.sh --uninstall --purge

彻底卸载会删除任务、会话、记录和项目数据卷，操作前请先备份。备份、恢复、网络排障和离线安装见[扩展运维指南](docs/OPERATIONS.md)。

## 数据与安全

任务和执行记录存于 SQLite 数据卷；账号凭据及浏览器会话使用安装目录中的密钥加密。请备份数据库和密钥，并限制备份文件访问。公网部署建议使用域名 HTTPS；IP＋端口模式使用 HTTP，不适合不可信网络。

## 开发与贡献

运行测试：

    python -m unittest discover -s tests -v

镜像构建与发布状态见 [GitHub Actions](https://github.com/mini3535/multi-site-checkin/actions/workflows/container.yml)。代码推送成功不代表镜像已发布，也不会自动更新服务器。
