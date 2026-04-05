# Tasks

## Task 1: 修复 _review_error_result() 缺少 review_error 字段（P0-致命）
- [x] 1.1 验证 `_review_error_result()` 返回字典包含 `"review_error": True`（已存在第171行）
- [x] 1.2 验证字段名匹配（修复 `issue` vs `retry_hint` 兼容读取）

**验证**: ✅ 审稿超时/格式异常时，write_and_review() 应进入"重试审稿"分支

---

## Task 2: 增强审稿异常重试机制（P0-重要）
- [x] 2.1 在 write_and_review() 中增加 `MAX_REVIEW_RETRIES = 3` 常量
- [x] 2.2 增加 `review_retry_count` 计数器（循环外初始化为0）
- [x] 2.3 在 `if result.get("review_error"):` 分支中检查重试次数
  - 未达上限(≤3) → continue（重试审稿），显示"第N/3次重试"
  - 达上限(>3) → pass降级（break到不通过分支，触发重写）
  - 字段名兼容：`result.get('issue') or result.get('retry_hint', '未知错误')`
- [x] 2.4 正常审稿通过后（双重审核通过）重置计数器为0

**验证**: 连续3次审稿异常后应降级触发重写，日志显示"降级为不通过处理"

---

## Task 3: 字数警告分级（P1-体验优化）
- [x] 3.1 新增硬截断机制（hard_limit = word_max * 1.2 = 4800）
  - 超过时按段落边界（\n\n）裁剪
  - 裁剪后打印 `[裁剪]` 日志
- [x] 3.2 三级警告逻辑:
  - ≤10% (≤4400) → 🟢 "略超: +X%，+Y字"
  - 10%-30% (4400-5200) → 🟡 "标超: +X%，+Y字 | 建议检查"
  - >30% (>5200) → 🔴 "严重超标: +X%，+Y字 | 建议重写/裁剪"
- [x] 3.3 移除旧的"可接受范围"误导性措辞

**验证**: 4200字→🟢略超；5000字→🟡标超25%；6000字→🔴严重超标50%+[裁剪]

---

## Task 4: 收紧 max_tokens + 硬截断机制（P1-核心控制）
- [x] 4.1 大模型模式 max_tokens: `min(int(word_max * 1.2), 4800)` = **4800**
  - 修改前: `min(int(word_max * 1.4), 5500)` = 5600
- [x] 4.2 分段模式 max_tokens: `min(int(word_max // 2 * 1.5), max_tokens_cfg)`
  - 修改前: `min(int(word_max // 2 * 1.75), ...)` 
  - 每段上限保持4096不变
- [x] 4.3 硬截断阈值 = word_max * 1.2 = 4800字
  - 超过时按段落边界自动裁剪

**验证**: 生成章节字数应在 3000-4500 范围内（±10%偏差可接受）

---

## Task Dependencies
```
Task 1 (P0) ✅ → 已完成（字段已存在）
  ↓
Task 2 (P0) ✅ → 已完成（增强重试机制）
  ↓
Task 3 (P1) ✅ → 已完成（三级警告+硬截断）
Task 4 (P1) ✅ → 已完成（max_tokens收紧）
```

**执行顺序**: 全部完成 ✅

---

## 🎉 项目状态：所有任务已完成！

**完成时间**: 2026-04-05
**总修改文件**: 2个（reviewer.py, writer.py）
**核心改动点**: 
- reviewer.py: ~20行（重试机制增强）
- writer.py: ~35行（警告分级+硬截断+max_tokens收紧）
**状态**: 生产就绪 ✅
