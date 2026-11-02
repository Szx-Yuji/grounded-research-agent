# 验证记录

## 2026-10-29 工程化版本

环境为本机 macOS、Python 3.14.6、Bun 1.4.2 与 PostgreSQL 16；写库测试使用独立 `grounded_research_test` 数据库。2026-11-02 完整入口实际通过：**229 项后端测试、14 项浏览器回归、TypeScript/Vite 生产构建**。另执行工程场景命令，36 个用例和7个场景均通过；它重跑部分专项，不应额外累加到229项中。

| 专项 | 实际结果 | 覆盖和替身范围 |
|---|---|---|
| MCP / Skills | 58 项通过 | 官方 SDK 内存协议与真实 stdio 子进程握手；固定 HTTP MockTransport，无数据库/外网/模型 |
| runtime gateway / 审计策略 | 13 项通过 | 原子共享预算、子调用层级、流式 usage/TTFT、Embedding batch、未知结局不得同轮重放、派发前持久化失败；原生审计未知报告失败关闭 |
| runtime PostgreSQL/API 集成 | 3 项通过 | 真实 lease、网关、DB 调用记录、跨用户归属和 unknown 展示 |
| 主 Agent 引擎 | 16 项通过 | 模型/工具替身，含技能加载接入 |
| PostgreSQL durable 执行 | 20 项通过 | 真实 PG、JWT、幂等并发、SKIP LOCKED、lease/心跳、取消与写栅栏；独立子进程消费及崩溃后不重领；事务内自然过期、worker护栏失锁退出 |
| 持久会话集成 | 13 项通过 | 独立 worker 夹具、多轮上下文、来源失效、取消与归属；供应商替身 |
| 检索单元/SQLite | 12 项通过 | 分词、BM25、RRF、授权投影和评估指标 |
| 检索 PostgreSQL 集成 | 4 项通过 | JSONB、替换回滚、级联删除、真实 API 上传→document 队列→ready 原子发布、失效 lease 拒绝发布；向量供应商替身 |
| 研究/检索 API 联动 | 19 项通过 | 异步文档索引→真实 Chroma/PG 分块→研究替身→持久结果和 SSE |
| 研究引擎 | 11 项通过 | 原图路由、有限研究补搜、取消与证据边界；供应商替身 |
| 继承账户/API/CRUD | 58 项通过 | 专用测试库；邮件发送替身 |
| 离线工程场景演示 | 36 用例、7 场景通过 | 独立命令重跑 runtime13/durable20/runtime集成3，真实生成逐用例 JSON，不调用供应商 |
| 浏览器回归 | 14 项通过 | 原研究5项、聊天5项、新工程4项；页面 API 使用替身 |
| Agent Harness fixture | 8 个用例通过 | 工具选择、引用契约、拒答、私有查询保护、Skills按需加载、追问来源复用；确定性模型替身 |
| 兼容 RAG / 指标接口 | 2 项通过 | 旧知识库适配器复用统一 RAG 子 Agent 与预算；管理员指标接口仅返回低基数状态 |
| 固定检索消融 | 成功生成报告 | 20 个合成 chunks、26 问，开发15/留出11；BM25、toy concept cosine、RRF；未调用真实 Embedding |
| 新模块静态检查 | 通过 | Ruff、shell语法；MCP/Skills/runtime/新集成API的ty检查；不等于对全部历史代码做严格类型验证 |

MCP 的 58 项包含本地配置范围、凭据脱敏、不读取 `.env`、固定接口、UUID/参数限制、禁止跳转、网络错误脱敏、整请求 deadline、响应大小及技能路径/符号链接/YAML 校验。这组隔离测试中的 stdio 子进程只发现工具，不调用真实后台或供应商；另有下述实际后台联调。

Agent Harness 报告在 [agent-fixture.json](../evals/results/agent-fixture.json)。其中 `tool_selection_accuracy` 等指标描述的是本项目编排契约在固定替身下的通过率，不是 DeepSeek 或其他真实模型的准确率；真实 provider 模式需另行显式授权、脱敏和计费记录。

补充真实本机联调：登录→上传合成TXT→返回uploaded→独立worker发布ready→带权限混合检索返回24个月保修片段，索引代和dense降级警告均存在；缺主模型Key时503且不保存用户消息。临时会话、知识库、文件和任务记录随后清理。新增执行详情已以模拟记录完成桌面/手机截图检查，手机无横向溢出，截图见[桌面](screenshots/engineering-desktop.png)与[手机](screenshots/engineering-mobile.png)；这些截图不能当作真实模型输出。官方MCP SDK经真实stdio子进程发现6个工具，并用配置JWT调用实际后端列知识库成功；这项不是MockTransport替身，也没有调用付费服务。

备份在`../grounded-research-agent-backups/20261029-210535`，包含源码、Git、私有配置、PostgreSQL冷备份与Chroma/文件。压缩包SHA256及2449个文件的SHA256已在临时目录实际解压逐一校验，PostgreSQL空目录也已保留。随后在隔离端口55441成功启动恢复出的PostgreSQL16，核实旧迁移版本c14e8f720ad1，再正常停止并清理；没有覆盖当前业务库。备份目录有私有凭据，不应上传公共仓库。

本轮依赖审计：Seroval固定到1.6.11；dnspython升级2.9.0、langgraph-sdk升级0.4.10。Python原始报告剩Chroma的4个已知公告/5条记录；Bun原始报告剩deepmerge的1条low、无已发布补丁。已有嵌入式Chroma例外和新增本地邮件模板转换选项范围的deepmerge精确例外均有到期检查；审计策略通过不等于原始报告零漏洞。依据与复查条件见[SECURITY.md](../SECURITY.md)。

耐久执行的子进程案例实际完成：一个单独 Python 进程领取 queued，运行假引擎后将结果写回 PostgreSQL；另一个进程领取后通过 `os._exit` 退出，确认任务仍为 running，lease 过期后变 interrupted，replacement worker 不能重领该不确定执行。它证明队列和进程故障处理，不证明供应商侧恰好一次执行。

### 可重复命令

一条命令生成最新工程场景结果：

```bash
bash scripts/verify-engineering.sh
```

结果 [engineering-scenarios.json](../evals/results/engineering-scenarios.json) 包含七个场景：排队意图/API 重开、竞争 worker 领取、幂等、子 Agent 共享预算、进程突然退出后 unknown 不重放、独立 worker 进程、资源权限。测试报告保留所有用例结果，只输出状态、标识和耗时；不保存 stdout/凭据/断言正文。`os._exit(17)` 是实际子进程退出，不是一次真实 SIGKILL 注入；不声称验证了收费供应商的崩溃行为。

根目录完整入口：

```bash
bash scripts/check.sh
```

不需数据库和供应商的专项，在 `backend/` 运行：

```bash
uv run --frozen --package app pytest --confcutdir=tests/mcp_skills tests/mcp_skills -q
uv run --frozen --package app pytest --confcutdir=tests/runtime tests/runtime -q
uv run --frozen --package app pytest --confcutdir=tests/retrieval tests/retrieval -q
```

真实 PostgreSQL 专项应先准备独立测试数据库及最新迁移，再执行：

```bash
uv run --frozen --package app pytest --confcutdir=tests/durable_integration tests/durable_integration -q
uv run --frozen --package app pytest --confcutdir=tests/assistant_integration tests/assistant_integration -q
```

这些独立目录使用自己的 fixture。不要绕过项目检查脚本，将继承的清理 fixture 指向真实业务数据库。

### 检索消融

根目录：

```bash
uv run --frozen --package app python evals/retrieval/run.py
```

报告在 `evals/results/retrieval-fixture.json` 和 `.md`，包含数据集 SHA256、配置、逐问排名与汇总。当前固定快照 k=4：

| 留出集方法 | Recall@4 | MRR@4 | nDCG@4 |
|---|---:|---:|---:|
| BM25 | 0.833 | 0.889 | 0.870 |
| Dense fixture | 1.000 | 0.944 | 0.959 |
| Hybrid RRF | 1.000 | 0.944 | 0.955 |

融合并非所有指标都高于单一路径。Dense 是显式 toy concept cosine，语料是人工合成/标注的小样本，无真实质量泛化结论；无答案空结果率也不等于生成模型拒答准确率。排序时延仅本地小语料计算时间，不是端到端模型时延。

真实供应商模式需要 `--mode provider --allow-paid-provider`，本轮未执行。不能把 fixture 分数写成真实 Embedding 的召回率或项目线上准确率。

### 尚未完成的验收

- 没有调用付费 DeepSeek、智谱或 Tavily，尚无真实供应商工具调用、流式 usage、成本或答案质量验收。
- 没有 Docker 镜像构建/生产容器联调、GitHub 托管 CI 实际运行或容量压测结果。
- 没有全链路 LangGraph 持久 checkpoint 和每节点续跑；只有 queued 意图可恢复，running 失联不自动重放。
- 取消与预算限制后续调用，无法证明已发出的供应商请求立即停止或未收费。
- 本地 trace 保存元信息，尚未集成 Langfuse/远端 OTel 后端；没有价格表，因此未知金额不是0。
- 文档索引已接入独立 worker，queued 意图可恢复，运行失联仍需显式重试；遗留独立知识库问答不继承统一聊天队列/trace 的全部保证。

教学演示可以用这些确定性证据解释工程边界。真实领域质量和改进结果必须在用户实际数据及独立留出集上另行验证。

## 2026-10-26 历史基线

以下保留上一版的实际记录，便于追溯。其进程内 worker 和重启语义已被新架构替换；旧结果不代表当前版本已经重复完成相同验证。

日期：2026-10-26。环境：本机macOS arm64、Python3.14.6、Bun1.4.2、专用PostgreSQL16（127.0.0.1:55439）。项目：grounded-research-agent。

## 实际执行

| 验证 | 结果 | 说明 |
|---|---|---|
| 研究引擎mock回归 | 11项通过 | 原图、KB-only、web-only、混合、私有证据边界、有限重试与取消 |
| PostgreSQL/API集成 | 19项通过 | 真实JWT登录、权限、历史、事件cursor、取消竞态、重启、临时写库失败协调、删除和检索过滤 |
| 继承账户/API/CRUD | 58项通过 | 专用测试数据库；邮件投递替身；不清业务数据 |
| 主Agent引擎mock回归 | 15项通过 | 工具循环、RAG与研究委派、历史指代、子Agent笔记、摘要与权限边界 |
| 持久对话API集成 | 13项通过 | 多轮历史、子Agent资料延续、摘要、并发、取消、来源失效和隔离 |
| 浏览器mock回归 | 10项通过 | 5项研究页与5项聊天：连续追问、刷新、事件续传、取消与旧流隔离 |
| 真实本地UI | 通过 | 登录、空知识库创建/选择/删除、研究空态、缺Key返回503及提示；测试记录已清理 |
| TypeScript / Vite | 通过 | 生产构建和生成客户端；保留研究页面按路由拆包 |
| Ruff / shell语法 / frozen依赖 | 通过 | 新后端及测试、启动脚本语法、uv与Bun锁文件 |

新增联动测试真实执行：TXT上传→文件存储→解析/分块→确定性本地Embedding→真Chroma→权限检索→原研究图→报告与来源入库→SSE读取。DeepSeek/Tavily用替身；默认模型下载和外部HTTP被拦截，断言没有网络请求。因此验证的是组件联动与控制流程，尚未验证真实供应商兼容与答案质量。

后端共116项通过。持续对话联动测试将API、数据库、worker、主Agent及RAG子Agent串联：第一轮最终回答只展开方案A，第二轮模型输入仍包含子Agent保存的方案B结论；删除依赖文档后，该结论不再进入模型上下文。删除已选知识库会清理会话选择，后续普通聊天仍可提交。

## 依赖审计

- Bun审计返回`{}`，修补zod与fast-uri。
- Python审计升级filelock、pypdf与OpenTelemetry；剩余5条记录属于4个已知Chroma服务端公告，其中1个重复记录。
- 当前使用嵌入式PersistentClient，没有启动Chroma HTTP服务或向用户开放模型仓库配置。精确例外、适用边界和复查期限见[SECURITY.md](../SECURITY.md)。按这些既有例外过滤后无新增已知漏洞；未把原始Python报告描述为零漏洞。

## 未验证与当前边界

- 没有调用付费DeepSeek、智谱或Tavily，没有生成可宣称真实质量的研究报告。
- 没有运行Docker构建、GitHub托管CI或并发容量压测。
- 进程内研究执行，持久的是运行记录和事件；没有LangGraph持久checkpoint与跨重启续跑。
- 取消是合作式取消，阻塞中的请求仍需返回/超时；已有请求设置边界，不承诺立即停止供应商计费。
- 源资料引用帮助追溯，未实现逐主张事实核验。

执行入口：`bash scripts/check.sh`，`cd frontend && bun run test:research`（包含研究与聊天共10项）。

统一聊天入口为 `/chat`。真实UI补测创建/重命名/刷新/删除会话、KB与web配置、缺DeepSeek Key时503且不保存用户消息；测试数据已清理。真实provider未调用。
