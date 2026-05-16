# 伏笔系统架构改造 Spec

## Why
当前项目存在两个互相独立、质量参差不齐的伏笔系统。动态伏笔（foreshadowing 表）是 AI 每章写完后自我提取的，存在"虚假埋入""虚假兑现""自引用闭环"三大问题——AI 自己写的东西、自己判断什么是伏笔、自己评估是否已兑现，全程无人类参与。大纲伏笔（outline_foreshadowing 表）虽已集成到写作流程中，但缺少伏笔质量审核环节，无检查生硬/OOC/基调一致性的机制。本次改造的目标是：**关闭不可控的动态伏笔、强化大纲伏笔生成与审核流程、确保伏笔闭环真正可控**。

## What Changes
- **增强伏笔生成提示词**：追加"不是倒计时/事件节点"等反模式规则，确保生成的伏笔是真正的叙事悬念
- **新增伏笔审核步骤**：生成草案后由审稿模型逐条检查生硬/OOC/基调一致性，只保留高质量伏笔
- **禁用动态伏笔提取**：注释（不删除）`_save_chapter_memory` 中的 `new_foreshadowing` 自动提取代码，伏笔来源仅保留大纲伏笔
- **写作流程**保持现有结构（大纲→伏笔→任务卡→写作），仅增强伏笔质量控制
- **更新 PROJECT_PROFILE.md**：完整记录项目的所有功能流程、使用流程、写作流程、审核流程及对应代码文件位置

## Impact
- Affected specs: 无（新建 spec）
- Affected code: `core/outline_manager.py`, `core/reviewer.py`, `main.py`, `docs/PROJECT_PROFILE.md`
- **BREAKING**: 无——动态伏笔代码仅被注释，不删除；现有数据库数据不受影响

## ADDED Requirements

### Requirement: 伏笔生成提示词增强
伏笔生成系统 SHALL 在现有 6 条设计原则基础上，追加反模式过滤规则。

#### Scenario: AI 避免生成"倒计时"类伪伏笔
- **WHEN** AI 根据大纲生成伏笔草案
- **THEN** 系统提示词中明确指示"不要将限时倒计时/限期/事件节点当作伏笔"，生成的伏笔必须包含"悬而未决的问题"性质

#### Scenario: AI 避免生成"已发生结果"类伪伏笔
- **WHEN** AI 生成伏笔时遇到大纲中"XX人被打败""XX地被攻陷"等确定性结果
- **THEN** 不生成以结果为核心的伏笔，而应挖掘该事件中隐藏的"秘密/原因/起源"

### Requirement: 伏笔审核机制
大纲伏笔生成后，系统 SHALL 使用审稿模型（call_reviewer_api）逐条检查伏笔质量。

#### Scenario: 审核生硬伏笔
- **WHEN** 一条伏笔的埋入方式过于刻意（如"主角突然想起小时候听到的一个传说"）
- **THEN** 审稿模型标记该伏笔为"生硬"，建议修改或删除

#### Scenario: 审核 OOC 伏笔
- **WHEN** 一条伏笔要求某角色做出不符合其人物档案的行为
- **THEN** 审稿模型标记该伏笔为"OOC 风险"，建议调整角色的参与方式

#### Scenario: 审核基调一致性
- **WHEN** 一条伏笔的暗黑风格与大纲的搞笑沙雕风格冲突
- **THEN** 审稿模型标记该伏笔为"基调不一致"，建议调整风格或删除

#### Scenario: 审核通过后入库
- **WHEN** 所有伏笔审核完毕
- **THEN** 系统展示审核结果，用户逐条确认后写入 outline_foreshadowing 表

### Requirement: 禁用动态伏笔自动提取
系统 SHALL 禁用每章写完后 AI 自动提取新伏笔的功能（代码注释不删除）。

#### Scenario: 写作完成后不再自动提取伏笔
- **WHEN** 一个章节审稿通过后执行 `_save_chapter_memory`
- **THEN** `new_foreshadowing` 提取逻辑被注释，不再调用 `add_foreshadowing`；章节摘要、伏笔兑现检测仍正常执行

#### Scenario: 写作时仍可使用已有动态伏笔
- **WHEN** 动态伏笔表中仍有旧数据（如当前书的 8 条）
- **THEN** `get_foreshadow_hints` 仍正常返回 hints，写作 prompt 中仍可提示，但不再产生新条

### Requirement: PROJECT_PROFILE.md 完整流程文档
项目白皮书 SHALL 包含所有功能流程、使用流程、写作流程、审核流程的完整说明及对应代码文件路径。

## REMOVED Requirements
（无移除项——动态伏笔代码仅注释，不删除）