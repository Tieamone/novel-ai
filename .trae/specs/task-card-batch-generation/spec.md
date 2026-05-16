# 任务卡分批生成优化 Spec

## Why

当前任务卡生成模块存在严重问题：当目标章节数较多（如150章）时，`split_outline_to_tasks` 的 `full_batch=True` 模式会让 AI 模型一次性输出全部章节的任务卡 JSON。大多数模型（尤其是中小模型）的输出 token 上限无法承载 50-150 条完整 JSON 记录，导致解析失败、任务卡全部丢失，系统回退到"实时分析模式"——每写一章都要重新调用 AI 分析大纲，严重影响写作效率和任务卡质量。

同时，现有分批机制（`extend_tasks`）缺乏跨批次的任务卡上下文继承，批次间的逻辑衔接完全依赖"上章结尾文字"，而非"上一批任务卡的具体规划内容"，容易出现前后矛盾、情节断裂。

## What Changes

- **新增**：自适应批次大小推荐引擎——根据模型输出能力自动推荐合理的每批章数
- **新增**：任务卡生成时三种批次选项——小批次(10章/批) / 中批次(30章/批) / 大批次(50章/批) / 全量
- **新增**：跨批次上下文桥接——每批生成时注入上一批最后 5 张任务卡的完整内容
- **新增**：情绪节奏跨越继承——每批生成时告知上一批最后 5 章的情绪标签序列
- **新增**：大纲覆盖进度追踪——确保每批任务卡覆盖的大纲段落不遗漏、不重复
- **修改**：`split_outline_to_tasks()` 签名增加 `batch_size` 参数，支持多批次循环生成
- **修改**：`_build_extend_task_prompt()` 增加上一批任务卡上下文注入
- **修改**：`run_planner()` 中的批次选择菜单增加小/中批次选项
- **修改**：`newbook.txt` 导入路径不再硬编码 `full_batch=True`，改为交互选择

## Impact

- Affected specs: `project-optimization`, `project-survey`
- Affected code: `core/planner.py` (split_outline_to_tasks, extend_tasks, _build_task_split_prompt, _build_extend_task_prompt, run_planner), `main.py` (导入路径), `config.yaml` (新增配置项)

## ADDED Requirements

### Requirement 1: 自适应批次大小推荐

系统 SHALL 在任务卡生成前，根据当前选择的作者模型的最大输出 token 数，自动计算并推荐合理的每批章节数。

#### Scenario: 小模型推荐小批次
- **WHEN** 作者模型的 max_output_tokens <= 4096
- **THEN** 系统提示"检测到当前模型输出能力有限，建议每批 10-15 章"，并默认使用小批次(10章)

#### Scenario: 中等模型推荐中批次
- **WHEN** 作者模型的 max_output_tokens 在 4097~16384 之间
- **THEN** 系统提示"建议每批 20-30 章"，并默认使用中批次(30章)

#### Scenario: 大模型可使用全量
- **WHEN** 作者模型的 max_output_tokens >= 16384
- **THEN** 系统提供全量选项，但用户仍可选择分批

#### Scenario: 无法获取模型信息
- **WHEN** 模型管理器无法获取当前模型的 token 上限信息
- **THEN** 系统默认使用中批次(30章)，并提供手动选择

### Requirement 2: 多级批次大小选择

在 `run_planner()` 的任务卡生成环节，系统 SHALL 提供以下批次选项（而非当前的二选一）。

#### Scenario: 目标章数较多时的选项
- **WHEN** `target_chapters > 10`
- **THEN** 菜单展示：小批次(10章/批)、中批次(30章/批)、大批次(50章/批)、全量一次性，并标注推荐项

#### Scenario: 目标章数较少时的选项
- **WHEN** `target_chapters <= 10`
- **THEN** 自动使用全量一次性生成

#### Scenario: 用户自定义批次大小
- **WHEN** 用户选择"自定义"
- **THEN** 提示输入每批章数，校验 3~100 之间的整数

### Requirement 3: 跨批次上下文桥接

`split_outline_to_tasks()` 在多批次循环生成模式中，SHALL 将上一批最后 5 张任务卡的完整内容（`chapter_num`、`plot_goal`、`emotion_tag`）注入到下一批生成的 prompt 中。

#### Scenario: 第二批及后续批次的上下文注入
- **WHEN** 正在生成第N批任务卡（N>1），且上一批已成功生成
- **THEN** prompt 中包含 `【上一批任务卡结尾衔接】` 区块，列出上一批最后5张任务卡，并要求"本章任务卡必须从上一批结尾自然衔接"

#### Scenario: 情绪节奏跨越
- **WHEN** 注入上一批任务卡结尾时
- **THEN** 同时告知上一批最后 5 章的情绪标签序列，约束提示：**"上一批结尾情绪节奏为 [...], 请确保本批开头情绪标签与上一批结尾平滑过渡，不得出现突兀的情绪跳跃"**

#### Scenario: 第一批生成
- **WHEN** 正在生成第一批任务卡
- **THEN** 不注入任何上批上下文，仅按现有逻辑使用大纲 + 大纲伏笔

### Requirement 4: 大纲覆盖进度追踪

系统 SHALL 在多批次生成过程中，将每批生成的任务卡章节范围追加记录到文件 `data/{novel_name}/task_coverage.log`，并在下一批生成时告知 AI 已覆盖的大纲段落范围，避免重复或遗漏。

#### Scenario: 批次间大纲段落传递
- **WHEN** 生成第N批任务卡时，已有前N-1批记录
- **THEN** prompt 中包含 `【已覆盖章节范围】第1-X章（前N-1批已生成）`，并要求"本批从第X+1章开始，请确保不重复覆盖已生成章节的剧情节点"

#### Scenario: 大纲覆盖记录格式
- **WHEN** 一批任务卡成功生成并入库
- **THEN** 追加一行到 `task_coverage.log`：`批次{N}：第{A}-{B}章，{C}条任务卡，时间戳`

### Requirement 5: 导入路径交互选择

`newbook.txt` 导入路径 (`main.py` 中 `full_batch=True` 硬编码) SHALL 改为交互模式，让用户根据模型能力选择批次大小。

#### Scenario: 导入小说时的批次选择
- **WHEN** 用户通过 `newbook.txt` 导入小说
- **THEN** 在生成任务卡前展示批次选项，与交互式向导保持一致

## MODIFIED Requirements

### Requirement: split_outline_to_tasks 多批次循环

原本的 `split_outline_to_tasks()` 在 `full_batch=False` 时仅生成第一批（最多 `pre_split` 章），剩余章节依赖后续的 `extend_tasks()` 调用。修改后，系统 SHALL 在 `full_batch=False` + 指定 `batch_size` 时，**循环调用**生成多批任务卡，每批生成完立即入库，并注入上下文作为下一批的衔接依据，直到达到 `target_chapters`。

#### Scenario: 150章用30章/批循环生成
- **WHEN** `target_chapters=150`, `batch_size=30`
- **THEN** 系统依次生成第1-30章、第31-60章、第61-90章、第91-120章、第121-150章，共5批，每批生成完成后展示进度 `[30/150]`、`[60/150]` ... `[150/150]`

#### Scenario: 某批次生成失败
- **WHEN** 某批次 AI 返回的 JSON 解析失败
- **THEN** 自动重试一次；若仍失败，提示用户可手动重试该批次或缩小批次大小后继续

### Requirement: extend_tasks 增强衔接

`extend_tasks()` 函数是写作进行中任务卡用完时的扩展入口。本次 SHALL 为其增加跨批次上下文桥接——在生成新一批任务卡时，注入已有任务卡的最后 5 张作为上下文。

#### Scenario: 写作途中任务卡扩展
- **WHEN** `get_next_chapter_goal()` 检测到任务卡已用完，调用 `extend_tasks()`
- **THEN** 从数据库读取最后 5 张已有任务卡，注入 prompt 的 `【已有任务卡结尾衔接】` 区块

## REMOVED Requirements

无