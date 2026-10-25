# 阶段 6：本地部署与公开项目交付

日期：2026-10-25 开始，2026-10-26 完成本轮验收。状态：阶段6工程、完整本地部署、隔离恢复、公开仓库与真实CI已交付。

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
- 重启持久化、隔离数据库/原文件恢复、哈希一致性、检索和引用通过；再次向已运行目标恢复被拒绝。前端常规浏览器 **29 passed / 7 skipped**（显式真实用例默认跳过），容器实测与恢复后实测分别通过。首轮浏览器脚本误折叠课程导致失败，修正脚本后通过，未改应用逻辑。公开文件审计通过。远端CI三项作业通过；匿名干净克隆的全新数据卷部署及真实入库/问答/引用/隔离通过。

正式验收使用独立 Compose 项目及自制内容，没有覆盖原用户数据库或资料。真实 LLM 结果仅为有限流程验证，不代表总体回答质量。普通 CI 的模拟模型/常量向量不代表语义质量。

## 证据与复现

命令和操作入口见 [README](../../README.md)。私人配置、运行状态和完整日志位于本机 `.runtime/stage6/`，不公开。公开证据包含本报告、机器可读摘要、公开文件清单、真实CI运行和版本标签。

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

## 首次远端 CI 与修正

[首次运行](https://github.com/2637992304-oss/StudyRAGAgent/actions/runs/40208868720)：前端及完整部署/重启/备份恢复/删除作业通过；后端在测试收集时发现 Linux 的 `uv run pytest` 未包含 scripts 路径。已在 pytest 配置明确加入项目根路径，保持 README 命令有效；对应入口的22项本机回归通过，修正版远端全量回归276项通过。

提交清单复核发现首提交漏排除了 `.pytest_tmp_fix/` 的6个旧测试临时条目（自制 hello 样本和临时链接）。本机原件保留，最新版本移除这些条目，忽略规则与审计新增对 `.pytest_*` 的检查。历史内容已复核，无私人资料或凭证，因此保留原始提交记录。

## 最终交付与可复现版本

- [公开仓库](https://github.com/2637992304-oss/StudyRAGAgent)，visibility 实际为 PUBLIC，默认分支 main。
- [代码验收提交 4815c93](https://github.com/2637992304-oss/StudyRAGAgent/commit/4815c938ec1119f0ed9ad23c0732f9e760b6d6a5)：[真实 CI 全部成功](https://github.com/2637992304-oss/StudyRAGAgent/actions/runs/40209346837)。后端276通过；前端29通过/7显式真实用例默认跳过；部署构建、重启、备份、隔离恢复及删除全部通过。
- 首个可复现版本标签：`v0.1.0`。该标签包含最终验收文档；代码与上述已验收提交一致。
- 匿名克隆使用空 credential helper 和禁止交互登录，成功获取仓库。以克隆自身 `.env.example` 填写随机本地密码、独立端口和自有LLM配置；没有复制原工作区 `.env` 或运行文件。
- 在克隆自身目录执行完整构建，全新的 PostgreSQL、Redis、原文件和模型卷均由本轮创建。真实FastEmbed首次下载约28秒，入库任务约32秒；这是单次观测，不代表稳定延迟。
- 使用克隆中的 smoke 脚本，经Nginx完成注册、课程创建、真实入库、严格课程问答、来源、文件哈希、实际SQL检索、会话历史及跨账号阻断。退出码为0。
- [公开文件清单](publication.json)与暂存/历史凭证扫描通过。首提交中的非敏感临时条目修正事实保留，不重写历史。正式镜像未打包密钥、用户原文件或模型权重。

## 清理与未运行项

本轮 `studyrag-stage6`、`studyrag-stage6-restore`、`studyrag-stage6-clean` 均为专用验收项目；结束后只移除这些项目及其测试数据卷，模型下载缓存亦随测试卷移除。本机原始失败、配置及一致性备份留在忽略目录 `.runtime/stage6/`，不公开。

未向GitHub上传本机模型密钥，因此远端手动真实模型工作流未运行；本机普通正式镜像与匿名克隆已实际调用配置的 DeepSeek Flash。真实Tavily与私有GitHub仓库质量验收未在本阶段重跑，按既有报告保留边界。它们不属于本阶段普通CI的模型质量证明。
