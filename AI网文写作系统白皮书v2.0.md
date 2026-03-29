# AI 网文写作自动化系统
## 系统白皮书 v2.0

> 本文档描述当前系统的实际实现状态，基于真实代码编写，非设计草案。

---

## 目录

1. 项目概述
2. 系统架构
3. 文件结构
4. 模块详细说明
5. 数据库设计
6. 数据流与执行顺序
7. 配置说明
8. 已实现功能清单
9. 技术选型
10. 成本参考

---

## 1. 项目概述

### 1.1 定位

AI 网文写作自动化系统是一个运行在本地的命令行工具，核心目标是：

**用户只需提供小说名称、类型、关键词和少量人名，系统自动完成从策划到章节导出的全部流程。**

### 1.2 核心能力

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

### 1.3 运行环境

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10/11（已验证） |
| Python | 3.10+ |
| 虚拟环境 | venv |
| 网络 | 国内网络（通义千问）/ 科学上网（Gemini） |

---

## 2. 系统架构

### 2.1 整体流程

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

### 2.2 模块关系图

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

## 3. 文件结构

```
D:\novel-ai\
│
├── main.py                    # 系统入口，总控逻辑
├── config.yaml                # 全局配置文件
├── .env                       # API Key（不入版本控制）
│
├── core\                      # 核心模块目录
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
├── data\                      # 每本书独立一个子目录
│   └── {小说名}\
│       ├── novel.db           # SQLite数据库
│       ├── settings.md        # 世界观（可读副本）
│       ├── characters.md      # 人物档案（可读副本）
│       ├── master_outline.md  # 总大纲
│       ├── foreshadowing.md   # 伏笔追踪表（可读副本）
│       ├── recent_summaries.md # 近期摘要（可读副本）
│       └── style.txt          # 当前风格key
│
├── output\                    # 导出的TXT章节
│   └── {小说名}\
│       ├── 第001章.txt
│       ├── 第002章.txt
│       └── ...
│
└── venv\                      # Python虚拟环境
```

---

## 4. 模块详细说明

### 4.1 `core/api_client.py` — API调用中枢

**职责：** 统一封装所有 API 调用，管理模型选择和费用统计。

**核心功能：**

- `select_model_interactive()` — 启动时交互式选择模型，真实发送测试请求验证可用性
- `call_api()` — 统一调用入口，自动路由到对应 provider
- `_call_dashscope()` — 通义千问调用实现
- `_call_gemini()` — Gemini 调用实现
- `get_session_stats()` / `print_session_stats()` — 费用统计

**支持模型：**

| 编号 | 模型 | 定价（输入/输出，元/百万token） |
|------|------|-------------------------------|
| 1 | qwen-plus | 0.80 / 2.00 |
| 2 | qwen-turbo | 0.30 / 0.60 |
| 3 | qwen-max | 40.00 / 120.00 |
| 4 | qwen-long | 0.50 / 2.00 |
| 5 | gemini-2.0-flash | 免费（需科学上网） |

**关键设计：**
- 所有模块调用 `call_api()` 时不传 `model_name` 参数，自动使用用户在启动时选择的模型
- 费用统计存储在模块级变量 `_session_stats`，整个会话共享
- 失败自动重试，指数退避（1s、2s、4s）

---

### 4.2 `core/config_loader.py` — 配置读取

**职责：** 统一读取 `config.yaml`，提供默认值兜底。

**使用方式：**
```python
from core.config_loader import get as cfg
max_retry = cfg("novel", "max_retry", 3)
```

**配置项说明：**

```yaml
model:
  max_tokens: 4096         # 单次最大输出token
  temperature: 0.85        # 生成温度

novel:
  chapter_word_target: 3000  # 目标字数
  max_retry: 3               # 审稿失败最大重试次数
  recent_summary_count: 5    # 上下文携带的近期摘要数量
  compress_after_chapters: 20 # 超过此数触发摘要压缩
  pre_split_chapters: 50     # 预拆分任务卡数量

paths:
  data_dir: data
  output_dir: output
```

---

### 4.3 `core/db.py` — 数据库管理

**职责：** 初始化数据库、管理连接、清理重复数据。

**核心函数：**
- `init_database(novel_name)` — 建表，每次启动时调用（幂等）
- `get_connection(novel_name)` — 返回 sqlite3 连接，启用 WAL 模式
- `clean_duplicate_chapters(novel_name)` — 清理章节表中的重复记录

**数据库位置：** `data/{小说名}/novel.db`

---

### 4.4 `core/memory_manager.py` — 记忆管理

**职责：** 所有数据库读写的统一入口，同步维护 MD 可读文件。

**核心方法：**

| 方法 | 说明 |
|------|------|
| `save_world_settings()` | 保存世界观到DB + settings.md |
| `load_world_settings()` | 读取世界观 |
| `save_character()` | 保存人物档案到DB + characters.md |
| `update_character_status()` | 更新人物动态状态（每章后调用）|
| `add_foreshadowing()` | 新增伏笔记录 |
| `redeem_foreshadowing()` | 标记伏笔已兑现 |
| `add_summary()` | 保存章节摘要 |
| `load_recent_summaries()` | 读取近期摘要（含压缩摘要）|
| `compress_old_summaries()` | 压缩旧摘要，降低token消耗 |
| `load_context()` | 加载写作所需完整上下文 |

**摘要压缩机制：**

当未压缩摘要数量超过 `compress_after_chapters`（默认20）时，自动触发压缩：
- 取最旧的 N 条摘要
- 调用 AI 合并为一段阶段摘要（200字以内）
- 原记录标记为 `is_compressed=1`
- 写入新的压缩记录

这样无论写多少章，传给 AI 的上下文摘要长度始终可控。

---

### 4.5 `core/planner.py` — 策划模块

**职责：** 新建小说时的一次性策划流程。

**执行顺序（修正后）：**

```
Step 1: 确定大纲
    ├── 选项A：AI根据类型+关键词生成
    └── 选项B：用户在CMD粘贴（输入END结束）

Step 2: 根据大纲生成世界观
    └── 世界观内容与大纲高度绑定，不自由发挥

Step 3: 确定角色名单
    ├── 选项A：AI从大纲中自动提取并确认
    └── 选项B：用户手动输入

Step 4: 根据大纲+世界观生成人物档案

Step 5: 预拆分前50章任务卡（存入chapter_tasks表）
```

**关键函数：**
- `get_outline_choice()` — 大纲来源选择
- `get_characters_choice()` — 角色确认
- `get_style_choice()` — 风格选择（含自定义）
- `split_outline_to_tasks()` — 大纲预拆分为任务卡
- `extend_tasks()` — 任务卡耗尽时自动扩展

---

### 4.6 `core/writer.py` — 写作模块

**职责：** 生成章节正文，支持多种作者风格。

**分段生成机制：**

```
前半段（~1500字）
    system_prompt = 作者风格 prompt
    user_message  = 世界观 + 人物状态 + 伏笔 + 前情摘要 + 任务

后半段（~1500字）
    system_prompt = 作者风格 + 续写要点
    user_message  = 情节目标 + 前半段结尾500字

拼接 → 总字数约3000字
```

**内置作者风格：**

| 编号 | 风格名 | 适合类型 |
|------|--------|---------|
| 1 | 爽文宗师 | 玄幻、打脸爽文 |
| 2 | 悬疑大师 | 悬疑推理、惊悚 |
| 3 | 情感流 | 言情、都市情感 |
| 4 | 热血战斗 | 战斗、兄弟情 |
| 5 | 世界构建者 | 史诗、硬核设定流 |
| 6 | 轻松日常 | 治愈、日常向 |
| 7 | 自定义 | 用户自由描述 |

**情绪节奏标签：**

每章都有情绪标签，影响具体写作策略：

- `铺垫` — 信息密度足，埋钩子
- `冲突` — 矛盾有层次，留悬念
- `爽点` — 有铺垫的爽，配角烘托
- `低谷` — 真实绝望，埋反弹种子
- `反转` — 先建假象，再颠覆

**内容清理：** 生成后自动过滤【标记】、---分割线、**加粗**等 AI 痕迹。

---

### 4.7 `core/reviewer.py` — 审稿模块

**职责：** 对生成的章节进行三级审查，不通过则触发重写。

**三级审查：**

| 级别 | 检查内容 | 不通过处理 |
|------|---------|----------|
| L1 逻辑 | 人物行为是否符合性格、时间线是否自洽、是否有死亡人物复活 | 退回重写 |
| L2 伏笔 | 是否遗忘需要兑现的伏笔、新埋伏笔是否自然 | 定向修复 |
| L3 质量 | 是否有大段注水、文风是否一致 | 润色重写 |

**重写机制：**
- 最多重试 `max_retry` 次（默认3次，读自config.yaml）
- 每次重写时把上次审稿意见追加到情节目标中
- 第3次仍不通过 → `force_approved`，记录日志，不阻塞流程

**审稿 prompt 低温度（0.3）**，输出更稳定的 JSON 格式结果。

---

### 4.8 `core/exporter.py` — 导出模块

**职责：** 将审核通过的章节清理格式后导出为 TXT。

**清理规则：**
- 删除 `【xxx】` 标记
- 删除 `**加粗**` 和 `*斜体*`
- 删除 `---` 分割线
- 压缩多余空行
- 敏感词过滤（词库可自定义扩充）

**导出路径：** `output/{小说名}/第001章.txt`（章节号三位补零）

---

## 5. 数据库设计

所有表存储于 `data/{小说名}/novel.db`（SQLite）。

### 5.1 表结构

#### `novel_info` — 小说基本信息
```sql
id INTEGER PRIMARY KEY
name TEXT NOT NULL
genre TEXT
created_at TIMESTAMP
status TEXT DEFAULT 'active'
```

#### `characters` — 人物档案
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
name TEXT NOT NULL UNIQUE
role TEXT                    -- 主角/配角/反派
appearance TEXT              -- 外貌（冻结字段）
personality TEXT             -- 性格（冻结字段）
secret TEXT                  -- 隐藏秘密（冻结字段）
weakness TEXT                -- 致命弱点（冻结字段）
current_location TEXT        -- 当前位置（每章更新）
current_status TEXT          -- 当前状态（每章更新）
relationships TEXT           -- 关系（JSON，每章更新）
updated_chapter INTEGER      -- 最后更新章节号
```

#### `chapters` — 章节正文
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
chapter_num INTEGER NOT NULL UNIQUE
title TEXT
emotion_tag TEXT
plot_goal TEXT
word_target INTEGER DEFAULT 3000
content TEXT                 -- 完整正文
summary TEXT
status TEXT                  -- pending/draft/approved/force_approved
retry_count INTEGER DEFAULT 0
created_at TIMESTAMP
updated_at TIMESTAMP
```

#### `chapter_tasks` — 章节任务卡（预拆分）
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
chapter_num INTEGER NOT NULL UNIQUE
plot_goal TEXT               -- 本章情节目标
emotion_tag TEXT DEFAULT '铺垫'
status TEXT DEFAULT 'pending'
created_at TIMESTAMP
```

#### `foreshadowing` — 伏笔追踪
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
fid TEXT UNIQUE NOT NULL     -- 伏笔ID，如 F001_1
plant_chapter INTEGER        -- 埋下章节
description TEXT             -- 伏笔描述
expected_redeem TEXT         -- 预计兑现章节
status TEXT DEFAULT 'active' -- active/redeemed
redeemed_chapter INTEGER     -- 实际兑现章节
```

#### `summaries` — 章节摘要
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
chapter_num INTEGER NOT NULL
summary TEXT NOT NULL
is_compressed INTEGER DEFAULT 0  -- 0=详细摘要 1=已压缩/压缩摘要
created_at TIMESTAMP
```

#### `world_settings` — 世界观
```sql
id INTEGER PRIMARY KEY       -- 固定为1
content TEXT NOT NULL
updated_at TIMESTAMP
```

#### `tone_samples` — 风格样本（预留）
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
content TEXT NOT NULL
locked INTEGER DEFAULT 0
```

---

## 6. 数据流与执行顺序

### 6.1 新建小说完整流程

```
用户输入 → planner.run_planner()
    ├── get_outline_choice()     → 写入 master_outline.md
    ├── generate_world()         → 写入 world_settings表 + settings.md
    ├── get_characters_choice()
    ├── generate_characters()    → 写入 characters表 + characters.md
    ├── get_style_choice()       → 写入 style.txt
    └── split_outline_to_tasks() → 写入 chapter_tasks表
```

### 6.2 每章生成完整流程

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

### 6.3 每章API调用次数

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

## 7. 配置说明

### 7.1 `.env` 文件

```env
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx
GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxx
```

### 7.2 `config.yaml` 文件

```yaml
model:
  max_tokens: 4096
  temperature: 0.85

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

## 8. 已实现功能清单

### ✅ 已实现

- [x] 启动时交互式模型选择（含真实可用性验证）
- [x] 显示各模型定价
- [x] 新建小说策划流程（大纲→世界观→角色→风格）
- [x] 自定义大纲输入（CMD粘贴，END结束）
- [x] AI自动从大纲提取角色名单
- [x] 6种内置写作风格 + 自定义风格描述
- [x] 大纲预拆分章节任务卡（50章）
- [x] 任务卡不足时自动扩展
- [x] 分段生成保证字数（前半段 + 后半段）
- [x] 情绪标签差异化写作策略
- [x] 三级审稿机制（L1逻辑/L2伏笔/L3质量）
- [x] 审稿不通过自动重写（最多3次）
- [x] 章节记忆自动提取（摘要/人物状态/伏笔）
- [x] 历史摘要自动压缩（超过20章触发）
- [x] 人物状态动态追踪（每章更新）
- [x] 伏笔追踪表（新增/兑现/状态查询）
- [x] 导出TXT过滤AI标记
- [x] 本次会话费用实时统计
- [x] 进度可视化面板（进度条+字数+伏笔数+任务卡数）
- [x] 查看章节任务卡（菜单选5）
- [x] 中途更换写作风格
- [x] 批量生成（支持Ctrl+C中断）
- [x] 重复章节自动清理
- [x] config.yaml 真正被读取
- [x] 继续写作已有小说

### ⏳ 预留/未启用

- [ ] `tone_samples` 风格样本表（已建表，暂未使用）
- [ ] 平台特定敏感词库（框架已有，词库为空）
- [ ] 自动续写模式（挂机全自动，可基于批量生成实现）

---

## 9. 技术选型

| 层级 | 技术 | 原因 |
|------|------|------|
| 运行时 | Python 3.10 | 稳定，生态丰富 |
| AI调用（国内）| 通义千问 dashscope SDK | 国内直连，免费额度充足 |
| AI调用（海外）| Google Gemini google-genai SDK | 免费层可用 |
| 本地存储 | SQLite | 零配置，单文件，无需服务 |
| 配置管理 | PyYAML | 人类可读，易修改 |
| 环境变量 | python-dotenv | 保护API Key |
| 命令行 | 原生 input/print | 无额外依赖，兼容性最好 |

### 依赖安装

```bash
pip install google-genai dashscope pyyaml python-dotenv
```

---

## 10. 成本参考

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

*文档版本：v2.0 | 基于实际代码生成 | 如代码更新请同步更新本文档*
