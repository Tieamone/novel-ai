# Checklist

## Task 1: 增强大纲伏笔生成提示词
- [x] outline_manager.py 中系统提示词新增 ≥3 条反模式规则
- [x] ai_suggest_outline_foreshadow() 和 generate_outline_foreshadow() 两处同步更新
- [x] 生成测试：调用 generate_outline_foreshadow() 时产出的伏笔不包含"倒计时/限期/事件结果"类伪伏笔

## Task 2: 新增伏笔审核函数
- [x] review_outline_foreshadow() 函数签名正确（接收伏笔列表 + 大纲 + 人物档案，返回审核结果字典）
- [x] 使用 call_reviewer_api（非 call_author_api）调用审稿模型
- [x] 审核维度包含：生硬检查 / OOC 检查 / 基调一致性 / 分布合理性
- [x] 审核函数被集成到 generate_outline_foreshadow() 中
- [x] 审核结果在入库前展示给用户，用户可逐条确认/跳过/删除
- [x] 代码编译通过 (`py_compile`)
- [x] 导入测试通过

## Task 3: 注释动态伏笔自动提取
- [x] main.py _save_chapter_memory() 中 new_foreshadowing 相关代码被注释（非删除）
- [x] 注释处标注日期和原因："2026-05 伏笔系统改造：禁自动提取"
- [x] redeem_foreshadowing 逻辑和摘要生成逻辑未被注释
- [x] 写一章后 foreshadowing 表无新增数据
- [x] 旧数据（已有的 8 条动态伏笔）仍在表中且 query 正常
- [x] 代码编译通过

## Task 4: 更新 PROJECT_PROFILE.md
- [x] 包含"新建小说流程"完整调用链及代码文件位置
- [x] 包含"续写/自动生成流程"完整调用链及代码文件位置
- [x] 包含"审稿流程"完整调用链及代码文件位置
- [x] 包含"导出流程"完整调用链及代码文件位置
- [x] 包含"大纲伏笔管理流程"完整调用链及代码文件位置
- [x] 包含"断点/恢复流程"完整调用链及代码文件位置
- [x] 包含项目目录结构图
- [x] 包含所有 11 张数据库表的结构说明
- [x] 包含 config.yaml 全部配置项说明
- [x] 包含 14 个核心模块的职责与接口清单