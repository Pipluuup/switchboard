# Switchboard Cookie 助手（浏览器扩展）

一键把 OpenCode Console / 火山方舟控制台的登录 Cookie 写入本机 Switchboard 网关的
「Plan 实时查询」配置，替代 F12 → Network → 手动复制 Cookie 的流程。

扩展通过 `chrome.cookies` API 读取整站 Cookie（**包含 httpOnly**，这是网页 JS 做不到的），
拼成与浏览器请求头一致的 `name=value; ...` 格式后 PUT 给网关的 `/api/settings`。

## 安装（Chrome / Edge 通用）

1. 打开 `chrome://extensions`（Edge 是 `edge://extensions`）
2. 打开右上角「开发者模式」
3. 点「加载已解压的扩展程序」，选择本目录（`extension/`）
4. 固定到工具栏：拼图图标 → 本扩展 → 图钉

## 使用

1. 先在浏览器里分别登录 [opencode.ai/console](https://opencode.ai/console) 和
   [console.volcengine.com/ark](https://console.volcengine.com/ark)
2. 点扩展图标：
   - 「写入 OpenCode Console Cookie」→ 写入工作区所在会话的完整 Cookie
   - 「写入火山方舟 Cookie」→ 同理
   - 弹窗底部链接可直接跳转到两个控制台
3. 回 Switchboard 控制台「用量」页，配额数据会实时显示

## 注意

- 网关地址默认 `http://127.0.0.1:8000`，端口改过的话在弹窗底部「网关地址 / 管理令牌」里改
- 后台设置了管理令牌的话，同样在这个面板里填一次（保存在扩展本地，不经过网关存储）
- Cookie 过期后（用量页显示查询失败）重复第 2 步即可
- 仅请求本机网关与这两个站点，不联网到任何其他服务器
