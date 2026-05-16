# 任务卡分批生成优化 验证清单

## 核心功能验证

### 自适应批次推荐
- [ ] `_recommend_batch_size()` 函数正确返回 (推荐章数, 模型等级, max_tokens)
- [ ] max_output_tokens <= 4096 时推荐 10 章/批
- [ ] max_output_tokens <= 8192 时推荐 20 章/批
- [ ] max_output_tokens <= 16384 时推荐 30 章/批
- [ ] max_output_tokens > 16384 时标记为大模型，推荐全量
- [ ] 无法获取模型信息时默认推荐 30 章/批
- [ ] `api_client.py` 暴露了 `get_current_author_max_tokens()` 供外部调用

### 跨批次上下文桥接
- [ ] `_build_batch_bridge_block()` 正确从数据库读取最后 N 张任务卡
- [ ] 桥接块格式包含：每章编号、情绪标签、情节目标
- [ ] 桥接块包含上一批结尾的情绪标签序列
- [ ] 桥接块包含"不得出现突兀情绪跳跃"的约束提示
- [ ] 无任务卡可读取时返回空字符串
- [ ] `_build_coverage_block()` 正确生成覆盖进度提示
- [ ] `_build_task_split_prompt()` 接受并正确插入桥接参数
- [ ] `_build_extend_task_prompt()` 接受并正确插入桥接参数
- [ ] 第一批生成时不注入任何桥接上下文（无意义）

### 多批次循环生成
- [ ] `split_outline_to_tasks` 的 `batch_size` 参数有效
- [ ] 计算总批数逻辑正确 `ceil(target_chapters / batch_size)`
- [ ] 每批完成后正确展示进度 `[已生成 X/Y 章任务卡]`
- [ ] 第二批及之后正确注入跨批次上下文
- [ ] 某批 JSON 解析失败时自动重试一次
- [ ] 重试仍失败时给出明确的用户提示
- [ ] 每批生成完成后正确写入 `task_coverage.log`
- [ ] `full_batch=True` 时保持原有全量一次性逻辑不变
- [ ] 任务卡写入数据库使用正确的 INSERT OR REPLACE 保证幂等

### 多级批次菜单 (run_planner)
- [ ] 目标章数 > 10 时展示多级菜单（小/中/大/全量/自定义）
- [ ] 目标章数 <= 10 时自动全量一次性
- [ ] 菜单正确标注推荐项（基于 `_recommend_batch_size()`）
- [ ] 选1 → batch_size=10, full_batch=False
- [ ] 选2 → batch_size=30, full_batch=False
- [ ] 选3 → batch_size=50, full_batch=False
- [ ] 选4 → full_batch=True
- [ ] 选5 → 自定义输入 3~100 间有效，无效时重新输入
- [ ] 自定义输入超出范围时给出友好提示

### 导入路径交互 (main.py)
- [ ] newbook.txt 导入不再硬编码 `full_batch=True`
- [ ] 导入路径展示与 run_planner 一致的批次选择菜单
- [ ] 选择后正确传递 batch_size/full_batch 到 split_outline_to_tasks
- [ ] 已有小说继续写作时 extend_tasks 增强衔接正常

### config.yaml 配置
- [ ] `batch_size_small` 默认 10
- [ ] `batch_size_medium` 默认 30
- [ ] `batch_size_large` 默认 50
- [ ] `batch_bridge_count` 默认 5
- [ ] 代码中正确读取配置项且带默认值兜底

### 端到端流程
- [ ] 交互式新建 → 小批次(10章) → 生成 30 章小说 → 3 批完成，批次间衔接无逻辑断裂
- [ ] 交互式新建 → 中批次(30章) → 生成 100 章小说 → 进度正确，情绪节奏连续
- [ ] 交互式新建 → 全量 → 生成 30 章 → 一次性成功
- [ ] newbook.txt 导入 → 批次选择 → 生成正确
- [ ] 写作途中任务卡耗尽 → extend_tasks 自动触发 → 新任务卡与已有任务卡衔接合理

### 回归验证
- [ ] 现有小说继续写作不受影响（无 batch_size 参数时走原逻辑）
- [ ] 审稿功能不受影响
- [ ] 导出功能不受影响
- [ ] 大纲伏笔注入任务卡的功能不受影响
- [ ] `pre_split_chapters` 旧配置项仍正确工作（作为大批次默认值）