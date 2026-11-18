# 第三方组件声明

本仓库源码仅用于作品展示（见 README「许可」一节）。下面列出运行期依赖的第三方组件及其许可，均通过 pip 安装，不随仓库分发。

| 组件 | 用途 | 许可 |
|---|---|---|
| Flask | 本地 Web 服务与模板渲染 | BSD-3-Clause |
| requests | 探测本机 Ollama（仅访问 127.0.0.1） | Apache-2.0 |
| pypdf | 解析 PDF 简历 | BSD-3-Clause |
| python-docx | 解析 DOCX 简历 | MIT |
| pytest | 测试框架（不写入 requirements.txt，需单独安装） | MIT |

前端没有引入第三方框架或字体，`static/` 下的样式与脚本为本仓库自带；页面图标使用内联 SVG。

`samples/jobs/` 下的简历基于作者真实项目的公开描述改写，姓名、公司、邮箱已替换为占位符，不对应真实个人。简历中的项目指标（385 个用例、191 条规则、1,480 部法规 / 63,653 条条文、源码行数）取自公开仓 [LHQCOCO/contract-local-scan-demo](https://github.com/LHQCOCO/contract-local-scan-demo) 的 README 与源码统计，不是虚构数据。

`samples/jobs/jd_ai_agent.md` 是虚构的岗位描述，公司与岗位不对应真实招聘需求。
