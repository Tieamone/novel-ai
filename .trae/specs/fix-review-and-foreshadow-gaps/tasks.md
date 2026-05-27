# Tasks

## T1: ✅ 大纲伏笔修正闭环

修改 `core/outline_manager.py` 中的 `generate_outline_foreshadow()` 函数：

- [x] T1.1: 新增 `_revise_single_foreshadow()` 函数 — 调用 reviewer API 修正单条不通过伏笔，失败返回 None
- [x] T1.2: 新增 `_revise_failed_foreshadow()` 函数 — 遍历不通过项逐条修正，返回修正后列表，打印总结
- [x] T1.3: 修改入库逻辑 — 审稿后先收集不通过项 → 修正 → 重审 → 通过入库 / 仍不通过丢弃

## T2: ✅ 读者审稿降级放行

修改 `core/reviewer.py` 中的 `write_and_review()` 函数：

- [x] T2.1: 读者审稿 `review_error` 路径从 "标记审稿失败 + return """ 改为 "警告 + 跳过二审 + 已审核保存 + 兑现伏笔 + return content"

# Task Dependencies

- T1 和 T2 无依赖，已并行实施完成