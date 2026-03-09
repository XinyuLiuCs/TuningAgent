# Terminal-Bench 评估上手指南

本文面向第一次接手本项目评估模块的同学，介绍当前 TuningAgent 的 Terminal-Bench 评估体系、环境准备、常用运行命令、参数含义、数据集情况，以及如何查看评估结果。

## 1. 当前评估体系概览

本项目已经接入 Terminal-Bench，当前评估链路如下：

1. 使用 `tuningagent benchmark ...` 作为统一入口。
2. 该入口内部调用 Terminal-Bench 官方执行器 `tb run`。
3. Terminal-Bench 为每个任务拉起独立 Docker Compose 环境。
4. 在任务容器内启动我们的自定义 agent 适配器：
   `tuningagent.benchmark.terminal_bench_agent:TuningAgentTerminalBenchAgent`
5. 适配器复用本项目现有的 `Agent`、`ModelPool`、`Config`、`AgentLogger`，并向任务容器注入远程版 `bash/file_read/file_write/file_edit` 工具。
6. Terminal-Bench 执行任务测试脚本并生成 `results.json`。
7. 本项目再把原始结果标准化为 `tuningagent_summary.json`，便于统一消费。

当前接入的是 Terminal-Bench 的真实执行环境，不是 mock，也不是离线打分。

## 2. Benchmark 目录与核心文件

当前本地 benchmark 仓库目录为：

- `bench/terminal-bench`

当前项目里的评估相关代码主要在：

- `tuningagent/benchmark/terminal_bench.py`
- `tuningagent/benchmark/terminal_bench_agent.py`
- `tuningagent/cli.py`
- `tuningagent/schema/schema.py`

补充说明文档：

- `bench/TERMINAL_BENCH_MVP.md`

## 3. 一个 Terminal-Bench 任务长什么样

本地 `original-tasks/` 中每个任务通常包含：

```text
<task-id>/
├── task.yaml
├── docker-compose.yaml
├── run-tests.sh
├── solution.sh 或 solution.yaml
└── tests/
```

关键组成：

- `instruction`
  agent 看到的英文任务指令。
- `run-tests.sh`
  Terminal-Bench 在 agent 执行完成后实际运行的测试入口。
- `tests/`
  任务完成判定逻辑，当前本地任务以 `pytest` 为主。
- `solution.sh`
  官方参考解法，通常可用于 oracle 基线验证。
- `docker-compose.yaml`
  任务执行环境定义。

## 4. 当前数据集情况

当前本地已探索的数据集是：

- `original-tasks`

本地快照统计结果：

- 总任务数：241
- 难度分布：
  - `easy`: 64
  - `medium`: 120
  - `hard`: 57
- 当前采样结果里 parser 主要为：
  - `pytest`: 241

本地快照中较多的任务类别包括：

- `software-engineering`
- `system-administration`
- `security`
- `data-science`
- `scientific-computing`
- `debugging`
- `file-operations`

此外，Terminal-Bench 注册表里还存在版本化数据集，例如：

- `terminal-bench-core==0.1.1`

建议：

- 内部稳定评估优先使用固定版本数据集或固定本地快照。
- 不建议把 `head` 这类浮动版本作为长期基线。

## 4.1 官方榜单与对应任务数量

截至目前，Terminal-Bench 官网公开的正式榜单主要有两个：

- `terminal-bench@1.0`
- `terminal-bench@2.0`

对应任务数量如下：

- `terminal-bench@1.0`
  - 对应数据集：`terminal-bench-core==0.1.1`
  - 任务数：80
- `terminal-bench@2.0`
  - 对应数据集：`terminal-bench==2.0`
  - 任务数：89

另外，官网首页还列出了：

- `Terminal-Bench 3.0`
- `Terminal-Bench Science`

但它们当前处于 `in progress`，不是稳定的公开正式榜单，不建议作为固定内部基线。

### 4.1.1 官方榜单只跑对应任务集的命令

跑官方 `terminal-bench@1.0` 榜单对应任务集：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --dataset terminal-bench-core==0.1.1 \
  --no-profile \
  --run-id tb-official-1p0-20260308
```

跑官方 `terminal-bench@2.0` 榜单对应任务集：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --dataset terminal-bench==2.0 \
  --no-profile \
  --run-id tb-official-2p0-20260308
```

说明：

- 这里必须使用 `--no-profile`，否则会叠加默认的 `curated-smoke` 任务。
- 这里使用的是 `--dataset`，不是 `--dataset-path`。
- 这类命令的语义是“只运行该官方榜单对应的数据集任务集合”。
- 如果本地或当前 Terminal-Bench 版本尚未缓存该 registry 数据集，首次执行可能需要额外下载或同步。
- 当前本地 clone 的 `registry.json` 已明确包含 `terminal-bench-core==0.1.1`，但不一定已经内置 `terminal-bench==2.0`。如果 `2.0` 命令报数据集不存在，优先检查 Terminal-Bench 版本是否过旧。

### 4.1.2 如果要跑官方榜单的 oracle 基线

官方 `1.0` oracle：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --dataset terminal-bench-core==0.1.1 \
  --no-profile \
  --agent oracle \
  --run-id tb-official-1p0-oracle-20260308
```

官方 `2.0` oracle：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --dataset terminal-bench==2.0 \
  --no-profile \
  --agent oracle \
  --run-id tb-official-2p0-oracle-20260308
```

## 5. 当前内置评测子集

项目里已经内置两个 profile。
这两个 profile 是本项目本地维护的任务集合，不是 Terminal-Bench 官方榜单名称，也不是官方数据集名称。

查看方式：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark --list-profiles
```

当前结果为：

- `curated-smoke`
  - `analyze-access-logs`
  - `processing-pipeline`
- `curated-core`
  - `analyze-access-logs`
  - `processing-pipeline`
  - `assign-seats`
  - `ancient-puzzle`
  - `simple-sheets-put`

含义：

- `curated-smoke` 适合先验证链路是否跑通。
- `curated-core` 适合作为当前较完整的小规模评测集合。

## 6. 运行前准备

### 6.1 项目依赖

在项目根目录执行：

```bash
uv sync
```

如果你希望可编辑安装：

```bash
pip install -e .
```

### 6.2 配置模型

先准备项目配置文件：

```bash
cp tuningagent/config/config-example.yaml tuningagent/config/config.yaml
```

然后在 `tuningagent/config/config.yaml` 中填入实际模型配置与 API 信息。

### 6.3 Terminal-Bench 依赖

如果 `bench/terminal-bench` 已存在，进入该目录安装依赖：

```bash
cd bench/terminal-bench
uv sync
cd ../..
```

### 6.4 Docker

Terminal-Bench 依赖 Docker。运行前请确认：

- Docker 已安装
- Docker daemon 正常运行
- 当前用户有权限访问 Docker socket

### 6.5 代理环境说明

当前环境下有一个实际踩过的坑：

- 如果环境变量里带有 `ALL_PROXY` / `all_proxy` 指向 SOCKS 代理，而 Python 依赖中又缺少 `socksio`，模型请求可能在初始化阶段失败。

当前实现默认会移除：

- `ALL_PROXY`
- `all_proxy`

同时保留：

- `HTTP_PROXY`
- `HTTPS_PROXY`
- `NO_PROXY`

如果你明确知道当前环境必须保留全部代理变量，可以加：

```bash
--keep-proxy-env
```

## 7. 统一入口命令

统一入口是：

```bash
python -m tuningagent.cli benchmark ...
```

如果当前 Python 环境还没装全依赖，也可以使用 benchmark venv 来运行：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark ...
```

在当前仓库中，这个入口默认会运行我们自己的 agent，不需要手动传 `--agent tuningagent`。

## 8. 参数说明

下面是当前 `benchmark` 子命令的参数说明。

### 8.1 基础路径参数

- `--bench-dir`
  Terminal-Bench 仓库目录。
  默认值：`bench/terminal-bench`

- `--tb-executable`
  `tb` 可执行文件路径，可以是相对 `--bench-dir` 的路径，也可以是绝对路径。
  默认值：`.venv/bin/tb`

### 8.2 数据集参数

- `--dataset-path`
  指向本地数据集目录，路径相对 `--bench-dir`。
  默认值：`original-tasks`

- `--dataset`
  使用注册表中的数据集名称或 `name==version` 形式，例如：
  `terminal-bench-core==0.1.1`

注意：

- `--dataset-path` 和 `--dataset` 二选一。

### 8.3 任务选择参数

- `--profile`
  使用内置任务集合。
  当前可选：`curated-smoke`、`curated-core`

- `--no-profile`
  禁用 profile，只运行你显式传入的 `--task-id`

- `--list-profiles`
  打印当前内置 profile 内容后退出

- `--task-id`
  额外指定任务 ID，可重复传入多次

行为规则：

- 默认会带上 `--profile curated-smoke`
- 如果你不想跑默认 profile，必须显式加 `--no-profile`

### 8.4 执行输出参数

- `--output-path`
  benchmark 输出目录，相对 `--bench-dir`
  默认值：`runs/tuningagent`

- `--run-id`
  显式指定本次运行 ID

建议：

- 日常排查时总是手动指定 `--run-id`
- 这样结果目录更容易定位和复现

### 8.5 agent 相关参数

- `--agent`
  Terminal-Bench agent 名称
  默认值：`tuningagent`

- `--agent-import-path`
  自定义 Terminal-Bench agent import path

- `--agent-kwarg`
  透传给 Terminal-Bench agent 的参数，格式为 `key=value`
  可重复多次

- `--model`
  透传给 Terminal-Bench 的模型参数

说明：

- 对当前集成来说，通常不需要手动传 `--agent`。
- 如果需要跑 oracle 基线，可以显式传：
  `--agent oracle`

### 8.6 并发与重试参数

- `--n-concurrent`
  同时运行多少个 trial
  默认值：`1`

- `--n-attempts`
  每个任务跑多少次
  默认值：`1`

建议：

- 新人首次上手一律用 `--n-concurrent 1`
- 先确认链路和模型稳定，再考虑提高并发

### 8.7 Docker 与环境参数

- `--no-rebuild`
  跳过 Docker rebuild

- `--no-cleanup`
  保留 Docker 资源，便于排查

- `--keep-proxy-env`
  保留当前代理环境变量，不移除 `ALL_PROXY/all_proxy`

### 8.8 调试参数

- `--dry-run`
  不实际执行 benchmark，只打印规范化后的运行元数据

适用场景：

- 检查任务选择是否正确
- 检查 run_id、dataset、output_path 是否符合预期

## 9. 常用命令清单

### 9.1 查看 profile

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark --list-profiles
```

### 9.2 Dry run，不真正执行

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --profile curated-smoke \
  --run-id dryrun-20260308 \
  --dry-run
```

### 9.3 跑 1 个任务

只跑 `analyze-access-logs`：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --no-profile \
  --task-id analyze-access-logs \
  --run-id tb-single-analyze-20260308
```

只跑 `processing-pipeline`：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --no-profile \
  --task-id processing-pipeline \
  --run-id tb-single-pipeline-20260308
```

### 9.4 跑 2 个任务

直接使用 smoke 子集：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --profile curated-smoke \
  --run-id tb-smoke-20260308
```

或者显式指定两个任务：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --no-profile \
  --task-id analyze-access-logs \
  --task-id processing-pipeline \
  --run-id tb-two-tasks-20260308
```

### 9.5 跑 5 个任务

使用当前内置 `curated-core`：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --profile curated-core \
  --run-id tb-curated-core-20260308
```

### 9.6 跑自定义任务集合

例如跑 3 个指定任务：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --no-profile \
  --task-id analyze-access-logs \
  --task-id processing-pipeline \
  --task-id assign-seats \
  --run-id tb-custom-3-20260308
```

### 9.7 跑 oracle 基线

用于验证任务本身、harness 或环境，而不是评估我们自己的 agent：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --profile curated-smoke \
  --agent oracle \
  --run-id tb-oracle-smoke-20260308
```

### 9.8 保留容器资源便于排查

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --no-profile \
  --task-id processing-pipeline \
  --run-id tb-debug-pipeline-20260308 \
  --no-cleanup
```

## 10. 结果输出在哪里

以如下命令为例：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --profile curated-smoke \
  --run-id tb-smoke-20260308
```

结果目录通常位于：

```text
bench/terminal-bench/runs/tuningagent/tb-smoke-20260308/
```

其中最重要的文件是：

- `results.json`
  Terminal-Bench 原始结果

- `tuningagent_summary.json`
  本项目标准化后的结果摘要

- `run.log`
  Harness 执行日志

- `<task-id>/<trial-name>/agent-logs/tuningagent.jsonl`
  我们自己的 agent 结构化日志

- `<task-id>/<trial-name>/sessions/agent.log`
  agent 终端会话日志

- `<task-id>/<trial-name>/sessions/tests.log`
  测试执行日志

- `<task-id>/<trial-name>/panes/post-agent.txt`
  agent 执行完成后终端面板内容

- `<task-id>/<trial-name>/panes/post-test.txt`
  测试完成后终端面板内容

## 11. 如何判断评估结果

最简单的方式是看：

- `tuningagent_summary.json`
- `results.json`

### 11.1 看整体结果

重点字段：

- `resolved_count`
  成功任务数

- `unresolved_count`
  失败任务数

- `accuracy`
  成功率

### 11.2 看单任务结果

在 `tasks` 数组或 `results` 数组中，重点看：

- `task_id`
  任务 ID

- `resolved`
  是否通过

- `failure_mode`
  失败模式

- `parser_results`
  具体测试项通过/失败情况

### 11.3 快速查看结果文件

查看标准化结果：

```bash
cat bench/terminal-bench/runs/tuningagent/<run-id>/tuningagent_summary.json
```

查看原始结果：

```bash
cat bench/terminal-bench/runs/tuningagent/<run-id>/results.json
```

## 12. 如何排查失败

如果一个任务失败，建议按这个顺序看：

1. 看 `tuningagent_summary.json`
   先确认哪个任务失败、哪些 parser case 没通过。

2. 看 `results.json`
   对照 Terminal-Bench 原始字段。

3. 看 `run.log`
   确认是否是 harness、Docker、环境初始化失败。

4. 看 `agent-logs/tuningagent.jsonl`
   确认 agent 的思考轨迹、工具调用、模型请求是否异常。

5. 看 `sessions/tests.log`
   直接看测试报错内容。

6. 看 `sessions/agent.log` 和 `panes/post-agent.txt`
   还原 agent 最终在容器里把东西改成了什么。

## 13. 当前已知问题与注意事项

### 13.1 Docker 权限问题

如果看到类似错误：

```text
PermissionError: [Errno 1] Operation not permitted
Error while fetching server API version
```

通常不是 benchmark 本身坏了，而是当前用户没有权限访问 Docker。

### 13.2 代理问题

如果模型请求初始化失败或无响应，优先检查：

- `ALL_PROXY`
- `all_proxy`
- `HTTP_PROXY`
- `HTTPS_PROXY`

当前默认策略是移除 `ALL_PROXY/all_proxy`，保留 HTTP/HTTPS 代理。

### 13.3 不要默认开并发

虽然 CLI 支持 `--n-concurrent`，但首次评估不要一上来就并发。

建议顺序：

1. 单任务
2. `curated-smoke`
3. `curated-core`
4. 再考虑更大任务集或并发

## 14. 推荐的新同学上手流程

建议严格按下面顺序执行。

1. 准备项目配置和模型密钥。
2. 确认 Docker 正常。
3. 先跑 `--list-profiles`，确认 benchmark 入口可用。
4. 跑一次 `--dry-run`，确认参数和输出目录。
5. 跑 1 个最简单任务：
   `analyze-access-logs`
6. 再跑 `curated-smoke`
7. 结果稳定后，再跑 `curated-core`
8. 如果失败，优先查 `tuningagent_summary.json`、`run.log`、`tests.log`

## 15. 一条最推荐的起步命令

如果你是第一次接手，先执行这条：

```bash
PYTHONPATH=$PWD bench/terminal-bench/.venv/bin/python -m tuningagent.cli benchmark \
  --bench-dir bench/terminal-bench \
  --profile curated-smoke \
  --run-id tb-smoke-first-run
```

跑完后重点看：

```bash
cat bench/terminal-bench/runs/tuningagent/tb-smoke-first-run/tuningagent_summary.json
```

如果这条命令能稳定跑完，你就已经完成了当前评估体系的最小可用验证。
