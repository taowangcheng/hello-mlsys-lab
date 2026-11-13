# 模拟器最小数据接口：用于三个现有任务的首轮对接

**状态：我方提交给模拟器组确认的数据契约，不表示接口已经实现。** 本轮保留三个任务、
负载、基线和评价方式，不扩展教学流程。优先确认输入与输出，先让一次完整仿真通过
HTTP 执行；排队任务系统、取消、流式进度、大文件下载等后续再完善。

## 1. 首轮只需要一个执行接口

建议 `POST /v1/simulate`，JSON 输入，完成后同步返回 JSON。路径可以由模拟器组确定，
不要求替换已有的 Web 表单接口。一次调用对应一个配置、一份固定负载和一个种子。

前端和学习助手仍调用我们的实验平台。**我们的后端负责发 HTTP、保存结果和统计，
模拟器服务负责模型结构、硬件性能参数、校准、调度和显存计算。** 不需要前端或 Agent
安装模拟器包、调用私有函数、持有模型结构或自己协调 DAG 执行。

首轮可使用双方确认的静态模型/硬件目录，不把新增 capabilities API 作为前置条件。
单并发可以接受。合法实验返回 HTTP 200，即使实验结果是 OOM 或未达标；格式/参数
不支持返回 400/422，服务忙返回 429，服务故障返回 5xx，并提供结构化错误。
客户端连接超时只表示未收到答复，不能直接宣称仿真已经超时或被取消；首轮不自动重试。

## 2. 我们发给模拟器的数据

完整字段见 [请求 JSON Schema](schemas/request.schema.json)。首轮使用现有公开配置
结构，仅把工作负载文件引用替换成内联请求数据，明确补出模拟时间截止值。

| 字段 | 内容与用途 |
| --- | --- |
| `schema` | 固定为 `mlab.simulator.v1`，本次拟定的接口版本 |
| `client_request_id` | 我方生成的调用标识，响应原样返回，用于对应记录；首轮不承诺幂等执行 |
| `catalog_version` | 双方确认的模型/硬件目录版本；样例的 `to-be-agreed` 必须在实际执行前确定 |
| `task`、`candidate_id` | 我方任务和配置标签，仅用于追踪；模拟器不需要理解教学流程 |
| `workload.mode` | `sequential`、`open_loop` 或 `dag` |
| `workload.requests` | 完整原始请求列表，不能在服务端重新生成近似负载 |
| `workload.source_sha256` | 原始任务 JSONL 文件的 SHA256，作为来源记录；不是内联 JSON 重新序列化后的哈希 |
| `infra` | 节点、设备 ID、硬件目录 ID；不包含算力、带宽、模型层数或校准系数 |
| `strategy` | 学生选择的部署和服务配置，字段含义见下表 |
| `evaluation` | 当前任务约束、统计分母、墙钟超时和 goodput 策略；最终评价由我方计算 |
| `execution` | 种子、重复编号、负载标签、初始状态和模拟时间截止 |

### 每条请求

| 字段 | 含义 |
| --- | --- |
| `request_id` | 原始唯一 ID，结果中必须保留 |
| `input_tokens` | 输入长度，正整数 |
| `output_tokens_target` | 强制回放的输出长度，正整数；这是执行目标，不能提前提供给在线调度策略做优先级判断 |
| `arrival_ms` | 开放到达模式的绝对到达时间；DAG 根节点的到达时间，省略时为 0 |
| `target_model` | DAG 请求对应的部署模型 ID；单部署任务可以省略，表示唯一部署 |
| `depends_on` | DAG 父请求 ID 列表；必须存在且无环 |
| `gap_after_deps_ms` | 所有父请求成功完成后，再等待的相对时间；不是绝对 release 时间 |
| `category`、`prefix_group`、`controller_id`、`subagent_id` | 原始负载标签，保留用于分析；不能从 prefix_group 自行推断缓存命中或前缀长度 |

9.1 的数组顺序就是顺序执行顺序。9.2 按 arrival_ms 到达。9.3 按依赖执行，不依赖数组
是否已拓扑排序。多部署请求不能缺少可唯一确定目标的模型 ID。

### 部署与服务配置

| 字段 | 首轮明确的含义 |
| --- | --- |
| `deployments[].model` | 双方目录中的模型/别名；服务端解析真实型号和版本，不按 `3B` 等字符串猜架构 |
| `weight_precision` / `kv_precision` | 分别为权重与 KV 精度；不支持的组合明确拒绝 |
| `context_tokens` | 上下文上限，至少容纳完整输入与目标输出；如何分配 KV 由模拟器决定 |
| `parallel.kind` / `degree` | `none` 时 degree=1；TP 的每个副本由 degree 张卡组成。PP 首轮不要求支持 |
| `replicas` / `devices` | devices 按“副本、TP rank”展开，数量等于 replicas×degree，各部署设备互斥 |
| `serving.max_concurrency` | 建议统一为**每个副本**最多活动请求数；全局字段对每个部署使用同一数值，本轮不新增按模型池分别配置 |
| `continuous_batching` | 是否启用连续批处理；首轮可仅支持 true，或 false 且并发为 1，其他组合明确拒绝 |
| `chunked_prefill_tokens` | null 表示关闭；否则为单请求每次 prefill chunk 的最大 token 数，不是整个 batch 的总 token 上限 |
| `gpu_memory_utilization` | 每张卡可使用的物理显存比例 |
| `kv_budget_gib` | 每 TP rank、每副本的 KV 池预算，1 GiB=2^30 字节 |
| `kv_fraction` | 每张卡“可用显存预算”的比例，不是减去权重后剩余显存的比例；与 kv_budget_gib 互斥 |
| 两种 KV 预算都为 null | 使用模拟器端已说明、可追溯的自动分配策略，并返回实际 KV 池预留量 |
| `scheduling`、`cache`、`speculative` | 保留现有字段；首轮以 FIFO、无抢占、无 prefix cache、无推测解码为基础，未支持项返回明确错误 |

例如 `replicas=2, degree=2, devices=[gpu0,gpu1,gpu2,gpu3]`，表示两组：
副本 0 使用 gpu0/gpu1，副本 1 使用 gpu2/gpu3。模拟器不能把它解释成四个副本。

## 3. 三个任务的执行时间语义

| 任务 | 必须保持的规则 |
| --- | --- |
| 9.1 | 第一条请求在 0 ms 就绪，后一条在前一条终止后就绪。请求级失败后继续下一条；整体部署失败或运行被终止时，尚未发出的记录为 not_started。不能用多个独立仿真重置部署状态来近似 |
| 9.2 | 每条请求在原始 arrival_ms 到达，与前一条是否完成无关。保留长短请求、突发间隔和输出目标，不重新采样 |
| 9.3 | 根节点按 arrival_ms 就绪；子节点 ready=max(父节点成功完成时间)+gap_after_deps_ms。父节点失败后，其后代标记 blocked；不依赖该节点的分支仍可执行 |

例：父节点在 1000 ms 完成、gap 为 100 ms，子节点应在 **1100 ms** 就绪。子节点不再
同时携带绝对 arrival_ms，避免两种时间混用。被阻塞节点没有伪造的 ready 或首 token。

首轮区分三个量，不引入新的负载窗口或排空策略：

- `evaluation.duration_s`：现有任务的吞吐/goodput 统计分母。
- `execution.max_simulated_ms`：模拟时钟截止值，届时仍未完成的请求保留部分输出。
  新样例显式使用原任务的 300000/60000/300000 ms，保持当前任务的截止行为。
- `evaluation.timeout_s`：服务实际执行的墙钟时间上限，不把它算入模拟延迟。

恰好在模拟截止时刻完成的请求计为完成，其他未完成请求记为超时/部分输出。初始状态
为空，warmup 为 null；不同运行不继承前一运行的 KV 或 cache。`repeat` 是记录标签，
不暗中改变 seed；`split`/`holdout` 也不授权服务生成或更换已经传入的请求。

## 4. 模拟器返回什么

完整字段见 [响应 JSON Schema](schemas/response.schema.json)。首先需要完整请求观测，
不要求模拟器先做我们的图表、任务评分、质量判断或全部聚合统计。

| 字段 | 要求 |
| --- | --- |
| `client_request_id` | 原样对应提交记录 |
| `input_sha256` | 服务对实际收到的 UTF-8 请求 body 字节计算 SHA256，格式为 `sha256:<64位小写十六进制>`；不重新序列化后计算 |
| `status`、`failure_class`、`error` | 运行是否完成、为什么失败；不能用执行成功表示 SLA 达标 |
| `backend`、`catalog_version`、`model_revisions` | 服务/引擎版本、目录版本及实际解析的模型版本，便于回溯 |
| `evidence_level` | 真实模拟器返回 simulated；协议展示样例明确标 mock，不能用于性能结论 |
| `calibration` | 校准版本、uncalibrated/calibrated/extrapolated、范围是否已知和假设；未校准时 in_domain=null |
| `effective_config` | 实际生效的公开 infra/strategy；不得静默换模型、精度、负载或并发值 |
| `requests` | 每个输入 ID 恰好一条最终记录，包括失败、被拒绝、部分输出、阻塞和未启动 |
| `per_device` | 每设备容量、物理显存峰值、KV 实际使用峰值和 KV 池预留字节数；不能把池预留与已包含在其中的使用量再相加 |
| `unsupported_fields` | 暂时无法返回的诊断字段及原因；未知不能填零 |
| `logs` | 可阅读的错误码和诊断信息，不包含私钥、内部文件路径或要求我方理解的私有引擎对象 |

### 每条结果记录

必需：`request_id`、`status`、`ready_ms`、`first_token_ms`、`finished_ms`、
`output_tokens`、`output_tokens_target`。

- completed 必须输出恰好达到目标，时间顺序正确；finished_ms 是最后一个 token 就绪
  的时刻。所有时间是同一模拟时钟下的毫秒，不混用 HTTP 接收时间。
- 失败、被拒绝、超时和 truncated 仍保留；有部分输出时返回真实数量与首 token 时间。
- blocked/not_started 的 ready、首 token、服务开始时间为 null，输出数量为 0。
  finished_ms 若有真实的终止判定时刻可返回，否则为 null。blocked_by 列出失败依赖。
- 崩溃导致观测丢失用 unknown，数量/时间可为 null；我方将相关统计标为不可评估，
  不把它改成确定的零输出或完成率 0。
- 完成请求还应返回 `deployment_model`、`replica_index`、`device_ids`，使 9.3 的记录
  能按模型和副本展示。副本编号对应输入 devices 的分组规则。
- `started_ms` 和 `service_ms` 是诊断字段：能提供最好；暂不支持可以为 null，
  并在 unsupported_fields 说明。service_ms 若提供，必须是不含排队/依赖等待的
  服务耗时，且不能把同时工作的 TP rank 时间相加。

## 5. 我们负责的正常适配

我方可以做字段改名、字节转 GiB、记录存储和基于完整明细的统计。不能补调度、猜模型
结构、构造缺失时间戳、改 trace 或把原生 makespan 改名当作 critical path。

保留现有任务口径：TTFT=首 token-ready；TPOT 仅对正常完成且输出≥2 的请求计算；
完成率分母包含所有输入请求；吞吐为正常完成的输出 tokens / duration_s；goodput
进一步筛选满足任务 SLA 的完成请求。未知观测使相关指标不可评估，不参与“伪造通过”。
分位数使用线性插值；仅一个输出 token 的请求没有 TPOT 样本。显存/KV 汇总采用各设备
对应峰值的最大值并转为 GiB，同时保留逐设备数据，不把它描述成同时发生的集群峰值。

父节点/服务时间足够时，我方可计算不含排队的 DAG 服务路径。暂缺 service_ms 时，
critical_path_s 返回 null 和原因，仍可使用全图成功时的 makespan 比较配置。
质量表与最终任务结论留在平台侧，不要求模拟器模拟模型回答质量。

平台侧后续要正常扩展现有记录类型，支持 blocked/not_started/unknown 和可空时间；
这一点由我们负责，不要求模拟器返回虚构数值来迎合旧类型。

## 6. 交给模拟器组的样例与首轮确认项

- [9.1 请求](examples/task-9.1.request.json)：6 条固定顺序请求。
- [9.2 请求](examples/task-9.2.request.json)：12 条原始混合长短、指定到达时间的请求。
- [9.3 请求](examples/task-9.3.request.json)：10 个原始 DAG 节点，多模型、多副本。
- [最小 DAG 请求与响应示例说明](examples/RESPONSE_EXAMPLES.md)：成功、父节点失败、
  观测丢失的格式，**仅用于协议说明，不是仿真性能证据**。
- [契约检查代码](contract.py)：只校验数据、ID 与因果时间，不实现模拟器。

对方按文档与 JSON Schema 实现即可，不需要依赖我们的 Python 包。Python 模型和检查
脚本用于我方维护样例一致性，不是要求模拟器组导入的运行组件。

原始请求列表与现有 task 文件逐项一致。`source_sha256` 可核对来源，`input_sha256`
绑定实际传输内容；两者用途不同。模型占位名仍原样保留，没有擅自替换真实模型。

请对方优先回复四件事：

1. **模型目录**：现有模型/硬件 ID 分别对应什么具体版本和配置，哪些精度组合可运行？
   其中 3B 等仍是我们需要共同解决的占位名，不能靠模拟器猜。
2. **输入含义**：是否同意每副本并发、KV 预算范围、TP 分组、顺序/依赖间隔和截止语义？
3. **输出能力**：必需时间与部署记录能否完整返回，哪些诊断字段暂时只能给 null？
4. **执行入口**：最终 URL、单次请求大小/时间限制、是否单并发，以及结构化错误格式？

首轮验收先用 9.2 原始 trace 验证输入/输出完整性，再验证 9.1 顺序释放和 9.3 的
100 ms 相对间隔、失败依赖传播、模型副本归属。正式判定接口跑通需要真实服务返回，
本目录的静态样例和校验通过均不能替代这一验收。
