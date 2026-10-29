# 部署范围

当前验证范围是本机原生PostgreSQL、uv后端和Bun/Vite前端，详见[README](README.md)。没有在本轮构建或运行Docker镜像，不能把继承模板的历史构建结果当本项目验证。

项目保留Dockerfile和Compose作为后续部署起点。使用前填写本项目`.env`，先停止本地PostgreSQL以释放55439端口，检查Compose暴露端口，并先运行数据库迁移：

```sh
docker compose build backend
docker compose run --rm backend bash scripts/prestart.sh
docker compose up -d
```

Compose覆盖数据库连接使用容器db服务，文件与向量存储固定在`/app/storage`并挂载新项目卷；它们和原生本地`.local`数据独立。后端保持1个worker，生产默认也不得直接调大。后台任务当前不耐久，重启会把unfinished运行标失败；数据库重启需同时重启后端。

公网部署前还需验证HTTPS、外部供应商、代理SSE行为、备份恢复、密钥/账户、任务限额及权限边界，并处理所选部署环境的依赖审计。当前交付未完成这些部署验收。
