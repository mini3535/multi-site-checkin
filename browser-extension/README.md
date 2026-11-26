# 签到助手会话导出器

这是 Chrome 扩展的源码。它把当前已登录 HTTPS 网站的 Cookie 和 localStorage 导出为签到助手可导入的文件。

## 普通用户流程

扩展通过 Chrome 网上应用店公开发布后，用户只需安装一次：

1. 在 Chrome 中登录要签到的网站，打开登录后的页面。
2. 点击工具栏中的扩展图标，选择“导出并下载”。首次导出时，Chrome 会询问是否允许读取当前网站。
3. 回到自己的签到助手，在“导入完整会话”中上传下载的文件，保存后继续验证签到。

扩展仅在用户点击导出后读取当前 HTTPS 标签页，并使用 Chrome 下载功能在本机生成文件。源码没有网络请求，不会把会话发送到扩展作者或其他服务。导出的文件仍是敏感登录凭据，导入后应删除。

## 商店发布状态

当前版本尚未发布到 Chrome 网上应用店，因此还不能向普通用户提供一键安装链接。发布前可按下方步骤预览；此方式需要开启开发者模式，只适合测试人员，不应当作普通用户安装流程。

## 开发预览

1. 下载或克隆项目源码。
2. 在 Chrome 打开 chrome://extensions 并开启“开发者模式”。
3. 点击“加载已解压的扩展程序”，选择本目录 browser-extension/。
4. 登录一个 HTTPS 测试网站，点击扩展图标并导出；随后检查文件含有 cookies 与 origins，并在签到助手测试导入。

## 支持范围

- 导出当前标签页 URL 对应的 Cookie，以及当前来源的 localStorage。
- 导出文件与 Playwright storage_state 兼容，大小最多 512 KiB。
- 仅支持 HTTPS 页面；每次导出前需授予当前网站 Cookie 权限。
- 不导出密码、sessionStorage、IndexedDB、Service Worker 或其他域名的本地存储。
- 使用第三方登录、跨域授权、sessionStorage 或额外设备验证的网站，可能需要项目单独适配。

## 隐私说明

权限仅用于用户主动发起导出：activeTab 读取当前标签页，scripting 读取该页 localStorage，按需申请的 cookies 和 HTTPS 主机权限读取当前页面 Cookie。扩展不包含后台脚本、遥测、远程代码或网络传输逻辑。隐私政策源文件见 PRIVACY.md。
