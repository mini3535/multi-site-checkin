# 网站任务配置示例

下列接口、选择器与提示文字只说明配置格式。它们不是 NodeSeek、hostloc 或 LINUX DO 当前可直接使用的实测规则。先在自己的账号确认页面与结果，再启用每日计划。LINUX DO 目前未确认有独立签到按钮，不应把普通页面访问当作签到成功。

## 浏览器页面流程

假设你的网站签到页为 `https://forum.example.com/checkin`，页面有唯一按钮 `#sign-in`，点击后出现一个完整显示“签到成功”的可见元素：

| 字段 | 示例 |
| --- | --- |
| 入口 | 其他论坛或网站 |
| 任务名称 | 我的论坛 · 主账号 |
| 起始页面 | `https://forum.example.com/checkin` |
| 允许的域名 | `forum.example.com`，以及页面实际需要的登录、接口与资源域名，每行一个 |
| 会话 | 从本机正常登录后导出的完整浏览器会话 JSON |
| 成功文字 | `签到成功` |
| 今日已签到文字 | `今日已签到` |
| 重新登录选择器 | `#login-form`，前提是它只在未登录时可见 |
| 人工验证选择器 | `#verification`，前提是它只在需要验证时可见 |
| 每日时间 / 时区 | `08:30` / `Asia/Shanghai` |

签到步骤：

```json
[
  {"action": "click", "selector": "#sign-in", "timeout_ms": 5000},
  {"action": "wait", "selector": "#checkin-result", "timeout_ms": 5000}
]
```

`#checkin-result` 是示例页面的结果元素；实际网站没有这个元素时，需要替换。`fill` 用于填写字段，格式为 `{"action":"fill","selector":"#field","value":"字段值","timeout_ms":5000}`。选择器应唯一匹配，不要直接使用 `button`、`a` 之类宽泛选择器。

成功文字和今日已签到文字按完整可见元素精确匹配，不是“网页里出现这几个字就算成功”。如果网站成功提示带动态积分数，需要先确认能否使用一个稳定、独立的成功元素。整次流程有 90 秒总时限，最多 20 步，不会无限等待或自动重试。

NodeSeek 和 hostloc 的模板也使用这个配置方式：先正常登录自己的账号，确认当前确有签到入口，进入实际签到页，确认点击步骤和成功提示，再填写。NodeSeek 可从 `https://www.nodeseek.com/board` 开始；Hostloc 使用官方 `.com` 域名，从 `https://hostloc.com/forum.php` 开始。入口地址与导出的登录会话都不能代替页面规则。LINUX DO 当前没有已确认的独立每日签到按钮，因此目录只展示社区信息，不提供自动签到任务；登录或浏览是否产生社区活跃记录由网站自身机制决定。

## 高级 HTTP：POST JSON

假设真实接口返回 `{"code":0,"data":{"checked":true}}`：

| 字段 | 示例 |
| --- | --- |
| 任务名称 | 每日签到 |
| 请求方法 | POST |
| 网址 | `https://example.com/api/checkin` |
| 请求头 JSON | `{"Cookie":"session=替换成真实值","Content-Type":"application/json"}` |
| 原样请求体 | `{}` |
| 成功条件 | JSON |
| 字段路径 | `data.checked` |
| 期望值 | `true` |

Bearer Token 放入请求头，例如 `{"Authorization":"Bearer 替换成真实Token"}`。JSON 期望值为字符串时需要双引号：响应 `{"message":"签到成功"}` 的路径填 `message`，期望值填 `"签到成功"`。

GET 请求的请求体留空。文本成功条件应选只有真正成功时出现的固定字符串；不要仅凭 HTTP 200 判断。普通表单 POST 使用相应 Content-Type，请求体填写编码后的字符串，例如 `action=checkin&source=web`。服务不自动生成动态签名或一次性令牌。

## 本机 HTTP 模拟测试

`mock_site.py` 只监听本机，用于验证“HTTP 200 仍可能签到失败”。需要 Python 3.12。在项目目录准备环境：

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

第一个终端：

```sh
. .venv/bin/activate
python examples/mock_site.py
```

第二个终端也进入项目目录，用独立测试配置启动服务：

```sh
. .venv/bin/activate
umask 077
python init_config.py > .env.local
set -a
. ./.env.local
set +a
export AUTOSIGN_ALLOW_PRIVATE_TARGETS=true
export AUTOSIGN_DATA_DIR=./data
python -m autosign
```

第一次才生成 `.env.local`。已有测试数据再次启动时，省略 `python init_config.py > .env.local`，继续使用原密钥。测试服务和 Docker 服务都使用 8080，需要先停止其中一个。

打开 `http://localhost:8080`，用 `.env.local` 中的密码登录。在高级 HTTP 任务中配置 POST `http://127.0.0.1:8099/api/checkin`，请求头填 `{"Cookie":"demo_session=valid"}`，请求体留空，JSON 路径填 `code`，期望值填 `0`。手动执行应成功；Cookie 改为其他值时，模拟网站仍返回 HTTP 200，但任务应判定失败。

这里直接在宿主机启动 Python，才使用 `127.0.0.1:8099`。Docker 容器中的 `127.0.0.1` 指容器自身，不能使用同一个地址访问宿主机模拟网站。公网部署保持 `AUTOSIGN_ALLOW_PRIVATE_TARGETS=false`。
