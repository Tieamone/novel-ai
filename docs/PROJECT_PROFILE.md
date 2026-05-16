# AI 网文写作系统 - 项目白皮书

## 1. 项目概况

| 属性         | 说明                                                         |
| ------------ | ------------------------------------------------------------ |
| 项目类型     | CLI 桌面应用（Python，无精确版本锁）                         |
| 总代码量     | ~7500+ 行（14 个核心模块）                                   |
| 数据库       | SQLite WAL 模式，每部小说独立 `novel.db` 文件                |
| AI 后端      | DashScope（阿里百炼）+ Mimo（小米）双 Provider，共 14 个可用模型 |
| 核心模块数   | 14 个                                                        |
| 数据库表数   | 11 张                                                        |
| 主要入口     | `main.py`（2000+ 行 CLI 菜单系统）                           |
| 配置管理     | `config.yaml`（YAML 格式，单例缓存加载）                     |

---

## 2. 目录结构

```
novel-ai/
├── main.py                   # CLI 主入口，菜单系统，2000+ 行
├── config.yaml                # 全局配置文件
├── .env                       # 环境变量（API Key 等）
├── sensitive_words.txt        # 敏感词列表（导出时替换）
├── newbook.txt                # 批量导入用小说信息模板
├── core/
│   ├── api_client.py          # AI API 统一调用层（950+ 行）
│   ├── writer.py              # 章节写作模块（1650+ 行）
│   ├── reviewer.py            # 审稿 + 写作流程 write_and_review（1250+ 行）
│   ├── planner.py             # 策划编排模块（1100+ 行）
│   ├── memory_manager.py      # 数据持久化与记忆管理（940+ 行）
│   ├── outline_manager.py     # 大纲伏笔管理模块（660+ 行）
│   ├── reader_reviewer.py     # 读者视角审稿（318 行）
│   ├── model_manager.py       # 模型发现与管理（230 行）
│   ├── db.py                  # 数据库初始化、连接管理、迁移系统（330 行）
│   ├── config_loader.py       # 配置加载器（120 行）
│   ├── exporter.py            # 章节导出模块（140 行）
│   └── utils.py               # 公共工具函数（150 行）
├── data/                      # 每部小说的独立数据目录
│   └── {小说名}/
│       ├── novel.db            # SQLite 数据库
│       ├── master_outline.md   # 总大纲
│       ├── characters.md       # 人物档案（自动刷新）
│       ├── settings.md         # 世界观设定
│       ├── foreshadowing.md    # 伏笔追踪表
│       ├── recent_summaries.md # 近期章节摘要
│       ├── style.txt           # 写作风格编号
│       └── target_chapters.txt # 目标章数
├── output/                    # 导出文件目录
│   └── {小说名}/
│       └── 第XXX章.txt
└── docs/                      # 项目文档
    ├── PROJECT_PROFILE.md      # 项目白皮书（本文件）
    ├── AI_CONTEXT.md           # AI 思维链/记忆体
    └── AI_ERROR_LOG.md         # 错误知识库
```

---

## 3. 所有功能流程

### 3.1 新建小说完整流程

**入口**：`main()` [main.py:L1856-L1916](file:///d:/novel-ai/main.py#L1856-L1916) → 选择「新建小说」→ 交互式向导 或 从 `newbook.txt` 导入

**交互式向导调用链**：

```
main() → setup_novel()                                         # [main.py:L1565]
  ↓ 输入：novel_name, genre, keywords
init_database(novel_name)                                      # [core/db.py:L30]
  ↓ 创建 11 张表 + 索引
_write_novel_info(novel_name, genre)                           # [main.py:L1578]
  ↓ 写入 novel_info 表
run_planner(novel_name, genre, keywords)                       # [core/planner.py:L1041]
  ↓
  ├─ Step 0: _choose_novel_length()                           # [core/planner.py:L359]
  │   ↓ 输出：target_chapters (40/80/150/300/自定义)
  │   ↓ 写入 target_chapters.txt
  │
  ├─ Step 1: get_outline_choice(genre, keywords, ...)         # [core/planner.py:L392]
  │   ↓ 调用 call_author_api() 生成总大纲
  │   ↓ 输出：outline (文本)
  │   ↓ 写入 master_outline.md
  │
  ├─ Step 1.5: _choose_draft_review_mode()                    # [core/planner.py:L278]
  │   ↓ 输出：review_mode (bool)
  │
  ├─ Step 1.6: generate_outline_foreshadow(novel_name, ...)   # [core/outline_manager.py:L349]
  │   ↓ 调用 ai_suggest_outline_foreshadow() 或直接 AI 生成
  │   ↓ 输出：20-30 条大纲伏笔 → outline_foreshadowing 表
  │
  ├─ Step 2: get_characters_choice(outline)                   # [core/planner.py:L431]
  │   ↓ 调用 call_author_api() 提取或手动输入角色名单
  │   ↓ 输出：character_names (list)
  │
  ├─ Step 3: generate_world(novel_name, genre, ...)           # [core/planner.py:L489]
  │   ↓ 调用 call_author_api() 生成世界观
  │   ↓ mm.save_world_settings(world) → world_settings 表
  │   ↓ 输出：world (str)
  │
  ├─ Step 4: generate_characters(names, outline, world, ...)  # [core/planner.py:L512]
  │   ↓ 调用 call_author_api() 生成角色档案 (JSON)
  │   ↓ mm.save_characters_batch() → characters 表
  │   ↓ 输出：characters (list of dict)
  │
  ├─ Step 5: get_style_choice()                               # [core/planner.py:L616]
  │   ↓ 选择 AUTHOR_STYLES 中的风格编号 或 自定义
  │   ↓ 输出：style_key (str)
  │
  └─ Step 6: split_outline_to_tasks(outline, novel_name, ...) # [core/planner.py:L663]
      ↓ 调用 call_author_api() 生成任务卡 (JSON)
      ↓ 写入 chapter_tasks 表
      ↓ 输出：saved 条任务卡

→ style_path.write_text(style_key)                             # [main.py:L1939]
→ chapters_menu(novel_name)                                    # 进入章节菜单
```

**从 newbook.txt 导入流程**：

```
main() → _import_from_text_file()                              # [main.py:L1607]
  ↓ 解析 newbook.txt → {novel_name, genre, outline, characters_text, world_setting, ...}
  ↓ init_database() → _write_novel_info()
  ↓ 写入大纲、世界观、角色档案
  ↓ get_style_choice() → style_key
  ↓ generate_outline_foreshadow(novel_name, target_chapters)   # [core/outline_manager.py:L375]
  │   ↓ 读取 master_outline.md → AI 生成 20-30 条伏笔建议
  │   ↓ review_outline_foreshadow() 审稿过滤
  │   ↓ 写入 outline_foreshadowing 表
  ↓ split_outline_to_tasks(..., full_batch=True)
  ↓ → chapters_menu()
```

### 3.2 续写 / 自动生成流程

**入口**：`chapters_menu(novel_name)` [main.py:L1430](file:///d:/novel-ai/main.py#L1430) → 选择「1. 自动生成下一章」

**完整调用链**：

```
chapters_menu → generate_chapter_auto(novel_name)              # [main.py:L429]
  ↓
  ├─ 从 chapter_tasks 找 MIN(chapter_num) WHERE status IN ('待处理')
  │   兜底：MAX(chapters.chapter_num) + 1
  │
  ├─ get_next_chapter_goal(novel_name, next_num)              # [main.py:L375]
  │   ↓ 查 chapter_tasks → plot_goal, emotion_tag
  │   ↓ 若任务卡用完 → extend_tasks() 自动扩展
  │   ↓ 兜底：实时 AI 从大纲生成目标
  │
  ├─ _claim_task_for_writing(novel_name, next_num, ...)      # [main.py:L119]
  │   ↓ BEGIN IMMEDIATE + execute_with_retry 原子认领
  │   ↓ 状态转换：待处理 → 进行中
  │
  ├─ write_and_review(novel_name, next_num, ...)              # [core/reviewer.py:L805]
  │   ↓ （详见 3.3 审稿流程）
  │
  ├─ if content 且 status IN ('已审核', '强制通过'):
  │   ├─ _save_chapter_memory(novel_name, next_num, ...)     # [main.py:L542]
  │   │   ↓ 调用 call_reviewer_api() 提取摘要、人物更新、关系变化、伏笔兑现
  │   │   ↓ mm.add_summary() / update_character_status() /
  │   │     update_character_relationship() / redeem_foreshadowing()
  │   │   ↓ _trigger_compression() → compress_old_summaries()
  │   ├─ export_chapter(novel_name, next_num)                 # [core/exporter.py:L103]
  │   └─ _update_task_status(novel_name, next_num, "已完成")
  │
  └─ if 连续3次重写未通过:
      提示切换模型重新生成 → select_model_interactive()
      重新调用 write_and_review()
```

**批量自动生成**（章节菜单选项 2）：

```
for i in range(count):
    generate_chapter_auto(novel_name)  # 逐章串联
```

### 3.3 审稿流程（write_and_review 状态机）

**入口**：`write_and_review()` [core/reviewer.py:L805-L1230](file:///d:/novel-ai/core/reviewer.py#L805-L1230)

**状态机流程图**：

```
write_and_review(novel_name, chapter_num, plot_goal, emotion_tag)
  ↓
  _update_status_safe → 'writing'
  ↓
  ┌─ while True (外层：任务卡重写后重新开始) ─────────────────────────┐
  │                                                                   │
  │  for attempt in range(max_retry):  # 默认 max_retry=3            │
  │    ↓                                                              │
  │    if _revise_mode and _original_content:                        │
  │      revise_chapter()          # [core/writer.py:L1289] 局部修改  │
  │    else:                                                          │
  │      write_chapter()           # [core/writer.py:L1420] 全新写作  │
  │        ├─ is_high_capacity_model()?                              │
  │        │   True  → build_full_chapter_prompt() → 一次性生成      │
  │        │   False → build_writer_prompt() → 前半段                │
  │        │         → build_continue_prompt() → 后半段              │
  │        │         → 字数补写 (supplement) 循环                     │
  │        └─ _self_check_and_revise() → AI痕迹自检修订              │
  │    ↓                                                              │
  │    review_chapter()           # [core/reviewer.py:L578] 责任编辑  │
  │      ├─ build_review_prompt() → REVIEWER_SYSTEM                 │
  │      ├─ score_foreshadowing() # [core/reviewer.py:L391] 伏笔专项  │
  │      └─ _apply_foreshadow_l2_judgment()                          │
  │    ↓                                                              │
  │    if review_error: retry 审稿 (最多3次)                          │
  │    ↓                                                              │
  │    if NOT pass:                                                   │
  │      ├─ 冲突检测 (_veto_code_counter, _failure_layers)            │
  │      │   if 连续≥2次相同 veto_code 且全是 L1/L2:                 │
  │      │    冲突菜单 → rewrite_task_for_chapter() / 手动 / 强制通过 │
  │      │    if 重写任务卡: _rewrite_requested = True → break        │
  │      │                                                            │
  │      └─ if attempt < max_retry-1:                                │
  │          _revise_mode = True (首次失败自动进入修改模式)            │
  │          或 用户选：继续修改/完全重写/切换模型                     │
  │        else:                                                      │
  │          提示切换模型 → 强制通过                                   │
  │    ↓                                                              │
  │    if pass (责任编辑通过):                                        │
  │      reader_review_chapter()  # [core/reader_reviewer.py:L177]   │
  │        ├─ 构建 READER_REVIEW_PROMPT                              │
  │        ↓                                                          │
  │        if pass (双重通过):                                        │
  │          _update_status_safe → '已审核'                           │
  │          _auto_redeem_foreshadowing()  # 自动兑现逾期伏笔         │
  │          _sync_outline_foreshadow()     # 同步大纲伏笔状态        │
  │          return content                                           │
  │        else:                                                      │
  │          同责任编辑不通过的重试逻辑                                │
  │                                                                   │
  │  if _rewrite_requested: continue (外层 while 重新开始)            │
  └───────────────────────────────────────────────────────────────────┘
```

**关键函数位置一览**：

| 函数                             | 文件                              | 行号     | 职责                               |
| -------------------------------- | --------------------------------- | -------- | ---------------------------------- |
| `write_and_review()`             | [core/reviewer.py](file:///d:/novel-ai/core/reviewer.py) | L805     | 写作+审稿完整状态机                |
| `_revise_mode` / `_retry_feedback` | [core/reviewer.py](file:///d:/novel-ai/core/reviewer.py) | L845-L846 | 局部修改模式与反馈积累             |
| `review_chapter()`               | [core/reviewer.py](file:///d:/novel-ai/core/reviewer.py) | L578     | 责任编辑审稿（L1/L2/L3三层评分）   |
| `reader_review_chapter()`        | [core/reader_reviewer.py](file:///d:/novel-ai/core/reader_reviewer.py) | L177     | 读者视角审稿（真实感+逻辑+一致+流畅） |
| `_sync_outline_foreshadow()`     | [core/reviewer.py](file:///d:/novel-ai/core/reviewer.py) | L717     | 审稿通过后同步大纲伏笔状态         |
| `_auto_redeem_foreshadowing()`   | [core/reviewer.py](file:///d:/novel-ai/core/reviewer.py) | L745     | 审稿通过后关键词匹配自动兑现逾期伏笔 |
| `score_foreshadowing()`          | [core/reviewer.py](file:///d:/novel-ai/core/reviewer.py) | L391     | 伏笔专项评分（embed/resolve/traceable/overload_risk） |
| `rewrite_task_for_chapter()`     | [core/planner.py](file:///d:/novel-ai/core/planner.py) | L874     | 冲突检测后基于大纲重写任务卡       |

### 3.4 导出流程

**入口**：章节菜单选项「3. 导出所有已审核章节」

```
export_all(novel_name)                   # [core/exporter.py:L126]
  ↓ 查询 chapters WHERE status IN ('已审核', '强制通过')
  for each chapter:
    export_chapter(novel_name, chapter_num)  # [core/exporter.py:L103]
      ↓ mm.load_chapter(chapter_num)
      ↓ clean_for_export(content)        # [core/exporter.py:L51]
      │   ├─ 去除【系统提示】标签
      │   ├─ 去除 Markdown 残留（**加粗**、*斜体*、## 标题、--- 分隔线）
      │   ├─ 去除 (本章完) 后模型旁白
      │   ├─ 合并多余空行
      │   └─ 敏感词替换（用 □ 字符）
      ↓ get_safe_output_dir(novel_name)  → 安全创建目录
      ↓ 写入 output/{小说名}/第XXX章.txt
```

### 3.5 大纲伏笔管理流程

**入口**：章节菜单选项「15. 大纲伏笔管理」

```
manage_outline_foreshadow(novel_name)    # [core/outline_manager.py:L459]
  ├─ 1. 查看全部伏笔规划
  │    list_outline_foreshadow(novel_name)  # [core/outline_manager.py:L68]
  │
  ├─ 2. 新增伏笔
  │    add_outline_foreshadow(novel_name, ...)  # [core/outline_manager.py:L38]
  │      ↓ 自增 FID (OF001, OF002, ...)
  │      ↓ INSERT INTO outline_foreshadowing
  │
  ├─ 3. 编辑伏笔
  │    update_outline_foreshadow(novel_name, fid, ...)  # [core/outline_manager.py:L87]
  │
  ├─ 4. 删除伏笔
  │    delete_outline_foreshadow(novel_name, fid)  # [core/outline_manager.py:L108]
  │
  └─ 5. AI 辅助生成伏笔建议
       ai_suggest_outline_foreshadow(novel_name)  # [core/outline_manager.py:L166]
         ↓ 读取 master_outline.md + 目标章数
         ↓ 调用 call_author_api() → 20-30 条伏笔建议
         ↓ 用户选择：全部录入 / 逐条确认 / 取消
         ↓ add_outline_foreshadow() 逐条写入
```

**新建小说自动伏笔生成**：

```
generate_outline_foreshadow(novel_name, target_chapters)  # [core/outline_manager.py:L349]
  ├─ if review_mode: ai_suggest_outline_foreshadow() 交互确认
  └─ else: 直接 AI 生成 → 全量录入 outline_foreshadowing 表
```

**审稿通过后自动状态更新**：

```
get_chapter_outline_tasks(novel_name, chapter_num)  # [core/outline_manager.py:L120]
  ↓ 返回 {to_plant: [...], to_resolve: [...]}
  ↓
mark_outline_foreshadow_status(novel_name, fid, "planted"/"resolved")  # [core/outline_manager.py:L150]
```

### 3.6 断点 / 恢复流程

**入口**：章节菜单选项「16. 断点管理」

```
_breakpoint_menu(novel_name)             # [main.py:L1285]
  ├─ 1. 查看异常任务（进行中超过30分钟）
  │    查询 chapter_tasks WHERE status='进行中' AND updated_at < now-30min
  │
  ├─ 2. 强制重置任务状态 → 待处理
  │    _update_task_status(novel_name, num, "待处理")
  │    清除节拍内存缓存 _cached_beat_plan = ""
  │
  ├─ 3. 查看章节写作历史
  │    查询 writing_sessions 表
  │
  ├─ 4. 重写指定章节（保留摘要）
  │    UPDATE chapters SET content=NULL, status='草稿'
  │    UPDATE chapter_tasks SET status='待处理'
  │
  └─ 5. 清除节拍缓存（指定章节）
      DELETE FROM beats_cache WHERE chapter_num=?
      清除内存缓存
```

**恢复审稿失败章节**（章节菜单选项「8」）：

```
_recover_review_failed(novel_name)       # [main.py:L727]
  ↓ 查询 chapters WHERE status='审稿失败'
  ↓ 用户选择处理方式：
  │  1. 仅重试审稿（不重写）
  │     review_chapter() → reader_review_chapter() → 更新状态
  │  2. 重写并重审
  │     write_and_review() → _save_chapter_memory() → export_chapter()
  │  3. 强制通过并导出
  │     mm.update_chapter_status("强制通过") → export_chapter()
```

---

## 4. 核心模块接口清单

### 4.1 `core/api_client.py` — AI API 统一调用层（950+ 行）
**职责**：封装 DashScope + Mimo 双 Provider 的模型调用、统计、故障转移。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `call_api()` | `(system_prompt, user_message, model_name=None, provider=None, max_tokens=None, temperature=None, retry=3) -> str` | 通用 LLM 调用入口，自动路由 Provider |
| `call_author_api()` | `(system_prompt, user_message, max_tokens=None, temperature=None) -> str` | 使用作者模型调用 |
| `call_reviewer_api()` | `(system_prompt, user_message, max_tokens=None, temperature=None) -> str` | 使用审稿模型调用 |
| `call_reader_reviewer_api()` | `(system_prompt, user_message, max_tokens=None, temperature=None) -> str` | 使用读者视角模型调用 |
| `set_author_model()` | `(model_name, provider=None) -> None` | 动态切换作者模型 |
| `set_reviewer_model()` | `(model_name, provider=None) -> None` | 动态切换审稿模型 |
| `set_reader_reviewer_model()` | `(model_name, provider=None) -> None` | 动态切换读者视角模型 |
| `get_available_models()` | `(refresh=False, usage=None) -> dict` | 获取可用模型菜单 |
| `get_session_stats()` | `() -> dict` | 获取会话费用统计 |
| `print_session_stats()` | `() -> None` | 打印会话用量统计 |
| `get_failure_stats()` | `() -> dict` | 获取各模型失败计数 |
| `check_switch_needed()` | `(counter_type) -> bool` | 检查是否触发自动切换阈值 |
| `select_model_interactive()` | `() -> None` | 交互式选择默认模型 |
| `select_all_models_interactive()` | `() -> None` | 交互式分别配置三种模型 |

**关键常量**：

| 常量 | 值 | 说明 |
|------|-----|------|
| `FREE_TRIAL_MODEL_NAMES` | `{qwen3.6-flash, glm-5.1, ...}` 共17个 | 免费试用模型集合 |
| `AVAILABLE_MODELS` | 14个模型的默认菜单 | 兜底模型列表 |
| `_DASHSCOPE_ENDPOINTS` | `{beijing, intl, us}` 3个端点 | DashScope 区域节点 |
| `_MIMO_BASE_URL` | `https://token-plan-cn.xiaomimimo.com/v1` | Mimo 端点 |

### 4.2 `core/writer.py` — 章节写作模块（1650+ 行）
**职责**：章节生成、节拍规划、自检修订、AI痕迹规则检测。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `write_chapter()` | `(novel_name, chapter_num, plot_goal, emotion_tag="铺垫", retry_feedback="") -> str` | 章节生成主函数 |
| `revise_chapter()` | `(novel_name, chapter_num, original_content, feedback, plot_goal, emotion_tag="铺垫", beat_plan="") -> str` | 基于审稿反馈局部修订 |
| `build_full_chapter_prompt()` | `(ctx, chapter_num, plot_goal, emotion_tag, beat_plan, prev_chapter_ending="", word_min=3000, word_max=4000, retry_feedback="") -> str` | 构建大模型一次性生成整章 prompt |
| `build_writer_prompt()` | `(...)` | 构建小模型前半段 prompt |
| `build_continue_prompt()` | `(...)` | 构建小模型后半段 prompt |
| `_plan_chapter_beats()` | `(ctx, chapter_num, plot_goal, emotion_tag) -> str` | AI 节拍规划 |
| `_self_check_and_revise()` | `(system_prompt, chapter_num, plot_goal, emotion_tag, full_content, beat_plan, max_tokens) -> str` | 自检 + 去AI化打磨 + 规则修订 |
| `_rule_based_ai_check()` | `(text) -> list` | 规则检测 AI 痕迹（37条正则） |
| `is_high_capacity_model()` | `() -> bool` | 判断当前模型是否走大模型策略 |

**关键数据结构**：

| 结构 | 说明 |
|------|------|
| `AUTHOR_STYLES` (dict, 9种) | 预设写作风格：爽文宗师/悬疑大师/情感流/热血战斗/世界构建者/轻松日常/刘慈欣/金庸/古龙 |
| `EMOTION_GUIDE` (dict, 5种) | 情绪标签指南：爽点/冲突/反转/低谷/铺垫 |
| `WRITER_HARD_CONSTRAINTS` (list, 7条) | 写作硬约束规则 |
| `WRITER_FORBIDDEN_RULES` (list, 11条) | 写作禁止项规则 |
| `AI_PATTERNS` (list, 37条) | AI 痕迹正则检测规则 |
| `NEGATIVE_EXAMPLES` (str) | 反面教材示例文本 |
| `HUMAN_WRITING_TECHNIQUES` (str) | 真实写作手法指引 |
| `BEAT_PLANNER_SYSTEM` (str) | 节拍规划 system prompt |

### 4.3 `core/reviewer.py` — 审稿 + 写作流程（1250+ 行）
**职责**：write_and_review 状态机、责任编辑审稿（L1/L2/L3三层）、伏笔专项评分、冲突检测。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `write_and_review()` | `(novel_name, chapter_num, plot_goal, emotion_tag="铺垫", max_retry=None) -> str` | 写作→审稿→保存 完整流程 |
| `review_chapter()` | `(novel_name, chapter_num, content, plot_goal) -> dict` | 责任编辑审稿主函数 |
| `score_foreshadowing()` | `(chapter_text, foreshadow_list, chapter_num=0) -> dict｜None` | 伏笔专项评分 |
| `_sync_outline_foreshadow()` | `(novel_name, chapter_num, content) -> None` | 审稿通过后同步大纲伏笔状态 |
| `_auto_redeem_foreshadowing()` | `(mm, chapter_num, content) -> None` | 关键词匹配自动兑现逾期伏笔 |
| `_update_status_safe()` | `(novel_name, chapter_num, status) -> None` | 带重试的原子状态更新 |
| `_increment_retry_safe()` | `(novel_name, chapter_num) -> None` | 带重试的重试计数递增 |
| `_build_retry_feedback()` | `(result: dict) -> str` | 从审稿结果构建重试反馈 |

**关键常量**：

| 常量 | 值 | 说明 |
|------|-----|------|
| `REVIEW_PASS_TOTAL` | 75 | 责任编辑总分通过线 |
| `REVIEW_PASS_L1` | 30 | L1 层最低通过分 |
| `REVIEWER_SYSTEM` | (长 prompt) | 责任编辑 system prompt |
| `FORESHADOW_REVIEWER_SYSTEM` | (长 prompt) | 伏笔专项审核 system prompt |

### 4.4 `core/planner.py` — 策划编排模块（1100+ 行）
**职责**：新建小说的完整策划流程、任务卡拆分与扩展、任务卡重写。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `run_planner()` | `(novel_name, genre, keywords) -> tuple` | 策划主入口，返回 (outline, style_key) |
| `_choose_novel_length()` | `() -> int` | 篇幅选择（短/中/长/超长/自定义） |
| `get_outline_choice()` | `(genre, keywords, novel_name, target_chapters) -> str` | 大纲生成/输入选择 |
| `get_characters_choice()` | `(outline) -> list` | 角色名单提取/输入 |
| `generate_world()` | `(novel_name, genre, outline, character_names, mm, review_mode) -> str` | 世界观生成 |
| `generate_characters()` | `(character_names, outline, world, mm, review_mode) -> list` | 人物档案生成 |
| `get_style_choice()` | `() -> str` | 写作风格选择 |
| `split_outline_to_tasks()` | `(outline, novel_name, review_mode, target_chapters, full_batch, start) -> int` | 大纲→任务卡拆分，返回 saved 数量 |
| `extend_tasks()` | `(novel_name, from_chapter) -> None` | 任务卡用完后自动扩展 |
| `rewrite_task_for_chapter()` | `(novel_name, chapter_num, veto_reasons, current_goal, current_emotion_tag) -> dict` | 冲突检测后重写任务卡 |

**关键数据结构**：

| 结构 | 说明 |
|------|------|
| `NOVEL_LENGTH_OPTIONS` (dict, 5种) | 篇幅选项：短篇40/中篇80/长篇150/超长篇300/自定义 |
| `WORLD_PROMPT` (str) | 世界观生成 system prompt |
| `CHARACTER_PROMPT` (str) | 角色档案生成 system prompt |
| `CHARACTER_EXTRACT_PROMPT` (str) | 角色名提取 system prompt |

### 4.5 `core/memory_manager.py` — 数据持久化模块（940+ 行）
**职责**：所有数据库 CRUD 的统一入口，记忆提取与压缩。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `MemoryManager(novel_name)` | 构造函数 | 绑定小说，自动 ensure_database |
| `save_world_settings()` | `(content) -> None` | 保存世界观 |
| `load_world_settings()` | `() -> str` | 加载世界观 |
| `save_character()` | `(name, data, _batch=False) -> None` | 保存/更新单个角色 |
| `save_characters_batch()` | `(characters: list) -> None` | 批量保存角色 |
| `load_characters()` | `() -> list` | 加载全部角色 |
| `update_character_status()` | `(name, location, status, chapter_num) -> None` | 更新角色位置/状态 |
| `update_character_relationship()` | `(name_a, name_b, relationship, chapter_num) -> None` | 双向更新人物关系 |
| `add_foreshadowing()` | `(fid, plant_chapter, description, expected_redeem) -> None` | 添加动态伏笔 |
| `redeem_foreshadowing()` | `(fid, chapter_num) -> None` | 兑现伏笔 |
| `load_active_foreshadowing()` | `() -> list` | 加载活跃伏笔 |
| `get_foreshadow_hints()` | `(chapter_num) -> list` | 智能伏笔提示（优先级排序+分批） |
| `get_foreshadow_report()` | `(current_chapter) -> dict` | 伏笔健康度报告 |
| `add_summary()` | `(chapter_num, summary) -> None` | 添加章节摘要 |
| `load_recent_summaries()` | `(count=5) -> list` | 加载近期摘要 |
| `compress_old_summaries()` | `() -> None` | 压缩旧摘要为阶段摘要 |
| `save_chapter()` | `(chapter_num, title, content, status, ...) -> None` | 保存章节 |
| `load_chapter()` | `(chapter_num) -> dict` | 加载章节 |
| `load_context()` | `(chapter_num) -> dict` | 加载写作上下文（世界观+人物+伏笔+摘要+大纲） |
| `update_chapter_status()` | `(chapter_num, status) -> None` | 更新章节状态 |
| `delete_chapter()` | `(chapter_num) -> None` | 删除章节+摘要记录 |

### 4.6 `core/outline_manager.py` — 大纲伏笔管理（660+ 行）
**职责**：大纲伏笔 CRUD、AI 辅助生成、章节任务查询、状态标记。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `add_outline_foreshadow()` | `(novel_name, description, category, plant_chapter, resolve_chapter, importance=3, notes="") -> str` | 新增伏笔，返回 FID |
| `list_outline_foreshadow()` | `(novel_name, status=None) -> list` | 列出伏笔 |
| `update_outline_foreshadow()` | `(novel_name, fid, **kwargs) -> bool` | 编辑伏笔 |
| `delete_outline_foreshadow()` | `(novel_name, fid) -> bool` | 删除伏笔 |
| `get_chapter_outline_tasks()` | `(novel_name, chapter_num) -> dict` | 获取章节伏笔任务 {to_plant, to_resolve} |
| `mark_outline_foreshadow_status()` | `(novel_name, fid, status) -> bool` | 标记伏笔状态 |
| `ai_suggest_outline_foreshadow()` | `(novel_name) -> None` | AI 辅助生成伏笔建议（交互式） |
| `generate_outline_foreshadow()` | `(novel_name, target_chapters, review_mode=False) -> int` | 自动化伏笔生成入口 |
| `manage_outline_foreshadow()` | `(novel_name) -> None` | 交互式管理菜单 |

### 4.7 `core/reader_reviewer.py` — 读者视角审稿（318 行）
**职责**：从真实读者角度评估章节的 AI 痕迹、逻辑、一致性和可读性。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `reader_review_chapter()` | `(novel_name, chapter_num, current_content) -> dict` | 读者视角审稿主函数 |

**关键常量**：

| 常量 | 说明 |
|------|------|
| `READER_REVIEWER_SYSTEM` (str) | 读者视角 system prompt |
| `READER_REVIEW_PROMPT` (str) | 读者视角评估 prompt 模板（四维25分制） |

### 4.8 `core/model_manager.py` — 模型发现（230 行）
**职责**：从 custom_models.json 加载模型列表，按用途筛选，转换为菜单格式。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `discover_all_models()` | `(refresh=False) -> list` | 发现所有可用模型（模块级缓存） |
| `get_models_for_usage()` | `(usage, top_k=5) -> List[Dict]` | 按用途筛选模型（author/reviewer/reader_reviewer） |
| `model_list_to_menu_format()` | `(models) -> Dict[str, Dict]` | 模型列表→菜单格式转换 |

**关键数据结构**：

| 结构 | 说明 |
|------|------|
| `MODEL_CATEGORIES` (dict) | 模型分类映射：cost_effective/balanced/premium/long_context |

### 4.9 `core/db.py` — 数据库初始化与迁移（330 行）
**职责**：数据库文件创建、表结构初始化、WAL 模式配置、旧库补列迁移。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `init_database()` | `(novel_name) -> None` | 创建所有表 + 索引 + 迁移 |
| `ensure_database()` | `(novel_name) -> None` | 幂等初始化（已初始化则跳过） |
| `get_connection()` | `(novel_name) -> sqlite3.Connection` | 获取数据库连接（WAL+timeout） |
| `get_db_path()` | `(novel_name) -> str` | 获取数据库文件路径 |
| `clean_duplicate_chapters()` | `(novel_name) -> None` | 清理重复章节记录 |
| `_migrate()` | `(conn, cursor) -> None` | 补列迁移（兼容旧库） |

### 4.10 `core/config_loader.py` — 配置加载（120 行）
**职责**：YAML 配置单例加载、多级 key 安全访问、目录路径解析。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `load_config()` | `() -> dict` | 加载 config.yaml（单例缓存） |
| `get()` | `(section, key, *args, default=None) -> any` | 安全多级 key 访问 |
| `get_data_dir()` | `(novel_name="") -> Path` | 数据目录路径 |
| `get_output_dir()` | `(novel_name="") -> Path` | 导出目录路径 |
| `get_project_root()` | `() -> Path` | 项目根目录 |

### 4.11 `core/exporter.py` — 章节导出（140 行）
**职责**：章节正文清理、敏感词替换、文件输出。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `export_chapter()` | `(novel_name, chapter_num) -> str` | 单章导出 |
| `export_all()` | `(novel_name) -> list` | 所有已审核章节导出 |
| `clean_for_export()` | `(text) -> str` | 清理 AI 生成物中的 Markdown + 系统标签 |
| `load_sensitive_words()` | `(cache_seconds=300) -> list` | 加载敏感词列表 |

### 4.12 `core/utils.py` — 公共工具（150 行）
**职责**：JSON 提取、类型转换、事务管理、重试机制。

**关键函数签名**：

| 函数 | 签名 | 说明 |
|------|------|------|
| `extract_json_obj()` | `(raw) -> dict` | 从字符串提取第一个 JSON 对象 |
| `to_int()` | `(value, default=0, min_value=None, max_value=None) -> int` | 安全整数转换 |
| `is_transient_error()` | `(error) -> bool` | 判断是否为暂时性错误 |
| `with_db_connection()` | `(novel_name) -> Generator` | 数据库连接上下文管理器 |
| `execute_with_retry()` | `(conn, sql, params, max_retries=3, initial_delay=0.1, backoff=2.0) -> Cursor` | SQL 自动重试（指数退避） |
| `DatabaseTransaction` | `class` | 事务管理器（BEGIN IMMEDIATE） |

---

## 5. 数据库表结构

### 5.1 `novel_info` — 小说元信息

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY | 固定为 1 |
| `name` | TEXT NOT NULL | 小说名称 |
| `genre` | TEXT | 小说类型 |
| `created_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| `status` | TEXT DEFAULT 'active' | 状态 |

### 5.2 `characters` — 角色设定

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `name` | TEXT NOT NULL UNIQUE | 角色名（唯一） |
| `role` | TEXT | 角色定位（主角/配角/反派等） |
| `appearance` | TEXT | 外貌描述 |
| `personality` | TEXT | 性格描述 |
| `secret` | TEXT | 隐藏秘密 |
| `weakness` | TEXT | 致命弱点 |
| `current_location` | TEXT | 当前位置 |
| `current_status` | TEXT | 当前状态 |
| `relationships` | TEXT | 人物关系（JSON 字符串） |
| `updated_chapter` | INTEGER DEFAULT 0 | 最后更新章节号 |

### 5.3 `chapters` — 章节正文与审稿

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `chapter_num` | INTEGER NOT NULL UNIQUE | 章节号（唯一） |
| `title` | TEXT | 章节标题 |
| `emotion_tag` | TEXT | 情绪标签 |
| `plot_goal` | TEXT | 情节目标 |
| `word_target` | INTEGER DEFAULT 3000 | 目标字数 |
| `content` | TEXT | 章节正文 |
| `summary` | TEXT | 章节摘要 |
| `status` | TEXT DEFAULT 'pending' | 状态（草稿/已审核/强制通过/审稿失败） |
| `retry_count` | INTEGER DEFAULT 0 | 重试次数 |
| `review_score_total` | INTEGER | 责任编辑总分 |
| `review_score_l1` | INTEGER | L1 评分 |
| `review_score_l2` | INTEGER | L2 评分 |
| `review_score_l3` | INTEGER | L3 评分 |
| `review_veto_items` | TEXT | 否决项（JSON） |
| `review_failure_attribution` | TEXT | 失败归因（JSON） |
| `review_updated_at` | TIMESTAMP | 审稿更新时间 |
| `reader_review_score` | INTEGER | 读者视角评分 |
| `reader_review_passed` | INTEGER | 读者视角通过标志（0/1） |
| `reader_review_issues` | TEXT | 读者视角问题列表（JSON） |
| `created_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | 更新时间 |

**索引**：`idx_chapter_num` (UNIQUE on chapter_num)

### 5.4 `chapter_tasks` — 任务卡

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `chapter_num` | INTEGER NOT NULL UNIQUE | 章节号（唯一） |
| `plot_goal` | TEXT | 情节目标 |
| `emotion_tag` | TEXT DEFAULT '铺垫' | 情绪标签 |
| `status` | TEXT DEFAULT '待处理' | 状态（待处理/进行中/已完成/审稿失败） |
| `original_plot_goal` | TEXT | 原始任务卡目标（重写时保留） |
| `rewrite_count` | INTEGER DEFAULT 0 | 已重写次数 |
| `created_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | TIMESTAMP | 最后更新时间 |

**索引**：`idx_task_chapter_num` (UNIQUE on chapter_num)

### 5.5 `foreshadowing` — 动态伏笔（旧系统，已禁用自动提取）

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `fid` | TEXT UNIQUE NOT NULL | 伏笔编号（如 F005_1） |
| `plant_chapter` | INTEGER | 埋下章节 |
| `description` | TEXT | 伏笔描述 |
| `expected_redeem` | TEXT | 预计兑现章节范围 |
| `status` | TEXT DEFAULT 'active' | 状态（active/redeemed） |
| `redeemed_chapter` | INTEGER | 实际兑现章节 |

### 5.6 `summaries` — 章节摘要

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `chapter_num` | INTEGER NOT NULL | 章节号 |
| `summary` | TEXT NOT NULL | 摘要内容 |
| `is_compressed` | INTEGER DEFAULT 0 | 是否已压缩（0=原始，1=阶段摘要） |
| `created_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**索引**：`idx_summary_chapter_compressed` (UNIQUE on chapter_num, is_compressed)

### 5.7 `world_settings` — 世界观设定

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY | 固定为 1 |
| `content` | TEXT NOT NULL | 世界观内容 |
| `updated_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | 更新时间 |

### 5.8 `model_switch_history` — 模型切换记录

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `switch_type` | TEXT NOT NULL | 切换类型（author/reviewer/reader_reviewer） |
| `old_model` | TEXT | 旧模型名 |
| `new_model` | TEXT | 新模型名 |
| `trigger_reason` | TEXT | 触发原因 |
| `failure_count` | INTEGER DEFAULT 0 | 触发时失败计数 |
| `chapter_num` | INTEGER | 切换时所在章节 |
| `created_at` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | 创建时间 |

### 5.9 `outline_foreshadowing` — 大纲伏笔规划

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `novel_name` | TEXT NOT NULL | 所属小说 |
| `fid` | TEXT NOT NULL | 伏笔编号（OF001, OF002...） |
| `description` | TEXT NOT NULL | 伏笔描述 |
| `category` | TEXT DEFAULT '情节伏笔' | 分类（情节伏笔/人物伏笔/世界观/宏观悬念） |
| `plant_chapter` | INTEGER | 计划埋入章节 |
| `resolve_chapter` | INTEGER | 计划兑现章节 |
| `status` | TEXT DEFAULT 'planned' | 状态（planned/planted/resolved） |
| `importance` | INTEGER DEFAULT 3 | 重要度（1-5星） |
| `notes` | TEXT | 备注 |

**索引**：UNIQUE(novel_name, fid)

### 5.10 `beats_cache` — 节拍规划缓存

| 列名 | 类型 | 说明 |
|------|------|------|
| `chapter_num` | INTEGER PRIMARY KEY | 章节号（主键） |
| `beats_text` | TEXT NOT NULL | 节拍规划文本 |
| `model_used` | TEXT | 使用的模型 |
| `created_at` | TEXT DEFAULT (datetime('now','localtime')) | 创建时间 |
| `used_count` | INTEGER DEFAULT 0 | 被使用次数 |

### 5.11 `writing_sessions` — 写作会话记录

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | 自增主键 |
| `chapter_num` | INTEGER NOT NULL | 章节号 |
| `attempt` | INTEGER NOT NULL | 尝试次数 |
| `started_at` | TEXT | 开始时间 |
| `ended_at` | TEXT | 结束时间 |
| `end_reason` | TEXT | 结束原因 |
| `word_count` | INTEGER | 字数统计 |
| `review_score` | REAL | 审稿评分 |

---

## 6. config.yaml 配置项说明

### 6.1 `model` 节

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `model.max_tokens` | int | 4096 | 单次 API 调用最大 token 数 |
| `model.api_region` | str | `beijing` | DashScope 接入节点：beijing / intl / us |
| `model.author.default_model` | str | `qwen3.6-flash` | 作者模型默认选型 |
| `model.reviewer.default_model` | str | `qwen3.6-max-preview` | 责任编辑模型默认选型 |
| `model.reader_reviewer.default_model` | str | `qwen3.6-flash` | 读者视角模型默认选型 |
| `model.reader_reviewer.enabled` | bool | true | 是否启用读者视角审稿 |
| `model.reader_reviewer.pass_threshold` | int | 75 | 读者视角通过分数线 |

### 6.2 `temperature` 节

| 配置项 | 类型 | 默认值 | 适用任务 |
|--------|------|--------|----------|
| `temperature.writing_main` | float | 0.85 | 大模型一次性生成整章 |
| `temperature.writing_first_half` | float | 0.90 | 小模型前半段 |
| `temperature.writing_second_half` | float | 0.70 | 小模型后半段 |
| `temperature.writing_supplement` | float | 0.75 | 字数补写 |
| `temperature.revision` | float | 0.70 | 自检修订 |
| `temperature.self_check` | float | 0.20 | 自检质检 |
| `temperature.beat_planner` | float | 0.65 | 节拍规划 |
| `temperature.outline_gen` | float | 0.90 | 总大纲生成 |
| `temperature.world_gen` | float | 0.85 | 世界观生成 |
| `temperature.character_extract` | float | 0.50 | 角色名提取 |
| `temperature.character_gen` | float | 0.70 | 角色档案生成 |
| `temperature.task_split` | float | 0.70 | 任务卡拆分 |
| `temperature.scene_replan` | float | 0.70 | veto 触发后场景重规划 |

### 6.3 `novel` 节

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `novel.chapter_word_target` | int | 3500 | 章节目标字数（中间值） |
| `novel.chapter_word_min` | int | 3000 | 章节最小字数（低于触发补写） |
| `novel.chapter_word_max` | int | 4000 | 章节最大字数（达到停止补写） |
| `novel.max_retry` | int | 3 | 每章最多重试次数 |
| `novel.recent_summary_count` | int | 5 | 加载最近摘要数量 |
| `novel.compress_after_chapters` | int | 20 | 摘要超过此数触发压缩 |
| `novel.pre_split_chapters` | int | 50 | 每批任务卡生成数量 |
| `novel.failure_switch_threshold` | int | 3 | 连续失败达到此数触发模型切换提示 |
| `novel.progress_review_window` | int | 10 | 进度面板审稿质量统计窗口（最近N章） |

### 6.4 `paths` 节

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `paths.data_dir` | str | `data` | 小说数据根目录 |
| `paths.output_dir` | str | `output` | 导出文件根目录 |

---

## 7. 当前进度

### 已完成功能
- [x] 数据库连接管理统一化（with_db_connection + DatabaseTransaction）
- [x] SQLite WAL 模式 + busy_timeout=5000
- [x] 任务认领原子性（BEGIN IMMEDIATE + 条件 UPDATE）
- [x] 双重审稿机制（责任编辑 + 读者视角）
- [x] execute_with_retry 重试函数（指数退避 0.1s→0.2s→0.4s）
- [x] 章节字数控制（chapter_word_min / chapter_word_max）
- [x] max_tokens 动态计算（word_max × 1.75 封顶 7000）
- [x] 14 个模型支持（DashScope + Mimo 双 Provider）
- [x] 默认模型更换为 qwen3.6-flash（有免费额度）
- [x] 大纲伏笔管理系统（规划→埋入→兑现 完整生命周期）
- [x] 冲突检测 + 任务卡自动重写
- [x] AI 痕迹规则检测（37条正则）+ 自检修订
- [x] 9 种预设写作风格 + 自定义风格
- [x] 章节摘要自动压缩（阶段摘要合并）
- [x] 伏笔健康度报告
- [x] 断点管理与异常恢复

### 待办事项
- [ ] **P0 — 安全与稳定性**
  - [ ] SQL 注入风险修复（动态 SQL 标识符白名单校验）
  - [ ] 裸 except 替换为精确异常处理
  - [ ] write_and_review() 函数拆分（~430行→多个子状态机）
  - [ ] api_client.py 职责拆分（API层/UI层/统计层分离）
  - [ ] 核心路径单元测试
- [ ] **P1 — 架构优化**
  - [ ] 引入 AppContext 依赖注入
  - [ ] Provider 抽象层（LLMProvider 接口）
  - [ ] 魔法数字配置化
  - [ ] N+1 查询优化
  - [ ] 日志敏感信息脱敏
  - [ ] 依赖版本锁定（requirements.txt）
- [ ] **P2 — 体验与性能**
  - [ ] 字符串拼接改用 join()
  - [ ] 章节级缓存层
  - [ ] 审稿维度插件化
  - [ ] 中期：FastAPI Web UI + asyncio 异步改造