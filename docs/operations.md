# 本地运行与故障验收

本地默认启动 PostgreSQL、API、独立 worker 和前端。API 能登录或返回健康检查，不代表队列有 worker 消费；排查时分别确认数据库、API、worker 和外部供应商。

## 安装、启动和停止

在项目根目录执行：

```bash
bash scripts/setup-local.sh
bash scripts/start-local.sh
```

setup 使用 frozen 锁文件安装依赖，已有 `.env` 会保留。首次启动应用 Alembic 迁移并初始化本项目管理员。端口为前端 5178、API 8010、PostgreSQL 55439。新项目不导入原项目账户和上传文件。

`start-local.sh` 启动 API、worker 和前端并监督这些子进程；其中一个退出时脚本会停止其余应用子进程。`Ctrl+C` 停止应用，数据库继续运行。需要停止数据库时，在应用停止后执行：

```bash
bash scripts/stop-local.sh
```

独立启动 worker：

```bash
bash scripts/start-worker.sh
```

也可在 `backend/` 使用虚拟环境执行 `python -m app.worker`。API 不会隐式启动这个循环。当前只允许一个 AI worker 进程，内部有界线程池；第二个 worker 会被 PostgreSQL session lock 拒绝。原锁连接失效后停止领取并退出，不自动换连接重新拿锁。PostgreSQL 重启后应重启 worker，确保新实例重新取得护栏。

队列层面验证了并发 `SKIP LOCKED`，并不代表整个应用可以水平扩容。当前 Chroma `PersistentClient` 面向本地开发/测试，共享本地持久目录的并发跨进程写入不是受支持的生产部署。API 检索/删除与 worker 索引仍接触该目录，单 worker 护栏不能消除这个限制。演示时避免一边索引一边删除/重建/并行维护；需要生产并发或多实例前，迁移到经验证的共享索引方案（例如 pgvector 或单独受支持服务），同时重新审计认证、备份与服务端安全，不应直接打开当前 Chroma HTTP 服务。当前没有吞吐承诺。

独立 worker 处理统一聊天、研究和文档索引。上传文件返回的是文档已接收，不等于 ready；上传/重试把索引意图入队，worker 负责解析与发布。queued 可保留，运行失联会令文档失败并等待显式重试，不自动重做不确定的 Embedding。遗留知识库独立问答不等同于统一聊天工具路径的完整预算/trace。

容器配置已拆开 API、worker 和一次性 prestart：先构建，再 `docker compose run --rm prestart`，之后启动 API/worker。此会话没有 Docker CLI，未执行镜像构建或容器联调；本机脚本是已验证入口。

## 配置和持久数据

| 配置 | 作用 |
|---|---|
| `DATABASE_URL` | PostgreSQL 业务、任务队列、事件与调用元信息 |
| `DOCUMENT_STORAGE_DIR` | 上传原文 |
| `CHROMA_PERSIST_DIRECTORY` | 本地持久向量索引 |
| `DEEPSEEK_*` | 主 Agent、摘要、子 Agent 和研究模型 |
| `ZHIPUAI_*` | 文档与查询 Embedding |
| `TAVILY_API_KEY` | 公开网页研究 |
| `REDIS_URL` | 可选公开搜索缓存；不是本版任务队列 |
| `WORKER_QUEUE_LIMIT` / `WORKER_OWNER_QUEUE_LIMIT` | 全局/单用户活动意图接纳上限 |
| `WORKER_LEASE_SECONDS` / `WORKER_HEARTBEAT_SECONDS` | lease 时限和续租间隔 |
| `RESEARCH_MAX_WORKERS` | 每个 worker 的执行线程上限 |
| `EXECUTION_MAX_*` | 一次执行的模型、搜索、Embedding、工具次数及输出 Token 预留 |

当前默认值以 `backend/app/core/config.py` 为准，配置由 Pydantic 校验。心跳间隔必须小于 lease 时长的一半。不要把极短 lease 当作更快恢复：正常调度停顿或数据库暂时不可用也可能触发失联处理。

备份应同时保存 PostgreSQL、上传原文和 Chroma，并记录代码/迁移/索引版本。只备份其中一个不能恢复完整资料。执行数据库结构变更前停止相关写入，保留可回滚备份；数据恢复后先核查索引版本及任务终态，再恢复 worker。

## 先确认任务在哪一步

1. 聊天/研究创建返回 `202`：查看业务轮次/研究任务与 `ExecutionJob`，确认状态为 queued 或 running。文档上传返回文档记录，继续观察 uploaded→processing→ready/failed。
2. queued 一直不变：检查 worker 是否运行、数据库地址是否相同、线程是否已占满。
3. running 没有 token：查看执行调用记录，区分模型未发出、正在阻塞、检索失败、预算耗尽和 usage 尚未返回。
4. 页面断开：重读消息和活动轮次，使用 SSE `after` 继续读取；不要为刷新进度重发创建请求。
5. worker 退出：等待 lease 到期与后续维护，将运行中失联任务终止为 interrupted；不手工把不确定收费任务改回 queued。

执行检查入口：

```text
GET /api/v1/integrations/turns/{turn_id}/execution
GET /api/v1/integrations/research/{run_id}/execution
```

使用已有 JWT，接口只返回当前用户的执行。检查 `status`、`attempt_count`、`calls`、`usage_complete`、`calls_truncated`，以及 input/output Token 是否实际存在。`estimated_cost` 未实现价格表时为 null，不表示免费。HTTP 响应的 `X-Request-Id` 可对照结构化接口日志，日志默认不记录请求正文和 Token。

管理员排查整体运行状态时可读取 `GET /api/v1/utils/metrics`。该接口返回 API 进程请求计数/耗时摘要，以及数据库中 queued、running、interrupted 和 provider call 状态数量。它不输出 Prompt、来源正文或账号敏感字段；当前是本地低基数指标，不等价于已接入 Prometheus、OpenTelemetry 或 Langfuse。

## 故障验收矩阵

五分钟内演示现有离线工程场景，在项目根执行：

```bash
bash scripts/verify-engineering.sh
```

需要本机 PostgreSQL 已启动、依赖已安装。脚本只给专用 `grounded_research_test` 应用迁移并运行 fixture，不启动生产 worker，不清理业务数据库。它复用 runtime、durable_integration、runtime_integration 专项，使用真实 PG、应用 API/JWT 和独立 Python worker 子进程；供应商由确定性替身代替，Python 外网连接被阻断。

输出 [engineering-scenarios.json](../evals/results/engineering-scenarios.json)，保留每个测试和七个场景的实际通过/失败/缺失状态及耗时，不保存 stdout、断言正文或凭据，不生成伪造的生产成功率。任一专项失败或必要用例缺失，脚本返回非零。崩溃注入复用真实子进程 `os._exit(17)`，同时验证持久调用的 unknown 展示；它模拟进程突然退出，没有实际发送 SIGKILL，也不调用真实收费供应商。

请与其他同库队列专项串行运行，避免两个测试执行器争抢彼此的 fixture 任务。最新报告包含运行时间；历史 JSON 不能替代重新验收。

先使用独立测试数据库和假供应商运行自动化案例。手工中断只操作自己记录的测试 worker PID，不操作正在执行真实收费任务的进程。

| 场景 | 期望结果 | 观察证据 |
|---|---|---|
| 没有 worker 时创建请求 | 已入队，未调用供应商 | 202、queued、零调用 |
| API 重启，任务仍 queued | 意图保留，随后 worker 可领取 | 同一个 job/target ID |
| 两个 worker 同时领取 | 各领取不同排队任务，不等待被锁首项 | claim 的 ID 与 lease token |
| 同键同参数重发 | 返回原意图，不新增用户消息或调用 | 业务 ID、消息数、job 数 |
| 同键不同参数 | 409 | 旧意图保持原值 |
| 取消 queued | 不再被领取，可开始后续轮次 | cancelled 与无供应商调用 |
| 取消 running，供应商迟到返回 | 不插入迟到助手消息、不改成功 | 取消终态、消息/事件 |
| worker 被中断并过 lease | interrupted/业务失败，不自动重跑 | lease、attempt_count、unknown/未完成调用 |
| 旧 lease 尝试写结果 | 拒绝事件与终态写入 | LeaseLost / 原结果未覆盖 |
| 预算耗尽 | 下一次供应商调用前拒绝 | 预留计数与派发次数 |
| 流中途断开 | 保留未知 usage/结局，不冒称完整结果 | 调用状态和 usage_complete |
| 文档删除或 ready 状态失效 | 不再检索，旧笔记/摘要不向模型复用 | 权限/来源检查与模型上下文 |
| 文档上传后 API 重启 | 排队索引仍可被 worker 领取 | 文档与 document job 同事务存在 |
| 文档 worker 失 lease 后尝试发布 | 不得发布 ready 或替换有效分块 | 文档状态、索引版本和 lease |
| 向量失败且词法有结果 | 明确词法降级提示 | retrieval.warnings |
| 向量失败且词法无匹配 | 报告检索不可用 | 错误而非无答案结论 |

queued 的恢复不等于 LangGraph 节点续跑。lease 栅栏防止迟到写库，不能证明供应商未执行。用户决定重试不确定请求时，应理解可能重新产生费用。

## 旧文档重索引

迁移不自动重建向量。命令从 `backend/` 运行，必须明确指定文档 ID，默认 dry-run：

```bash
uv run --frozen --package app python -m app.services.retrieval_index --document-id <UUID>
```

只重建词法投影，不访问 Embedding：

```bash
uv run --frozen --package app python -m app.services.retrieval_index --document-id <UUID> --apply
```

同时重建向量需要两项显式开关，可能计费：

```bash
uv run --frozen --package app python -m app.services.retrieval_index --document-id <UUID> --apply --dense --allow-paid-provider
```

CLI 是本机运维工具，运行者拥有服务器资料访问权限；它不是可让模型或远端匿名调用的接口。重建后检查 ready 状态、发布版本、降级警告和样例查询。

该 CLI 直接运行维护过程，不会先领取 document job。仅对没有 queued/running 索引任务的文档操作；先让相关活动 worker 完成、停止新的索引接纳，再重建，避免与队列执行同时修改同一文档。

固定检索评估从项目根目录运行：

```bash
uv run --frozen --package app python evals/retrieval/run.py
```

默认生成 `evals/results/retrieval-fixture.json` 与 `.md`，不访问供应商。先看逐问排名与开发/留出拆分，再看汇总。向量 fixture 是 toy concept cosine，不用这份报告宣称真实模型质量。真实供应商模式必须显式选择 `--mode provider --allow-paid-provider`，本轮未执行。

## MCP、容器与生产部署

MCP 在宿主机通过 loopback:8010 读取固定接口。配置一个既有用户 JWT，使用 stdio 客户端启动，详见 [MCP 与 Skills](mcp-and-skills.md)。不要用数据库管理员身份替代应用资源授权。

容器部署需要分别启动 API 和 `python -m app.worker`，二者共用同一 PostgreSQL、文档/Chroma 存储和一致配置；镜像需包含根 `skills/`。本地脚本是本次主要验收入口，未完成 Docker 镜像构建和生产容器联调，不能把配置存在当作部署已验收。

生产使用还需完成 HTTPS、凭据管理、备份恢复演练、worker 进程监督、外部供应商限流和真实容量测试。持久调用记录不包含私有 prompt，但业务消息、来源、上传文件和索引本身仍是敏感数据，应在部署层控制访问与备份。
