# 隐私说明

HERALD 设计为在使用者自己的 GitHub 仓库中运行。项目维护者不接收或托管使用者配置。

## GitHub 中哪些内容是公开的

如果 fork 是公开仓库，`main`、`state`、`page` 三个分支通常都可以被任何人读取，GitHub Pages 也公开可访问。因此 `state/page` 只允许保存公开公告、结构化活动、来源 URL、匿名任务 ID、日期队列与不含收件人的通知回执。

正式部署时，以下内容只能放在 GitHub Repository Secrets 中，不能提交：

- 常驻城市和可达城市；
- 收件邮箱；
- AI API Key；
- 微博 Cookie；
- SMTP 用户名、密码或授权码。

本地真实调试时，这些值可以暂存在被 Git 忽略的 `local.env`，由 `scripts/run-local.py` 注入独立的 HERALD 子进程环境；该文件不得强制加入 Git、上传为 Artifact 或粘贴到 Issue/日志。HERALD 与 GitHub Actions 都不会直接读取它。

## 地点推断

即使网页不直接写出常驻城市，“上海活动 = 本地”也可能间接暴露地点。因此 `PUBLISH_REACHABILITY` 默认是 `false`，网页只显示 `unknown`。只有使用者理解公开推断风险并显式设为 `true` 后，页面才输出 `local/reachable/cross_region` 等分类；配置字段本身仍不会被写入。

请只填写城市或省市级范围，不要填写家庭地址、公司地址、精确坐标或日常行程。

## AI 请求

AI Provider 只接收公开来源材料：官号名称、发布时间、微博正文、海报公开 URL、已提取海报文字、公开外链、来源 URL 和 IP 名称提示。地点配置、邮箱、Cookie、SMTP 凭据和任何 Key 不会进入模型请求。

使用第三方免费或付费 API 前，请阅读该服务的数据保留与训练政策。开启 `AI_VISION` 会把微博海报的公开 URL 交给所选模型服务。

## 邮件与回执

邮件从使用者自己的 SMTP 账号直接发送。`state/notified` 只记录任务 ID、语义键和发送时间，用于防止重复发送，不记录收件邮箱。发送失败时不写回执，以便以后重试。

## 日志与泄露防护

来源、AI 和邮件异常使用脱敏消息。生成页面后还会扫描已知的高风险 Secret；发现匹配时构建失败，不推送页面。GitHub 自带 Secret masking 只是补充措施，不能代替本项目的边界检查。

如果怀疑 Secret 泄露，请立即撤销或轮换对应 Key、Cookie 或 SMTP 授权码，并清理 Git 历史与 Actions 日志。
