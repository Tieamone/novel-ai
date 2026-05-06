> 请始终用中文回复，包括所有提示、选择项、确认信息和错误说明。

# 项目概述

AI 网文写作自动化系统，支持 **策划 → 写作 → 审稿 → 导出** 完整流水线。
入口：`main.py`，运行方式：`python main.py`。

# 技术栈

- Python 3.10 + SQLite (WAL 模式) + DashScope API
- 配置：`config.yaml`（模型、温度、字数等）
- 模型列表：`data/custom_models.json`
- 依赖管理：`venv/` 虚拟环境，尽量不引入新依赖

# 核心模块 (core/)

| 文件 | 职责 |
|------|------|
| `api_client.py` | DashScope API 调用封装 |
| `planner.py` | 章节策划（节拍规划） |
| `writer.py` | 章节写作（分段/整章） |
| `reviewer.py` | 责任编辑审稿 + 写作-审稿循环 |
| `reader_reviewer.py` | 读者视角审稿（可读性/AI味评分） |
| `memory_manager.py` | 记忆/伏笔/角色状态管理 |
| `model_manager.py` | 模型选择与切换 |
| `config_loader.py` | YAML 配置加载 |
| `db.py` | 数据库初始化与连接管理 |
| `utils.py` | 公共工具（连接管理、事务、重试） |
| `exporter.py` | 章节导出 |

# 数据目录 (data/)

每个小说独立子目录 `data/{小说名}/`，包含：
- `novel.db` — SQLite 数据库
- `master_outline.md` — 总大纲
- `settings.md` — 小说设定
- `characters.md` — 人物设定
- `foreshadowing.md` — 伏笔记录
- `recent_summaries.md` — 近期章节摘要

导出目录：`output/{小说名}/`

# 代码规范

- 日志、注释、用户提示均使用中文
- 数据库操作使用 `with_db_connection` + `DatabaseTransaction`
- 错误重试使用 `execute_with_retry`（指数退避）
- 路径处理使用基于 `__file__` 的绝对路径，不要依赖 CWD

# 修改禁区

- 不要修改 `data/` 下的小说内容文件（大纲、设定、角色等）
- 不要修改 `config.yaml` 中的模型配置而不先确认
- 不要修改数据库 schema 而不先确认
- 不要引入新的第三方依赖而不先确认

# 已知架构改进项

- 路径敏感性：部分代码依赖相对路径（如 `sensitive_words.txt`），应统一为 `__file__` 推导

# 相关文档

- `README.md` — 快速说明
- `CHANGELOG.md` — 变更日志
- `docs/PROJECT_PROFILE.md` — 项目白皮书（含详细进度）
- `docs/AI_ERROR_LOG.md` — 错误知识库
- `docs/AI_CONTEXT.md` — AI 上下文/思维链
