# Action 参与事项改动与验证（2026-09-12）

保持 Campaign → Activity → Action。首页仍为 Activity 卡片，使用最近一个未来日程节点，折叠展示各 Action。未知时间保留，Action 截止不写回 Activity 截止。

新增 gift、discount 类型，以及 scope、quantity_limit、end_condition、ended 字段。明确结束停止提醒；限量或赠完即止不产生虚构截止日期。旧 related_offers 兼容读取，新 Prompt 要求优惠分别建 Action。旧事实不自动重新提取。

验证：198 项离线测试通过；随后更新 Few-shot 与加强 Prompt 后，AI 工作流、合并及参与事项目标测试通过，compileall 与 diff 检查通过。浏览器验证卡片可展开且无脚本错误。

真实纯文本回放使用用户当前 Qwen/Qwen3.5-4B 配置，最终调用显式指定 AI_THINKING=disabled、AI_MAX_TOKENS=4096；是否接受 thinking 的语义取决于服务端。没有读图、OCR 或发送邮件。

最终测试产物位于被忽略的 `.herald-work/three-views-20260911/action-windows-final/`。模型生成购买、满赠、折扣三个 Action，优惠截止成功入队；但仍错误地将满赠时间用于整体预售，并把优惠时分降成日期。因此这次验证证明结构和调度链路可运行，不代表该模型提取准确。保留原始结果，未人工修正以冒充模型成功。

当前已知限制：仅凭引文存在性校验不能发现上述时间归属错误。还需持续改进模型选择或提取约束，不应依靠推断补齐缺失的预售时间。
