# Security Policy

## 报告问题

请通过 GitHub Security Advisory 私下报告可能导致 Secret、Cookie、邮箱或位置泄露的问题。不要在公开 Issue 中粘贴真实凭据、Actions 日志或完整 Cookie。

## 使用者责任

- 只在 Repository Secrets 中保存敏感值，并使用权限最小、可随时撤销的账号或授权码。
- 微博 Cookie 可能等同于登录会话；能匿名运行时不要配置，必须配置时应使用专用低权限账号并定期轮换。
- SMTP 优先使用应用专用授权码，不使用主密码。
- AI Key 应设置消费限额或免费额度提醒。项目不会替使用者支付费用。
- 公开 Pages 前阅读 `PRIVACY.md`，默认不要开启个性化可达性发布。

## 项目边界

- 工作流只有 `contents: write`，用于 `state/page`；不需要云服务器或数据库权限。
- 所有外部正文和模型输出都按不可信输入处理，使用严格 Pydantic schema、路径安全检查和 DOM `textContent`，不执行来源 HTML 或脚本。
- 微博移动接口为非官方接口，可能限流或改变格式。失败只记录脱敏警告，不输出响应正文或 Cookie。
- 真实网络与凭据不参与测试。
