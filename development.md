# Grounded Research 本地开发

本项目采用一个 uv Python workspace 和一个 Bun 前端 workspace。完整安装和启动见 [README](README.md)。

- 首次：`bash scripts/setup-local.sh`。
- 前台启动：`bash scripts/start-local.sh`，本地端口5178/8010，专用PostgreSQL端口55439。
- Ctrl+C停止前后端，`bash scripts/stop-local.sh`停止专用数据库。
- 回归：`bash scripts/check.sh`；`cd frontend && bun run test:research`。
- API变化后：`bash scripts/generate-client.sh`。

所有运行凭据与数据在本项目`.env`、`.local/`，均不提交Git。真实模型/搜索Key由使用者配置，测试用替身。测试只能连接grounded_research_test数据库，旧账户测试会清理该测试库；禁止绕过守卫指向业务库。

进程内研究worker必须单后端进程。开发中修改Python代码后停止并重新启动，正在执行的研究需重新创建；本版不承诺热重载恢复。

架构与后续学习见 [docs/architecture.md](docs/architecture.md) 和 [docs/learning.md](docs/learning.md)。
