# 部署问题记录

记录日期：2026-11-20。仅保存复现信息和处理办法，不记录服务器地址、账号、Cookie 或代理凭据。

## 1. 构建中 pip 无法解析域名

- 现象：构建日志出现 Temporary failure in name resolution，随后出现 from versions: none。
- 已确认：容器内的 Python 请求在 socket.getaddrinfo 阶段报错，尚未建立 HTTPS 连接。
- 后续只读检查：宿主机可解析 PyPI 与 GHCR，访问 GHCR 返回预期的 HTTP 401；现有基础容器无法解析这两个域名，范围已缩小到容器 DNS 或 bridge 网络。尚未修改解析或防火墙配置。
- 排查：先对比宿主机和一次性容器的域名解析，再检查 HTTPS 请求；不要仅凭 pip 最后一行修改依赖版本。具体命令见 README。
- 当前状态：目标服务器的容器网络尚未恢复。重试和包索引切换不能修复通用 DNS 故障。

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
- 仍需在真实 Linux Docker 环境完成镜像构建、Chromium 沙箱启动、健康检查和登录页面检查，并在目标服务器完成连通性验证。

## 4. Playwright 安装器缺少 libatomic.so.1（2026-11-25）

- 首次 GitHub Actions 构建（运行 45530394090）在 amd64 和 arm64 均失败：Playwright 自带的 Node 在安装浏览器前无法加载 libatomic.so.1，退出码 127。
- 修复：Dockerfile 在运行 Playwright 安装命令之前安装 Debian 的 libatomic1 包。该包同时提供 amd64 和 arm64 构建。
- 验证：以修复后的双架构构建、Chromium 沙箱、完整测试和健康检查结果为准；本地 Python 测试无法覆盖此 Linux 动态链接错误。

## 5. Compose run 参数与实际 CLI 不兼容（2026-11-25）

- 修复 libatomic1 后，双架构镜像均构建成功；运行 45530989314 在初始化配置时失败：unknown flag: --no-build。
- 原因：docker compose up 支持 --no-build，但 docker compose run 不支持该参数。模拟 Docker 命令的测试未发现 CLI 参数错误。
- 修复：仅从 run 命令移除 --no-build，保留 --pull never，up 继续使用 --no-build；测试同步区分两种子命令。真实镜像测试必须通过后才能发布。

## 6. 目标服务器旧 bridge 网络失效（2026-11-25）

- 只读检查：宿主机可解析域名；旧默认 bridge 的临时容器解析失败。Docker 网络记录中存在 docker0，但宿主机接口列表未出现对应接口。未改动宿主机解析配置或防火墙。
- 对照验证：新建专用 autosign-diagnostics bridge，在该网络运行同一个 Python 基础镜像，DNS 解析成功，访问 PyPI HTTPS 返回 200。
- 部署处理：让签到服务使用 Compose 新建的专用网络，并在实际应用容器内重新检查 DNS 和 HTTPS。无需通过关闭防火墙、host 网络或重启所有容器来绕过问题。诊断网络在验证完成后清理。
