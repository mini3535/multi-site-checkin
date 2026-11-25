# 平台接入与验证边界

更新日期：2026-11-25。平台接口会变化，以下为已写入本项目的协议适配，
不代表已经使用真实账号验证可签到，也不保证每个账号都有奖励活动。

## 怎样添加

1. 打开“网站目录”，选择平台，点击“连接平台账号”。
2. 在平台官网正常登录，按页面说明填写自己账号的 Cookie / Token 和必要设备信息。
3. 保存账号。接口地址、请求格式和结果判断由适配器提供，不需要写 JSON。
4. 新任务默认暂停。点击“立即执行”，然后到官网核对实际奖励与签到状态。
5. 验证成功后，编辑任务并启用每日计划。多账号分别添加任务。

当前凭据仍需手动获取和填写，没有实现登录后自动采集；不能只输入任意网站地址就自动适配。
凭据使用现有数据密钥加密保存，编辑页面不回显，留空保留原值；清空凭据会暂停任务。
登录失效或需要人工验证时，执行结果会进入对应状态并暂停自动任务。

## 截图中的十个平台

| 平台 | 本项目的处理方式 | 需要提供 / 当前限制 |
| --- | --- | --- |
| WorkBuddy | 查询状态 → daily-checkin → 再查状态确认 | accessToken、UID；固定 copilot.tencent.com，暂不支持其他企业域；部分账号需要当天先完成正常对话，本项目不会代发 |
| 百度千帆（百度搭子 DuMate） | loginBonusInfo → loginBonus → 再查领取状态 | console.bce.baidu.com 的 Cookie，含 BDUSS 和 bce-user-info；自动提取 CSRF；这是搭子登录积分，不是模型调用额度 |
| MiniMax Code | 查询当日面板 → claim，检查 claim_result | agent.minimax.cn 网页 Token，以及自己真实请求中的 user_id、device_id、uuid；不使用模型 API Key |
| Qoder | 查询国内版积分活动 → 领取一项可领活动 → 再查该活动确认 | 国内版 Token、machine ID；无活动不会报已签；国际版设备校验暂不支持；不领取订阅广告 |
| Link AI | 请求 sign/in，识别成功、今日已签和验证码错误码 | 登录 Token；870 提示人工验证，不集成参考项目的自动验证码求解器 |
| WPS 灵犀 | 查询 daily_check_in → claim → 再查任务状态 | 灵犀登录 Cookie；日期按 Asia/Shanghai 生成；锁定或未知任务不发起领取 |
| Trae Work | Cookie 换短期 Token → 查状态 → 单次 claim | work.trae.cn Cookie、真实 x-device-id；req_source=2；9004、9074 不算成功 |
| Coze 扣子 | 请求每日登录奖励 → 检查 activity_type=2 的领取标记 | www.coze.cn Cookie；不自动对话、生成内容或做额外积分任务 |
| 华为码道 | 查询每日活动 → claim → confirm → 回读确认 | 动态发现活动 ID，支持最多 6 个子奖励；devcloud Cookie（包含 devclouddevuibjtcftk）；自动提取并 URL 解码 cftk，门户 Cookie 不通用 |
| 豆包 Doubao | 目录说明与官网入口 | 参考项目仍将签到关闭；暂不提供可运行的自动签到适配器 |

Qoder 每次运行最多领取一项明确标记为 CLAIM_BENEFIT、CREDITS、CLAIMABLE 的活动。
活动数量或开放时间由平台决定，不把普通广告、未知状态或无活动视为每日签到成功。

所有接口适配使用固定 HTTPS 主机、DNS 地址校验与绑定连接、TLS 校验、响应大小限制、
不跟随跳转和执行超时。未提供任意远程签名服务地址，不自动重试写请求。
日志和页面只展示固定结果说明，不返回完整上游响应或异常中的 Cookie / Token。

## 参考项目怎样实现

- wangmingdong/workbuddy-signin：主要用 Cookie / Token 调用各平台接口，另有本机凭据同步、
  浏览器代理和客户端工具。本项目按自己的任务队列、加密存储和网络执行器重写适配，
  不导入其服务器地址、示例账号设备参数或凭据发现代码。其千帆卡依赖另一个自建服务。
- Ayanguser/SigninHub：既有接口执行，也有 Android WebView 登录采集和客户端处理。
  最新检视提交为 e9b90d1；DuMate 的接口、Cookie/CSRF 字段与 Qoder 国内版协议信息用于
  独立实现。本项目没有复制该仓库的代码、UI 或资源，也没有加入其 Android 登录组件。
- 参考项目对豆包的记录存在差异。SigninHub 最新 DoubaoWebCheckin.ENABLED=false，
  注释记录 2026-11-24 仍有 710010702 写接口错误。workbuddy-signin 的豆包路径依赖
  浏览器签名代理。没有真实可签账号完成验证前，本项目只提供说明和官网链接。

本项目的测试使用模拟响应，覆盖成功、重复领取、登录过期、验证码、接口变化、
业务失败、响应超限、凭据保留与清空。它们证明程序按这些协议处理响应，不能替代真实账号验收。

## 更新服务器

GitHub 推送与 Actions 镜像发布不会自动更新已运行的服务器。
等对应提交的镜像发布成功后，在原安装目录执行 sh setup.sh 拉取并重建服务，
然后在网页“网站目录”查看新增平台。已有任务、访问设置和数据按原部署流程保留。
