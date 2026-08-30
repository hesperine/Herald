# Third-party notices and acknowledgements

核验日期：2026-08-30。

## Horizon

[Thysrael/Horizon](https://github.com/Thysrael/Horizon) 是 MIT 许可的 Python AI 新闻雷达，Copyright (c) 2026 Thysrael。其 GitHub Actions、每日聚合和 GitHub Pages 产品形态为本项目提供了架构启发。Horizon 当前从 `main/docs` 触发并发布至 `gh-pages`；HERALD 独立实现 `main/state/page` 隔离，以避免用户运行状态影响 fork 与上游同步。

本仓库没有直接复制 Horizon 源码。若将来复制或修改 MIT 代码，必须在分发中保留对应版权和许可文本。

## Weibo projects

- [nghuyong/WeiboSpider](https://github.com/nghuyong/WeiboSpider)：MIT License，Copyright (c) 2019 HuYong。
- [dataabc/weibo-crawler](https://github.com/dataabc/weibo-crawler)：核验时未发现 LICENSE 文件。
- [dataabc/weiboSpider](https://github.com/dataabc/weiboSpider)：核验时未发现 LICENSE 文件。

这些项目用于理解微博采集领域的常见能力与术语。HERALD 的微博适配器为独立实现，没有复制上述项目的源码、配置、注释或测试，也没有把无明确许可证的项目作为依赖或重新分发。“致谢/侵删”不构成授权；若将来需要实质使用其代码，必须先取得适用许可。

## Product references

[CSBaoyan DDL](https://ddl.csbaoyan.top/) 的时间信息浏览方式曾作为产品讨论参考。当前前端刻意保持最低可用实现，没有复制其代码、样式或视觉资产。
