# 任务卡质量审核 验证清单

## 审核器基础功能
- [x] `task_card_reviewer.py` 文件存在且语法正确
- [x] `review_task_cards()` 正确调用 `call_reviewer_api()`
- [x] 审核返回四维评分 + 综合评分（满分 40）
- [x] 审核通过标准：综合 >= 28 且单维 >= 2
- [x] 审核返回 issues 列表，每个 issue 含 chapter_num, problem, suggestion
- [x] `revise_task_cards()` 只修正标记为不通过的章节
- [x] 修正后重新评分，最多修正 2 次
- [x] `build_task_card_review_prompt()` 正确组装大纲 + 任务卡 + 评分标准

## 伏笔注入改造
- [x] `_add_outline_fs_to_prompt()` 在 "guided" 模式下使用建议语气
- [x] 引导式注入包含"融入提示"和自然度约束
- [x] 每章伏笔任务 > `max_foreshadow_per_chapter` 时自动降级
- [x] 降级伏笔在日志中打印（`[伏笔密度]` 格式）
- [x] "forced" 模式下保持旧行为
- [x] inject_style 参数正确读取 config 默认值

## 审核集成 — split_outline_to_tasks
- [x] `_generate_single_batch()` 生成后自动触发审核（enabled=true）
- [x] 审核通过时打印评分摘要
- [x] 审核不通过时自动修正一次
- [x] 修正后仍不通过 + review_mode → 交互选择
- [x] 修正后仍不通过 + 非 review_mode → 打印警告，继续入库
- [x] `task_card_review_enabled=false` 时跳过审核
- [x] 审核使用 reviewers 模型（不占用 author 资源）

## 审核集成 — extend_tasks
- [x] `extend_tasks()` 生成后自动触发审核
- [x] 运行时不通过 → 自动修正一次 → 仍不通过 → 警告但继续入库
- [x] 不中断写作流程

## 节奏报告
- [x] `generate_rhythm_report()` 统计情绪标签分布
- [x] 检测连续 >3 章同一标签
- [x] 检测高潮节点间隔
- [x] 报告在全部批次完成后打印

## config.yaml
- [x] `task_card_review_enabled` 默认 true
- [x] `task_card_review_pass_score` 默认 28
- [x] `max_foreshadow_per_chapter` 默认 2
- [x] `foreshadow_injection_style` 默认 "guided"
- [x] 代码中正确读取配置并带默认值兜底

## 端到端流程
- [ ] 交互式新建 → 生成 30 章任务卡 → 审核通过 → 入库（需实际运行测试）
- [ ] 审核不通过 → 自动修正 → 修正后通过 → 入库（需实际运行测试）
- [ ] 审核不通过 → 修正仍不通过 + review_mode → 用户手动选择（需实际运行测试）
- [ ] 导入路径 → 审核正常运行（需实际运行测试）
- [ ] 写作途中 extend_tasks → 审核运行 → 不中断写作（需实际运行测试）
- [ ] 节奏报告在全部任务卡生成后打印（需实际运行测试）

## 回归验证
- [ ] 旧小说继续写作不受影响（需实际运行测试）
- [ ] 章节生成/审稿/导出功能不受影响（需实际运行测试）
- [ ] 伏笔系统其他部分不受影响（需实际运行测试）
- [ ] `foreshadow_injection_style = "forced"` 时行为与旧版一致（需实际运行测试）