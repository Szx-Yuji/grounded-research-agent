# 怎样教、怎样学这个项目

目标是能解释、修改和排错，逐步具备独立完成类似应用的能力。现有代码不用重新手敲；教学按“假设从空项目实现，先需要什么”的依赖顺序展开，每次只解决一个小目标。

## 每一步的讲解要求

1. 先说明当前要解决什么问题、要看哪个文件/函数，以及为什么现在需要它。
2. 对新函数说明是谁提供的：Python 标准库、第三方库或本项目自定义。首次出现的装饰器、类型、类和框架调用也如此。
3. 逐个解释参数的值从哪来、类型是什么、函数实际做什么、返回什么、谁接收返回值。区分“创建对象”和“发出网络请求”。
4. 遇到没讲过的自定义函数，进入定义，按依赖解释清楚再返回调用处；不要用一个未解释的函数带过另一个函数。第三方 API 讲清本次用到的契约，不展开整个库的内部源码。
5. 区分模型提出的工具调用、服务端实际执行、工具结果返回模型、最终消息落库；也区分数据库状态、Python 内存和浏览器临时状态。
6. 一次给一小段可理解的代码或观察任务，留出理解和提问空间。用户说“下一步”再沿主线推进，避免一次铺完整仓库。
7. 对已有代码通过阅读、断点、改一处小参数或输出预测理解，不要求重打旧代码。新练习需要实际修改时，明确文件、位置、预期行为和验证方式。
8. 基础不足就在当前调用处补 Python、SQL、HTTP、异步/线程知识，再回业务主线。测试框架可以集中讲解，不让写测试长期替代业务和 Agent 教学。

## 学习顺序

### 1. HTTP 和身份：发送一句话

入口 `backend/app/api/routes/assistant.py`。先创建会话，再发送一条用户消息。画出 JWT、conversation_id、content 和可选 Idempotency-Key 从前端到接口的路径。

进入 `CurrentUser` 的定义，解释 FastAPI 依赖如何把请求 Token 变成当前用户；再说明 `SessionDep`、Pydantic 请求模型和项目自定义 service 各做什么。返回 `202` 只说明入队，不表示答案已生成。

观察点：参数是谁传的、哪个函数实际被框架调用、轮次 ID 如何回到前端。

### 2. 事务与队列：为什么先保存意图

入口 `services/assistant_conversations.py:start_turn` 和 `services/durable_jobs.py`。先讲单条正常请求，再讲同会话互斥和幂等重发。

进入 `existing_intent`、`enforce_capacity`、`enqueue`，解释锁、请求摘要、Session.add/flush/commit 的差别。把“用户消息、业务轮次、执行意图同事务”连到崩溃窗口，不能只背事务定义。

观察点：没有 worker 时请求保持 queued，相同键返回同一个意图，同键不同输入为何409。

### 3. 独立 worker：谁真正开始执行

入口 `app/worker.py:run_worker`，依赖 `claim_next`、`execute_claim`、`heartbeat`、`assert_lease`。

先看一个线程执行一个任务，再解释线程池与 ContextVar。`SKIP LOCKED` 是 PostgreSQL 语义；lease_token 是项目定义的执行身份。用两个 worker 的时间线说明旧结果为什么不能覆盖已失效执行。

观察点：queued 跨重启保留；running 过期变 interrupted，当前不会从中断节点继续，也不自动重放不确定收费请求。

再用文档索引走同一条队列：进入上传/重试接口和 enqueue_document，看返回上传成功与索引 ready 的区别，最后进入 processor 的分块发布，解释为什么文档状态、分块和任务成功必须在有效 lease 下同事务提交。

### 4. 主 Agent：意图怎样变成函数执行

入口 `assistant/engine.py:execute_chat`。先用普通回答，再用一次 ask_knowledge，最后用 research_topic。

依次看工具 schema、模型客户端、tool_calls、Pydantic 参数校验、实际 `_call_tool`、工具返回消息。遇到 `load_skill` 进入 `assistant/skills.py`，区分发现元信息和读取正文。用户身份和知识库范围不由模型生成。

观察点：模型只返回调用意图，Python 代码实际执行；工具失败是错误信息，不是假成功证据。

### 5. RAG：权限、索引与排名

入口 `services/knowledge_retrieval.py:retrieve_evidence`，递归进入 `validate_knowledge_access`、`retrieval_ranking.py` 的 tokenize/lexical_rank/reciprocal_rank_fusion，以及 `rag_service.py:create_retriever`。

先用两份短文档手算一个词法排名和一轮 RRF，再看 Chroma 候选如何映射为 Evidence。解释 embedding 客户端对象何时建立、何时真正请求模型；中文 bigram、BM25 和向量相似度各代表什么。

进入 `KnowledgeChunk` 和文档处理流程，理解 ready、内容哈希、索引版本和发布顺序。迁移改表不等于重建向量，旧文档兼容路径也不等于已有新词法索引。

观察点：越权在供应商调用前失败；向量故障的词法降级应可见；k 是最终结果上限，不等于每条召回路径的候选数。

### 6. 研究图：哪些节点固定、哪些地方有选择

入口 `research/graph.py`、各 agents 和 `tools/search.py`。按 Planner、Researcher、条件 Refiner、Analyst、Writer 展开，区分固定图条件路由与主 Agent 自主工具循环。

讲清 ThreadPoolExecutor、copy_context 和有限子问题搜索。将一个私有来源和一个网页来源并排看，解释私有材料不应成为公网链接，也不能随意进入公开搜索查询。

观察点：并发搜索不是无限并发 Agent；prompt 里的“不要泄露”不是完整出站安全机制。

### 7. 预算和 trace：实际请求之前发生什么

入口 `runtime/context.py`、`runtime/gateway.py`。进入 execution_context、reserve、provider_call，再回实际 SDK 派发点。

先追一条普通模型请求，再看流式 response、usage-only chunk 和 Embedding batch。解释主/子 Agent 如何共享计数、为什么先预留和落库、未知 usage 为什么不能填0。

观察点：max_output_tokens 预留与供应商实际 Token 不同；网络超时不证明供应商没有执行，lease 不能做到恰好一次计费。

### 8. 记忆：摘要、近期消息和子 Agent 笔记

入口 `assistant/context.py` 和 `assistant_conversations.py:load_context`。先验证两轮短对话，再看超过预算的摘要。

说明摘要覆盖哪些成功消息、最近窗口保留什么、delegate_notes 为什么让未在最终回答展开的方案 B 也可追问。进入来源有效性检查，解释删除文档后旧笔记和摘要如何失效。

观察点：实际第二轮模型输入，而不只是网页保留了上一条回答；摘要仍有损，必要时回原资料检索。

### 9. SSE 与 React：关闭页面为什么还能看

入口事件查询和 frontend 的聊天 routes/hooks/client。追踪发送→返回turn→SSE增量→成功后重读持久消息。

解释网络块与 SSE 帧的区别、缓冲、after 游标和去重。再看取消、刷新和迟到结果，区分关闭连接、取消执行和进程崩溃。

观察点：重新读事件不调用模型，看到 token 不代表完整答案已经提交。

### 10. 评估和扩展：自己做一次有意义的修改

运行根目录 `evals/retrieval/run.py`，先理解数据、相关性标签、开发/留出拆分，再讲 Recall、MRR、nDCG 和无答案问题。

改变一个分词或候选参数，先预测结果，再看消融与失败样例。向量 fixture 是可重复 toy cosine，不是现实 Embedding 效果。之后才做小预算真实供应商实验。

MCP 最后从官方 Client 的 tools/list、tools/call 进入适配器，解释 stdio 协议、固定接口与 JWT 转发；不因用了 MCP 就跳过 HTTP 权限。

## 每阶段的小练习

- 画一轮请求的数据流，并对每个函数说出输入、输出、执行者和来源。
- 关闭 worker 提交一条假任务，解释为什么 queued；再领取，不调用真实供应商。
- 用同一个 Idempotency-Key 重发，然后换一个输入，预测消息数和状态码。
- 让第一轮最终回答只展开 A，第二轮追问 B，检查模型输入确实有经过来源校验的子 Agent 笔记。
- 故意使用别人的知识库 ID，证明没有发生 Embedding 请求。
- 模拟向量不可用、预算耗尽、取消与 lease 失效，解释错误处于哪一层。
- 对一个检索失败样例提出一个修改，跑留出集确认改善是否只发生在开发样本。

所有练习逐个进行。真实供应商、领域质量、容量与恢复演练需要用户亲手完成并能解释；完成标准与面试演示见 [工程路线](engineering-roadmap.md)，故障步骤见 [运维说明](operations.md)。
