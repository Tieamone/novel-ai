# Code Wiki - AI网文写作系统

## 目录
1. [项目概述](#项目概述)
2. [系统架构](#系统架构)
3. [文件结构](#文件结构)
4. [核心模块详解](#核心模块详解)
5. [数据库设计](#数据库设计)
6. [配置说明](#配置说明)
7. [运行方式](#运行方式)
8. [依赖关系](#依赖关系)

---

## 项目概述

### 项目定位
AI网文写作自动化系统是一个运行在本地的命令行工具，核心目标是：**用户只需提供小说名称、类型、关键词和少量人名，系统自动完成从策划到章节导出的全部流程。**

### 核心能力
- 支持自定义大纲或 AI 自动生成大纲
- 根据大纲自动生成世界观、人物档案
- 预拆分章节任务卡，写作时直接读取，无需每次重新分析大纲
- 分段生成章节（前半段 + 后半段），保证每章 3000 字左右
- 多轮审稿机制，不通过自动重写，最多重试 3 次
- 章节记忆自动提取（摘要、人物状态、伏笔追踪）
- 超过阈值自动压缩历史摘要，保障长篇性能
- 导出干净的 TXT 文件，过滤所有 AI 标记
- 支持多模型切换，启动时实际验证可用性
- 实时费用统计，精确到每次 API 调用

### 技术栈
- **运行时**: Python 3.10+
- **AI调用（国内）**: 通义千问 dashscope SDK
- **AI调用（海外）**: Google Gemini google-genai SDK
- **本地存储**: SQLite
- **配置管理**: PyYAML
- **环境变量**: python-dotenv

---

## 系统架构

### 整体流程

```
用户启动
    ↓
选择模型（验证可用性）
    ↓
新建小说 / 继续写作
    ↓
【新建流程】
    ↓
输入基本信息（名称、类型、关键词）
    ↓
Step 1：确定大纲（自定义粘贴 或 AI生成）
    ↓
Step 2：生成世界观（根据大纲）
    ↓
Step 3：确定角色（AI从大纲提取 或 手动输入）→ 生成人物档案
    ↓
Step 4：选择写作风格（预设6种 或 自定义描述）
    ↓
Step 5：预拆分前50章任务卡（存入数据库）
    ↓
【写作循环】
    ↓
读取任务卡 → 生成前半段 → 生成后半段 → 拼接（~3000字）
    ↓
审稿（L1逻辑 + L2伏笔 + L3质量）
    ↓
通过 → 提取记忆 → 导出TXT
不通过 → 重写（最多3次）→ 第3次强制通过
    ↓
摘要压缩检查（超过20章触发）
    ↓
进入下一章
```

### 模块关系图

```
main.py（总控）
    ├── core/api_client.py      ← 所有模块调用API都通过这里
    ├── core/planner.py         ← 仅新建小说时调用
    ├── core/writer.py          ← 每章写作时调用
    ├── core/reviewer.py        ← 每章审稿时调用
    ├── core/memory_manager.py  ← 每章完成后调用
    ├── core/exporter.py        ← 每章通过后调用
    ├── core/db.py              ← 被memory_manager调用
    └── core/config_loader.py   ← 被所有模块调用
```

---

## 文件结构

```
/workspace/
├── main.py                    # 系统入口，总控逻辑
├── config.yaml                # 全局配置文件
├── .env                       # API Key（不入版本控制）
├── sensitive_words.txt        # 敏感词词库
├── AI网文写作系统白皮书v2.0.md  # 系统白皮书
├── CHANGELOG.md               # 变更日志
├── README.md                  # 项目说明
├── CODE_WIKI.md              # 本文档
│
├── core/                      # 核心模块目录
│   ├── __init__.py
│   ├── api_client.py          # API调用封装 + 费用统计 + 模型选择
│   ├── config_loader.py       # 配置文件读取
│   ├── db.py                  # 数据库初始化 + 连接管理
│   ├── memory_manager.py      # 记忆读写 + 摘要压缩
│   ├── planner.py             # 策划模块（大纲/世界观/角色/任务卡）
│   ├── writer.py              # 写作模块（分段生成 + 风格系统）
│   ├── reviewer.py            # 审稿模块（三级审查 + 重写机制）
│   └── exporter.py            # 导出模块（清理 + TXT输出）
│
├── data/                      # 每本书独立一个子目录
│   └── {小说名}/
│       ├── novel.db           # SQLite数据库
│       ├── settings.md        # 世界观（可读副本）
│       ├── characters.md      # 人物档案（可读副本）
│       ├── master_outline.md  # 总大纲
│       ├── foreshadowing.md   # 伏笔追踪表（可读副本）
│       ├── recent_summaries.md # 近期摘要（可读副本）
│       └── style.txt          # 当前风格key
│
└── output/                    # 导出的TXT章节
    └── {小说名}/
        ├── 第001章.txt
        ├── 第002章.txt
        └── ...
```

---

## 核心模块详解

### 1. main.py - 主入口模块

**职责**: 系统总控，负责用户交互、流程调度和状态管理。

**核心函数**:

| 函数名 | 说明 |
|--------|------|
| `main()` | 程序主入口，显示主菜单 |
| `setup_novel()` | 设置新小说基本信息 |
| `chapters_menu()` | 章节操作菜单 |
| `generate_chapter_auto()` | 自动生成下一章 |
| `show_progress()` | 显示小说进度面板 |
| `_list_novels()` | 列出已有小说 |
| `_delete_novel()` | 删除小说 |
| `_recover_review_failed()` | 恢复审稿失败的章节 |

**关键变量**:
- `TASK_PENDING` / `TASK_IN_PROGRESS` / `TASK_COMPLETED`: 任务状态常量

**参考文件**: [main.py](file:///workspace/main.py)

---

### 2. core/api_client.py - API调用中枢

**职责**: 统一封装所有 API 调用，管理模型选择和费用统计。

**核心函数**:

| 函数名 | 说明 |
|--------|------|
| `select_model_interactive()` | 启动时交互式选择模型，真实发送测试请求验证可用性 |
| `call_api()` | 统一调用入口，自动路由到对应 provider |
| `_call_dashscope()` | 通义千问调用实现 |
| `_call_gemini()` | Gemini 调用实现 |
| `get_session_stats()` / `print_session_stats()` | 费用统计 |
| `get_current_model()` | 获取当前选择的模型 |

**支持模型**:

| 编号 | 模型 | 定价（输入/输出，元/百万token） |
|------|------|-------------------------------|
| 1 | qwen-plus | 0.80 / 2.00 |
| 2 | qwen-turbo | 0.30 / 0.60 |
| 3 | qwen-max | 40.00 / 120.00 |
| 4 | qwen-long | 0.50 / 2.00 |
| 5 | gemini-2.0-flash | 免费（需科学上网） |

**关键设计**:
- 所有模块调用 `call_api()` 时不传 `model_name` 参数，自动使用用户在启动时选择的模型
- `temperature` 由各业务调用点显式控制，`call_api()` 仅保留兜底默认值
- 费用统计存储在模块级变量 `_session_stats`，整个会话共享
- 失败自动重试，指数退避（1s、2s、4s）

**参考文件**: [api_client.py](file:///workspace/core/api_client.py)

---

### 3. core/planner.py - 策划模块

**职责**: 新建小说时的一次性策划流程。

**执行顺序**:
1. 大纲 → 2. 角色名单 → 3. 世界观（含角色信息）→ 4. 人物档案 → 5. 风格 → 6. 任务卡

**核心函数**:

| 函数名 | 说明 |
|--------|------|
| `run_planner()` | 主策划流程入口 |
| `get_outline_choice()` | 大纲来源选择（AI生成或用户输入） |
| `get_characters_choice()` | 角色确认（AI提取或手动输入） |
| `generate_world()` | 生成世界观设定 |
| `generate_characters()` | 生成人物档案 |
| `get_style_choice()` | 风格选择（含自定义） |
| `split_outline_to_tasks()` | 大纲预拆分为任务卡 |
| `extend_tasks()` | 任务卡耗尽时自动扩展 |

**参考文件**: [planner.py](file:///workspace/core/planner.py)

---

### 4. core/writer.py - 写作模块

**职责**: 生成章节正文，支持多种作者风格。

**分段生成机制**:
```
前半段（~1500字）
    system_prompt = 作者风格 prompt
    user_message  = 世界观 + 人物状态 + 伏笔 + 前情摘要 + 任务

后半段（~1500字）
    system_prompt = 作者风格 + 续写要点
    user_message  = 情节目标 + 前半段结尾500字

拼接 → 总字数约3000字
```

**内置作者风格**:

| 编号 | 风格名 | 适合类型 |
|------|--------|---------|
| 1 | 爽文宗师 | 玄幻、打脸爽文 |
| 2 | 悬疑大师 | 悬疑推理、惊悚 |
| 3 | 情感流 | 言情、都市情感 |
| 4 | 热血战斗 | 战斗、兄弟情 |
| 5 | 世界构建者 | 史诗、硬核设定流 |
| 6 | 轻松日常 | 治愈、日常向 |
| 7 | 自定义 | 用户自由描述 |

**情绪节奏标签**:
- `铺垫` — 信息密度足，埋钩子
- `冲突` — 矛盾有层次，留悬念
- `爽点` — 有铺垫的爽，配角烘托
- `低谷` — 真实绝望，埋反弹种子
- `反转` — 先建假象，再颠覆

**核心函数**:

| 函数名 | 说明 |
|--------|------|
| `write_chapter()` | 主写作函数，调用前半段+后半段生成 |
| `build_writer_prompt()` | 构建前半段写作提示 |
| `build_continue_prompt()` | 构建后半段续写提示 |
| `_plan_chapter_beats()` | 规划章节节拍 |
| `_self_check_and_revise()` | 自检与自动修订 |
| `clean_content()` | 清理AI生成内容的标记 |

**字数策略**:
- 章节目标字数来自 `config.yaml` 的 `chapter_word_target`
- 最低阈值为目标字数的 85%
- 不足时最多补写 2 轮
- 不做强制截断，避免误伤结尾内容

**参考文件**: [writer.py](file:///workspace/core/writer.py)

---

### 5. core/reviewer.py - 审稿模块

**职责**: 对生成的章节进行三级审查，不通过则触发重写。

**三级审查**:

| 级别 | 检查内容 | 不通过处理 |
|------|---------|----------|
| L1 逻辑 | 人物行为是否符合性格、时间线是否自洽、是否有死亡人物复活 | 退回重写 |
| L2 伏笔 | 是否遗忘需要兑现的伏笔、新埋伏笔是否自然 | 定向修复 |
| L3 质量 | 是否有大段注水、文风是否一致 | 润色重写 |

**评分标准**:
- 总分: 0-100分（通过线: 75分）
- L1: 0-45分（通过线: 30分）
- L2: 0-25分
- L3: 0-30分

**一票否决项**:
1. 核心设定冲突（setting_conflict）
2. 重大时间线矛盾（timeline_break）
3. 主角或核心角色严重OOC（core_ooc）
4. 关键承诺伏笔被硬性遗忘且导致断裂（critical_payoff_missing）

**重写机制**:
- 最多重试 `max_retry` 次（默认3次，读自config.yaml）
- 每次重写时把上次审稿意见追加到情节目标中
- 第3次仍不通过 → `force_approved`，记录日志，不阻塞流程

**核心函数**:

| 函数名 | 说明 |
|--------|------|
| `write_and_review()` | 写作+审稿主循环 |
| `review_chapter()` | 执行审稿并返回结果 |
| `build_review_prompt()` | 构建审稿提示 |
| `_normalize_review_result()` | 标准化审稿返回结果 |

**参考文件**: [reviewer.py](file:///workspace/core/reviewer.py)

---

### 6. core/memory_manager.py - 记忆管理

**职责**: 所有数据库读写的统一入口，同步维护 MD 可读文件。

**核心类**: `MemoryManager`

**核心方法**:

| 方法 | 说明 |
|------|------|
| `save_world_settings()` / `load_world_settings()` | 保存/读取世界观 |
| `save_character()` / `load_characters()` | 保存/读取人物档案 |
| `update_character_status()` | 更新人物动态状态（每章后调用） |
| `update_character_relationship()` | 双向更新人物关系 |
| `add_foreshadowing()` | 新增伏笔记录 |
| `redeem_foreshadowing()` | 标记伏笔已兑现 |
| `load_active_foreshadowing()` | 读取未兑现伏笔 |
| `add_summary()` | 保存章节摘要 |
| `load_recent_summaries()` | 读取近期摘要（含压缩摘要） |
| `compress_old_summaries()` | 压缩旧摘要，降低token消耗 |
| `load_context()` | 加载写作所需完整上下文 |
| `get_last_chapter_ending()` | 获取上一章结尾用于衔接 |
| `save_chapter()` / `load_chapter()` | 保存/读取章节 |
| `update_chapter_status()` / `update_chapter_summary()` | 更新章节状态/摘要 |

**摘要压缩机制**:
当未压缩摘要数量超过 `compress_after_chapters`（默认20）时，自动触发压缩：
- 取最旧的 N 条摘要
- 调用 AI 合并为一段阶段摘要（200字以内）
- 原记录标记为 `is_compressed=1`
- 写入新的压缩记录

**参考文件**: [memory_manager.py](file:///workspace/core/memory_manager.py)

---

### 7. core/exporter.py - 导出模块

**职责**: 将审核通过的章节清理格式后导出为 TXT。

**清理规则**:
- 删除 `【xxx】` 标记
- 删除 `**加粗**` 和 `*斜体*`
- 删除 `---` 分割线
- 压缩多余空行
- 敏感词过滤（读取根目录 `sensitive_words.txt`，词库可自定义扩充）

**核心函数**:

| 函数名 | 说明 |
|--------|------|
| `export_chapter()` | 导出单章为TXT |
| `export_all()` | 批量导出所有已审核章节 |
| `clean_for_export()` | 清理导出内容 |
| `load_sensitive_words()` | 加载敏感词词库 |

**导出路径**: `paths.output_dir/{小说名}/第001章.txt`（章节号三位补零）

**参考文件**: [exporter.py](file:///workspace/core/exporter.py)

---

### 8. core/config_loader.py - 配置读取

**职责**: 统一读取 `config.yaml`，提供默认值兜底。

**核心函数**:

| 函数名 | 说明 |
|--------|------|
| `load_config()` | 加载配置文件 |
| `get(section, key, default)` | 获取配置项 |
| `get_data_dir(novel_name)` | 获取数据目录 |
| `get_output_dir(novel_name)` | 获取输出目录 |

**使用方式**:
```python
from core.config_loader import get as cfg
max_retry = cfg("novel", "max_retry", 3)
```

**参考文件**: [config_loader.py](file:///workspace/core/config_loader.py)

---

### 9. core/db.py - 数据库管理

**职责**: 初始化数据库、管理连接、清理重复数据。

**核心函数**:

| 函数名 | 说明 |
|--------|------|
| `init_database(novel_name)` | 建表并对旧版数据库执行补列迁移（幂等） |
| `get_connection(novel_name)` | 返回 sqlite3 连接，启用 WAL 模式 |
| `ensure_database(novel_name)` | 确保数据库已初始化 |
| `clean_duplicate_chapters(novel_name)` | 清理章节表中的重复记录 |
| `_migrate(conn, cursor)` | 安全补列迁移逻辑 |

**数据库位置**: `paths.data_dir/{小说名}/novel.db`

**参考文件**: [db.py](file:///workspace/core/db.py)

---

## 数据库设计

所有表存储于 `paths.data_dir/{小说名}/novel.db`（SQLite）。

### 表结构

#### `novel_info` — 小说基本信息
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY | 主键 |
| name | TEXT NOT NULL | 小说名称 |
| genre | TEXT | 小说类型 |
| created_at | TIMESTAMP | 创建时间 |
| status | TEXT | 状态（默认'active'） |

#### `characters` — 人物档案
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | 主键 |
| name | TEXT NOT NULL UNIQUE | 角色名 |
| role | TEXT | 主角/配角/反派 |
| appearance | TEXT | 外貌（冻结字段） |
| personality | TEXT | 性格（冻结字段） |
| secret | TEXT | 隐藏秘密（冻结字段） |
| weakness | TEXT | 致命弱点（冻结字段） |
| current_location | TEXT | 当前位置（每章更新） |
| current_status | TEXT | 当前状态（每章更新） |
| relationships | TEXT | 关系（JSON，每章更新） |
| updated_chapter | INTEGER | 最后更新章节号 |

#### `chapters` — 章节正文
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | 主键 |
| chapter_num | INTEGER NOT NULL UNIQUE | 章节号 |
| title | TEXT | 标题 |
| emotion_tag | TEXT | 情绪标签 |
| plot_goal | TEXT | 情节目标 |
| word_target | INTEGER | 目标字数 |
| content | TEXT | 完整正文 |
| summary | TEXT | 章节摘要 |
| status | TEXT | pending/draft/approved/force_approved |
| retry_count | INTEGER | 重试次数 |
| review_score_total | INTEGER | 审稿总分 |
| review_score_l1 | INTEGER | L1评分 |
| review_score_l2 | INTEGER | L2评分 |
| review_score_l3 | INTEGER | L3评分 |
| review_veto_items | TEXT | 一票否决项（JSON） |
| review_failure_attribution | TEXT | 失败归因（JSON） |
| review_updated_at | TIMESTAMP | 审稿更新时间 |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |

#### `chapter_tasks` — 章节任务卡（预拆分）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | 主键 |
| chapter_num | INTEGER NOT NULL UNIQUE | 章节号 |
| plot_goal | TEXT | 本章情节目标 |
| emotion_tag | TEXT | 情绪标签 |
| status | TEXT | pending/in_progress/completed/review_failed |
| created_at | TIMESTAMP | 创建时间 |

#### `foreshadowing` — 伏笔追踪
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | 主键 |
| fid | TEXT UNIQUE NOT NULL | 伏笔ID，如 F001_1 |
| plant_chapter | INTEGER | 埋下章节 |
| description | TEXT | 伏笔描述 |
| expected_redeem | TEXT | 预计兑现章节 |
| status | TEXT | active/redeemed |
| redeemed_chapter | INTEGER | 实际兑现章节 |

#### `summaries` — 章节摘要
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | 主键 |
| chapter_num | INTEGER NOT NULL | 章节号 |
| summary | TEXT NOT NULL | 摘要内容 |
| is_compressed | INTEGER | 0=详细摘要 1=已压缩/压缩摘要 |
| created_at | TIMESTAMP | 创建时间 |

#### `world_settings` — 世界观
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PRIMARY KEY | 固定为1 |
| content | TEXT NOT NULL | 世界观内容 |
| updated_at | TIMESTAMP | 更新时间 |

---

## 配置说明

### .env 文件
```env
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx
GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxx
```

### config.yaml 文件
```yaml
model:
  max_tokens: 4096

novel:
  chapter_word_target: 3000    # 可调整目标字数
  max_retry: 3                 # 审稿重试上限
  recent_summary_count: 5      # 上下文携带摘要数
  compress_after_chapters: 20  # 压缩阈值
  pre_split_chapters: 50       # 任务卡预生成数量

paths:
  data_dir: data
  output_dir: output
```

---

## 运行方式

### 环境准备
1. Python 3.10+
2. 安装依赖:
```bash
pip install google-genai dashscope pyyaml python-dotenv
```
3. 创建 `.env` 文件并配置 API Key

### 启动程序
```bash
python main.py
```

### 使用流程
1. 选择写作模型（系统会验证可用性）
2. 选择"新建小说"或"继续写作"
3. 新建时按提示完成策划流程
4. 进入章节菜单开始写作

---

## 依赖关系

### 模块依赖图
```
main.py
├── api_client.py (被所有模块调用)
├── planner.py
│   ├── api_client.py
│   ├── memory_manager.py
│   └── config_loader.py
├── writer.py
│   ├── api_client.py
│   ├── memory_manager.py
│   └── config_loader.py
├── reviewer.py
│   ├── api_client.py
│   ├── memory_manager.py
│   ├── writer.py
│   └── config_loader.py
├── memory_manager.py
│   ├── db.py
│   └── config_loader.py
├── exporter.py
│   ├── memory_manager.py
│   └── db.py
├── db.py
│   └── config_loader.py
└── config_loader.py
```

### Python包依赖
- `google-genai` - Google Gemini API
- `dashscope` - 阿里云通义千问API
- `pyyaml` - YAML配置文件解析
- `python-dotenv` - 环境变量管理

---

## 数据流与执行顺序

### 新建小说完整流程
```
用户输入 → planner.run_planner()
    ├── get_outline_choice()     → 写入 master_outline.md
    ├── generate_world()         → 写入 world_settings表 + settings.md
    ├── get_characters_choice()
    ├── generate_characters()    → 写入 characters表 + characters.md
    ├── get_style_choice()       → 写入 style.txt
    └── split_outline_to_tasks() → 写入 chapter_tasks表
```

### 每章生成完整流程
```
main.generate_chapter_auto()
    ├── get_next_chapter_goal()
    │   ├── 优先读 chapter_tasks表
    │   ├── 不足则 extend_tasks() 自动扩展
    │   └── 兜底实时AI分析
    │
    ├── reviewer.write_and_review()
    │   ├── writer.write_chapter()
    │   │   ├── memory_manager.load_context()   ← 读取上下文
    │   │   ├── 生成前半段（API调用#1）
    │   │   ├── 生成后半段（API调用#2）
    │   │   └── memory_manager.save_chapter()   ← 保存草稿
    │   │
    │   ├── reviewer.review_chapter()           ← API调用#3
    │   │   └── 返回 {pass, score, issues}
    │   │
    │   ├── 通过 → update_chapter_status('approved')
    │   └── 不通过 → 重写（最多3次）
    │
    ├── _save_chapter_memory()                  ← API调用#4
    │   ├── 提取摘要 + 人物更新 + 伏笔变动
    │   ├── memory_manager.add_summary()
    │   ├── memory_manager.update_character_status()
    │   ├── memory_manager.add/redeem_foreshadowing()
    │   └── _trigger_compression()              ← 按需压缩
    │
    └── exporter.export_chapter()              ← 导出TXT
```

### 每章API调用次数

| 调用 | 用途 | 模型温度 |
|------|------|---------|
| #1 | 生成前半段正文 | 0.90 |
| #2 | 生成后半段正文 | 0.90 |
| #3 | 审稿 | 0.30 |
| #4 | 提取记忆/摘要 | 0.20 |
| (+) | 重写（不通过时） | 0.90 |
| (+) | 摘要压缩（每20章一次）| 0.30 |

**正常情况每章4次API调用，重写时最多增加3次。**

---

## 成本参考

以 `qwen-plus` 模型为例，生成一章（约3000字）的估算消耗：

| 调用 | 输入token | 输出token | 费用（元） |
|------|----------|----------|-----------|
| 前半段生成 | ~1500 | ~800 | ~0.003 |
| 后半段生成 | ~600 | ~800 | ~0.002 |
| 审稿 | ~2000 | ~300 | ~0.002 |
| 记忆提取 | ~1500 | ~200 | ~0.002 |
| **合计/章** | | | **~0.009元** |

**100章（约30万字）预估费用：约 0.9 元**

> 实际费用以系统内费用统计为准，qwen-max 约贵50倍，qwen-turbo 约便宜3倍。

---

*文档版本: v1.0 | 最后更新: 2026-04-04*
