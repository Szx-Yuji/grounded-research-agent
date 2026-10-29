# 2026-10-29 Agent 技术方向核实

核查方式：公开 GitHub 仓库元数据/最新正式发布 + 官方文档正文；此项生态核查未读取项目 .env，也未调用模型、Tavily或付费搜索。文档未显示可解析发布日期的，以下按2026-10-29访问日标识，不冒充发布时间。下列版本是当日查询快照，不是永久最新版本，也不意味着本项目必须升级到该版本。

## 本次落地范围

已提供官方 MCP Python SDK 2.x 的 stdio 适配器，将现有带身份校验的知识库检索、会话和研究结果读取接口暴露为六个只读工具。新增三个本地审核的 SKILL.md 配方与显式发现/加载模块，覆盖文档问答、来源比较和研究规划。配置、权限边界与验证方法见 [mcp-and-skills.md](mcp-and-skills.md)。

这份调研中的 Langfuse、Deep Agents、OpenHands 是有依据的选型参考，不代表这些框架已被本项目集成。其他工程化能力是否完成，应以各模块实现及验证记录为准。

## 判断

不能据此说RAG与multi-agent过时。当前LangChain官方仍将检索分为2-step、Agentic、Hybrid RAG；Deep Agents仍把subagents作为核心能力。官方multi-agent指南同时明确，不是所有复杂任务都需要多个Agent，单Agent+适当工具经常足够。更合理的变化方向是把既有能力放入可管理上下文、可恢复执行、可观测可评估的Agent harness，而不是按新名词重写项目。

## 可核实的生态活动（不是就业排名）

| 官方仓库 | API核实最新release | 发布日期UTC | 说明 |
|---|---|---|---|
| https://github.com/langchain-ai/deepagents | deepagents==0.7.32 | 2026-10-23 | README列上下文、子Agent、filesystem、skills、HITL、MCP；建于LangGraph |
| https://github.com/langchain-ai/langgraph | 1.2.18 | 2026-10-26 | 图运行时、持久化、interrupt及故障处理 |
| https://github.com/modelcontextprotocol/python-sdk | v2.3.0 | 2026-10-02 | MCP官方Python客户端/服务端SDK |
| https://github.com/langfuse/langfuse | v4.71.0 | 2026-10-27 | LLM tracing/evals/prompt与数据集实验 |
| https://github.com/OpenHands/OpenHands | v1.34.0 | 2026-10-27 | 当前偏Agent Canvas/应用集成；SDK在独立software-agent-sdk |

全部仓库未归档且10/27—10/29仍有提交活动。仅证明这些项目正在维护；没有用stars推断市场份额、招聘数量或录用概率。

## 项目建议排序

### 第一批：评估与观测 + 上下文工程

1. 为一次turn记录trace_id、模型请求、工具调用、来源数量、阶段时延、token usage、取消/重试和错误类别；与现有SSE用户进度分开。先本地结构化记录/脱敏，再可选接Langfuse或OpenTelemetry。不得默认上传原始私有文档或完整聊天到外部观测平台。
2. 建小型有标注的回归集：普通聊天不应检索、何时调用RAG、追问第二方案、旧文档删除、工具失败、来源引用、摘要前后事实一致。先用确定性断言/录制替身与人工标注，真实模型评估和LLM Judge单独运行并显示成本。产出能比较prompt/tool策略变更的结果，不生造成功率。
3. 现有摘要和delegate_notes已有context工程雏形。补消息/证据版本与上下文预算记录、必要信息按需读取、摘要正确性评估；不要只堆更长system prompt或更多Agent。

依据：
- https://docs.langchain.com/oss/python/langchain/context-engineering ：model/tool/lifecycle context；动态工具选择、历史和权限上下文。
- https://langfuse.com/docs/observability/overview ：模型、工具、检索trace，输入输出、token、时延。
- https://langfuse.com/docs/evaluation/overview ：在线/离线评价、数据集、确定性Code Evaluators、CI回归。

### 第二批：可恢复任务 + 有选择的HITL

当前进程内任务重启标失败是明确缺口。先把一次工具决策/执行/结果作为可持久化步骤，使用LangGraph持久checkpointer或其他耐久执行后端；为外部副作用设幂等键、恢复权限校验、取消及过期策略。不能只增加一张checkpoint表就宣称跨重启exactly-once。

HITL使用interrupt/Command(resume=...)，审批和执行跟同一已授权thread/turn关联。适用于高成本批量研究或未来写回/发布工具，不必给普通只读搜索逐次弹审批。文档明确resume时节点从头重跑，因此interrupt之前的副作用需要幂等或拆分。

依据：
- https://docs.langchain.com/oss/python/langgraph/persistence ：持久checkpointer与跨thread store区别，RAM saver重启丢失。
- https://docs.langchain.com/oss/python/langgraph/interrupts ：checkpointer/thread_id/interrupt/resume及节点重执行。
- https://docs.langchain.com/oss/python/langgraph/fault-tolerance ：节点重试、timeout、error handler及graceful shutdown。

### 第三批：MCP + Skills

MCP用于标准化接入外部工具，Skills用于按需加载领域流程，不是同一种东西。先选一个真实跨应用复用点：把带权限KB搜索暴露为只读MCP服务，或用客户端接一个官方维护工具；身份必须从服务端授权取得，不能让模型传user_id越权。不要把示例服务器直接当生产方案：官方servers README明确其为reference implementations、非production-ready。

Skills先做少量审核过的流程文件，例如资料对比/证据核对，name+description常驻，完整说明按需读取；记录skill版本和工具权限。本次已做三个固定配方。不需要因此引入任意代码执行，也不要自动信任网上SKILL.md附带脚本。

依据：
- https://modelcontextprotocol.io/docs/getting-started/intro ：开放连接协议，当前文档标版本2026-07-28。
- https://github.com/modelcontextprotocol/python-sdk ：官方SDK。
- https://github.com/modelcontextprotocol/servers ：明确reference而非production-ready警告。
- https://agentskills.io/specification ：SKILL.md元信息/正文/资源渐进披露。
- https://docs.langchain.com/oss/python/deepagents/skills ：按任务读取技能、角色隔离与支持资源。

### 框架选择

Deep Agents适合借鉴或选一条功能分支比较：它把planning、subagents、filesystem context、memory、skills、HITL合为harness。当前项目已有自定义主Agent及权限/数据库接口，直接全量迁移未证明净收益；先用同一评估集做薄适配对照，确认模型接口、流协议、授权和部署兼容再决定。

OpenHands是较成熟的代码执行Agent生态参考，适合要做“改代码→运行测试→修复”时使用独立SDK/沙箱，当前知识研究助手不需要为了更新换成编码Agent。当前仓库README明确OpenHands/OpenHands、software-agent-sdk、typescript-client、automation分工。

依据：
- https://github.com/langchain-ai/deepagents
- https://docs.langchain.com/oss/python/deepagents/overview
- https://github.com/OpenHands/OpenHands
- https://github.com/OpenHands/software-agent-sdk

## 单独的招聘证据（小样本，不是趋势统计）

2026-10-29再次读取腾讯官方岗位API：
- WeTalk-大模型应用研发工程师(Agent Harness)，两年以上；官方最后更新2026-10-28。职责同时列Agent Loop、Context压缩、Skill、跨会话Memory、检索、长任务状态、评测/Trace/用户反馈。https://careers.tencent.com/jobdesc.html?postId=1934984932162117632
- Agent Harness工程师-AI infra，三年以上；最后更新2026-10-20。含工具/Skill、checkpoint恢复、SubAgent并发、OTel/trace、eval；也含GPU/推理性能专项，不能当所有应用岗或应届通用门槛。https://careers.tencent.com/jobdesc.html?postId=2098332758412804096

这两条证明相关能力在具体岗位出现，也同时保留RAG、Memory和SubAgent；不足以说明整个2026市场已从某技术全面迁移。

用于读取上述岗位正文的官方 API（2026-10-29 再次请求均 Code=200）：

- [WeTalk 岗位 API](https://careers.tencent.com/tencentcareer/api/post/ByPostId?postId=1934984932162117632&language=zh-cn)：`RecruitPostName` 为“WeTalk-大模型应用研发工程师-（Agent Harness）”；正文原句“建设 Context Engineering 能力，包括上下文压缩、Skill、跨会话 Memory、检索和长任务状态管理”。
- [AI infra 岗位 API](https://careers.tencent.com/tencentcareer/api/post/ByPostId?postId=2098332758412804096&language=zh-cn)：`RecruitPostName` 为“Agent Harness 工程师-AI infra方向-上海/深圳/北京/杭州”；要求含“RAG 与记忆机制”，职责含“长任务的状态持久化与 checkpoint 恢复、Sub-Agent 并发编排”。
