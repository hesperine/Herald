# HERALD

> **HERALD = Herald for Events, Reservations, Announcements, Linkups & Deadlines**
>
> 中文定位：游戏联动预告与提醒信使

每天自动寻找你关注的游戏 IP 官方联动公告，把分散的微博材料合并成活动，提前提醒预约、开售、抽签或活动开始，并生成一份静态网页。

这是一个核心功能优先的早期版本。数据抓取、去重合并、历史提醒、邮件和静态发布已经接通；页面目前只有最低可用的 HTML，视觉布局、配色和卡片设计将在核心稳定后单独设计。

## 它每天做什么

```text
内置 IP 官号 + 用户补充 UID
              ↓
       读取微博最新页（中国区）
              ↓
  来源去重 → 规则初筛 → 用户自己的 AI
              ↓
 Campaign（联动企划）
   └─ Activity（商品 / 快闪 / 线上 / 巡展……）
       └─ Action（预约 / 开售 / 抽签 / 开始……）
              ↓
 state 历史与日期队列 ──→ 当天邮件
              ↓
       page 静态活动网页
```

当天邮件包含两类内容：今天新发现或变更的联动，以及以前公告中已经写入日期队列、今天应当提醒的内容。因此，即使官号今天没有再发微博，“明天开售”仍然可以按旧资料提醒。

多官号发布同一材料时会先去重；同一 `IP × 品牌` 企划的全国商品、上海快闪和线上预约会尽量合并成一个 Campaign 下的多个 Activity。证据不足时进入待审核状态，不强行合并。

## 最终输出

GitHub Pages 首页只列出仍有效、且属于当前关注 IP 的活动。点击后可以查看：

- 活动日期和具体时间；
- 线下省市、场地与地址，或线上 App/平台；
- 联动 IP、合作品牌与活动类型；
- 是否需要预约、抢购、抽签或抢号；
- 可操作链接以及官方原始信息来源。

活动过期后仍保留在 `state` 历史中，但会从首页、日历索引和 `page` 详情文件中移除。

## 为什么不需要服务器或数据库

- GitHub Actions 每天运行一次 Python；
- `state` 分支充当文件型历史状态和日期队列；
- `page` 分支由 GitHub Pages 托管静态网页；
- 邮件通过使用者自己的 SMTP 账号发送；
- AI 使用每位使用者自己配置的免费额度或付费 Key。

项目维护者不代付使用者的模型、邮件、服务器或数据库费用。公开仓库通常可以在 GitHub 免费额度内使用 Actions 与 Pages，但额度和服务条款以 GitHub 当前规则为准。

## 三分支与上游同步

| 分支 | 内容 | 谁会写入 |
|---|---|---|
| `main` | 上游代码和工作流 | 只通过正常上游同步/开发修改 |
| `state` | 公告、活动、游标、日期队列、通知回执 | 每日 Action |
| `page` | 可公开访问的最小静态网页 | 每日 Action |

关注 IP、地点、AI 和邮箱都放在 Repository Variables/Secrets 中，不放进 Git。因此修改个人配置不会产生 commit，fork 的 `main` 仍可直接同步上游。Horizon 会从 `main/docs` 发布到 `gh-pages`；本项目额外隔离 `state/page`，避免生成内容污染 `main`。

## 部署

1. Fork 本仓库，在 fork 的 **Actions** 页面启用工作流。
2. 打开 **Settings → Secrets and variables → Actions**，按下一节添加配置。
3. 手动运行一次 **Daily collaboration scan**。第一次运行会自动创建 `state` 和 `page`。
4. 打开 **Settings → Pages**，选择 **Deploy from a branch**，分支选 `page`，目录选 `/ (root)`。
5. 以后工作流每天约在北京时间 08:15 自动运行；GitHub 定时任务可能延后几分钟。

整个配置过程不要求修改或提交仓库文件，也不需要创建 `local.env`。GitHub Actions 会在运行时把 Repository Variables/Secrets 注入为环境变量，HERALD 再从运行进程中读取；这些配置不会进入 Git 历史，因此不会妨碍 fork 同步上游。

## 清晰的关注档案

下面是一份完整示例。左侧名称必须完全一致；值在 GitHub 网页中逐项填写，不要提交 `local.env`。

### Repository Variables（非敏感）

| 名称 | 示例 | 说明 |
|---|---|---|
| `WATCH_IPS` | `原神,明日方舟` | 必填；第一版只接受内置 IP 名称或别名 |
| `COUNTRY` | `CN` | 第一版固定支持中国 |
| `EXTRA_WEIBO_UIDS` | `原神:123456\n明日方舟:987654` | 可给已经内置的 IP 补充官方 UID，不能创建未知 IP |
| `REMIND_DAY_BEFORE` | `true` | 是否生成提前一天提醒 |
| `INCLUDE_IN_GAME` | `false` | 预留项；当前版本仍聚焦品牌/线下/商品联动 |
| `TIMEZONE` | `Asia/Shanghai` | 日期队列和邮件时区 |
| `PUBLISH_REACHABILITY` | `false` | 是否把地点推导的“本地/可达”发布到公开网页；默认关闭 |
| `AI_PROVIDER` | `openai_compatible` | 通用兼容服务用 `openai_compatible`；智谱用 `zhipu_openai`（也接受 `zhipu-openai`） |
| `AI_BASE_URL` | `https://example.com/v1` | 兼容服务地址；`zhipu_openai` 未填时默认为智谱开放平台 v4 |
| `AI_MODEL` | `your-model-name` | 模型名；不填则不调用 AI |
| `AI_VISION` | `false` | 模型支持图片输入时设为 `true`，用于读取公告海报 |
| `AI_JSON_MODE` | `true` | 服务不支持 `response_format` 时设为 `false` |
| `SMTP_HOST` | `smtp.example.com` | 邮件服务器 |
| `SMTP_PORT` | `465` | SMTP 端口 |
| `SMTP_USE_SSL` | `true` | `false` 时使用 STARTTLS |

### Repository Secrets（敏感）

| 名称 | 示例含义 | 是否必需 |
|---|---|---|
| `ORIGIN_CITY` | 常驻城市，只写城市级别 | 可选 |
| `REACHABLE_CITIES` | 愿意前往的城市，用逗号或换行分隔 | 可选 |
| `AI_API_KEY` | 使用者自己的模型 Key | 使用 AI 时必需 |
| `WEIBO_COOKIE` | 使用者自己的微博 Cookie | 匿名接口受限时可选 |
| `NOTIFY_EMAIL` | 收件地址 | 使用邮件时必需 |
| `SMTP_USERNAME` | SMTP 登录账号/发件地址 | 使用邮件时必需 |
| `SMTP_PASSWORD` | SMTP 授权码或密码 | 使用邮件时必需 |

`codex://threads/...` 是 Codex 任务引用，不是 GitHub Actions 可调用的模型 Key。开发期间可以让 Codex/Luna处理脱敏公开样本，但每日项目运行仍使用上述使用者自配 API。

没有配置 AI 时，疑似联动材料会进入 `state/pending-extraction`，不会被丢弃；以后补上 Key，即使微博已经不再返回那条旧帖，系统也会通过索引重新处理它。

智谱的 OpenAI 兼容层对采样参数有额外限制，使用时请设置 `AI_PROVIDER=zhipu_openai`，不要只替换通用 Provider 的 Base URL。该适配器会使用智谱支持的非零 `temperature`，同时保留官方支持的 JSON Object 模式。智谱返回 429 时不会在短时间内连续重试；候选材料会保留在 `pending-extraction`，留待下次运行。

## 内置信息来源

第一版只监控内置 IP 的官方微博：

- 原神：`6593199887`
- 明日方舟Arknights：`6279793937`
- 明日方舟终末地：`7745672941`
- 崩坏星穹铁道：`7643376782`

微博移动接口是非官方且可能变化。程序每天从最新页开始翻页，直到遇到“上次最新微博 ID”、越过最近 72 小时的日期下界、页面为空或达到安全页数上限；只把最新微博 ID 保存为增量游标，不会把页码或响应中的 `since_id` 保存成下一次运行的游标。官号转发时，转发正文与海报也会作为公开材料保留。若匿名访问失败，可由使用者自行配置 Cookie。

抓取请求使用 `https://m.weibo.cn/api/container/getIndex`，但材料中的 `source_url` 会根据官号 UID 和微博 bid 规范化为公开详情地址 `https://weibo.com/{uid}/{bid}`。前者是传输接口，后者是证据来源链接，不表示改用桌面网页抓取正文。

RSS 适配器目前只预留接口，因为多数品牌联动首先出现在社交官号，RSS 不保证覆盖这类公告。

## 本地开发

需要 Python 3.11。每个仓库副本都应创建自己的 `.venv`，不要直接把依赖安装到全局 Python。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
$env:PYTHONPATH='src'
python -m unittest discover -s tests -v
```

macOS 或 Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
PYTHONPATH=src python -m unittest discover -s tests -v
```

`.[dev]` 会安装 `pyproject.toml` 中声明的运行依赖和测试工具。`.venv/` 含有当前操作系统和 Python 安装相关的二进制文件，已被 `.gitignore` 排除，不应上传仓库。其他开发者只需执行上面的命令即可重建环境；不要提交从个人或全局环境生成的 `pip freeze`，依赖声明以 `pyproject.toml` 为准。

退出虚拟环境可运行 `deactivate`。如果 PowerShell 的脚本执行策略不允许激活，也可以不激活，直接把后续命令中的 `python` 换成 `.\.venv\Scripts\python.exe`。

本地真实调试可以使用和 GitHub Actions 完全相同的环境变量接口：

```powershell
Copy-Item local.env.example local.env
# 编辑 local.env，填入本地调试所需的公开配置和使用者自己的 Key
python scripts/run-local.py
```

`run-local.py` 可在 Windows、macOS 和 Linux 运行。它负责读取 `local.env` 的 `KEY=value` 和可选的本地 AI 档案，为 HERALD 创建独立的子进程环境，然后调用 `python -m herald`；不会修改当前终端的环境。HERALD 本身不读取这些本地文件，GitHub Actions 也不读取它们：Action 工作流直接把 Repository Variables/Secrets 注入环境。因此从 CLI 开始，本地与远端运行的是同一套逻辑。

### 本地 AI Provider 档案

需要频繁切换不同平台或模型时，可以把完整 AI 配置（包括真实 `AI_API_KEY`）放入本地私密 TOML：

```powershell
Copy-Item local-ai-providers.toml.example local-ai-providers.toml
```

macOS 或 Linux 使用：

```bash
cp local-ai-providers.toml.example local-ai-providers.toml
```

`local-ai-providers.toml` 已被 Git 忽略，可以保存多个本地档案。仓库只跟踪不含真实 Key 的 `local-ai-providers.toml.example`。例如：

```toml
[openrouter-minimax-minimax-m3]
AI_PROVIDER = "openai_compatible"
AI_BASE_URL = "https://openrouter.ai/api/v1"
AI_MODEL = "minimax/minimax-m3:free"
AI_VISION = true
AI_JSON_MODE = true
AI_API_KEY = "在本地填写真实 Key"

# 名称含点号时，TOML 表名必须加引号。
["siliconflow-qwen-qwen3.5-4b"]
AI_PROVIDER = "openai_compatible"
AI_BASE_URL = "https://api.siliconflow.cn/v1"
AI_MODEL = "Qwen/Qwen3.5-4B"
AI_VISION = true
AI_JSON_MODE = true
AI_API_KEY = "在本地填写真实 Key"
```

然后在 `local.env` 中只选择一个档案：

```dotenv
AI_PROFILE=openrouter-minimax-minimax-m3
```

档案名区分大小写，必须与 TOML 表名完全一致。选中档案后，其中六个 `AI_*` 字段会覆盖 `local.env` 中同名字段；其他本地配置（微博 Cookie、关注 IP、SMTP 等）仍来自 `local.env`。不填写 `AI_PROFILE` 时，原来的直接 `AI_PROVIDER`、`AI_MODEL`、`AI_API_KEY` 配置方式保持不变。

这套档案只由 `scripts/run-local.py` 解析，HERALD 包、正式 CLI 和 GitHub Actions 都不会读取它。若要把私密 TOML 放在其他位置，可传入 `--provider-file`：

```text
python scripts/run-local.py --provider-file "其他位置/local-ai-providers.toml"
```

### 采集指定日期范围的微博材料

以下命令只运行微博采集与候选筛选，不调用 AI、不读入 AI Provider 档案，也不运行 Campaign 合并：

```text
python scripts/run-local.py --collect-weibo-samples --sample-start-date 2026-08-01 --sample-end-date 2026-08-31 --sample-max-pages 20
```

起止日期均按中国标准时间理解，并且包含结束日期整天；两个日期必须同时提供。也可以使用相对范围：

```text
python scripts/run-local.py --collect-weibo-samples --lookback-days 45 --sample-max-pages 20
```

结果默认写入被 Git 忽略的 `.herald-work/ai-sample-candidates.json`。每条记录的 `extraction_input` 由生产流水线共用的构造函数生成，形状与真实 AI Provider 收到的输入完全一致，包括爬虫得到的正文、公开图片 URL、公开外链、规范化来源链接和 IP 提示。人工 Few-shot 只能在这个对象旁边添加 `expected_output`，不能补写或替换 `extraction_input`。

### 只测试 AI 提取

配置好 `AI_PROFILE` 后，可以只对固定的公开历史联动样本执行一次真实 AI 提取：

```text
python scripts/run-local.py --ai-smoke-test
```

该命令使用“原神 × 美团丨大众点评”的公开历史正文，不抓取微博、不运行 Campaign 合并、不读写 state/page，也不发送邮件。输出中的 `result` 是通过 `ExtractionResult` Schema 校验后的结构化结果，`checks` 会检查活动数量、原文证据、日期保守性以及是否凭空补充地点或操作链接。当前样本没有海报 URL，因此这条命令不验证视觉输入；视觉测试需要另行准备带公开图片 URL 的固定样本。

### 检查本地输出

默认调试结果分别写入：

- `.herald-work/local-state`：抓取游标、观察记录、活动状态、日期队列和通知回执；
- `.herald-work/local-page`：可直接预览的静态网页。

Windows PowerShell 可以这样列出文件：

```powershell
Get-ChildItem .herald-work/local-state -Recurse
Get-ChildItem .herald-work/local-page -Recurse
```

macOS 或 Linux 可以使用：

```bash
find .herald-work/local-state -type f
find .herald-work/local-page -type f
```

这些目录都被 Git 忽略。不要把本地调试状态或生成页面强制提交到 `main`。

### 预览静态页面

在项目根目录运行：

```text
python -m http.server 8000 --directory .herald-work/local-page
```

然后访问 <http://localhost:8000>。预览结束后按 `Ctrl+C` 停止临时服务器；这只用于本机检查，不改变项目正式部署时无常驻服务器的架构。

### 固定时间复现

需要检查“指定日期的即时通知、提前一天提醒或历史到期队列”时，可以隔离状态目录并传入带时区的时间：

```text
python scripts/run-local.py --now "2026-08-31T10:00:00+08:00" --state-dir ".herald-work/debug-state" --page-dir ".herald-work/debug-page"
```

`--now` 必须包含时区偏移。单独使用 `debug-state/debug-page` 可以避免与默认调试结果混在一起。该命令仍可能访问真实微博和 AI；如果 `local.env` 中同时配置了完整 SMTP 凭据与收件地址，也可能真实发送到期提醒。

没有配置 `AI_MODEL` 或 `AI_API_KEY` 时，候选材料会进入 `pending-extraction`，不会丢失；以后补上 Key 后会从索引重新处理。若运行报告出现来源访问警告，先保留生成的 state，再检查网络、微博匿名访问限制或可选的 `WEIBO_COOKIE`。

本地验证通过后，将 `local.env` 中的非敏感项逐项填入 GitHub **Repository Variables**，敏感项逐项填入 **Repository Secrets**。GitHub 不能直接导入整个文件；`local.env` 已被 `.gitignore` 排除，绝不能强制提交。

默认调试状态和页面写入 `.herald-work/`，同样不会进入 Git。若填写了完整 SMTP 配置和收件地址，本地运行可能真实发送到期提醒；只想调试微博和 AI 时，请让 `NOTIFY_EMAIL` 或 SMTP 凭据保持为空。

真实 API、微博和 SMTP 不参与离线测试。测试使用固定响应与 Mock Provider，因此无需 Key。

## 隐私、许可与致谢

请在启用公开 Pages 前阅读 [PRIVACY.md](PRIVACY.md)；安全说明见 [SECURITY.md](SECURITY.md)。项目采用 [MIT License](LICENSE)，第三方参考与许可证核验记录见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

感谢 [Thysrael/Horizon](https://github.com/Thysrael/Horizon) 提供 GitHub Actions + AI 信息雷达的产品启发；感谢 [dataabc/weibo-crawler](https://github.com/dataabc/weibo-crawler)、[dataabc/weiboSpider](https://github.com/dataabc/weiboSpider) 与 [nghuyong/WeiboSpider](https://github.com/nghuyong/WeiboSpider) 展示微博数据采集领域的实现思路。本项目没有复制前两个无明确许可证仓库的源码、配置、注释或测试；“致谢/侵删”不替代授权。如公开链接或说明存在权利问题，欢迎提交 Issue 联系处理。
