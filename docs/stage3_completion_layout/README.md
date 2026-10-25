# 自生成版面专项

许可CC0-1.0。PDF由 `backend/scripts/completion_layout.py --prepare` 确定性生成，原始事实来自生成程序。
`mixed_scan.pdf`第1页有文字层、第2页是图片扫描页；`complex_layout.pdf`包含双栏、表格列关联及简单文本公式。
6题（5道有答案、1道困难无答案），独立于50题开发/保留实验；冻结配置后运行。
这不是实际用户教材，也不代表任意噪声扫描、复杂数学或图表理解质量。

真实OCR、页码和锚点记录及三页渲染位于 `../stage3_completion_runs/layout_probe/`。
真实问答及实际引用位于 `../stage3_completion_runs/layout_e2e/`，模型评审位于judge_layout。
PDF渲染做过模型视觉检查，所有真人审核字段仍待用户确认。
