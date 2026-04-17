# AI 网文写作系统 - 项目白皮书

## 项目概述
基于 AI 的网络小说自动写作系统，支持策划→写作→审稿→导出的完整流水线。

## 技术栈
- **语言**: Python 3.x
- **数据库**: SQLite（WAL 模式）
- **AI 接口**: 通过 core/api_client.py 调用 LLM API
- **配置**: YAML (config.yaml)

## 目录结构
```
d:\novel-ai/
├── main.py              # 主入口，CLI 菜单系统
├── config.yaml          # 全局配置
├── core/
│   ├── utils.py         # 公共工具（连接管理、事务、重试）
│   ├── db.py            # 数据库初始化与连接管理
│   ├── reviewer.py      # 写作+审稿流程（write_and_review）
│   ├── writer.py        # 写作模块
│   ├── reader_reviewer.py  # 读者视角审稿
│   ├── api_client.py    # API 调用封装
│   ├── memory_manager.py # 记忆/状态管理
│   ├── planner.py       # 章节策划器
│   ├── exporter.py      # 章节导出
│   ├── config_loader.py # 配置加载器
│   └── model_manager.py # 模型管理
├── data/{小说名}/       # 每部小说的数据目录
│   ├── novel.db         # SQLite 数据库
│   ├── characters.md    # 人物设定
│   ├── master_outline.md # 总大纲
│   └── ...
├── output/{小说名}/     # 导出文件
└── docs/                # 项目文档
```

## 当前进度

### 已完成功能
- [x] 数据库连接管理统一化（with_db_connection + DatabaseTransaction）
- [x] SQLite WAL 模式 + busy_timeout=5000
- [x] 任务认领原子性（BEGIN IMMEDIATE + 条件 UPDATE）
- [x] 双重审稿机制（责任编辑 + 读者视角）
- [x] **并发安全增强**（2026-04-03）：
  - [x] execute_with_retry 重试函数（指数退避）
  - [x] _claim_task_for_writing 全路径 retry 保护
  - [x] write_and_review 状态机事务保护
- [x] **章节字数控制优化**（2026-04-04）：
  - [x] 新增配置项：chapter_word_min (3000) / chapter_word_max (4000)
  - [x] 提示词明确告知模型字数范围（替代模糊的"大约X字"）
  - [x] max_tokens 动态计算（基于 word_max，而非固定倍数）
  - [x] 字数补写增加上限保护（达到 word_max 时停止）
  - [x] 日志输出显示目标范围和超标/不足警告
- [x] **模型列表更新与默认模型更换**（2026-04-17）：
  - [x] 将 qwen3.6-plus 标记为无额度（has_free_quota=false）
  - [x] 新增8个模型到使用列表：qwen3.6-flash、qwen3.6-flash-2026-04-16、qwen3.6-35b-a3b、glm-5.1、qwen3.6-plus-2026-04-02、gui-plus-2026-02-26、qwen-flash-character-2026-02-26、qwen3.5-35b-a3b
  - [x] 默认模型从 qwen3.6-plus 更换为 qwen3.6-flash（有免费额度）
  - [x] 更新 model_manager.py 的 MODEL_CATEGORIES 以支持新模型分类

### 开发中功能
- （无）

### 待办事项
- （无）

## 核心并发安全机制（2026-04-03 新增）

| 机制 | 文件 | 说明 |
|------|------|------|
| `execute_with_retry()` | core/utils.py | SQL 锁定自动重试，0.1s→0.2s→0.4s 指数退避 |
| `_claim_task_for_writing()` | main.py | BEGIN IMMEDIATE + 全路径 execute_with_retry |
| `_update_status_safe()` | core/reviewer.py | 带重试的状态原子更新 |
| `_increment_retry_safe()` | core/reviewer.py | 带重试的重试计数递增 |
| `write_and_review()` 入口状态标记 | core/reviewer.py | 函数入口标记 'writing'，终态保证 |

## 数据库表
- `novel_info` - 小说元信息
- `characters` - 角色设定
- `chapters` - 章节正文与状态
- `chapter_tasks` - 任务卡（含状态机：pending→in_progress→completed/review_failed）
- `foreshadowing` - 伏笔管理
- `summaries` - 章节摘要
- `model_switch_history` - 模型切换记录
