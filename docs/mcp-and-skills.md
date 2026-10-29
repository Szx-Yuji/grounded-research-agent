# MCP 与本地 Skills

MCP 让外部客户端用标准协议读取本项目中当前用户有权访问的数据；Skills 给主 Agent 提供按任务加载的工作流程。二者分别解决工具连接和工作方法的问题。

实现入口：

- `backend/app/mcp_server.py`：官方 `mcp>=2.3.0,<3` SDK 的 `MCPServer`，使用 stdio。
- `backend/app/assistant/skills.py`：固定目录和名单内的发现、校验与按需加载。
- 根目录 `skills/`：三个可阅读、可版本控制的工作流程。

## MCP 的身份与调用路径

```text
本机 MCP 客户端
    → stdio MCP 进程（一个应用用户的既有 JWT）
    → http://127.0.0.1:8010/api/v1/...
    → 原有 JWT 校验和资源归属校验
    → 当前用户可访问的数据
```

适配器将 `Authorization: Bearer <JWT>` 原样转发给应用 API，不直接读取数据库或向量库。模型不能传入 `user_id`、Token、任意 URL、文件路径或命令。UUID 仅选择资源，不能代替授权。应用仍需验证知识库、会话、研究任务是否属于该身份。

这是由本机客户端启动的单用户 stdio 进程。能使用该进程的客户端拥有所配置 JWT 的读取权限；不同应用用户应启动不同进程。它没有提供远程共享 MCP 服务、OAuth 授权服务器或跨用户身份切换。不要将此入口自行包装成无需认证的公共 HTTP 服务。

| 工具 | 现有应用接口 | 输入边界 |
|---|---|---|
| `grounded_list_knowledge_bases` | `GET /api/v1/knowledge-bases/` | `skip` 0–100000；`limit` 1–100 |
| `grounded_search_evidence` | `POST /api/v1/integrations/search-evidence` | 1–5 个知识库 UUID；非空问题最多 2000 字符；`k` 1–20（默认 4） |
| `grounded_list_conversations` | `GET /api/v1/assistant/conversations` | 同上分页 |
| `grounded_get_conversation` | `GET /api/v1/assistant/conversations/{id}` | 会话 UUID |
| `grounded_list_messages` | `GET /api/v1/assistant/conversations/{id}/messages` | 会话 UUID |
| `grounded_get_research_run` | `GET /api/v1/research/runs/{id}` | 研究任务 UUID |

六个工具声明 read-only、non-destructive、idempotent。证据检索使用 POST 承载查询，不会开始研究任务或修改用户文档；它仍可能调用已配置的 Embedding 提供方，产生网络请求和费用。MCP 注解是客户端提示，实际边界来自固定工具实现及后端权限检查。

每次 HTTP 调用默认最多 20 秒，连接最多 3 秒，响应解码后最多 2 MiB。不跟随重定向、不使用环境代理。只允许数字回环地址 `127.0.0.1` 或 `[::1]` 的 8010 端口，拒绝任意域名、userinfo、路径前缀、查询串和片段。401/403、404、上游错误、超时与无效响应返回有限的工具错误，不转发响应正文或凭据。

消息接口目前没有分页，超过 2 MiB 的会话读取会失败；此时可读取会话元信息或使用较小会话，不能声称该入口支持任意长度的消息归档。超时也不表示上游服务的全部工作已停止。

## 启动和客户端配置

先启动现有后端并确认本机 8010 端口可访问。MCP 进程只需要：

```text
GROUNDED_API_BASE=http://127.0.0.1:8010
GROUNDED_API_TOKEN=<既有应用登录得到的 access_token>
```

`GROUNDED_API_BASE` 有上述默认值，`GROUNDED_API_TOKEN` 必填。两者来自进程环境；该模块不会读取 `.env`，也不会导入应用的数据库、模型密钥配置。JWT 过期后，应通过现有登录流程取得新 Token 并重启适配器，不需要新增登录接口。不要将真实 Token 提交到仓库或写进聊天记录。

在项目根目录，可用 zsh 的隐藏输入设置 Token，然后运行服务：

```zsh
read -rs "GROUNDED_API_TOKEN?Application access token: "
export GROUNDED_API_TOKEN
export GROUNDED_API_BASE=http://127.0.0.1:8010
uv run --frozen --package app --directory backend python -m app.mcp_server
```

正常服务的 stdout 是 MCP JSON-RPC 协议流，不应当作普通交互终端使用。客户端应启动并连接这个进程。

下方是常见 `mcpServers` 格式的示意，各客户端的配置入口可能不同。把根目录和 Token 占位符替换为本机实际值；凭据配置保存在客户端的本地安全位置，不加入项目 Git。

```json
{
  "mcpServers": {
    "grounded-research": {
      "command": "/absolute/path/to/grounded-research-agent/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/absolute/path/to/grounded-research-agent/backend",
      "env": {
        "GROUNDED_API_BASE": "http://127.0.0.1:8010",
        "GROUNDED_API_TOKEN": "<existing application access_token>"
      }
    }
  }
}
```

此适配器默认在宿主机运行，通过已映射的 8010 端口读取后端。容器内部的 `127.0.0.1` 指向该容器自身，不能直接套用宿主机配置。

## Skills 的渐进加载

| 名称 | 作用 | 声明可用工具 |
|---|---|---|
| `document-qa` | 用已选文档回答问题并引用证据 | `ask_knowledge` |
| `source-comparison` | 按共同标准比较证据和方案 | `ask_knowledge research_topic search_web` |
| `research-planning` | 限定研究问题、分解证据需求，再按请求开展研究 | 同上 |

文件采用 [Agent Skills 规范](https://agentskills.io/specification) 的 YAML frontmatter：`name`、`description`、`metadata.version`、`allowed-tools`，正文是流程说明。

`list_skills()` 只返回 `name/description/version/allowed_tools`；`load_skill(name)` 才返回该技能的 `instructions`。主 Agent 可以先发现可用技能，再按任务读取说明，避免将全部工作流程永久塞进提示词。

加载器只接受上述三个精确名字，使用仓库根目录的固定 `skills/`。路径穿越、未知名称、目录或文件符号链接、过大的文件、不安全 YAML 和未知工具声明会被拒绝。添加目录本身不会自动注册技能；必须审核后修改固定名单并运行测试。正文不会被当作 Python/Shell 执行，不会自动下载额外技能、脚本或资源。

`allowed-tools` 是该工作流程的能力声明；实际工具执行仍经过主 Agent 已有的知识库、网页开关和参数校验，加载技能不能扩大用户权限。检索内容和技能正文都不能授权对外发布、执行代码或泄露私有材料。当前加载器没有通用的脚本运行器，也没有动态插件安装功能。

容器部署需要把根 `skills/` 复制到镜像的 `/app/skills/`，与 `/app/backend/app/assistant/skills.py` 的相对位置对应。

## 验证

在 `backend/` 执行：

```bash
uv run --frozen --package app pytest -q tests/mcp_skills --confcutdir=tests/mcp_skills
```

使用独立测试目录以避开旧的数据库清理 fixture。测试覆盖固定技能发现和加载、路径和符号链接拒绝、安全 YAML、配置边界、Token 转发、固定接口、输入限制、上游错误脱敏、重定向拒绝、响应大小限制、官方 SDK 内存客户端的 `tools/list`/`tools/call`，以及真实 stdio 子进程的协议握手。

这组测试使用 `httpx.MockTransport`，不读取项目 `.env`，不访问数据库、外网或真实模型。它验证适配器及协议边界；应用 API 的真实 JWT/跨用户权限和检索链路还需通过相应后端集成测试验证。不要将 Mock 返回的证据理解为一次真实知识库检索结果。

## 官方参考

- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)：本项目使用 2.x 的 `MCPServer`，并非旧版示例中的 `FastMCP`。
- [MCP 工具规范](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)：工具发现、调用及注解。
- [Agent Skills 格式规范](https://agentskills.io/specification)：元信息、完整说明和资源的渐进披露。
- [Deep Agents Skills](https://docs.langchain.com/oss/python/deepagents/skills)：技能按需读取的生态参考；本项目没有为此迁移到 Deep Agents。
