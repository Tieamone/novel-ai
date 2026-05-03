# AI 思维链 / 记忆体

## 当前上下文

**时间**: 2026-04-26
**任务**: 全项目代码审查——检查所有模块的 bug（已完成）

## 本次完成的工作

### 任务: 完整项目代码审查（14个源文件 + 3个配置文件）

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

## 下一步计划

- 按优先级修复 BUG-04/BUG-05/BUG-06（影响面最大）
- 清理死参数/死代码（BUG-02/BUG-03）
- 考虑统一路径处理（CQ-01）

***

## 历史上下文（最近一次记录：2026-04-20）

### 任务1: 模型列表更新与默认模型更换

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

## 下一步计划

- （无待办任务，等待用户下一步指令）

## 本次完成的工作

### 任务1: 章节字数范围控制优化

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

## 下一步计划

- （无待办任务，等待用户下一步指令）

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

