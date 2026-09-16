# 本地提取调试网页

启动：`python scripts/debug-web.py`，打开 http://127.0.0.1:8766 。只监听本机，不参与 GitHub Actions 和正式静态发布，不发送邮件。

1. 选择 `.herald-work` 下已有 dataset、observations 或 candidates 记录，查看原帖和配图链接，可按中国时间的发帖日期筛选并勾选。
2. 调整模型 ID（空值沿用私有配置）、thinking、输出上限、初始化批大小、系统 Prompt、初始化分界及模拟页面日期。24,000 字符预算仍有效。同账号历史帖子合批，增量逐帖。
3. 点击运行，新目录保存所选正文、公开调试参数、完整实际请求、模型回答、诊断、保存事实及静态页。每次独立运行，不覆盖已有样本。
4. 右侧同时显示原始提取及校验后保存的事实。JSON 和请求可展开；校验通过不代表语义正确。
5. 可单独运行现有社区采集器，输入开始、结束（不含）及每账号页数；采集不调用 AI。刷新后选择生成的记录，反复调用模型无需再次爬取。采集涉及内置米游社／森空岛官方来源，页数上限不保证日期范围完整。采集 manifest 有完整性记录。

私有配置仍只由 `scripts/run-local.py` 读取后注入独立子进程；网页和调试服务不读 Key、Cookie、SMTP 配置。参数和 Prompt 不要填写凭据。Prompt 编辑只覆盖该调试子进程中的基础系统文字；Few-shot、Schema、批处理及合并说明仍由正式代码附加，完整请求以结果为准。不修改生产默认参数。

默认 Few-shot 来自 `src/herald/data/activity-examples.json`。本页暂不提供 Few-shot 或 Schema 编辑，也不处理 OCR／视觉提取。

运行目录为 `.herald-work/debug-web/runs/`，采集目录为 `.herald-work/debug-web/collections/`，均被 Git 忽略。关闭本地进程即可停止服务；模型任务运行中应等待结束后再关闭。
