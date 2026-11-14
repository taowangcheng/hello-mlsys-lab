# 模拟器接口联调与验收

[返回对接入口](README.md) · [字段与语义](DATA_CONTRACT.zh-CN.md)

本页区分本地契约检查和真实服务联调。**1.20 的公开协议配置已通过真实联调，
见[最新结果](REVIEW-v1.20.zh-CN.md)；原课程基线仍需确认模型与硬件目录。**
文档、Schema、样例和真实响应都已在仓库中，无需额外 ZIP。

## 1. 阅读与检查材料

如果只实现接口，直接使用[请求 Schema](schemas/request.schema.json)、
[响应 Schema](schemas/response.schema.json)和[字段说明](DATA_CONTRACT.zh-CN.md)即可，
不需要安装本项目或将我们的 Python 包加入模拟器依赖。

如果希望运行我方的跨字段检查，在本仓库的新检出中准备 Python 3.12 环境：

```bash
git clone https://github.com/taowangcheng/hello-mlsys-lab.git
cd hello-mlsys-lab
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q tests/test_simulator_data_contract.py tests/test_simulator_http.py tests/test_metrics.py
```

已有检出和环境时直接运行最后一条命令。这个检查只使用本地数据，不启动 Web、Codex、
学习 Agent 或模拟器，不调用模型 API。无需执行 `scripts/setup.sh`。

测试覆盖原始任务数据保持一致、成功/失败/未知观测、重复或缺失 ID、配置变更、
放置错误、DAG 相对间隔、顺序与开放到达语义、截止时间、空值和输入哈希。
JSON Schema 检查字段结构，Python 检查还验证跨字段关系；二者都不预测性能。

## 2. 准备第一次真实请求

建议先验证 [9.2](examples/task-9.2.request.json)。双方确认以下内容后再提交：

- 将 `catalog_version: to-be-agreed` 替换为服务端已公布的目录版本。
- 确认模型/硬件别名能由服务端解析。需要改 ID 时，同步修改部署模型、请求中的
  `target_model` 和相关硬件引用；不变更请求 ID、token 长度、到达时间、依赖和间隔。
- 确认精度、上下文长度、每副本并发、KV 预算及截止语义；不支持的配置由双方另行
  确认，不能由服务静默改成其他配置。目录变更本身不意味着允许改动这些参数。
- 使用新的 `client_request_id`，并记录已确认的协议与服务版本。

每次联调用新目录保存传输的原始文件。以下命令均从仓库根目录执行，并在同一个终端
继续后续步骤：

```bash
mkdir -p workspaces
SIMULATOR_CHECK_DIR="$(mktemp -d "$PWD/workspaces/simulator-contract.XXXXXX")"
export SIMULATOR_CHECK_DIR
cp integrations/mlsys-simulator/examples/task-9.2.request.json "$SIMULATOR_CHECK_DIR/request.json"
printf '%s\n' "$SIMULATOR_CHECK_DIR"
```

在复制出的 `request.json` 中填写双方已确认的目录与调用 ID。仓库样例保留原貌。
`workload.source_sha256` 记录原始任务 JSONL 的来源，不是修改后传输 body 的哈希；
响应中的 `input_sha256` 应由服务端根据实际接收的 body 字节计算。

## 3. 通过 HTTP 提交并保存结果

将 `SIMULATOR_URL` 改为双方确认的完整地址，认证方式按服务约定补充。
**下例路径已由 upstream 1.20 实现；原基线的目录和模型占位名仍需先处理。**
示例请求的墙钟执行上限是 1800 秒，curl 多留 30 秒接收结果；双方另有上限时同步调整。

```bash
SIMULATOR_URL='http://127.0.0.1:8877/v1/simulate'
curl --silent --show-error --fail-with-body \
  --connect-timeout 10 --max-time 1830 \
  --request POST "$SIMULATOR_URL" \
  --header 'Content-Type: application/json' \
  --data-binary "@$SIMULATOR_CHECK_DIR/request.json" \
  --dump-header "$SIMULATOR_CHECK_DIR/http.headers" \
  --output "$SIMULATOR_CHECK_DIR/response.json" \
  --write-out '%{http_code}\n' > "$SIMULATOR_CHECK_DIR/http.status"
```

只在 curl 成功且 `http.status` 为 200 后继续校验运行结果。400/422 表示输入或能力
问题，429 表示服务忙，5xx 表示服务故障；保留响应中的错误码、消息与字段定位。
错误响应不要求伪装成一份已经执行的完整请求记录。首次版本不自动重试；连接中断或
客户端超时不能证明服务端已经停止。

使用 `--data-binary` 保持文件字节不变。收到结果前后不要重新格式化已发送的文件，
否则计算出的输入哈希不再代表这次传输。

## 4. 校验真实返回的数据

```bash
.venv/bin/python - <<'PY'
from collections import Counter
import json
import os
from pathlib import Path
import runpy

directory = Path(os.environ["SIMULATOR_CHECK_DIR"])
if (directory / "http.status").read_text().strip() != "200":
    raise SystemExit("Expected HTTP 200; inspect the saved error response first.")
contract = runpy.run_path("integrations/mlsys-simulator/contract.py")
request_bytes = (directory / "request.json").read_bytes()
request = contract["RunRequest"].model_validate_json(request_bytes, strict=True)
if request.catalog_version in {"to-be-agreed", "EXAMPLE_ONLY"}:
    raise SystemExit("Agree a real catalog before validating a service exchange.")
response = contract["check_response"](
    request_bytes,
    json.loads((directory / "response.json").read_text(encoding="utf-8")),
)
if response.evidence_level != "simulated":
    raise SystemExit("Mock examples cannot certify a real simulator exchange.")
print(json.dumps({
    "contract_validation": "passed",
    "run_status": response.status,
    "request_outcomes": dict(Counter(row.status for row in response.requests)),
    "backend": response.backend.model_dump(),
    "catalog_version": response.catalog_version,
    "note": "Inspect the real HTTP evidence separately; this check does not evaluate task SLAs.",
}, indent=2))
PY
```

校验通过表示这份数据满足当前提案的结构与跨字段规则，不代表配置达标、数值校准
准确或证明服务真的执行了引擎。失败/部分输出若被准确返回，也可以是有效的接口结果；
`run_status` 和 `request_outcomes` 需一起查看。模拟器应保留服务端执行依据供联调核对。

## 5. 三个任务与边界情况的验收清单

| 场景 | 需要核对的行为 |
| --- | --- |
| 9.2 | 原始 12 个 ID 全部保留；按原 arrival_ms 就绪，长度/目标不变，不重新生成负载 |
| 9.1 | 6 条按原顺序执行；第一条在 0 ms 就绪，之后跟随前一条终止时间；请求级失败后可以继续，整体部署失败时保留 not_started |
| 9.3 | 10 个节点和多模型/副本配置保持一致；子节点 ready 为最后一个成功父节点的完成时间加相对间隔；返回实际模型、副本和设备 |
| 依赖失败 | 父节点失败时后代 blocked，包含原因；其他独立分支可执行，不伪造子节点时间 |
| 模拟截止 | 截止时已完成的请求保留完成状态；其他请求保留真实部分输出和失败原因；恰好截止时完成有效 |
| 丢失观测 | 记录 ID 仍保留，未知时间/数量为 null，状态 unknown；不能宣称完整成功 |
| 显存与诊断 | 逐设备字节单位明确；暂缺诊断值返回 null 及 unsupported_fields 原因，不用零值代替未知 |
| 不支持/服务忙 | 明确的 400/422 或 429；不静默替换模型、精度、负载或并发配置 |

失败边界可通过模拟器自身的测试环境验证；不要为了制造失败而改变三个任务的正式
请求记录。首轮不要求取消或异步查询。需要双方达成不同语义时，先更新契约和样例，
再调整校验代码，避免两边靠特殊处理勉强通过。

完成联调后，双方记录对应 lab commit、服务/目录版本、实际 HTTP 请求和响应、
校验结果及尚不支持的字段。对外保存的材料排除认证信息。随后由我方把 HTTP 适配接入
应用，再验收页面中的运行记录、引用和结果展示；本页的静态检查不会自动完成应用接入。
