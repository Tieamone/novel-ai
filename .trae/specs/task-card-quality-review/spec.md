# 任务卡质量审核 Spec

## Why

当前任务卡生成后**无任何质量审核环节**，直接入库。相比之下，章节正文有完整的三重审核体系（责任编辑 L1/L2/L3 + 伏笔专项评分 + 读者视角四维评估）。这种落差导致：

1. **劣质任务卡直接污染写作流程**：模糊的 plot_goal（如"继续推进剧情"）→ AI 自由发挥 → 偏离大纲 → 连续审稿失败 → 多次重写 → 浪费大量 token
2. **伏笔注入过于强硬**：`_add_outline_fs_to_prompt()` 将批次内所有伏笔以"必须埋入/必须兑现"的强制指令注入 prompt，导致 AI 在任务卡中生硬缝合伏笔要求，牺牲了情节的自然流畅
3. **情绪节奏失衡**：批次边界处情绪标签可能 5 连"铺垫"或连续"冲突"，缺乏节奏校验
4. **缺乏修正闭环**：章节重写时有 `rewrite_task_for_chapter()` 兜底，但这是被动的、被否决后才触发的补救；而如果在任务卡入库前就审核修正，可以从源头减少重写次数

## What Changes

- **新增模块**: `core/task_card_reviewer.py` — 任务卡质量审核器
- **新增功能**: 任务卡审核评分（四维度：具体性/一致性/伏笔融合度/情绪节奏）
- **新增功能**: 伏笔注入密度控制 — 每章上限 + 自然度约束
- **修改**: `split_outline_to_tasks()` — 生成后自动触发审核，不达标则自动修正或请求重试
- **修改**: `_add_outline_fs_to_prompt()` — 伏笔注入从"强制指令"改为"建议+自然融入约束"
- **修改**: `config.yaml` — 新增任务卡审核配置项
- **修改**: `run_planner()` — 新增审核模式选项

## Impact

- Affected specs: `task-card-batch-generation`
- Affected code: `core/planner.py` (_build_task_split_prompt, _build_extend_task_prompt, _add_outline_fs_to_prompt, split_outline_to_tasks, run_planner), `core/task_card_reviewer.py` (新文件), `config.yaml`, `main.py` (导入流程)

---

## ADDED Requirements

### Requirement 1: 任务卡质量审核器

系统 SHALL 提供 `core/task_card_reviewer.py` 模块，对已生成的任务卡进行 AI 审核。

#### Scenario: 四维评分
- **WHEN** 一批任务卡生成完成
- **THEN** 系统调用审核模型，按以下四维评估（每维 1-5 星）：

| 维度 | 检查内容 |
|------|----------|
| **具体性** | plot_goal 是否场景级？有没有"继续推进"这类空泛表述？ |
| **一致性** | 章节间逻辑是否连贯？是否偏离大纲主线？ |
| **伏笔融合度** | 伏笔埋入是否自然融入情节？是否有"强行插入"感？ |
| **情绪节奏** | 情绪标签分布是否合理？是否有连续 >3 章同一标签？ |

#### Scenario: 综合评分与通过标准
- **WHEN** 审核模型返回四维评分
- **THEN** 综合分 = 具体性×3 + 一致性×2 + 伏笔融合度×2 + 情绪节奏×1，满分 40
- **AND** 通过线 = 28（70%）且单维不低于 2 星
- **AND** 输出审核报告：各维评分 + 问题任务卡编号 + 修改建议

#### Scenario: 审核未通过的处理
- **WHEN** 综合评分 < 28 或有维度 < 2 星
- **THEN** 展示审核报告，提供三个选项：
  1. 自动修正（AI 根据审核建议修正问题任务卡）
  2. 重试生成（丢弃当前批次，重新生成）
  3. 手动接受（强制入库，标注为"未经审核"）
- **AND** 默认选项为"自动修正"

#### Scenario: 审核通过
- **WHEN** 综合评分 >= 28 且所有维度 >= 2 星
- **THEN** 直接入库，打印审核摘要（各维评分 + 是否有建议修改项）

### Requirement 2: 任务卡自动修正

系统 SHALL 支持基于审核反馈自动修正问题任务卡。

#### Scenario: 自动修正流程
- **WHEN** 用户选择"自动修正"
- **THEN** 系统构建修正 prompt，内容包括：
  - 原任务卡 JSON
  - 审核报告（具体问题、问题编号、修改建议）
  - 大纲原文
- **AND** 只对标记为"不通过"的任务卡进行修正，通过的任务卡保留不变
- **AND** 修正后重新评分，最多修正 2 次，仍不通过则降级为手动选择

#### Scenario: 修正后入库
- **WHEN** 修正后评分通过
- **THEN** 打印修正摘要（修正了哪些章节、改了什么），入库

### Requirement 3: 伏笔注入密度控制

系统 SHALL 限制每章任务卡的伏笔埋入/兑现数量，并提供自然融入约束。

#### Scenario: 伏笔密度上限
- **WHEN** `_add_outline_fs_to_prompt()` 查询到某章同时有 ≥ 3 个伏笔任务（埋入+兑现合计）
- **THEN** 自动只保留重要度最高的 2 个，其余标记为"延后"，并在日志中输出 `[伏笔密度] 第X章超限(N个)，自动降级(M个)`

#### Scenario: 伏笔注入语气从"强制"改为"引导"
- **WHEN** 伏笔信息注入到任务卡 prompt 中
- **THEN** 当前："必须埋入/必须兑现" → 改为：
  ```
  【大纲伏笔建议（请自然融入，不要生硬插入）】
  以下伏笔建议在本章中安排，但请以情节自然流畅为优先：
  - 第X章建议埋入: OF003 古老封印的松动迹象 (★重要度3)
    融入提示：可以在场景描写或角色对话中不经意地提及，不需要大段展开
  ```
- **AND** 增加"伏笔自然度"约束规则：不得为埋入伏笔而强行改变情节走向

#### Scenario: config 中的伏笔密度配置
- **WHEN** 系统读取配置
- **THEN** `novel.max_foreshadow_per_chapter` 默认值 2，作为每章伏笔上限
- **AND** `novel.foreshadow_injection_style` 默认值 "guided"（引导式），可选 "forced"（强制式，旧行为）

### Requirement 4: split_outline_to_tasks 集成审核

系统 SHALL 在 `split_outline_to_tasks()` 和 `extend_tasks()` 生成任务卡后，自动触发审核流程。

#### Scenario: 新建小说时审核
- **WHEN** `run_planner()` 调用 `split_outline_to_tasks()` 生成任务卡
- **THEN** 每批任务卡生成后自动调用审核
- **AND** 如果启用了 `review_mode`（审稿模式），审核使用交互式确认
- **AND** 如果未启用 `review_mode`，审核通过自动入库，不通过自动修正一次

#### Scenario: 运行时扩展时审核
- **WHEN** `extend_tasks()` 被自动触发
- **THEN** 因为此时处于写作流程中，审核自动运行，不打断用户
- **AND** 不通过时自动修正一次，仍不通过则降级（打印警告但继续入库）

#### Scenario: 审核开关
- **WHEN** 用户在 config 中设置 `novel.task_card_review_enabled = false`
- **THEN** 跳过审核，直接入库（保持向后兼容）

### Requirement 5: 任务卡序列整体节奏校验

系统 SHALL 在全部任务卡生成完毕后，对整体序列进行节奏校验。

#### Scenario: 全序列节奏报告
- **WHEN** 所有批次任务卡生成完毕
- **THEN** 统计情绪标签分布并输出报告：
  ```
  [任务卡节奏报告]
  总计 150 章任务卡
  情绪分布: 铺垫 65章(43%) / 冲突 38章(25%) / 爽点 22章(15%) / 低谷 15章(10%) / 反转 10章(7%)
  ── 节奏检测 ──
  ✓ 情绪标签分布合理
  ✓ 高潮节点分布均匀（平均每 6.8 章一个）
  ⚠ 第88-93章连续6章"铺垫"，建议手动检查
  ⚠ 第120-135章共15章内无"爽点"或"反转"节点，可能缺乏追读动力
  ```
- **AND** 不在写入时阻塞，仅作为信息展示

---

## MODIFIED Requirements

### Requirement: _add_outline_fs_to_prompt 伏笔注入优化

原函数将批次内所有伏笔以"必须埋入"/"必须兑现"的强制语气注入。修改后 SHALL 改为引导式语气 + 密度控制。

#### Scenario: 引导式注入
- **WHEN** `foreshadow_injection_style = "guided"`（默认）
- **THEN** 伏笔注入使用建议语气，附带融入提示，不强制要求
- **AND** 每章伏笔总数不超过 `max_foreshadow_per_chapter`

#### Scenario: 强制式注入（兼容旧行为）
- **WHEN** `foreshadow_injection_style = "forced"`
- **THEN** 保持原有强制注入行为

### Requirement: _build_task_split_prompt 任务卡生成规则增强

原有 prompt 规则侧重于数量控制（不赶进度），修改后 SHALL 增加质量约束。

#### Scenario: 质量约束注入
- **WHEN** 构建任务卡 prompt
- **THEN** 增加以下规则：
  - "伏笔融入"规则：伏笔必须自然融入情节，不得为此改变原有剧情走向
  - "情节目标"规则：禁止使用"继续推进剧情"、"进一步发展"等空泛表述
  - "结尾状态约束"规则：每章结尾必须有一个具体的情节状态（如"主角拿到了日记但被跟踪"），而不是"故事继续发展"

---

## REMOVED Requirements

无