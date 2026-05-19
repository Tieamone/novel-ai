# AI 思维链 / 记忆体

## 当前上下文

**时间**: 2026-05-19
**任务**: 竞品分析 → 筛选可引入的优化点 → 创建优化方案文档

## 本次完成的工作

### 2026-05-19 (3): 竞品分析 + 优化方案文档

- **分析对象**: Narcooo/inkos（6.2k⭐）、NousResearch/autonovel（952⭐）、RhythmicWave/NovelForge（847⭐）
- **筛选原则**: 只保留与 Python CLI 技术栈兼容 + 能直接提升输出稳定性的优化点
- **输出**: [docs/newidea.md](file:///d:/novel-ai/docs/newidea.md) — 包含 5 个通过筛选的建议 + 10 个排除项及理由
- **核心结论**:
  - ✅ 采纳（按优先级）：平台期检测、输入治理控制面、Anti-Slop 三层升级、文法指纹分析、对抗性精简
  - ❌ 排除：卡片式 UI、工作流系统（架构不兼容）；EPUB 导出、多模型路由（不提升输出质量）；封面/有声书（偏离核心目标）
- **Phase 1 建议先行实施**：平台期检测（20行）+ 输入治理控制面（80行），投入极小、立竿见影

### 2026-05-19 (2): PROJECT_PROFILE.md 白皮书信息更新

- **背景**: 用户反馈白皮书信息落后，要求全面阅读项目后更新
- **读取内容**:
  - 完整阅读 [docs/PROJECT_PROFILE.md](file:///d:/novel-ai/docs/PROJECT_PROFILE.md)（当前白皮书）
  - 完整阅读 [docs/AI_CONTEXT.md](file:///d:/novel-ai/docs/AI_CONTEXT.md)（历史上下文）
  - 扫描目录结构，读取 [main.py](file:///d:/novel-ai/main.py), [core/api_client.py](file:///d:/novel-ai/core/api_client.py), [core/writer.py](file:///d:/novel-ai/core/writer.py), [core/reviewer.py](file:///d:/novel-ai/core/reviewer.py), [core/planner.py](file:///d:/novel-ai/core/planner.py), [core/memory_manager.py](file:///d:/novel-ai/core/memory_manager.py), [core/outline_manager.py](file:///d:/novel-ai/core/outline_manager.py), [core/db.py](file:///d:/novel-ai/core/db.py), [config.yaml](file:///d:/novel-ai/config.yaml), [requirements.txt](file:///d:/novel-ai/requirements.txt)
  - 统计所有核心文件行数（排除 venv）
- **更新内容**:
  1. **项目概况**: 代码量 ~7500+ → ~10300+ 行，标注依赖版本见 requirements.txt
  2. **目录结构**: 更新所有模块行数标注，新增 newbook_template.txt、requirements.txt、pytest.ini、check_dashscope.py、tools/cleanup_foreshadow.py、core/__init__.py、data/custom_models.json
  3. **模块接口**: outline_manager.py (660+ → 839 行) 新增 review_outline_foreshadow()、CJK 宽度处理工具、交互式子函数；其他模块更新精确行数
  4. **config.yaml 配置项**: 新增 novel.batch_size_small/medium/large、novel.batch_bridge_count 配置项说明
  5. **已完成功能**: 新增 6 项（单章/批量删除、导入伏笔生成、模型分类、依赖锁定、测试配置、伏笔清理工具）
  6. **待办事项**: P1 中移除"依赖版本锁定"（已完成）

## 下一步计划
- 伏笔系统改造后续步骤（大纲伏笔集成到写作流程）
- 用户确认 P0 优先级的修复范围后开始实施

---

### 历史: 2026-05-14 (3): 修复导入流程缺失大纲伏笔生成

- **问题**: 从 `newbook.txt` 导入小说时，`generate_outline_foreshadow()` 被完全跳过
- **对比**:
  - 交互式创建：`run_planner()` [planner.py:L1068-L1069](file:///d:/novel-ai/core/planner.py#L1068-L1069) → Step 1.6 调用 `generate_outline_foreshadow()`
  - 导入创建：`main.py` 只做了存大纲→存世界观→存角色→任务卡，完全跳过了伏笔生成
- **修复**: 在 `main.py` 导入流程中，`split_outline_to_tasks()` 之前添加 `generate_outline_foreshadow(novel_name, target_chapters, review_mode=False)`
- **位置**: [main.py:L1919-L1920](file:///d:/novel-ai/main.py#L1919-L1920)
- **效果**: 导入的小说将自动生成 20-30 条大纲伏笔，并经过 `review_outline_foreshadow()` 审稿过滤
- **幂等性**: 如果伏笔已存在，会自动跳过

### 2026-05-14 (2): PROJECT_PROFILE.md 全面重写

- **位置**: [docs/PROJECT_PROFILE.md](file:///d:/novel-ai/docs/PROJECT_PROFILE.md)
- **内容**: 完整重写，包含以下 7 个章节：
  1. **项目概况** — 项目类型、代码量、数据库、AI后端、模块/表数量
  2. **目录结构** — 完整目录树 + 每行代码量标注
  3. **所有功能流程** — 6 大流程的完整调用链（新建小说/续写/审稿/导出/大纲伏笔/断点恢复）
  4. **核心模块接口清单** — 14 个模块的函数签名、常量、数据结构
  5. **数据库表结构** — 11 张表的完整 Column + Index 定义
  6. **config.yaml 配置项说明** — 4 节的每项默认值与说明
  7. **当前进度** — 已完成功能清单 + P0/P1/P2 待办事项

### 2026-05-14 (1): 伏笔系统改造 — 禁用 _save_chapter_memory() 自动提取新伏笔

- **位置**: [main.py](file:///d:/novel-ai/main.py), `_save_chapter_memory()` 函数 (L629-L637)
- **改动**: 注释掉 `new_foreshadowing` 提取循环，保留 `redeemed_foreshadowing` 兑现检测
- **原因**: 自动提取的伏笔质量不可控，改用大纲预定义伏笔体系（outline_manager.py）
- **保留**: 旧代码以注释形式保留，方便回滚
- **验证**: 编译通过 ✅

## 下一步计划
- 伏笔系统改造后续步骤（大纲伏笔集成到写作流程）
- 用户确认 P0 优先级的修复范围后开始实施

---

### 历史: 代码库全面架构分析（模块依赖、数据流、接口盘点）

已读取并分析全部 14 个核心源文件，产出完整的项目结构分析报告。关键发现如下：

#### 模块依赖层次图
```
Layer 0 (基础设施):
  core/config_loader.py  ── 配置加载（单例缓存，YAML→dict）
  core/db.py             ── 数据库初始化、连接管理、迁移系统

Layer 1 (核心服务):
  core/utils.py          ── 公共工具（连接管理、重试、JSON提取）
  core/model_manager.py  ── 模型发现与管理（缓存+动态发现）

Layer 2 (AI 接口):
  core/api_client.py     ── 统一 API 调用入口（DashScope+Mimo双Provider）

Layer 3 (业务逻辑):
  core/memory_manager.py ── 数据持久化（人物、伏笔、摘要、世界观）
  core/writer.py         ── 章节写作（prompt构建、生成、自检修订）
  core/reviewer.py       ── 双重审稿（责任编辑+伏笔评分+状态机）
  core/reader_reviewer.py── 读者视角审稿（AI检测+阅读体验评估）
  core/planner.py        ── 策划编排（大纲→世界观→角色→任务卡）
  core/exporter.py       ── 章节导出（清理+敏感词+文件输出）
  core/outline_manager.py── 大纲伏笔管理（CRUD+AI辅助建议）

Layer 4 (应用层):
  main.py                ── CLI主入口（菜单系统+完整控制流）
```

#### 数据流关键路径
```
新建流程: main.py → planner.py → writer.py → reviewer.py → memory_manager.py → db.py
续写流程: main.py → reviewer.py → writer.py → reader_reviewer.py → memory_manager.py
导出流程: main.py → exporter.py → memory_manager.py → db.py
```

#### 核心接口盘点（12 个关键接口）
| 接口 | 模块 | 入参 | 出参 | 用途 |
|------|------|------|------|------|
| call_api() | api_client | system_prompt, user_message, model, tokens, temp | str | 统一LLM调用 |
| call_author_api() | api_client | system_prompt, user_message, tokens, temp | str | 作者模型专用调用 |
| call_reviewer_api() | api_client | system_prompt, user_message, tokens, temp | str | 审稿模型专用调用 |
| call_reader_reviewer_api() | api_client | system_prompt, user_message, tokens, temp | str | 读者视角模型专用调用 |
| write_chapter() | writer | novel_name, chapter_num, plot_goal, emotion_tag, retry_feedback | str | 章节生成主函数 |
| write_and_review() | reviewer | novel_name, chapter_num, plot_goal, emotion_tag, max_retry | str | 写作+审稿完整流程 |
| review_chapter() | reviewer | novel_name, chapter_num, content, plot_goal | dict | 责任编辑审稿 |
| reader_review_chapter() | reader_reviewer | novel_name, chapter_num, current_content | dict | 读者视角审稿 |
| load_context() | memory_manager | chapter_num | dict | 加载写作上下文 |
| MemoryManager(novel_name) | memory_manager | novel_name | MemoryManager实例 | 数据访问统一入口 |
| run_planner() | planner | novel_name, genre, keywords | (outline, style_key) | 新建小说策划 |
| init_database() | db | novel_name | None | 数据库表结构初始化 |

- **总体评分**: 2.6/5（功能原型→可维护产品的过渡阶段）
- **最大风险**: 零测试覆盖、模块边界模糊（3个上帝模块）、裸except/SQL注入安全隐患
- **最大优势**: 完整的写作流水线、去AI化系统、错误日志积累

### 发现的 14 个问题（按优先级）

**P0 — 立即修复（5项）**:
| # | 问题 | 文件 |
|---|------|------|
| 10 | SQL 注入风险（动态SQL拼接） | db.py, main.py |
| 5 | 裸 except + 异常体系缺失 | api_client.py, reviewer.py |
| 13 | 零单元测试覆盖 | 全局 |
| 4 | write_and_review() 445行超长函数 | reviewer.py:L777-L1221 |
| 1 | api_client.py 上帝模块（7种职责） | api_client.py |

**P1 — 短期优化（6项）**:
| # | 问题 |
|---|------|
| 2 | 全局状态泛滥，缺乏依赖注入 |
| 3 | 缺乏 Provider 抽象层（新增AI后端成本高） |
| 6 | 魔法数字硬编码（阈值、超时、截断字数） |
| 8 | N+1 查询（人物关系、伏笔排序） |
| 11 | 日志敏感信息泄露风险 |
| 14 | 依赖版本未锁定 |

**P2 — 中期改进（3项）**:
| # | 问题 |
|---|------|
| 7 | 字符串 += 拼接性能 |
| 9 | 缺乏章节级缓存 |
| 12 | 审稿维度缺乏插件化 |

**详细评估报告**: 见对话记录中的完整分析。

## 历史上下文

### 2026-04-26: 全项目 Bug 审查

- **结果**: 发现 **7 个 Bug** + **2 个代码质量问题**
- **详细记录**: [docs/AI\_ERROR\_LOG.md](file:///d:/novel-ai/docs/AI_ERROR_LOG.md)

### Bug 摘要

| #      | 严重度 | 文件                       | 简述                            |
| ------ | --- | ------------------------ | ----------------------------- |
| BUG-01 | 🔴  | main.py:L5               | sys.path.insert 双层 dirname 错误 |
| BUG-02 | 🔴  | writer.py:L116,L960      | author\_style 死参数             |
| BUG-03 | 🟡  | reader\_reviewer.py:L112 | \_truncate\_content 死代码       |
| BUG-04 | 🔴  | writer.py:L1264          | 大模型模式跳过复用检测                   |
| BUG-05 | 🔴  | reader\_reviewer.py:L158 | score\_ai=0 时语义歧义             |
| BUG-06 | 🔴  | reader\_reviewer.py:L217 | 禁用时返回字段缺失                     |
| BUG-07 | 🟡  | writer.py:L759           | .lower() 对中文无意义               |

### 2026-04-20: 模型列表更新与默认模型更换

- **位置**: [data/custom\_models.json](file:///d:/novel-ai/data/custom_models.json), [config.yaml](file:///d:/novel-ai/config.yaml), [core/model\_manager.py](file:///d:/novel-ai/core/model_manager.py)
- **问题背景**: 用户反馈默认使用的 `qwen3.6-plus` 模型已无额度，需要将图片中的新模型添加到使用列表，并更换默认模型

### 核心修改内容

#### 1. 模型配置层（data/custom\_models.json）

- **标记无额度模型**: qwen3.6-plus 的 `has_free_quota` 改为 `false`
- **新增8个模型**:
  - qwen3.6-flash（⭐新推荐主模型，128K上下文，快速低成本）
  - qwen3.6-flash-2026-04-16（带日期标记的flash版本）
  - qwen3.6-35b-a3b（35B参数MoE架构，高质量）
  - glm-5.1（智谱GLM 5.1旗舰）
  - qwen3.6-plus-2026-04-02（带日期标记的plus版本）
  - gui-plus-2026-02-26（GUI专用版本）
  - qwen-flash-character-2026-02-26（角色生成专用）
  - qwen3.5-35b-a3b（3.5版本35B参数）

#### 2. 默认配置层（config.yaml）

- **修改默认模型**: 从 `qwen3.6-plus` 更换为 `qwen3.6-flash`
  - `model.author.default_model`: qwen3.6-plus → qwen3.6-flash
  - `model.reviewer.default_model`: qwen3.6-plus → qwen3.6-flash
  - `model.reader_reviewer.default_model`: qwen3.6-plus → qwen3.6-flash

#### 3. 模型分类层（core/model\_manager.py）

- **更新 MODEL\_CATEGORIES**，新增6个模型映射：
  - qwen3.6-flash → balanced（快速低成本）
  - qwen3.6-plus → premium（高级）
  - qwen3.6-35b-a3b → premium（高质量）
  - qwen3.5-35b-a3b → balanced（稳定版）
  - glm-5 → premium（智谱旗舰）
  - glm-5.1 → premium（智谱旗舰）

### 验证结果

- 成功加载 16 个模型（全部为通义千问/智谱/Mimo模型）
- 所有模型配置正确，分类映射完整

### 2026-04-04: 章节字数范围控制优化

- **位置**: [core/writer.py](file:///d:/novel-ai/core/writer.py), [config.yaml](file:///d:/novel-ai/config.yaml)
- **问题背景**: 用户反馈生成的章节字数过大（"太大杯"），需要控制在3000-4000字范围内
- **根本原因分析**:
  1. 大模型模式 `max_tokens * 2 = 8192` tokens 过于宽松
  2. 提示词只说"大约3000字"，没有明确上限
  3. 字数补写只有下限（90%）没有上限控制

### 核心修改内容

#### 1. 配置层（config.yaml）

- **新增配置项**:
  - `chapter_word_min: 3000` - 最小字数（低于此值触发补写）
  - `chapter_word_max: 4000` - 最大字数（达到此值停止补写）
- **调整配置项**:
  - `chapter_word_target`: 3000 → 3500（作为中间目标值）

#### 2. 提示词层（core/writer.py）

- **修改函数**: `build_full_chapter_prompt()` (第52-147行)
  - 新增参数: `word_min=3000, word_max=4000`
  - 提示词改为: "现在要写第X章的完整内容，3000-4000字"
  - 结尾指令: "请严格控制在3000-4000字范围内，不要过度展开或压缩"
- **修改函数**: `build_writer_prompt()` (第698-797行)
  - 新增参数: `word_min=3000, word_max=4000`
  - 计算: `half_min = word_min // 2`, `half_max = word_max // 2`
  - 提示词改为: "现在要写第X章，1500-2000字，是完整章节的前半部分"
  - 结尾指令: "请严格控制在1500-2000字范围内"
- **修改函数**: `build_continue_prompt()` (第799-831行)
  - 新增参数: `word_min=3000, word_max=4000`
  - 提示词改为: "接着写后半段，1500-2000字，把这章写完"

#### 3. 核心逻辑层（write\_chapter() 主函数，第867-1037行）

- **配置读取增强**:
  ```python
  word_target = cfg("novel", "chapter_word_target", 3500)
  word_min = cfg("novel", "chapter_word_min", 3000)      # 新增
  word_max = cfg("novel", "chapter_word_max", 4000)      # 新增
  ```
- **max\_tokens 动态计算**:
  - 大模型模式: `min(int(word_max * 1.75), 7000)` （替代原来的 `max_tokens * 2 = 8192`）
  - 小模型分段模式: `min(int(word_max // 2 * 1.75), max_tokens_cfg)` （每段上限约3500 tokens）
- **字数补写逻辑优化**:
  ```python
  min_words = word_min           # 使用配置的最小值（替代旧的 word_target * 0.90）
  max_words = word_max           # 新增：最大值保护
  while len(full_content) < min_words and ...:
      if len(full_content) >= max_words:  # 上限判断
          print(f"已达字数上限，停止补写")
          break
  ```
- **日志输出增强**:
  ```
  [OK] 第X章完成，总字数：XXXX字（目标：3000-4000）
  ⚠️ 警告：字数不足（XXXX/3000），建议手动检查或重写    # 如果不足
  ⚠️ 提示：字数略超上限（XXXX/4000），可接受范围        # 如果超标
  ```

### 向后兼容性设计

- 如果 config.yaml 中没有新的 `chapter_word_min` / `chapter_word_max` 配置项
- 系统会回退到默认值：`word_min=3000`, `word_max=4000`
- 保持100%向后兼容，旧配置文件无需修改即可运行

## 架构决策记录

1. **为什么选择"字数范围"而非单一目标值？**
   - 单一目标值（如3000字）对AI模型来说仍然模糊，容易超标或不足
   - 明确的范围（3000-4000）给模型更清晰的约束空间
   - 符合人类写作习惯：章节自然有长短波动，但在合理范围内
2. **为什么 max\_tokens 使用动态计算公式** **`word_max * 1.75`？**
   - 中文字符的 token 化比例约为 1.5-2 tokens/字（取决于分词器）
   - 4000字 × 1.75 = 7000 tokens，留有余量但不会过度宽松
   - 比原来固定的 `*2`（8192 tokens）更精准，减少模型"自由发挥"的空间
   - 使用 `min(..., 7000)` 作为硬上限，防止极端情况
3. **为什么补写逻辑要增加上限保护？**
   - 原来的逻辑只有下限（90%），没有上限，可能导致过度补写
   - 用户反馈"太大杯"说明实际经常远超预期
   - 增加上限后，即使模型生成偏多，也不会通过补写进一步放大问题
   - 达到 word\_max 时停止补写，并给出明确的日志提示
4. **为什么选择 3000-4000 这个范围？**
   - 网络小说单章标准篇幅通常在 3000-4000 字
   - 3000 字保证基本情节完整（不会过于仓促）
   - 4000 字上限防止注水（避免为凑字数而重复表达）
   - 与用户需求完全一致："告诉模型生成3000-4000字就行"

## 关键依赖关系图

```
config.yaml
 └─ novel:
     ├─ chapter_word_target: 3500    ← 调整（原3000）
     ├─ chapter_word_min: 3000       ← 新增
     └─ chapter_word_max: 4000       ← 新增

core/writer.py
 ├─ build_full_chapter_prompt()     ← 修改：新增 word_min/word_max 参数 + 提示词优化
 ├─ build_writer_prompt()           ← 修改：新增 word_min/word_max 参数 + half_min/half_max 计算
 ├─ build_continue_prompt()         ← 修改：新增 word_min/word_max 参数 + 提示词优化
 └─ write_chapter()                 ← 修改：
     ├─ 配置读取（新增 word_min/word_max）
     ├─ 大模型 max_tokens 动态计算（基于 word_max）
     ├─ 分段模式 max_tokens 动态计算（基于 half_max）
     ├─ 补写逻辑优化（新增上限保护）
     └─ 日志输出增强（显示范围+警告提示）
```

