# 大纲伏笔与读者审稿闭环修复 Spec

## Why

闭环走查发现两个缺口：
1. **大纲伏笔审稿无修正**：`generate_outline_foreshadow()` 中，审稿不通过的伏笔直接丢弃（用户实际遇到 12/22 丢弃），没有类似任务卡的"修正→重审"闭环。
2. **读者审稿失败硬中断**：`write_and_review()` 中，读者审稿 API 异常时直接标记"审稿失败"并丢弃整章内容——责任编辑已通过的章节，被一个二次校验的 API 抖动白白浪费。

## What Changes

- **大纲伏笔修正闭环**：`generate_outline_foreshadow()` 中，审稿不通过的伏笔调用 AI 修正一次 → 重审 → 仍不通过再丢弃
- **读者审稿降级策略**：`write_and_review()` 中，读者审稿 API 异常时打印警告→跳过二审→按责任编辑结果放行（而非硬中断）

## Impact

- Affected specs: `task-card-quality-review`（同属审稿闭环体系）
- Affected code: `core/outline_manager.py`, `core/reviewer.py`

## MODIFIED Requirements

### Requirement: 大纲伏笔生成带修正闭环

`generate_outline_foreshadow()` 在自动化模式下，审稿不通过的伏笔 SHALL 先调用 AI 修正一次再重审。

#### Scenario: 审稿通过
- **WHEN** 伏笔审稿结果为 passed=true
- **THEN** 直接入库

#### Scenario: 审稿不通过，修正后通过
- **WHEN** 伏笔审稿 passed=false，且 review 结果包含 issues/suggestion
- **THEN** 调用 AI 修正该伏笔（传入原描述 + 审稿意见）
- **AND** 修正后重新审稿
- **AND** 若通过则入库

#### Scenario: 修正后仍不通过
- **WHEN** 修正后重审仍不通过
- **THEN** 打印警告并丢弃该伏笔（与原行为一致）

#### Scenario: 修正 API 异常
- **WHEN** 修正 API 调用失败
- **THEN** 打印警告，丢弃该伏笔（不中断主流程）

### Requirement: 读者审稿异常降级放行

`write_and_review()` 中，读者视角审稿异常时 SHALL 降级放行而非硬中断。

#### Scenario: 读者审稿 API 异常
- **WHEN** `reader_review_chapter()` 返回 `review_error: True`
- **THEN** 打印警告"读者视角评估异常，跳过二审，编辑审核通过即放行"
- **AND** 继续走已审核流程（return content，后续自动兑现伏笔等）
- **AND** 不影响章节完成状态

#### Scenario: 读者审稿禁用
- **WHEN** `reader_reviewer.enabled = false`
- **THEN** 跳过审核，直接通过（保持现有行为不变）

#### Scenario: 读者审稿正常不通过
- **WHEN** 读者审稿正常返回但不通过
- **THEN** 走重写流程（保持现有行为不变）