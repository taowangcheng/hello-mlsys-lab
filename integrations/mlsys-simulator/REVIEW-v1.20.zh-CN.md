# 1.20 独立联调结果与责任划分

2026-11-14，对 [mlsys-simulator 1.20.0 / feda667](https://github.com/eyuansu62/mlsys-simulator/tree/feda6677b90a6f50f35ac78c1d5eabff986ed5b7)
进行独立验证。模拟器代码未修改，仅启动正式 `mlsys_sim.agent_service`，课程端只通过
`GET /v1/catalog` 和 `POST /v1/simulate` 交换数据。接口说明见
[对方文档](https://github.com/eyuansu62/mlsys-simulator/blob/feda6677b90a6f50f35ac78c1d5eabff986ed5b7/docs/agent-integration.md)。

**结论：三种任务负载的数据接口通过验证，没有发现可以确认归因于模拟器数据接口的缺陷。
原定课程基线尚未全部可执行；成功记录使用对方公开且单独命名的协议测试配置。**
HTTP 客户端与本方结果统计已经实测，学生页面的外部后端选择尚未注册。

## 三个任务实际跑了什么

| 任务 | 实际执行配置 | HTTP 与本方接收结果 | 原基线状态 |
| --- | --- | --- | --- |
| 9.1 | Qwen3-1.7B、BF16、1 张 RX9070XT | 原顺序负载 6/6 完成，时间与统计通过 | 原 llm-3b/FP8 未确认，拒绝执行 |
| 9.2 | Qwen3-1.7B、BF16、1 张 RX9070XT | 原到达负载 12/12 完成，时间与统计通过 | 原 llm-3b/FP8 未确认，拒绝执行 |
| 9.3 | Qwen3-4B + Qwen3-0.6B、BF16、4 张 RX9070XT；后者 3 副本 | 原 DAG 10/10 完成，依赖间隔和副本归属通过 | 原 llm-32b/llm-7b、FP8、W7900D 未确认，拒绝执行 |

请求 ID、输入长度、输出目标、到达时间和依赖间隔保持一致。9.3 的 `target_model`
引用随明确选择的部署 ID 一起更新。配置差异逐字段保存在
[协议样例来源与差异](examples/protocol-v1.20/sources.json)，没有改写正式任务文件。
这些结果不证明原 FP8、大模型或 W7900D 基线已经运行，也不是硬件测量或质量达标证明。

9.1/9.2 的任务选择空间已经允许 1.7B/BF16，后续可明确配置 `llm-1.7b` 与
`qwen3-1.7b` 的身份对应后接入学生流程。9.3 当前限定 32B/7B 和 W7900D，协议测试的
4B/0.6B 与 RX9070XT 不能直接作为该任务的正式实验。

## 独立 HTTP 检查

[24 项真实 HTTP 检查报告](evidence/agent-v1.20/report.json)包含每次请求、响应的路径与哈希。
拒绝测试的“通过”表示服务按预期拒绝，不表示该配置运行成功。

- 三个任务原样提交均返回 422 `CATALOG_VERSION_MISMATCH`。
- 只更新目录版本后，9.1/9.2 仍返回 `UNKNOWN_MODEL_ID`，9.3 返回 `UNKNOWN_HARDWARE_ID`。
- 三个公开协议配置全部完成，9.2 相同请求字节重复执行的完整响应相同。
- 未支持模型、FP8、W7900D 和 TP degree=2 均明确拒绝，无静默替换。
- 三种模式的模拟截止均保留完整记录；未启动或被阻塞项没有伪造活动时间。
- 部署 OOM 保留 12 条未启动记录，尚无显存账本时返回 null 和原因。
- 顺序模式中第一条因 KV 容量失败，第二条仍成功执行。
- DAG 父节点失败时子节点 blocked，独立分支继续成功。
- 墙钟超时返回 504 `AGENT_TIMEOUT`。
- 两个真实并发调用得到 200 与 429 `AGENT_BUSY`，忙碌响应含 `Retry-After: 1`。

完整字节均在 [evidence/agent-v1.20](evidence/agent-v1.20)，无需额外压缩包。
源码 commit 来自启动前核对的本地检出；HTTP 响应仅声明 1.20.0 版本，
本报告没有声称通过 HTTP 验证了源码 commit。

## 确认并修复的本方问题

旧 `RequestObservation` 要求 ready/finish/service 时间均为数值，而且不接受
blocked/not_started/unknown。原指标计算直接相加 service_ms，遇到合法的 null 会失败。
这是**本方结果接收和统计的问题**，不能要求模拟器编造数值来兼容。

已完成以下正常适配：

- [共用数据模型](../../src/mlab/contracts/simulator.py)与文档使用同一份传输字段定义，
  不改变既有请求/响应 JSON Schema。
- [HTTP 客户端](../../src/mlab/backends/simulator_http.py)按原字节提交，检查输入哈希、全部
  ID、因果时间、部署及实际配置；保留错误和原始回复，不自动重试或降级为 mock。
- [本方记录类型](../../src/mlab/contracts/__init__.py)保留空值、阻塞、未启动、未知观测和副本归属。
- [共用统计](../../src/mlab/core/metrics.py)支持缺失服务耗时和非拓扑顺序的返回列表；
  未知观测不被计成确定失败，也不产生假零吞吐。
- `service_ms=null` 时，TTFT、TPOT、吞吐和成功 DAG 的 makespan 仍可计算；
  `critical_path_s=null` 并给出原因。逐设备显存保留原值，不叠加已经包含的 KV 池。
- `uncalibrated` 与 `in_domain=null` 原样保留，没有改成已校准或真实硬件测量。

三次额外执行通过这个客户端和统计层，完整产物见：
[9.1](evidence/agent-v1.20/client/task-9.1)、[9.2](evidence/agent-v1.20/client/task-9.2)、
[9.3](evidence/agent-v1.20/client/task-9.3)。每份包含原始请求、原始响应、逐请求记录、
指标汇总和产物哈希。它们没有附着到学生会话，也未授予任务最终成绩。

## 当前需要双方确认的事项

**本轮无需转交“数据接口错误修复单”。** 如需继续沿用原课程配置，需要确认目录范围：

| 事项 | 归属与处理 |
| --- | --- |
| llm-3b / llm-7b / llm-32b 的具体身份 | 占位名来自本方任务；先明确真实型号/版本或命名的教学配置，再商定服务目录，不能让模拟器猜 |
| W7900D、FP8 和其他量化配置 | 公开目录明确不支持；若保留这些课程配置，需要双方确认定义，再由模拟器暴露已支持组合。这是能力范围协商，不是 JSON 或调度协议错误 |
| TP>1 | 尚无公开通信拓扑；若保留这部分探索空间，需要确认目录，不把私有拓扑配置放进 Agent |
| service_ms 暂缺 | 已按契约返回 null 和原因，本方已兼容，不阻塞首轮对接；独立服务关键路径暂不可评估 |
| Web/Agent 后端选择、会话存储和展示 | 本方负责；本次完成 HTTP 客户端与统计层，页面尚未切换到新服务 |

如果之后发现异常，先保留原始请求/响应，独立复现，并区分请求是否符合目录、HTTP
返回是否违约，以及本方统计/界面是否出错，再决定是否转交。

## 复现

准备环境见[联调步骤](VALIDATION.zh-CN.md)。在独立模拟器源码目录启动：

```bash
python -m mlsys_sim.agent_service --port 8877
```

在本仓库运行独立检查，输出目录必须尚不存在：

```bash
.venv/bin/python scripts/probe_simulator_agent_api.py \
  --base-url http://127.0.0.1:8877 \
  --service-commit feda6677b90a6f50f35ac78c1d5eabff986ed5b7 \
  --out workspaces/simulator-agent-review
```

`--service-commit` 记录操作者核对的部署版本；服务版本不同请填写实际版本。
通过本方客户端执行并统计三个协议配置：

```bash
for task_id in 9.2 9.1 9.3; do
  .venv/bin/python scripts/run_simulator_http.py \
    --base-url http://127.0.0.1:8877 \
    --request "integrations/mlsys-simulator/examples/protocol-v1.20/task-${task_id}.protocol.request.json" \
    --out "workspaces/simulator-client-review/task-${task_id}" || exit "$?"
done
```

本地契约与接收层检查：

```bash
.venv/bin/python -m pytest -q tests/test_simulator_data_contract.py tests/test_simulator_http.py tests/test_metrics.py
```

测试使用保存的真实 HTTP 响应与明确的单元测试输入，不依赖运行中的模拟器。
完整回归及本次验证范围见[验证记录](evidence/agent-v1.20/verification.json)。
