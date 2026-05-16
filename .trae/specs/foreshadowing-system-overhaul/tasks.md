# Tasks

- [x] Task 1: 增强大纲伏笔生成提示词
  - [x] 修改 `core/outline_manager.py` 中 `generate_outline_foreshadow()` 和 `ai_suggest_outline_foreshadow()` 的系统提示词
  - [x] 追加反模式规则："不要把事件倒计时当作伏笔""不要把确定性结果当作伏笔""真正的伏笔必须包含悬而未决的问题"
  - [x] 验证：重新生成大纲伏笔时，生成的条目不包含"XX天后会XX"类伪伏笔

- [x] Task 2: 新增伏笔审核函数 `review_outline_foreshadow()`
  - [x] 在 `core/outline_manager.py` 中新增函数，接收大纲伏笔列表 + 大纲全文 + 人物档案
  - [x] 调用审稿模型 `call_reviewer_api`，逐条检查：生硬/OOC/基调一致性/分布合理性
  - [x] 返回审核结果字典：`{fid: {passed: bool, issues: [str], suggestion: str}}`
  - [x] 在 `generate_outline_foreshadow()` 中，AI 生成草案后调用此审核函数
  - [x] 展示审核结果给用户，用户确认前的伏笔不写入数据库

- [x] Task 3: 注释动态伏笔自动提取代码
  - [x] 在 `main.py` 的 `_save_chapter_memory()` 函数中，注释 `new_foreshadowing` 提取和 `add_foreshadowing` 调用
  - [x] 保留伏笔兑现检测（`redeemed_foreshadowing`）和摘要生成
  - [x] 在注释处添加说明："2026-05 伏笔系统改造：禁自动提取，改用大纲伏笔。旧代码保留以备回滚"
  - [x] 验证：编译通过

- [x] Task 4: 更新 PROJECT_PROFILE.md 完整流程文档
  - [x] 补充项目技术栈版本、目录结构
  - [x] 记录 6 大流程：新建小说 / 续写/自动生成 / 审稿 / 导出 / 大纲伏笔管理 / 断点恢复
  - [x] 每个流程标注完整的函数调用链和代码文件位置
  - [x] 记录所有 14 个核心模块的职责、函数清单、接口定义
  - [x] 记录数据库 11 张表的结构说明
  - [x] 记录 config.yaml 所有配置项说明

# Task Dependencies
- Task 2 依赖 Task 1（审核提示词需要与生成提示词的规则对齐）✅
- Task 1、3、4 可并行执行 ✅