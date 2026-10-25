# 本地基础设施

根目录 `docker-compose.yml` 启动 pgvector PostgreSQL 16 和 Redis 7。数据保存在 Docker 命名卷中；开发时的原文件存放于 `.env` 中的 `STORAGE_DIR`，默认 `backend/storage/`。

`--profile app` 增加迁移、API、Worker、Nginx；完整部署请使用新的项目名，避免将旧数据库与空原文件卷组合。部署、停止/更新、维护窗口备份和空目标恢复的具体命令见[根 README](../README.md)。

`compose.ci.yml` 和 `ci/` 只用于隔离 CI 数据流测试。测试模型与常量向量不能衡量语义质量，也不能用于已有学习资料。

基础服务默认仅绑定宿主机回环地址。正式运行镜像不含 `.env`、上传文件、模型权重、历史问答或 CI 替身。

