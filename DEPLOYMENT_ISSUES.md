# 部署问题记录

创建日期：2026-11-20；最近更新：2026-11-25。仅保存复现信息和处理办法，不记录服务器地址、账号、Cookie 或代理凭据。

## 1. 构建中 pip 无法解析域名

- 现象：构建日志出现 Temporary failure in name resolution，随后出现 from versions: none。
- 已确认：容器内的 Python 请求在 socket.getaddrinfo 阶段报错，尚未建立 HTTPS 连接。
- 后续只读检查：宿主机可解析 PyPI 与 GHCR，访问 GHCR 返回预期的 HTTP 401；现有基础容器无法解析这两个域名，范围已缩小到容器 DNS 或 bridge 网络。尚未修改解析或防火墙配置。
- 排查：先对比宿主机和一次性容器的域名解析，再检查 HTTPS 请求；不要仅凭 pip 最后一行修改依赖版本。具体命令见 README。
- 当前状态（2026-11-25）：签到服务使用 Compose 新建的专用 bridge 后，实际应用容器的 DNS 与 HTTPS 已正常。旧默认 bridge 未改动，源码构建仍可能受其影响；重试和包索引切换不能修复通用 DNS 故障。

## 2. 首次部署需要在服务器构建全部依赖

- 原流程：克隆源码，在目标服务器安装 Python 包、Debian 依赖和 Chromium 后才能启动。
- 问题：服务器需要访问多个下载源，构建慢，网络故障会中断首次部署。
- 改进：默认安装改为下载预构建镜像；install.sh 下载部署文件，setup.sh 拉取镜像、生成配置、检查 Chromium 沙箱并等待健康检查。源码构建保留为显式 --local 选项。
- 网络受限时：可在能访问镜像仓库且架构相同的机器下载镜像，通过 docker save / docker load 导入，使用 --loaded 启动。
- 发布条件：GitHub Actions 首次构建成功、镜像包公开，并验证无需登录即可拉取后，才能对外宣称一键安装可用。

## 3. 发布权限与验证边界

- 此前 GitHub 写入返回 HTTP 403；登录身份可读取不代表拥有仓库写入或工作流写入权限。
- 预构建镜像流程不能代替实际发布；本地脚本完成不代表镜像已上传或服务器部署成功。
- 本地开发机没有 Docker。本次通过 110 项 Python 测试，其中 10 项模拟安装命令，覆盖下载失败、初始化失败、配置保留、权限与沙箱失败时停止等行为。
- 2026-11-25 已在两个原生 Linux 架构完成镜像构建、Chromium 沙箱、完整测试、健康接口与登录页检查，并在目标 amd64 服务器完成部署和连通性验证。此结果不代表真实网站账号签到已通过。

## 4. Playwright 安装器缺少 libatomic.so.1（2026-11-25）

- 首次 GitHub Actions 构建（运行 45530394090）在 amd64 和 arm64 均失败：Playwright 自带的 Node 在安装浏览器前无法加载 libatomic.so.1，退出码 127。
- 修复：Dockerfile 在运行 Playwright 安装命令之前安装 Debian 的 libatomic1 包。该包同时提供 amd64 和 arm64 构建。
- 验证：以修复后的双架构构建、Chromium 沙箱、完整测试和健康检查结果为准；本地 Python 测试无法覆盖此 Linux 动态链接错误。

## 5. Compose run 参数与实际 CLI 不兼容（2026-11-25）

- 修复 libatomic1 后，双架构镜像均构建成功；运行 45530989314 在初始化配置时失败：unknown flag: --no-build。
- 原因：docker compose up 支持 --no-build，但 docker compose run 不支持该参数。模拟 Docker 命令的测试未发现 CLI 参数错误。
- 修复：从 setup.sh、CI 和 README 的 run 命令移除 --no-build，保留 --pull never，up 继续使用 --no-build；测试同步区分两种子命令。运行 45533651799 已通过实际容器验证。

## 6. 目标服务器旧 bridge 网络失效（2026-11-25）

- 只读检查：宿主机可解析域名；旧默认 bridge 的临时容器解析失败。Docker 网络记录中存在 docker0，但宿主机接口列表未出现对应接口。未改动宿主机解析配置或防火墙。
- 对照验证：新建专用 autosign-diagnostics bridge，在该网络运行同一个 Python 基础镜像，DNS 解析成功，访问 PyPI HTTPS 返回 200。
- 部署结果：签到服务使用 Compose 新建的 autosign_default 专用网络；实际应用容器的 DNS 解析成功，访问 PyPI HTTPS 返回 200。未修改防火墙、使用 host 网络或重启其他服务；验证后已移除本次创建的 autosign-diagnostics 网络。

## 7. 首次镜像发布与目标部署验收（2026-11-25）

- 验证代码版本：2c5f95d72c9542582a06c20bd313fc5af9e77a8b；GitHub Actions 运行 45533651799 全部成功。
- 原生 linux/amd64 与 linux/arm64 均完成构建、110 项测试、Chromium 沙箱启动、Compose 健康状态、/healthz 与 /login 检查后才推送镜像，并合并发布 latest 与提交 SHA 标签。
- 公开镜像：ghcr.io/mini3535/multi-site-checkin:latest。首次多架构索引摘要为 sha256:431c559212b0b39864f0ed3d0ce5404bf29349e4c7d9b67a7a11c2e9cece2d8a。后续 latest 可能更新，复现此版本可使用对应提交 SHA 标签或摘要。
- 匿名访问：本地未提供账号或令牌即可取得 GHCR 匿名访问令牌，读取两个平台的清单和配置；目标 amd64 服务器以临时空 DOCKER_CONFIG 成功完整拉取镜像，之后移除该空目录。尚未在独立 arm64 目标服务器验收匿名完整拉取与部署。
- 目标服务器：setup.sh --loaded 成功初始化并启动，Chromium 沙箱检查通过，容器健康；/healthz 返回正常状态，/login 返回 HTTP 200；容器 UID 为 10001，绑定仅为 127.0.0.1:8080，.env 权限 600。
- 应用容器内 DNS 解析与出站 HTTPS 返回 200；既有 nginx 和管理面板容器保持运行。管理员密码和数据密钥只保存在服务器 .env，未写入仓库或问题记录。
- 仍需人工完成：通过 SSH 隧道登录管理页，导入各站点自己的会话、配置并核实签到规则；NodeSeek、hostloc 等真实账号的签到效果尚未验证。
