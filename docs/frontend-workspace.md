# 课程对话界面

左侧「我的课程」悬停出现的 + 用于创建课程。点击课程名称可展开或收起对话；悬停课程时，书本按钮打开该课程的资料库，右侧的 + 创建新对话。手机使用左上角导航按钮打开课程抽屉，操作图标常驻显示。

左下角账号右侧的月亮图标可切换深色模式，深色模式下显示太阳图标以切回浅色。偏好保存在当前浏览器，刷新及重新登录后继续使用，并在同源标签页间同步；浏览器无法保存偏好时仍可在当前页面切换。手机从课程抽屉底部使用同一入口。

资料库在居中弹窗内显示，可以预览原文件、刷新状态、删除或重试。选择资料类型后，点击弹窗内的 + 选取文件并上传。打开其他课程的资料库不会切换当前对话，也不会清空草稿。

右侧仅消息区纵向滚动。顶部显示当前对话名称；输入框固定在底部，Enter 发送、Shift+Enter 换行，输入法选字期间不会触发发送。向上阅读时不强制滚回底部，可点击向下箭头回到最新消息。生成期间暂时锁定对话切换，避免意外中断。图片附件、深度思考、来源引用、停止生成均保留。

新对话初始名称为「新对话」，首次有效发送后，以首条问题压缩空白后的前 20 个字符命名；仅图片问题命名为「图片问答」。右键对话名称或点击其右侧的 … 可重命名、删除。手动重命名后不会再自动覆盖。标题保存到数据库；当前对话选择保存在本地且按账号区分，文字草稿保存在当前浏览器会话中并按账号、课程和对话区分。

底部「资料范围」默认折叠，默认整门课程，可选择已就绪的具体资料，或切回整门课程。范围限制之后的资料检索；已有历史消息及其来源仍保留。修改范围时服务端验证账号、课程归属及资料状态，生成期间不能修改范围。

## 更新和接口

更新后先在 `backend/` 执行 `.venv/Scripts/python.exe -m alembic upgrade head`，再重启后端。迁移 `0007_session_titles` 增加标题和手动命名标记，并给旧对话按首条问题补标题，不删除历史消息或资料。

`GET /api/v1/chat/sessions` 返回 `title` 和 `title_renamed`。`PATCH /api/v1/chat/sessions/{id}` 可接收 `title`、`document_ids`；`document_ids: null` 代表整门课程。重命名仅允许非空且不超过 120 字的名称。全部操作按账号隔离。

## 验证

后端在 `backend/` 执行：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_session_workspace.py tests/test_stage2.py tests/test_stage4.py tests/test_thinking.py tests/test_registration.py -q --basetemp=.stage5-test-temp/workspace-regression
```

前端服务运行后，在 `frontend/` 执行：

```powershell
npm run build
npx playwright test e2e/workspace.spec.ts e2e/composer.spec.ts e2e/courses.spec.ts e2e/layout.spec.ts e2e/registration.spec.ts
```

浏览器回归使用模拟 API，不修改真实账号和资料。真实接口验收可运行 `WORKSPACE_LIVE=1` 的 `e2e/workspace-live.spec.ts`，仅创建并清理本次测试的临时账号、课程、对话，不调用模型。
