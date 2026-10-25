# 项目文档

- [本地部署、配置、备份与测试入口](../README.md)
- [阶段 6 实施与验收](stage6/report.md)
- [阶段 6 进度](stage6/progress.md)
- [课程对话界面与注册后的使用](frontend-workspace.md)
- [阶段 4 图文与单 Agent](stage4/report.md)
- [阶段 4 思考模式](stage4_thinking/report.md)
- [阶段 5 外部工具初版](stage5/report.md)
- [阶段 5 默认自动工具（当前交互）](stage5_auto_tools/report.md)
- [图片答案核验与有界修正](answer_review/report.md)
- [基础评测样本](evaluation_data/README.md)
- [50 题公开/自制评测样本及第三方许可](stage3_completion_data/README.md)
- [自制版面与 OCR 样本](stage3_completion_layout/README.md)
- [第三方许可说明](../THIRD_PARTY_NOTICES.md)

公开文档保留历史结论和失败，不将旧验收当作当前代码的全部功能证明。阶段 3 生产默认仍是 dense / 512 维 / pgvector；新的切块、混合检索、重排、表格核对均按原有开关区分，不自动重建用户资料。

原始模型协议、逐题问答、浏览器运行目录、日志和私人 PDF 试点材料保留在本机忽略目录，不作为公开仓库的一部分。公开历史报告中，对未发布原件的链接改为文字说明；本机原报告副本保存在 `.runtime/stage6/original-docs/`。这不表示原始结果已重新核验或全部通过。
