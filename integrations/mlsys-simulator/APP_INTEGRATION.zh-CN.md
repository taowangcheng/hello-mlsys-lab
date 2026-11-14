# Web/Agent 与模拟器的应用联调

2026-11-14。模拟器为未修改的 1.20.0，源码检出为
[`feda667`](https://github.com/eyuansu62/mlsys-simulator/tree/feda6677b90a6f50f35ac78c1d5eabff986ed5b7)。
课程端通过公开 HTTP API 调用，不导入模拟器内部 Python 模块，也不计算替代性能数据。

## 当前结论

**三个任务的负载已通过应用执行链路接通。当前没有新增、已确认需要模拟器同学修复的数据接口缺陷。**
9.2 的长提示词保留负载另出现 `MEMORY_NO_PROGRESS`，这是按契约返回完整记录的模型化资源失败；没有将它算作成功，也没有据此宣称实际 GPU 一定会失败。原来的 [24 项独立 HTTP 验证](evidence/agent-v1.20/report.json)仍有效。
本轮增加了 Web/Agent、运行记录、引用、请求详情、模拟与沙盒对比、笔记和报告的验证。也修正了本方的失败准备循环、配对比较混入旧选择，以及报告把未评估误标为违反约束的问题。旧报告中“超出波动”的标签改为“配对请求偏移”：该标记按固定阈值判断，不是噪声估计或统计显著性检验；Agent 获得的说明也同步修正。

这里的“跑通”指明确选择的联调配置完成执行与结果展示，**不等于原课程基线或正式验收通过**。

| 任务 | 联调配置 | 原始开发负载 | 演示中的探索 |
| --- | --- | --- | --- |
| 9.1 | Qwen3-0.6B / 1.7B，BF16，RX9070XT | 6 个顺序请求全部完成 | 同精度模型比较、显存口径、重复与另一组提示词 |
| 9.2 | Qwen3-1.7B，BF16，RX9070XT | 12 个到达请求全部完成 | 并发 32/16/20、手动修改配置、请求级检查、短/长提示词 |
| 9.3 | Qwen3-4B + 0.6B，BF16，4 张 RX9070XT | 10 个 DAG 节点全部完成 | 工作模型 1/3 副本、依赖间隔、另一 DAG 和并发变化 |

显式的 `protocol-v1.20` 会话配置保留请求 ID、token 长度、到达与依赖间隔。
9.3 的目标模型引用随选定部署一起更新。原 `tasks/`、`examples/` 和课程目录文件没有改写。
会话保存原始来源哈希、选用模型的公开 ID 映射与说明，页面和报告均显示范围提示。
原质量表不能套用到新模型，所以联调会话的质量状态是 `not_evaluated`。

## 仍需处理的事项及归属

| 事项 | 当前状态与归属 |
| --- | --- |
| 原 llm-3b/7b/32b 占位名 | 我方先确定正式模型身份，再与模拟器组确认目录；不是让对方猜模型结构 |
| 原 FP8、W7900D、TP>1 等组合 | 当前公开目录未支持。若保留课程要求，需要双方确认支持范围并扩展服务目录 |
| `service_ms` 与独立服务关键路径 | API 合法返回 null 和原因。我方保留缺失，使用实际时间戳展示最晚完成依赖链，不把含排队时间的链当成独立服务路径 |
| 9.1 能耗及新模型质量 | 尚无证据，不能用延迟或参数量估算成“已验证”结果 |
| 真实硬件测量及校准 | 沙盒后端仍是 stand-in 演示数据；两边对比检验操作流程，不能作为模拟器精度验证 |
| Agent 的解释与调用 | 回复由真实模型生成。演示保留原文，并通过引用记录追问、复核不严谨的解释。9.2 有 4 次、9.3 有 5 次工具调用因参数、路径、命令或单批额度被拒绝，随后恢复；这些是本方 Agent 使用问题，不是模拟器接口缺陷，也没有从记录中删除 |

因此，目前没有要转交的“接口 bug 修复单”。正式配置支持和真实测量是下一步工作，
不能把这些能力范围或证据缺口描述成已经解决。

## 我方接入方式

- 注册 `mlsys` 后端，将已校验的会话负载作为原始 JSON 请求提交。
- 保存精确的请求/响应字节、状态和哈希；模型、服务版本、实际配置、校准状态可追溯。
- 固定服务版本为 1.20.0，固定目录版本为 `mlsys-agent-catalog-v1`；版本不符时明确报错。
- 本机多个会话共享服务提交锁，适配当前单执行槽。429、超时或断线不自动重发 POST。
- 中止只取消本地等待；服务没有取消 API，远程执行可能继续。晚到结果不覆盖已结束记录。
- 模拟 OOM 或请求失败可进入沙盒确认，确认卡保留失败状态；学生可以用相同配置继续检查。断线等不可用结果明确停止准备，不再循环显示“模拟中”。
- 缺失观测保持未知，不填零，不把未知请求全部计成失败，不回退到 fixture。
- Codex 固定为 0.167.2；学习对话使用 SoC `qwen3.8:27b`，模型调用保持串行。
  Codex 会话配置保存在项目工作区，密钥只经环境变量传递。

测试：Python 全套 **298 passed / 6 skipped**；浏览器全套 **29 passed**，之后针对确认卡和配对选择的 **13 项回归**再次通过；报告状态与偏移说明修正后，指标、工作流、后端、历史、页面读取和翻译的 **82 项回归**通过。

代码入口：[HTTP 执行适配](../../src/mlab/backends/simulator_backend.py)、
[会话联调配置](../../src/mlab/backends/simulator_profiles.py)、
[应用回归测试](../../tests/test_simulator_backend.py)。

## 启动与录制

先在独立模拟器仓库安装其依赖并启动服务：

```bash
python -m mlsys_sim.agent_service --port 8877
```

在本仓库启动新的课程目录。脚本提示时输入已有的 SoC key，不写入源文件：

```bash
scripts/start-soc.sh workspaces/simulator-demo-2026-11-14/course \
  --simulator mlsys --simulator-profile protocol-v1.20 \
  --simulator-url http://127.0.0.1:8877 \
  --port 8878 --no-browser --no-opening
```

录制使用真实页面点击和输入，API 查询只用于核对。学生提问是演示脚本，模型回复不是预制文本。
每次重录应使用全新的课程目录和输出目录。先构建前端，再运行：

```bash
cd frontend
PLAYWRIGHT_BROWSERS_PATH=../.venv/playwright \
  ../.venv/web-toolchain/node_modules/.bin/node scripts/record-simulator-demo.mjs 9.1 9.2 9.3
PLAYWRIGHT_BROWSERS_PATH=../.venv/playwright \
  ../.venv/web-toolchain/node_modules/.bin/node scripts/record-simulator-review.mjs 9.1 9.2 9.3
cd ..
.venv/bin/python scripts/trim_demo_waits.py \
  workspaces/simulator-demo-2026-11-14/videos/task-9.1 \
  workspaces/simulator-demo-2026-11-14/videos/task-9.2 \
  workspaces/simulator-demo-2026-11-14/videos/task-9.3
.venv/web-toolchain/node_modules/.bin/node frontend/scripts/build-simulator-demo.mjs --zip
```

`MLAB_REVIEW_URL` 和 `MLAB_REVIEW_DIR` 可覆盖录制地址与输出路径。
剪辑只移除长模型等待的中段，前后各保留 7 秒；所有操作、确认和回复按 **1×** 播放。
每个任务保留未剪辑 WebM、原始分段、剪辑清单、章节、截图、未改写对话、公开工具活动和报告。工具活动列出实际调用及读取的记录名，不包含私有推理或完整工具输出。
公开包不包含密钥、课程数据库或私有模型运行日志。

应用的运行证据见 [app-v1.20](evidence/app-v1.20/verification.json)。
视频的时长、哈希和回归检查见[演示核验清单](evidence/app-v1.20/demo-verification.json)。
视频文件位于本次工作区 `workspaces/simulator-demo-2026-11-14/`，未将二进制录像提交到 Git。三个成片分别为 7:04、9:23、8:59。
打开其中 `index.html` 可查看带章节的播放器、截图和完整对话，`simulator-demo.zip` 是公开文件的完整打包。

也可以启动只开放公开演示文件的本地播放器服务。它支持视频分段请求，保证章节跳转；不会开放课程数据库或私有 Agent 文件：

```bash
.venv/bin/python scripts/serve_simulator_demo.py workspaces/simulator-demo-2026-11-14 --port 8884
```

## 证据导出

导出器逐项校验本方产物哈希和模拟器原始响应，复制公开运行文件，不复制课程数据库或私有 Agent 文件：

```bash
.venv/bin/python scripts/export_simulator_app_evidence.py \
  --workspace 9.1=workspaces/simulator-demo-2026-11-14/course/9.1-1 \
  --workspace 9.2=workspaces/simulator-demo-2026-11-14/course-final/9.2-1 \
  --workspace 9.3=workspaces/simulator-demo-2026-11-14/course-final/9.3-1 \
  --simulator-source ../mlsys-simulator \
  --out integrations/mlsys-simulator/evidence/app-v1.20
```

9.1 与后两项使用独立演示课程。后两项在修复沙盒准备循环后重新录制；旧尝试及其已用预算保留，没有修改或删除失败运行。末段复核展示完整追问回复及修正后的报告，早先片段与原始回答也保留。9.3 对“超过重采样噪声”的错误解释另有追问，最终说明应以复核为准。9.3 原录制请求的 4 次实测超过单批 3 次额度，拒绝后分批完成；后续录制脚本已改为主动遵守单批额度。Agent 说明同时补充了任务负载路径的根目录，减少错误查找，但不保证模型永远不会误用工具。
