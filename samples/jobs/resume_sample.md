# 秦某某

上海 · 求职方向：AI 应用研发 / Agent 开发 · demo@example.com

## 技能

- 语言：Python、JavaScript、SQL
- 后端：Flask、waitress、SQLite
- 模型：Ollama（qwen2.5:3b / gemma3:4b），本地推理与内存档位控制
- 检索：自实现 BM25，jieba 分词与概念扩展
- 工具：PyInstaller、Cython、Git、PDF/DOCX/TXT 解析与 OCR 降级

## 项目经历

### 合同本地审阅工具（个人项目，公开演示版）

- 负责 Python 后端与原生 JavaScript 前端，后端约 12.8k 行、前端约 2.6k 行
- 实现 PDF/DOCX/TXT 解析与扫描件识别，OCR 不可用时自动降级
- 基于 191 条规则与 1,480 部法规的 63,653 条条文构建审阅链路，自实现 BM25 检索并逐条挂法条原文
- 接入 Ollama 完成字段补抽、合同类型消歧与争议复核三类受控调用，模型不可用时回落规则
- 编写 385 条自动化测试用例，覆盖解析、规则与导出；公开演示版见 github.com/LHQCOCO/contract-local-scan-demo

## 教育经历

- 2025 年 9 月毕业，金融学硕士
- 雅思 6.5
