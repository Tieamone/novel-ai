# 任务卡分批生成优化 任务列表

## 任务概览

| 序号 | 任务 | 依赖 | 可并行 |
|------|------|------|--------|
| T1 | 创建自适应批次大小推荐函数 | 无 | ✅ |
| T2 | 实现跨批次上下文桥接 Prompt 构建 | 无 | ✅ (可与T1并行) |
| T3 | 改造 split_outline_to_tasks 支持多批次循环 | T1, T2 | ❌ |
| T4 | 改造 extend_tasks 增强衔接 | T2 | ❌ (可与T3并行) |
| T5 | 改造 run_planner 多级批次菜单 | T1 | ❌ |
| T6 | 改造 main.py 导入路径交互选择 | T1 | ❌ |
| T7 | 新增 config.yaml 配置项 | 无 | ✅ (可与T1并行) |
| T8 | 端到端测试与验证 | T1~T7 | ❌ |

---

## T1: 创建自适应批次大小推荐函数

**文件**: `core/planner.py`

### T1.1 在文件顶部新增 `_recommend_batch_size()` 函数

- 尝试从 `api_client.py` 获取当前作者模型的 `max_output_tokens`
- 若 max_output_tokens <= 4096 → 推荐 10 章/批
- 若 max_output_tokens <= 8192 → 推荐 20 章/批
- 若 max_output_tokens <= 16384 → 推荐 30 章/批
- 若 max_output_tokens > 16384 → 标记为"大模型"，推荐全量
- 若无法获取 → 推荐 30 章/批

函数签名:
```python
def _recommend_batch_size() -> tuple:
    """返回 (推荐每批章数, 模型等级字符串, max_tokens)"""
```

### T1.2 从 api_client.py 暴露 `get_current_author_max_tokens()` 函数

- 在 `api_client.py` 中新增函数，返回当前作者模型的 `max_tokens` 配置值
- 若模型信息无法获取，返回 None

---

## T2: 实现跨批次上下文桥接 Prompt 构建

**文件**: `core/planner.py`

### T2.1 新增 `_build_batch_bridge_block()` 函数

从数据库读取指定范围的最后 N 张任务卡（默认5张），构建 prompt 注入块。

函数签名:
```python
def _build_batch_bridge_block(novel_name: str, prev_end_chapter: int, count: int = 5) -> str:
    """
    读取第 (prev_end_chapter - count + 1) 到 prev_end_chapter 的任务卡，
    构建跨批次衔接 prompt 块。
    返回空字符串如果没有任务卡可读取。
    """
```

返回格式:
```
【上一批任务卡结尾衔接】
以下为上一批最后{count}章的任务卡，本批任务卡必须从上一批结尾自然衔接，不能出现剧情跳跃或重复：

  第{ch}章 | 情绪:{tag} | {plot_goal}
  ...

上一批结尾情绪节奏为: [{tag1}, {tag2}, ..., {tag5}]
请确保本批前3章的情绪标签与上一批结尾平滑过渡，不得出现突兀的情绪跳跃。
```

### T2.2 新增 `_build_coverage_block()` 函数

构建已覆盖章节范围的 prompt 块。

函数签名:
```python
def _build_coverage_block(from_chapter: int) -> str:
    """返回已覆盖进度提示"""
```

返回格式:
```
【已覆盖章节范围】第1-{from_chapter-1}章（前面批次已生成）
本批从第{from_chapter}章开始，请确保不重复覆盖已生成章节的剧情节点，自然延续前文发展。
```

### T2.3 修改 `_build_task_split_prompt()` 接受桥接参数

- 新增可选参数 `prev_tasks_context: str = ""` 和 `coverage_info: str = ""`
- 将桥接块插入到 prompt 中大纲之前的位置

### T2.4 修改 `_build_extend_task_prompt()` 接受桥接参数

- 新增可选参数 `prev_tasks_context: str = ""`
- 将桥接块插入到 prompt 中

---

## T3: 改造 split_outline_to_tasks 支持多批次循环

**文件**: `core/planner.py`

### T3.1 修改函数签名

```python
def split_outline_to_tasks(outline: str, novel_name: str,
                           review_mode: bool = False,
                           target_chapters: int = 0,
                           full_batch: bool = False,
                           batch_size: int = 50,
                           start: int = 1):
```
新增 `batch_size` 参数。

### T3.2 实现多批次循环逻辑

当 `full_batch=False` 时：
1. 计算总批数 `total_batches = ceil(target_chapters / batch_size)`
2. 循环：`for batch_idx in range(total_batches)`
3. 每批：
   - 计算本批范围 `[start_ch, end_ch]`
   - 若 batch_idx > 0，调用 `_build_batch_bridge_block()` 和 `_build_coverage_block()` 获取上下文
   - 调用 `call_author_api()` 生成任务卡
   - JSON 解析入库
   - 打印进度 `[已生成 {end_ch}/{target_chapters} 章任务卡]`
4. 若某批 JSON 解析失败 → 自动重试一次 → 若仍失败 → 提示用户手动干预

### T3.3 保留全量模式逻辑

当 `full_batch=True` 时，保持现有逻辑不变（但 max_tokens 需要动态调整以匹配实际章数）。

### T3.4 覆盖进度记录

每批成功后，追加写入 `data/{novel_name}/task_coverage.log`

---

## T4: 改造 extend_tasks 增强衔接

**文件**: `core/planner.py`

### T4.1 在生成 prompt 前注入已有任务卡上下文

在 `extend_tasks()` 函数中：
1. 调用 `_build_batch_bridge_block(novel_name, from_chapter - 1)` 获取上一批结尾上下文
2. 将上下文传入 `_build_extend_task_prompt()` 的 `prev_tasks_context` 参数

### T4.2 extend_tasks 也接受 batch_size 参数

用于控制扩展时每批生成多少章，默认沿用 `config.yaml` 中的 `pre_split_chapters`。

---

## T5: 改造 run_planner 多级批次菜单

**文件**: `core/planner.py`

### T5.1 替换当前二选一菜单

位置：`run_planner()` 中 Step 6 任务卡部分（约第1089-1098行）

当前代码：
```python
pre_split = cfg("novel", "pre_split_chapters", 50)
if target_chapters > pre_split:
    print(f"\n任务卡生成方式：")
    print(f"  1. 分批生成（每次{pre_split}章，推荐用于超长篇小说）")
    print(f"  2. 一次性生成全部{target_chapters}章（推荐）")
    batch_choice = input(f"\n请选择（默认2）：").strip() or "2"
    full_batch = (batch_choice == "2")
else:
    full_batch = True
```

改为：调用 `_recommend_batch_size()` 获取推荐值，展示多级菜单。

菜单格式:
```
任务卡生成方式（当前模型推荐：每批{推荐值}章）：
  1. 小批次（10章/批）    -- 适合输出能力较弱的模型
  2. 中批次（30章/批）    -- 推荐 [RECOMMENDED]
  3. 大批次（50章/批）    -- 适合中大型模型
  4. 一次性生成全部{X}章  -- 仅推荐大模型使用
  5. 自定义每批章数
请选择（默认{推荐选项}）：
```

### T5.2 传递参数到 split_outline_to_tasks

解析用户选择，设置 `batch_size` 和 `full_batch` 参数：
- 选1 → `full_batch=False, batch_size=10`
- 选2 → `full_batch=False, batch_size=30`
- 选3 → `full_batch=False, batch_size=50`
- 选4 → `full_batch=True`
- 选5 → 输入自定义数字 → `full_batch=False, batch_size=custom`

---

## T6: 改造 main.py 导入路径交互选择

**文件**: `main.py`

### T6.1 修改 newbook.txt 导入路径

位置：约第1913-1925行

当前代码：
```python
target_chapters = import_data.get("target_chapters", 100)
...
split_outline_to_tasks(
    import_data.get("outline", ""), novel_name,
    target_chapters=target_chapters,
    full_batch=True,
)
```

改为：在调用 `split_outline_to_tasks()` 前增加批次选择交互，复用 `planner.py` 中的 `_recommend_batch_size()`。

```python
batch_size, model_level, _ = _recommend_batch_size()
if target_chapters > batch_size:
    print(f"\n任务卡生成方式（当前模型推荐：每批{batch_size}章）：")
    ...  # 与 run_planner 一致的菜单
```

---

## T7: 新增 config.yaml 配置项

**文件**: `config.yaml`

### T7.1 新增配置

在 `novel` 节点下新增：

```yaml
novel:
  # ... 现有配置 ...
  
  # 任务卡分批配置
  batch_size_small: 10     # 小批次每批章数
  batch_size_medium: 30    # 中批次每批章数  
  batch_size_large: 50     # 大批次每批章数（与 pre_split_chapters 保持一致）
  batch_bridge_count: 5    # 跨批次上下文桥接引入的任务卡数量
```

---

## T8: 端到端测试与验证

### T8.1 小模型测试

用输出 token 较小的模型（如 qwen3.6-flash），选择小批次(10章)，验证：
- 每批 10 章任务卡正确生成
- 跨批次上下文正确注入
- 情绪节奏标识正确传递
- 覆盖日志正确写入

### T8.2 大模型测试

用长上下文模型，选择全量生成 30 章，验证一次性生成正常。

### T8.3 导入路径测试

用 `newbook.txt` 导入，验证批次选择交互正常。

### T8.4 写作途中扩展测试

写到最后一张任务卡后自动触发 `extend_tasks`，验证上下文桥接正确。

---

## 任务依赖关系

```
T1 (推荐函数) ──┬── T3 (split_outline_to_tasks 循环)
                ├── T5 (run_planner 菜单)
                └── T6 (main.py 导入)

T2 (桥接Prompt) ──┬── T3 (split_outline_to_tasks 循环)
                  └── T4 (extend_tasks 增强)

T7 (config.yaml) ─── 无依赖，可随时完成

T8 (端到端测试) ─── 依赖 T1~T7 全部完成
```