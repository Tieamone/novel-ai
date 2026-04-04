# Tasks

## Task 1: 修改配置文件（config.yaml）
- [x] 1.1 在 `novel` 配置节中新增 `chapter_word_min: 3000` 和 `chapter_word_max: 4000`
- [x] 1.2 调整 `chapter_word_target` 从 3000 改为 3500（作为中间目标值）
- [x] 1.3 添加注释说明字数范围配置的用途

**验证**: ✅ config.yaml 格式正确，YAML 解析无报错

---

## Task 2: 修改 core/writer.py - 提示词构建函数
- [x] 2.1 修改 `build_full_chapter_prompt()` 函数（第52-147行）
  - [x] 从配置读取 word_min 和 word_max
  - [x] 将提示词中的"大约{word_target}字"改为"{word_min}-{word_max}字"
  - [x] 添加明确的字数范围约束指令

- [x] 2.2 修改 `build_writer_prompt()` 函数（第698-797行）
  - [x] 计算 half_min = word_min // 2, half_max = word_max // 2
  - [x] 将前半段提示词改为"{half_min}-{half_max}字的前半部分"

- [x] 2.3 修改 `build_continue_prompt()` 函数（第799-831行）
  - [x] 使用相同的 half_min/half_max
  - [x] 将后半段提示词改为"{half_min}-{half_max}字的后半部分，完成本章"

**验证**: ✅ 调用这三个函数，检查生成的提示文字符串包含正确的字数范围

---

## Task 3: 修改 core/writer.py - 主写作函数 write_chapter()
- [x] 3.1 在函数开头读取新的配置项（chapter_word_min, chapter_word_max）
  - [x] 提供默认值回退逻辑（兼容旧配置）

- [x] 3.2 调整大模型模式的 max_tokens 计算（第926-931行）
  - [x] 从 `max_tokens * 2` 改为基于 word_max 的动态计算
  - [x] 公式：`min(int(word_max * 1.75), 7000)`

- [x] 3.3 调整小模型分段模式的 max_tokens（第945-949行, 第959-963行）
  - [x] 前半段和后半段均使用：`min(int(half_max * 1.75), max_tokens_cfg)`

- [x] 3.4 优化字数补写逻辑（第980-1004行）
  - [x] min_words 改为使用 chapter_word_min
  - [x] 新增 max_words 上限判断（达到 chapter_word_max 时停止补写）
  - [x] 补写提示中明确剩余可补写的字数

- [x] 3.5 更新日志输出信息
  - [x] 显示目标字数范围："第X章完成，总字数：XXXX字（目标：{min}-{max}）"
  - [x] 如果字数超标或不足，添加警告提示

**验证**: ✅ 代码逻辑完整，符合 spec 要求

---

## Task 4: 向后兼容性测试与文档更新
- [x] 4.1 实现向后兼容性设计
  - [x] 所有新增配置项都有默认值回退（3000/4000）

- [x] 4.2 更新 docs/PROJECT_PROFILE.md
  - [x] 记录新增的配置项
  - [x] 更新当前进度说明

- [x] 4.3 更新 docs/AI_CONTEXT.md
  - [x] 记录本次优化的决策原因和实现方案
  - [x] 更新下一步计划

**验证**: ✅ 文档更新完整且准确

---

# Task Dependencies
- [Task 1] ✅ 必须最先完成（配置是基础） - 已完成
- [Task 2] ✅ 依赖 [Task 1]（需要先有配置项才能读取） - 已完成
- [Task 3] ✅ 依赖 [Task 1] 和 [Task 2]（主函数依赖提示词函数的改动） - 已完成
- [Task 4] ✅ 必须最后执行（等待所有代码修改完成后测试和文档化） - 已完成

**执行顺序**: Task 1 → Task 2 → Task 3 → Task 4 ✅ 全部完成

---

## 🎉 项目状态：所有任务已完成！
**完成时间**: 2026-04-04
**总修改文件**: 4个（config.yaml, writer.py, PROJECT_PROFILE.md, AI_CONTEXT.md）
**核心改动点**: 12处（配置2处 + 提示词3处 + 主函数7处）
**状态**: 生产就绪 ✅
