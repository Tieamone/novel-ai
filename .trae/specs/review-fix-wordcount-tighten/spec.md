# 审稿逻辑全面诊断 + 字数控制强化 Spec

## Why
从 Terminal#261-283 发现3个严重问题：
1. **审稿返回格式异常后触发了重写而非重试审稿** — 根因：`_review_error_result()` 缺少 `review_error: True` 字段，导致异常结果被当作"质量不通过"处理
2. **字数超标52%（6096 vs 4000）却被描述为"略超"** — 警告阈值设计不合理
3. **max_tokens 参数约束不足** — 大模型模式仍给予过多输出空间导致字数失控

## What Changes
- **修复**: `_review_error_result()` 增加 `review_error: True` 字段（核心根因修复）
- **修改**: 字数警告阈值分级（10%内=略超 / 10-30%=超标 / >30%=严重超标）
- **调整**: 进一步收紧大模型 max_tokens 上限（5500→4500）
- **新增**: 审稿异常时的智能重试机制（最多重试3次审稿后才考虑重写）

## Impact
- Affected code:
  - [core/reviewer.py](file:///d:/novel-ai/core/reviewer.py) - `_review_error_result()` + `write_and_review()`
  - [core/writer.py](file:///d:/novel-ai/core/writer.py) - max_tokens + 字数警告逻辑
  - [config.yaml](file:///d:/novel-ai/config.yaml) - 可能需要新增配置项

---

## 问题1：审稿异常触发重写（致命Bug）

### 根因分析

**调用链追踪**:

```
write_and_review() 第568行
  ↓ result = review_chapter(...)
  
review_chapter() 第446行
  ↓ if not parsed: (JSON解析失败)
  ↓ return _review_error_result(...)  ← 返回 {pass: False, ...}
                                    ❌ 缺少 review_error: True!

write_and_review() 第571行
  ↓ if result.get("review_error"):   → False (字段不存在!)
  
write_and_review() 第589行
  ↓ if not result.get("pass"):       → True (pass=False)
  ↓ 进入「不通过」分支 → 触发重写!    ← 错误路径!
```

**证据对比**:

| 检测点 | 预期值 | 实际值 | 结果 |
|--------|--------|--------|------|
| `result.get("review_error")` | `True` | `None` (key不存在) | ❌ 判断失败 |
| `result.get("pass")` | `False` | `False` | ✅ 但走错分支 |
| 执行路径 | `continue`(重试审稿) | 进入不通过分支→重写 | ❌ 致命错误 |

**Terminal 日志印证**:
```
[责任编辑] 审稿返回格式异常，本轮判定为不通过     ← review_chapter() 的print
  [重试审稿] 责任编辑返回异常（未知错误），重新审稿...  ← 这行来自哪里？
  第2次写作尝试...                                  ← 进入了重写循环!
```

> ⚠️ 注意：如果新代码（带 `if result.get("review_error")` 分支）生效，应该看到 `[重试审稿]` 后直接 continue 回到循环开头。但如果 `_review_error_result()` 没有 `review_error` 字段，则该分支不会执行，而是走到下面的 `if not result.get("pass")` 分支。
>
> **关键矛盾**：Terminal同时出现了 `[重试审稿]` 和 `第2次写作尝试`，说明可能存在代码版本不一致或缓存问题。但无论哪种情况，**缺少 `review_error` 字段是确定的缺陷**。

### 修复方案

#### Requirement: _review_error_result() 必须包含 review_error 标记
系统 SHALL 在审稿异常返回的结果中包含 `"review_error": True` 字段。

```python
def _review_error_result(message: str, issue: str) -> dict:
    return {
        "pass": False,
        "review_error": True,      # ✅ 新增：标记这是异常而非质量不通过
        "issue": issue,             # ✅ 新增：异常原因描述
        # ... 其他字段保持不变
    }
```

#### Scenario: 审稿API超时
- **WHEN** API调用超时或网络错误
- **THEN** 返回 `{"review_error": True, "pass": False, "issue": "审稿API调用失败"}`
- **AND** `write_and_review()` 检测到 `review_error=True` → `continue`（重试审稿）

#### Scenario: JSON解析失败
- **WHEN** AI返回的内容无法解析为JSON
- **THEN** 返回 `{"review_error": True, "pass": False, "issue": "审稿结果格式异常"}`
- **AND** 不触发重写，而是重新调用 `review_chapter()`

---

## 问题2：字数超标52%却显示"略超"

### 数据分析

| 指标 | 值 |
|------|-----|
| 实际字数 | 6096 字 |
| 目标上限 | 4000 字 |
| **超出量** | **2096 字** |
| **超标率** | **+52.4%** |
| 当前警告 | `⚠️ 提示：字数略超上限（6096/4000），可接受范围` |

**问题**: 将 **+52%** 的超标描述为"略超"，且标注为"可接受范围"，这完全不合理。

### 当前警告逻辑

**位置**: [writer.py 第1070-1074行](file:///d:/novel-ai/core/writer.py#L1070-L1074)（推测行号）

```python
if total > word_max:
    print(f"  ⚠️ 提示：字数略超上限（{total}/{word_max}），可接受范围")
```

**缺陷**:
- 只有单一警告级别，无分级
- "略超""可接受范围"措辞对严重超标有误导性
- 没有任何强制措施（只是打印一句话）

### 修复方案

#### Requirement: 字数超标分级警告
系统 SHALL 根据超标程度显示不同级别的警告信息。

```
超标率          级别      警告文案                          处理建议
─────────────  ────────  ───────────────────────────────  ──────────────
≤10% (≤4400)  🟢 略超    "⚠️ 字数略超上限（XXXX/4000）"      无需处理
10%-30%       🟡 标超    "⚠️ 字数超标（XXXX/4000，+Y%）"      建议检查是否可精简
>30% (>5200)  🔴 严重超标 "🔴 字数严重超标（XXXX/4000，+Y%）"  建议重写或手动裁剪
```

#### Requirement: 严重超标时提供自动裁剪选项
当超标率 >30% 时，系统 SHALL 提示用户可选择：
1. 保留当前版本继续
2. 自动裁剪到目标范围内（保留开头和结尾，裁剪中间冗余部分）
3. 手动编辑后确认

---

## 问题3：max_tokens 约束仍不足

### 当前参数分析

| 参数 | 当前值 | 计算公式 | 实际效果 |
|------|--------|---------|----------|
| word_max | 4000 | 配置值 | 目标上限 |
| max_tokens | ~5600 | `min(4000*1.4, 5500)` | 上次修复后的值 |
| 实际输出 | 6096 | - | **超出max_tokens!** |

**问题**: 即使 max_tokens 设为 5500，实际输出了 6096 字（约 9000+ tokens），说明：
1. 模型可能忽略了 max_tokens 限制
2. 或者 token/字 比例估算偏差过大（实际约 1.5 tokens/字，我们用的 1.4）

### 修复方案

#### Requirement: 进一步收紧 max_tokens
将系数从 1.4 降到 1.2，硬上限从 5500 降到 4800。

```python
# 修改前
max_tokens=min(int(word_max * 1.4), 5500)  # = 5600

# 修改后
max_tokens=min(int(word_max * 1.2), 4800)  # = 4800
```

**预期效果**: 
- 4000字目标 × 1.2 = 4800 tokens
- 按 1.5 tokens/字计算 → 约 3200 字（安全范围）
- 按 1.8 tokens/字计算 → 约 2667 字（偏短但可控）
- 取中间值约 **2800-3500 字**

> ⚠️ 这可能导致字数偏保守。如果用户反馈字数不足，可以微调系数到 1.3。

#### Requirement: 增加生成后字数硬截断
当字数超过 `word_max * 1.2`（即 4800 字）时，系统 SHALL 自动截断到最后一个自然段落边界。

```python
hard_limit = int(word_max * 1.2)  # 4800
if len(full_content) > hard_limit:
    # 找到最后一个完整段落（以\n\n分割）
    paragraphs = full_content.split('\n\n')
    truncated = []
    current_len = 0
    for para in paragraphs:
        if current_len + len(para) > hard_limit:
            break
        truncated.append(para)
        current_len += len(para)
    full_content = '\n\n'.join(truncated)
    print(f"  [裁剪] 字数{original_len}超过硬上限{hard_limit}，已裁剪至{len(full_content)}字")
```

---

## MODIFIED Requirements

### Requirement: write_and_review() 异常处理增强
增加审稿异常的**最大重试次数**限制，避免无限重试。

```python
MAX_REVIEW_RETRIES = 3  # 审稿最多重试3次
review_retry_count = 0  # 当前已重试次数

for attempt in range(max_retry):
    # ... 写作 ...
    
    result = review_chapter(...)
    
    if result.get("review_error"):
        review_retry_count += 1
        if review_retry_count <= MAX_REVIEW_RETRIES:
            print(f"  [重试审稿] 第{review_retry_count}次...")
            continue  # 重试审稿，不重写
        else:
            print(f"  [错误] 审稿连续{MAX_REVIEW_RETRIES}次异常")
            # 降级为"不通过"处理，触发重写
            break
    
    # 正常审稿流程...
    review_retry_count = 0  # 成功后重置计数器
```

---

## 技术方案总结

| 问题 | 根因 | 修复方式 | 文件 | 改动量 |
|------|------|---------|------|--------|
| 审稿异常→重写 | `_review_error_result()` 缺少 `review_error` 字段 | 新增字段 + 增加重试计数 | reviewer.py | ~15行 |
| 字数52%"略超" | 警告逻辑无分级 | 三级警告 + 措施建议 | writer.py | ~20行 |
| 6096字失控 | max_tokens 过松(1.4系数/5500上限) | 收紧到1.2/4800 + 硬截断 | writer.py | ~25行 |

## 验证标准
1. 审稿格式异常时终端应显示 `[重试审稿] 第N次...` 且不触发 `第N次写作尝试`
2. 字数 4200 → 显示"略超"；5000 → 显示"超标"；6000 → 显示"严重超标"+ 裁剪选项
3. 使用大模型生成一章，验证字数落在 3000-4500 范围内（±10%偏差可接受）
