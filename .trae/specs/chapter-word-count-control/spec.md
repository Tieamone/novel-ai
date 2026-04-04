# 章节字数控制优化 Spec

## Why
当前 AI 网文写作系统生成的章节字数过大（"太大杯"），用户反馈需要控制作者模型的输出篇幅在 3000-4000 字范围内。当前系统虽然配置了 `chapter_word_target: 3000`，但实际生成经常远超这个数值，原因包括：
1. 大模型模式下 `max_tokens * 2`（8192 tokens）给予过多输出空间
2. 提示词中只说了"大约3000字"但没有明确上限
3. 字数补写机制只有下限（90%）没有上限控制
4. 分段生成模式下前后半段各1500字，合计目标就是3000字，但实际容易超标

## What Changes
- **修改写作提示词**：在所有生成场景中明确告知模型字数范围为 3000-4000 字
- **调整 max_tokens 配置**：根据字数目标合理设置输出 token 上限
- **优化字数补写逻辑**：增加上限保护，避免过度补写
- **统一字数控制策略**：确保一次性生成和分段生成两种模式的字数控制一致

## Impact
- Affected specs: 写作模块（core/writer.py）、配置文件（config.yaml）
- Affected code:
  - [writer.py](file:///d:/novel-ai/core/writer.py) - build_full_chapter_prompt()、build_writer_prompt()、build_continue_prompt()、write_chapter()
  - [config.yaml](file:///d:/novel-ai/config.yaml) - 可能需要调整 chapter_word_target 或新增相关配置

## ADDED Requirements

### Requirement: 字数范围约束
系统 SHALL 在作者模型生成小说时明确告知模型生成 3000-4000 字的内容，并在提示词和参数层面进行双重约束。

#### Scenario: 大模型一次性生成模式
- **WHEN** 系统检测到高质量/大容量模型（is_high_capacity_model() 返回 True）
- **THEN** 提示词中明确说明"请生成 3000-4000 字的完整章节"，并且 max_tokens 设置为合理值（约 6000-7000 tokens，考虑中文字符 token 化比例）

#### Scenario: 小模型分段生成模式
- **WHEN** 系统使用标准分段模式（前后半段分别生成）
- **THEN** 前半段提示"1500-2000字"，后半段提示"1500-2000字"，确保总和在 3000-4000 字范围内

#### Scenario: 字数补写触发
- **WHEN** 生成内容字数不足下限（2700字，即3000*0.9）
- **THEN** 执行补写，但如果补写后总字数超过 4000 字，则停止补写并提示用户当前字数

### Requirement: 配置项优化
系统 SHALL 支持通过配置文件灵活设置字数范围，便于后续调整。

#### Scenario: 新增配置项
- **WHEN** 管理员需要在 config.yaml 中调整字数要求
- **THEN** 系统支持以下配置：
  - `chapter_word_target`: 目标字数（默认 3500，调整为中间值）
  - `chapter_word_min`: 最小字数（默认 3000）
  - `chapter_word_max`: 最大字数（默认 4000）

## MODIFIED Requirements

### Requirement: 提示词构建函数修改
修改以下函数以包含明确的字数范围指令：

1. **build_full_chapter_prompt()** (第52-147行)
   - 当前：`大约{word_target}字`
   - 修改为：`请生成 {word_min}-{word_max} 字的完整章节内容`

2. **build_writer_prompt()** (第698-796行)
   - 当前：`大约{half_target}字`
   - 修改为：`请生成 {half_min}-{half_max} 字的前半部分`

3. **build_continue_prompt()** (第799-831行)
   - 当前：`大约{half_target}字`
   - 修改为：`请生成 {half_min}-{half_max} 字的后半部分，完成本章`

4. **write_chapter() 主函数** (第867-1028行)
   - 调整 max_tokens 计算逻辑
   - 修改字数补写的上限判断条件

## REMOVED Requirements
无（纯增量优化）

## 技术方案细节

### 1. 配置层修改（config.yaml）
```yaml
novel:
  chapter_word_target: 3500    # 目标字数（中间值）
  chapter_word_min: 3000       # 最小字数
  chapter_word_max: 4000       # 最大字数
  # ... 其他配置保持不变
```

### 2. writer.py 核心修改点

#### 2.1 全局变量/常量调整
- `CHAPTER_MIN_RATIO` 从 0.90 调整为基于 `chapter_word_min` 计算
- 新增 `CHAPTER_MAX_RATIO` 常量（1.15，允许5%超限容错）

#### 2.2 提示词修改示例
```python
# build_full_chapter_prompt() 中
return f"""现在要写第{chapter_num}章的完整内容，{word_min}-{word_max}字。
...
开始写。第一行是章节标题（第{chapter_num}章 + 你拟的标题），然后直接进入正文。
请严格控制在{word_min}-{word_max}字范围内，不要过度展开。"""
```

#### 2.3 max_tokens 动态计算
```python
# write_chapter() 中
if use_single_pass:
    # 中文字符约 1.5-2 tokens/字，4000字 ≈ 6000-8000 tokens
    # 设置为 7000 给一定余量，但不会像之前 *2 那么夸张
    max_tokens_output = min(int(word_max * 1.75), 7000)
else:
    # 分段模式：每段按 half_max * 1.75 计算
    max_tokens_output = min(int(half_max * 1.75), 4096)
```

#### 2.4 补写逻辑优化
```python
# 当前的补写逻辑（第980-1004行）需修改
min_words = chapter_word_min  # 使用配置的最小值
max_words = chapter_word_max  # 新增：最大值保护

while len(full_content) < min_words and supplement_round < MAX_SUPPLEMENT_ROUNDS:
    # ... 补写逻辑 ...
    if len(full_content) >= max_words:  # 新增上限判断
        print(f"  [补写] 已达字数上限（{len(full_content)}/{max_words}），停止补写")
        break
```

### 3. 向后兼容性
- 如果配置文件中没有新的配置项（`chapter_word_min`, `chapter_word_max`），则回退到旧的 `chapter_word_target` 并使用默认值（min=2700, max=4500）
- 保持现有数据库结构不变
- 不影响已生成章节的审稿和导出流程

## 验证标准
1. 生成一章后，检查输出字数是否在 3000-4000 范围内（允许±10%偏差）
2. 日志输出应显示："第X章完成，总字数：XXXX字（目标范围：3000-4000）"
3. 如果字数超标或不足，应有明确的警告提示
4. 大模型模式和分段模式均应遵循相同的字数约束
