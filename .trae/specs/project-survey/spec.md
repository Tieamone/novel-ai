# 项目全景调研 Spec

## Why
对"AI 网文写作系统"进行完整的架构摸底与文档整理，建立单一事实来源的项目知识库，为后续开发、维护和新人上手提供权威参考。

## What Changes
- 无代码变更。本次为只读调研，产出项目全景技术文档
- 记录项目的模块架构、数据流、状态机、并发安全机制、模型管理层等核心设计

## Impact
- Affected specs: 无（纯文档调研）
- Affected code: 无

---

## ADDED Requirements

### Requirement: 项目架构全景
系统 SHALL 提供一份完整的项目架构文档，覆盖所有模块的功能、接口和数据流。

#### Scenario: 新开发者上手
- **WHEN** 新开发者查看项目文档
- **THEN** 能够理解项目的整体架构、各模块职责和模块间调用关系

#### Scenario: 现有功能维护
- **WHEN** 开发者需要修改某个模块
- **THEN** 能够通过文档快速定位相关代码和理解影响范围

---

## 项目全景分析

### 一、项目概况

| 属性 | 值 |
|------|-----|
| 项目名称 | AI 网文写作系统 v2.0 |
| 技术栈 | Python 3.x + SQLite (WAL) + YAML |
| AI 接口 | DashScope (通义千问/GLM/DeepSeek/Kimi) + Mimo (小米) |
| 入口文件 | [main.py](file:///d:/novel-ai/main.py) |
| 配置方式 | config.yaml + .env + data/custom_models.json |

### 二、目录结构

```
d:\novel-ai/
├── main.py                  # 主入口：CLI 菜单 + 章节写作调度 + Ctrl+C 状态回滚
├── config.yaml              # 全局配置（模型、字数、温度、路径）
├── core/                    # 核心模块层
│   ├── __init__.py          # 模块导出（空文件）
│   ├── config_loader.py     # YAML 配置加载 + 多级 key 安全访问
│   ├── db.py                # SQLite 数据库初始化 + 表结构 + 自动迁移
│   ├── utils.py             # 公共工具（DB连接管理器、重试、JSON解析）
│   ├── api_client.py        # API 调用层（DashScope/Mimo）+ 模型管理 + 统计
│   ├── model_manager.py     # 模型发现与分类（custom_models.json）
│   ├── writer.py            # 写作模块：prompt构建、节拍规划、自检修订、主写作函数
│   ├── reviewer.py          # 责任编辑审稿：三层评分 + 伏笔专项 + 一票否决
│   ├── reader_reviewer.py   # 读者视角审稿：AI痕迹检测 + 阅读体验评估
│   ├── memory_manager.py    # 记忆管理：人物/伏笔/摘要/世界观持久化
│   ├── planner.py           # 策划器：大纲生成、世界观、角色、任务卡拆分
│   ├── exporter.py          # 章节导出：格式清理 + 敏感词替换 + 文件输出
│   └── outline_manager.py   # 大纲伏笔管理：CRUD + 交互式菜单
├── data/{小说名}/           # 每部小说独立数据目录
│   ├── novel.db             # SQLite 数据库（含所有表）
│   ├── characters.md        # 人物档案（数据库的 MD 镜像）
│   ├── foreshadowing.md     # 伏笔追踪表
│   ├── master_outline.md    # 全书大纲
│   ├── recent_summaries.md  # 近期摘要
│   ├── settings.md          # 世界观设定
│   ├── style.txt            # 写作风格选择
│   └── target_chapters.txt  # 目标章数
├── output/{小说名}/         # 导出文件目录
├── docs/                    # 项目文档
│   ├── PROJECT_PROFILE.md   # 项目白皮书
│   ├── AI_CONTEXT.md        # AI 思维链记忆体
│   └── AI_ERROR_LOG.md      # 错误知识库
└── .trae/specs/             # 规范文档管理
```

### 三、核心模块详解

#### 3.1 config_loader.py — 配置加载器
- **load_config()**: 加载 config.yaml，缓存全局配置
- **get(section, key, *args)**: 多级 key 安全访问，支持嵌套路径和默认值。每层做 isinstance 检查防止 TypeError
- **get_data_dir() / get_output_dir()**: 基于 PROJECT_ROOT 解析数据/输出目录
- **PROJECT_ROOT**: 基于 `__file__` 推导（`Path(__file__).resolve().parents[1]`），不依赖 CWD

#### 3.2 db.py — 数据库管理
- **get_connection()**: 创建 SQLite 连接，配置 WAL 模式 + busy_timeout=5000
- **init_database()**: 初始化 9 张表 + 索引：
  1. `novel_info` — 小说元信息
  2. `characters` — 角色档案（含 relationships JSON）
  3. `chapters` — 章节正文 + 状态 + 审核分数 + 读者视角结果
  4. `chapter_tasks` — 任务卡（状态机 + 重写追踪）
  5. `foreshadowing` — 动态伏笔追踪
  6. `summaries` — 章节摘要（支持压缩标记）
  7. `world_settings` — 世界观设定
  8. `model_switch_history` — 模型切换记录
  9. `outline_foreshadowing` — 大纲级别伏笔规划
- **_migrate()**: 自动补列，兼容旧数据库结构升级
- **clean_duplicate_chapters()**: 清理重复章节记录

#### 3.3 utils.py — 公共工具
- **with_db_connection(novel_name)**: 上下文管理器，自动关闭连接
- **execute_with_retry(conn, sql, params)**: SQL 执行 + 指数退避重试（0.1s → 0.2s → 0.4s），处理 SQLite 锁定
- **DatabaseTransaction**: 事务管理器，使用 BEGIN IMMEDIATE 防止延迟加锁
- **extract_json_obj(raw)**: 从字符串提取第一个 JSON 对象
- **to_int(value, min, max)**: 安全整数转换 + 范围约束
- **is_transient_error(error)**: 判断是否暂时性错误（可重试）

#### 3.4 api_client.py — API 调用层
这是项目中最大的单文件模块（956 行），承担以下职责：

**模型管理：**
- 三种独立模型：`_author_model`（写作）、`_reviewer_model`（责任编辑审稿）、`_reader_reviewer_model`（读者视角）
- 模型切换函数：`set_author_model()` / `set_reviewer_model()` / `set_reader_reviewer_model()`
- 失败计数器 + 自动切换阈值（`failure_switch_threshold`，默认 3 次）

**API 调用：**
- **call_api()**: 统一入口，根据 provider 分发到 DashScope 或 Mimo
- **call_author_api()** / **call_reviewer_api()** / **call_reader_reviewer_api()**: 三种角色独立调用
- **_call_dashscope()**: OpenAI 兼容接口调用
  - 支持端点自动切换（连接失败时 beijing↔intl 切换）
  - 思考模型识别（`_is_thinking_model()`），对 Qwen3.x 系列关闭 `enable_thinking`
  - 限速自动等待（30s * attempt）
- **_call_mimo()**: 小米 Mimo 模型调用，独立端点 + API Key

**会话统计：**
- 输入/输出 token 计数 + 费用估算
- 免费试用模型定价修正（`FREE_TRIAL_MODEL_NAMES` + `get_model_pricing()`）

**交互式选择：**
- `select_model_interactive()`: 启动时选择作者模型，含可用性验证
- `select_all_models_interactive()`: 高级模式，分别设置三种模型

#### 3.5 model_manager.py — 模型发现
- **discover_all_models()**: 优先读取 `data/custom_models.json`，回退到内置默认列表
- **get_models_for_usage(usage)**: 按用途（author/reviewer/reader_reviewer）筛选合适模型，排除视觉/代码/数学专用模型
- **MODEL_CATEGORIES**: 预定义模型分类映射（cost_effective/balanced/premium/long_context）

#### 3.6 writer.py — 写作模块
这是系统的核心创作引擎（1559 行），包含：

**模型能力检测：**
- **is_high_capacity_model()**: 三级判断逻辑
  1. 小模型关键词优先排除（flash/turbo/lite/mini）
  2. 大模型关键词匹配（plus/max/35b/glm/deepseek/kimi）
  3. 上下文长度兜底（>= 32K）

**提示词系统：**
- **build_full_chapter_prompt()**: 大模型一次性生成整章（第 1-253 行）
- **build_writer_prompt()**: 小模型前半段生成（第 982-1111 行）
- **build_continue_prompt()**: 后半段续写（第 1114-1155 行）
- 进度感知：`_build_progress_block()` 根据章节占比输出阶段指引（开局/发展前期/发展后期/高潮/收尾）
- 大纲感知：`_build_outline_block()` 注入全书大纲摘要
- 人物行为约束：自动提取 OOC 硬约束

**风格系统 (AUTHOR_STYLES)：**
9 种预设写作风格（爽文宗师/悬疑大师/情感流/热血战斗/世界构建者/轻松日常/刘慈欣/金庸/古龙）+ 自定义风格

**情绪标签指南 (EMOTION_GUIDE)：**
5 种情绪节奏（爽点/冲突/反转/低谷/铺垫），每种含具体写作指引

**硬约束与禁止项：**
- `WRITER_HARD_CONSTRAINTS`: 7 条必须满足的规则
- `WRITER_FORBIDDEN_RULES`: 10 条必须避免的 AI 痕迹

**自检修订系统：**
- **AI_PATTERNS**: 35+ 条正则规则检测 AI 写作痕迹
- **_rule_based_ai_check()**: 规则引擎检测 + 连续对话检查
- **_self_check_and_revise()**: 规则检测 → AI 自检（JSON）→ 自动修订
- **clean_content()**: 清理 Markdown + 模板句过滤

**节拍规划 (beat planning)：**
- `BEAT_PLANNER_SYSTEM`: 5-7 条节拍规划提示词
- **_plan_chapter_beats()**: 调用 AI 生成节拍计划
- `_cached_beat_plan`: 重试时复用节拍规划缓存

**主写作函数 write_chapter()：**
```
┌──────────────────────────────────────────────┐
│  load_context (人物/伏笔/摘要/世界观/大纲)     │
│      ↓                                       │
│  读取 style.txt → 确定 system_prompt         │
│      ↓                                       │
│  获取上一章结尾 (prev_chapter_ending)          │
│      ↓                                       │
│  节拍规划 (_plan_chapter_beats, 重试时复用)   │
│      ↓                                       │
│  is_high_capacity_model() 判断生成策略        │
│  ├── 大模型：一次性生成 (build_full_chapter)   │
│  └── 小模型：分段生成 (前半段→后半段)          │
│      ↓                                       │
│  上一章复用检测 (_count_matching_words)        │
│      ↓                                       │
│  字数补写 (最多 3 轮, 有上限保护)              │
│      ↓                                       │
│  自检与修订 (_self_check_and_revise)           │
│      ↓                                       │
│  字数裁剪 (超过 120% 硬上限时自动裁剪)          │
│      ↓                                       │
│  save_chapter (存入数据库)                     │
└──────────────────────────────────────────────┘
```

#### 3.7 reviewer.py — 责任编辑审稿
- **REVIEWER_SYSTEM**: 三层评分体系的完整提示词
  - L1 逻辑与设定一致性 (0-45分)
  - L2 伏笔与剧情承接 (0-25分)
  - L3 可读性与网文节奏 (0-30分)
- **一票否决项**: 核心设定冲突/时间线矛盾/核心OOC/关键伏笔断裂
- **通过条件**: 无否决项 + 总分 >= 75 + L1 >= 30
- **伏笔专项评分 (score_foreshadowing)**: 埋入自然度/兑现完整度/线索可追溯性/积压风险
- **_normalize_review_result()**: 完善的 JSON 结果规范化，含分数校验和失败归因

#### 3.8 write_and_review() — 写作→审稿→重试 完整流程
这是系统的核心状态机（[reviewer.py:L777-L1221](file:///d:/novel-ai/core/reviewer.py#L777-L1221)）：

```
write_chapter
    ↓ success
review_chapter (责任编辑)
    ↓ fail → 重试 (max_retry 次)
    ├── 第1次失败 → 局部修改模式 (revise_chapter)
    ├── 第2+次失败 → 用户选择：继续修改/完全重写/切换模型
    └── 冲突检测 → 任务卡重写菜单 (veto_code 连续命中)
    ↓ pass
reader_review_chapter (读者视角)
    ↓ fail → 重试逻辑（同上）
    ↓ pass
双重审核通过 → 自动兑现逾期伏笔 → 返回 completed
```

**并发安全保障：**
- 入口标记 `'writing'` 状态
- 所有状态变更使用 `execute_with_retry`
- 异常路径保证终态（approved/force_approved/review_failed）
- Ctrl+C 处理器回滚章节状态

**冲突检测系统：**
- 连续 ≥2 次相同 veto_code + 全部 L1/L2 层级 → 触发任务卡冲突菜单
- 支持：基于大纲自动重写、手动输入新目标、切换模型、强制通过

#### 3.9 reader_reviewer.py — 读者视角审稿
- 四维评分：AI真实感/剧情逻辑/前后一致/阅读流畅（各 25 分）
- 敏感于 AI 痕迹：情绪标签/烂俗动作/模板句/对称句式/句子节奏
- 智能截断：`_truncate_content()` 优先保留完整段落，超长时首尾保留
- 上一章只传 1500 字结尾（避免 token 超限）
- 当前章节截断上限 5000 字

#### 3.10 memory_manager.py — 记忆管理
MemoryManager 是系统的持久化核心：

- **世界观**: save/load world_settings（DB + MD 双写）
- **人物**: 批量保存 + 双向关系镜像 + 自动生成 characters.md
- **伏笔**: 
  - `get_foreshadow_hints()`: 智能优先级排序（应兑现>逾期>久悬>待推进>可铺垫），每章最多 8 条
  - `get_foreshadow_report()`: 健康度报告（逾期/即将到期/宏观悬念/趋势）
  - `_auto_redeem_foreshadowing()`: 审核通过后关键词自动兑现
- **摘要**: 压缩机制（每 20 章一批，10 条/批，最终合并为阶段摘要）
- **上下文加载 (load_context)**: 单次 DB 连接批量查询，合并静态文件读取

#### 3.11 planner.py — 策划器
- **run_planner()**: 完整的新书策划流程
  1. 选择篇幅（短/中/长/超长/自定义）
  2. 生成大纲 (call_author_api)
  3. 提取角色名单
  4. 生成世界观设定
  5. 生成角色详细档案
  6. 选择写作风格
  7. 拆分任务卡（split_outline_to_tasks）
- **extend_tasks()**: 动态扩展任务卡（任务卡用完时）
- **rewrite_task_for_chapter()**: 冲突检测后重写指定章节任务卡
- **get_style_choice()**: 交互式风格选择

#### 3.12 exporter.py — 导出器
- **clean_for_export()**: 8 步清理流程（系统标签/加粗/斜体/标题/分隔线/代码/空行/本章完）
- **敏感词替换**: 使用 □ 字符替代，支持正则批量匹配（词表 >= 100 时）
- **_sanitize_path_name()**: Windows 文件名安全处理
- 导出格式：`output/{小说名}/第XXX章.txt`

#### 3.13 outline_manager.py — 大纲伏笔管理
- `outline_foreshadowing` 表的完整 CRUD 操作
- 交互式管理菜单（查看/新增/编辑/删除）
- 状态流转：planned → planted → resolved
- **get_chapter_outline_tasks()**: 供任务卡生成时注入章节级伏笔任务
- **mark_outline_foreshadow_status()**: 审稿通过时自动更新状态

#### 3.14 main.py — 主入口
- **main()**: 启动 → 选模型 → 主菜单循环
- **chapters_menu()**: 15 个功能的章节管理菜单
- **generate_chapter_auto()**: 自动写下一章（找最小待处理章节 → 认领任务 → write_and_review → 导出）
- **Ctrl+C 处理器**: 注册 SIGINT 信号，回滚章节状态为"待处理"
- **日志系统**: 控制台 INFO + 文件 DEBUG（logs/run.log）
- **小说管理**: 新建（交互式+newbook.txt 导入）/ 继续写作 / 删除（二次确认）
- **章节管理**: 删除/批量删除/恢复审稿失败/任务卡编辑/伏笔报告

### 四、数据流总览

```
用户输入 (CLI)
    ↓
main.py (菜单调度)
    ↓
┌──────────────────────────────────────────────────┐
│  planner.py    →  大纲/世界观/角色/任务卡         │
│  writer.py     →  章节正文生成                    │
│  reviewer.py   →  责任编辑审稿 (L1/L2/L3)        │
│  reader_reviewer.py →  读者视角审稿               │
│  memory_manager.py → 数据持久化 + 上下文加载      │
│  exporter.py   →  文件导出                        │
└──────────────────────────────────────────────────┘
    ↕
api_client.py → DashScope/Mimo API
    ↕
model_manager.py → 模型发现与分类
    ↕
SQLite DB (data/{小说名}/novel.db)
```

### 五、章节状态机

```
任务卡状态流转:
  pending (待处理) → in_progress (进行中) → completed (已完成)
                                           → review_failed (审稿失败)

章节状态流转:
  (空) → writing → 草稿 → 已审核  (双重审核通过)
                        → 强制通过  (用户手动通过)
                        → 审稿失败  (API 异常/审核连续失败)
                        → 草稿(有问题)  (检测到复用)
```

### 六、技术亮点

1. **并发安全**: WAL 模式 + BEGIN IMMEDIATE + execute_with_retry 三重保护
2. **智能模型选择**: 自动检测模型能力（大模型一次性 / 小模型分段），flash 系列精确排除
3. **去 AI 化系统**: 35+ 条正则规则 + AI 自检 + 自动修订 + 反面教材示例
4. **节拍规划缓存**: 重试时复用节拍计划，节省 token
5. **伏笔智能管理**: 优先级排序 + 沉睡检测 + 自动兑现 + 健康度报告
6. **摘要压缩**: 多批合并 + 阶段摘要，防止上下文爆炸
7. **端点自动切换**: DashScope 连接失败时 beijing↔intl 自动切换
8. **优雅中断**: SIGINT 处理器回滚章节状态 + 打印费用统计
9. **向后兼容**: 数据库自动迁移补列 + 配置缺省值回退

### 七、外部依赖

| 依赖 | 用途 |
|------|------|
| `openai` | DashScope/Mimo 的 OpenAI 兼容接口调用 |
| `dashscope` | DashScope 原生 SDK（openai 不可用时的兜底） |
| `pyyaml` | config.yaml 解析 |
| `python-dotenv` | .env 环境变量加载 |
| `sqlite3` | 数据持久化（Python 内置） |