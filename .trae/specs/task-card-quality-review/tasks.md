# 任务卡质量审核 任务列表

## 任务概览

| 序号 | 任务 | 依赖 | 可并行 |
|------|------|------|--------|
| T1 | 创建 task_card_reviewer.py 模块 | 无 | ✅ |
| T2 | 改造伏笔注入：密度控制 + 语气引导化 | 无 | ✅ (可与T1并行) |
| T3 | 集成审核到 split_outline_to_tasks | T1, T2 | ❌ |
| T4 | 集成审核到 extend_tasks | T1, T2 | ❌ (可与T3并行) |
| T5 | 新增 config.yaml 配置项 | 无 | ✅ (可与T1并行) |
| T6 | 任务卡序列整体节奏报告 | T1 | ❌ |
| T7 | 运行端到端测试验证 | T1~T6 | ❌ |

---

## T1: 创建 task_card_reviewer.py 模块

**文件**: `core/task_card_reviewer.py`（新文件）

### T1.1 编写审核 System Prompt

参考 `core/reviewer.py` 中的 `REVIEWER_SYSTEM` 风格，构建 `TASK_CARD_REVIEWER_SYSTEM`：

- 角色定位：小说策划师，审核章节任务卡的可行性
- 四维评估：具体性(5分)、一致性(5分)、伏笔融合度(5分)、情绪节奏(5分)
- 输出格式：JSON

### T1.2 实现 `review_task_cards()` 函数

```python
def review_task_cards(novel_name: str, tasks: list,
                      outline: str = "", batch_start: int = 1) -> dict:
    """审核一批任务卡，返回审核结果。"""
```

- 调用 `call_reviewer_api()`，使用审核模型
- temperature=0.25（与责任编辑一致）
- 返回 dict：{score_total, score_specificity, score_consistency, score_fs_integration, score_rhythm, issues: [{chapter_num, problem, suggestion}], pass: bool}

### T1.3 实现 `revise_task_cards()` 函数

```python
def revise_task_cards(novel_name: str, tasks: list,
                      review_result: dict, outline: str = "") -> list:
    """根据审核反馈修正问题任务卡，返回修正后的 tasks。"""
```

- 只修正 review_result 中标记为"不通过"的章节
- 调用 `call_author_api()`
- 返回修正后的完整 tasks 列表

### T1.4 编写审核 Prompt 模板

构建 `build_task_card_review_prompt()` 函数，组装审核输入：
- 大纲摘要（截取 500 字）
- 任务卡列表（仅包含 chapter_num + plot_goal + emotion_tag，简洁格式）
- 四维评分标准

---

## T2: 改造伏笔注入

**文件**: `core/planner.py`

### T2.1 修改 `_add_outline_fs_to_prompt()` 语气

- 将"必须埋入"/"必须兑现"改为"建议埋入"/"建议兑现"
- 增加融入提示：`融入提示：可以在场景描写或角色对话中不经意地提及，不需要大段展开`
- 增加自然度约束：`请以情节自然流畅为优先，不得为埋入伏笔而强行改变情节走向`

### T2.2 新增伏笔密度控制

- 在 `_add_outline_fs_to_prompt()` 开头加入每章计数逻辑
- 若某章伏笔任务 > `cfg("novel", "max_foreshadow_per_chapter", 2)`，按重要度排序，只保留最多的 N 个
- 被过滤掉的伏笔打印日志：`[伏笔密度] 第X章超限(5个)，保留重要度最高的2个，降级: OF003, OF007, OF012`

### T2.3 增加 inject_style 控制

- 在函数签名字新增 `inject_style: str = None` 参数
- 若传入 None 则从 `cfg("novel", "foreshadow_injection_style", "guided")` 读取
- "guided" 模式使用引导语气，"forced" 保持旧行为

### T2.4 修改 `_build_task_split_prompt()` 中的伏笔注入

- 更新 prompt 中"任务卡规则"的第 2 条（已有），额外增加伏笔自然度规则

---

## T3: ✅ 集成审核到 split_outline_to_tasks

**文件**: `core/planner.py`

### T3.1 修改 `_generate_single_batch()` 增加审核步骤

在 `_generate_single_batch()` 中，任务卡 JSON 解析成功后、入库前：

1. 检查 `cfg("novel", "task_card_review_enabled", True)`
2. 若启用 → 调用 `review_task_cards()`
3. 若通过 → 直接入库
4. 若不通过 → 自动修正一次 → 重新评分 → 仍不通过 → 根据 `review_mode` 决定交互或降级入库

### T3.2 增加审核进度提示

- `[任务卡审核] 第1-30章审核中...`
- `[OK] 审核通过: 综合28/40 (具体性4 一致性3 伏笔融合4 情绪节奏3)`
- `[修正] 审核未通过(22/40)，自动修正中...`

---

## T4: ✅ 集成审核到 extend_tasks

**文件**: `core/planner.py`

### T4.1 在 extend_tasks 尾部加入审核

任务卡解析成功后，加入审核流程（使用 `call_reviewer_api`）。
因为是运行时自动扩展，审核失败时自动修正一次，仍失败则打印警告但继续入库（不中断写作流程）。

---

## T5: 新增 config.yaml 配置项

**文件**: `config.yaml`

### T5.1 在 `novel` 节新增

```yaml
novel:
  # ... 现有配置 ...
  
  # 任务卡质量审核
  task_card_review_enabled: true     # 是否启用任务卡审核
  task_card_review_pass_score: 28    # 审核通过线（满分40）
  max_foreshadow_per_chapter: 2      # 每章伏笔埋入/兑现上限
  foreshadow_injection_style: "guided"  # 伏笔注入语气: guided(引导式) / forced(强制式)
```

---

## T6: ✅ 任务卡序列整体节奏报告

**文件**: `core/task_card_reviewer.py`

### T6.1 实现 `generate_rhythm_report()` 函数

```python
def generate_rhythm_report(novel_name: str) -> str:
    """读取全部任务卡，生成情绪节奏报告。"""
```

- 统计情绪标签分布
- 检测连续同一标签超过 3 章
- 检测高潮节点间隔
- 仅打印报告，不阻塞流程

### T6.2 在 split_outline_to_tasks 全部完成后调用

- 在所有批次生成完毕后，调用一次 `generate_rhythm_report()`
- 打印报告摘要到控制台

---

## T7: ✅ 端到端测试验证

### T7.1 审核器测试
- 生成一批任务卡 → 调用 review → 验证评分在 0-40 范围
- 构造低质任务卡（空泛 plot_goal）→ 验证评分被压低
- 修正功能：审核不通过 → 自动修正 → 重新评分通过

### T7.2 伏笔密度测试
- 构造某章有 5 个伏笔 → 验证只注入 2 个
- 验证日志打印正确

### T7.3 导入流程测试
- 用现有小说大纲导入 → 验证审核正常运行
- 验证 disabled 时跳过审核

### T7.4 节奏报告测试
- 生成 30 章任务卡 → 验证节奏报告输出

---

## 任务依赖关系

```
T1 (审核模块) ──┬── T3 (split_outline_to_tasks)
                ├── T4 (extend_tasks)
                └── T6 (节奏报告)

T2 (伏笔改造) ──┬── T3 (split_outline_to_tasks)
                └── T4 (extend_tasks)

T5 (config.yaml) ─── 无依赖

T7 (测试) ─── 依赖 T1~T6
```

**建议实施顺序**: T1 + T2 + T5 并行 → T3 + T4 并行 → T6 → T7