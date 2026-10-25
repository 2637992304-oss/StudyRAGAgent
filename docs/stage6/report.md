# 阶段 6：本地部署与公开项目交付

日期：2026-10-25 开始。状态：实施和验收进行中，远端 CI 与干净克隆验收尚待完成。

## 已实现

- `app` Compose profile：Nginx、API、Worker、单次迁移、PostgreSQL/pgvector、Redis。
- 独立原文件与模型缓存卷、宿主机回环端口绑定、服务健康检查、同源 SSE 代理。
- 维护窗口一致性备份及空目标恢复工具，数据库与原文件哈希清单。
- 结构化请求日志、完整 SSE 耗时、请求/任务/job 关联；正文和密钥不进入新增日志。
- 普通 CI 专用可控模型/Embedding 替身、真实服务手动工作流。
- README、MIT 自有代码许可及第三方材料独立声明。

## 当前实测

- 前端类型检查、生产构建通过；仍有既有 JS chunk 大小提示。
- 独立容器从空数据库迁移至 0007，所有常驻服务健康。
- 通过 Nginx 的真实 HTTP：注册、课程创建、上传、自制中文笔记经真实 Celery/FastEmbed 入库、真实 LLM 严格课程回答、引用/原文件访问、账号越权阻断通过。
- 首轮完整后端 269 passed / 2 failed，失败为新增日志处理影响既有 caplog 断言；修复后最终 **276 passed**（增加5项备份安全测试）。原失败日志保留本机。
- 重启持久化、隔离数据库/原文件恢复、哈希一致性、检索和引用通过；再次向已运行目标恢复被拒绝。前端常规浏览器 **29 passed / 7 skipped**（显式真实用例默认跳过），容器实测与恢复后实测分别通过。首轮浏览器脚本误折叠课程导致失败，修正脚本后通过，未改应用逻辑。公开文件审计通过。远端 CI 和干净克隆仍待完成。

正式验收使用独立 Compose 项目及自制内容，没有覆盖原用户数据库或资料。真实 LLM 结果仅为有限流程验证，不代表总体回答质量。普通 CI 的模拟模型/常量向量不代表语义质量。

## 证据与复现

命令和操作入口见 [README](../../README.md)。私人配置、运行状态和完整日志位于本机 `.runtime/stage6/`，不公开。最终公开证据将在本报告更新为具体测试数量、摘要、提交及 Actions 链接。

## 本轮本地验证命令

- 后端：`ruff check . ../infra`、`ruff format --check . ../infra`、`pytest -q`，隔离 PostgreSQL/Redis 已迁移至 head。
- 锁文件：`uv lock --check --offline`；恢复实例 `alembic check` 无差异。
- 前端：`npm run typecheck`、`npm run build`、`npx playwright test`；真实网页使用 `STAGE6_LIVE=1` 和独立 `PLAYWRIGHT_BASE_URL`。
- 部署：`docker compose --profile app up -d --build --wait`；`scripts/smoke_stage6.py create/verify`。
- 备份恢复：`infra/manage.py backup/restore`，使用两个专用项目及不同端口；对恢复实例执行 verify 和浏览器实测。
- 公开检查：`infra/audit_public.py` 扫描暂存文件、已知本机密钥值、常见凭证特征和公开 Markdown 本地链接；对正式镜像检查无 .env、CI 替身、上传文件、模型权重。

[机器可读摘要](checks.json)仅包含非敏感结果。截图使用本轮生成的笔记、问题和一次性测试账号，没有原用户资料。完整日志及首次失败仅存本机忽略目录。

## 已知工程限制

- 初次下载基础镜像/依赖/模型需要网络；本机 Docker Hub 解析失败，改用 README 已说明的可信 ECR 基础镜像入口后构建成功。没有关闭 TLS 验证或修改系统网络安全设置。
- 备份使用维护窗口，要求操作者停止其他写入同一数据的脚本和开发服务；拒绝仍有 queued/processing/pending 工作的状态，不提供不停机快照。凭证另行保管，模型缓存可重新下载。
- 手动真实工作流尚未配置仓库模型密钥，不自动上传本机密钥。真实 Tavily/私有仓库未在本阶段重新评测。
- 默认模型、dense/512维/pgvector、原用户数据与索引均未更改。
