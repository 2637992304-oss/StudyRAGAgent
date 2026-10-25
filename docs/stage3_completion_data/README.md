# 新一轮阶段3数据

50题，dev=25、holdout=25；每集19道有答案、5道困难无答案、1道权限边界。
两集可访问文档不相交，问题和标注在开发实验前固定。题目由本项目编写，参考事实从来源定位，尚未经真人确认。
这不是官方基准，也不代表真实用户教材的分布。

- 12份概念材料及2份权限诱饵为自生成CC0-1.0资料，覆盖英文术语索引与中文正文分离。
- `book_dev.md`、`book_holdout.md`分别来自Allen B. Downey的《Think Python》第二版第4、8章。
  作者主页：<https://greenteapress.com/wp/think-python-2e/>。
  授权为 **CC BY-NC 3.0**：<https://creativecommons.org/licenses/by-nc/3.0/>，保留署名，仅非商业用途。
  这些教材文本不随自生成资料改为CC0；商业使用需换成许可合适的材料或另获授权。
- 原章节URL、作者、许可、原始HTML与文本哈希保存在 `provenance.json` 和数据集文档元数据。
  原HTML保留在 `raw/`。转换仅提取文字块、保留标题、归一化空白；没有提取教材图片。
- 表格/公式/混合扫描PDF是单独的CC0自生成专项，位于 `../stage3_completion_layout/`，不冒充真实教材扫描页。

在backend目录运行：

```powershell
python -m scripts.build_stage3_completion
# 仅需要更新原始下载时使用 --download；默认使用已保存的原章节。
```

更新原文、问题、划分后必须建立新数据版本；不得继续把已查看、用于分析的保留集称为未见测试集。
装载和清理沿用 `scripts.evaluate_stage3 --dataset ...` 的专用账号与清理清单，正文/向量不写入用户资料。
