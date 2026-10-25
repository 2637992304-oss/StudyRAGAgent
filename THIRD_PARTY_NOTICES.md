# 第三方材料与模型

项目自有代码的许可证不改变第三方材料、依赖和模型的许可。

- `docs/stage3_completion_data/sources/book_dev.md` 和 `book_holdout.md`：
  Allen B. Downey，《Think Python: How to Think Like a Computer Scientist》，
  第二版第 4、8 章的文本节选，经提取文本和空白归一化，适用
  [CC BY-NC 3.0](https://creativecommons.org/licenses/by-nc/3.0/)。
  [原书](https://greenteapress.com/wp/think-python-2e/)，
  [来源记录](docs/stage3_completion_data/provenance.json)。
  仅供非商业用途；商业使用请替换数据或另获授权。
- `docs/evaluation_data/` 为项目自制评测材料；
  `docs/stage3_completion_data/` 除上述教材节选外的自制材料、
  `docs/stage3_completion_layout/` 的自制版面 PDF、
  `docs/stage4_thinking/fixtures_final/equation.png` 为 CC0 自制测试样本。
- 默认 Embedding 模型 `BAAI/bge-small-zh-v1.5` 由首次运行下载，
  模型权重不包含在仓库或镜像中。使用前查阅
  [模型卡及许可](https://huggingface.co/BAAI/bge-small-zh-v1.5)。
- Python/npm 依赖和容器基础镜像适用各自许可证，版本见锁文件与 Dockerfile。
  DeepSeek、Tavily 和 GitHub 服务适用各自服务条款；凭证由部署者自行提供。
