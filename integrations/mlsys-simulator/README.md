# 模拟器 API 对接：给模拟器组的统一入口

这里集中提供 `hello-mlsys-lab` 与 `mlsys-simulator` 对接需要的数据定义、样例、
已发现的差异和验收步骤。**转发本页面链接即可；阅读材料不需要本地文件、聊天记录、
API Key 或运行我们的 Web/Agent。**

**最新进展（2026-11-14）：1.20 的三种任务负载通过真实 HTTP 和本方结果统计验证，
24 项接口检查通过。Web/Agent 已接通真实服务，并以明确标注的联调配置完成三个任务的操作演示。原定课程配置仍受模型/硬件目录限制。**
请先看[Web/Agent 接入与演示](APP_INTEGRATION.zh-CN.md)，再看[新版独立联调结果与责任划分](REVIEW-v1.20.zh-CN.md)：成功配置、完整证据、
本方已修复的问题和目录待确认项均已区分；目前没有发现需对方修复的数据接口缺陷。

## 先看这几份材料

| 顺序 | 材料 | 用途 |
| --- | --- | --- |
| 0 | [1.20 最新联调结果](REVIEW-v1.20.zh-CN.md) | 哪些已跑通、实际用了什么配置、尚需双方确认什么 |
| 1 | [当前待确认事项](REVIEW-v1.20.zh-CN.md#当前需要双方确认的事项) | 模型/硬件范围及双方分工 |
| 2 | [最小数据接口说明](DATA_CONTRACT.zh-CN.md) | 输入/输出字段、单位、时间、并发、显存和失败语义，以这份为当前提案依据 |
| 3 | [请求 Schema](schemas/request.schema.json) / [响应 Schema](schemas/response.schema.json) | 完整 JSON 结构，包含可空字段和枚举；可独立于我们的 Python 项目使用 |
| 4 | [三个任务的请求样例](#三个任务的完整输入) / [响应示例](examples/RESPONSE_EXAMPLES.md) | 对照具体数据实现和检查接口 |
| 5 | [联调与验收步骤](VALIDATION.zh-CN.md) | 环境准备、HTTP 提交、返回数据校验和验收清单 |

[English API summary](API_PROPOSAL.md) · [Detailed findings in English](UPSTREAM_REQUESTS.md)

## 首轮要对齐什么

1.20 已提供 **`POST /v1/simulate`，JSON 请求，同步返回完整 JSON**，以及
`GET /v1/catalog`。一次调用对应一个配置、一份原始请求列表和一个种子。
单并发可以接受，静态模型/硬件目录也可以；首次联调不要求排队、轮询、取消、流式进度
或独立的 capabilities API。

```mermaid
flowchart LR
  Student[Student] <--> Lab[Lab web interface and learning agent]
  Lab <--> Backend[Lab backend]
  Backend <-->|JSON over HTTP| Simulator[Simulator service]
```

| 我方发送 | 模拟器返回 | 责任边界 |
| --- | --- | --- |
| 原始 request ID、token 长度、到达时间、DAG 依赖及相对间隔 | 每条请求的实际状态、输出数量和时间戳，包括失败、阻塞、未完成记录 | 模拟器执行调度；我方不补写调度器或重新生成近似负载 |
| 模型/硬件目录 ID、精度、部署、副本、TP、并发及 KV 配置 | 实际生效配置、模型/服务/校准版本、每条请求的部署归属和逐设备显存 | 模型结构、硬件性能参数和校准由模拟器管理 |
| 调用 ID、种子、统计时长、模拟截止和墙钟超时 | 对应调用 ID、实际输入 body 的哈希和明确的失败原因 | 我方保存记录、聚合指标、判断任务约束，并支持对话、引用、对比与报告 |

字段名可以经双方确认后调整。缺失功能应明确说明；未知观测用 `null` 和原因，
不能用零值或伪造完成记录补齐。模拟预测和真实硬件测量保持区分。

## 三个任务的完整输入

这些 JSON 文件完整保留当前任务的基线配置和原始请求，可直接在 GitHub 阅读或下载。
**其中的模型占位名和 `catalog_version: to-be-agreed` 尚未达成共识，不能原样宣称可执行。**
另有[1.20 公开协议测试配置](examples/protocol-v1.20)，与这些原基线分开保存。

| 任务 | 完整请求 | 现有负载与首轮验证重点 |
| --- | --- | --- |
| 9.1 | [task-9.1.request.json](examples/task-9.1.request.json) | 6 条固定顺序请求；前一条终止后释放下一条，不重置部署状态 |
| 9.2 | [task-9.2.request.json](examples/task-9.2.request.json) | 12 条不同长度、指定到达时间的请求；不重新采样或改成统一长度 |
| 9.3 | [task-9.3.request.json](examples/task-9.3.request.json) | 10 个 DAG 节点，多模型、多副本；子节点在父节点成功结束后再等待指定间隔 |

当前三份基线请求涉及 `llm-3b`、`llm-32b`、`llm-7b`，硬件 ID 为 `rx9070xt`、
`w7900d`。这些名字本身不能唯一确定真实模型、版本或性能参数。请双方先确认服务端
映射、支持的精度/上下文长度及目录版本；如果保留别名，由服务端解析并返回真实身份。
如果改用新 ID，由我方同步更新映射及相关引用，不由客户端猜架构。

[响应示例](examples/RESPONSE_EXAMPLES.md)覆盖成功、父节点失败和观测丢失。
示例使用独立的两节点 DAG，全部明确标记为 `mock`；其中的时间和容量用于说明格式，
不是三个任务的性能结果。真实联调应返回 `evidence_level: simulated`。

## 当前需要确认的正式配置

1.20 已明确接口地址、限制、单并发和输出行为，并通过独立检查。当前待确认的是
正式课程配置；可以通过 GitHub issue、PR 或现有沟通渠道逐项讨论。

| 确认项 | 请提供的信息 |
| --- | --- |
| 模型身份 | 原 llm-3b/7b/32b 具体对应哪个真实型号/版本或命名教学配置；这些是本方占位名，需要先明确 |
| 硬件与精度 | 是否保留原 W7900D 与 FP8 配置；如保留，双方需确认并扩展服务目录，不能用协议测试的小模型和 RX9070XT 替代正式结果 |
| 探索范围 | TP>1 等尚未公开的组合是否在首轮正式配置范围内，哪些先明确禁用 |

我方已完成 HTTP 客户端、记录类型/空值支持、统计验证、学生会话接入与页面展示。联调配置显式映射到公开模型 ID；正式课程模型身份仍待确认。
不要求模拟器依赖我们的 Python 包，或实现 Web 页面、Agent 对话和任务评分。

## 如何确认接通

按照[联调与验收步骤](VALIDATION.zh-CN.md)，先提交 9.2 的原始 trace，再验证 9.1
顺序执行和 9.3 多模型 DAG。检查返回 ID 完整、时间因果正确、部署归属与配置一致，
并保留真实 HTTP 请求和响应。HTTP 200、JSON Schema 通过或任务未达 SLA，分别表示
不同结果；完成接口联调不要求把每一次配置都变成性能达标。

当前已有[真实 HTTP 验证](evidence/agent-v1.20/report.json)和
[本方客户端运行产物](evidence/agent-v1.20/client)。此前的
[静态契约检查](evidence/data-contract-verification.json)作为历史记录保留。

## 已有实测证据与实现依据

最新服务检查为[1.20](REVIEW-v1.20.zh-CN.md)。以下 1.19 记录用于追溯已经解决的差异。

先前检查的模拟器为 [1.19.0 / fc6379f](https://github.com/eyuansu62/mlsys-simulator/tree/fc6379ff66dd58362a06cb27ae0f1366347e5df9)，
未修改其代码。这个版本的 HTTP 表单不能接收三个任务的完整数据；核心 Python API
支持的部分能力尚未暴露为 HTTP 接口。以上结论只针对该次检查版本。

- [HTTP 检查说明与复现命令](HTTP_REVIEW.md)。
- [HTTP 实测记录](evidence/http-report.json)及[原生接口语义复现](evidence/core-gaps.json)。
- [跨字段校验实现](contract.py)、[24 项契约测试](../../tests/test_simulator_data_contract.py)。
- [任务样例导出脚本](../../scripts/export_simulator_requests.py)、[Schema 与格式示例导出脚本](../../scripts/export_simulator_contract.py)。

本目录是双方确认协议的入口，应用已注册 `mlsys` HTTP 后端。历史检查、接口提案、
格式示例与真实运行证据分别标明用途；不需要下载额外 ZIP 才能获得对接信息。
