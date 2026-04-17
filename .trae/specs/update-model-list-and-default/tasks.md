# Tasks

- [x] Task 1: 更新 data/custom_models.json 模型列表
  - [x] 将现有 qwen3.6-plus 的 has_free_quota 标记为 false（无额度）
  - [x] 新增 qwen3.6-flash 模型配置
  - [x] 新增 qwen3.6-flash-2026-04-16 模型配置
  - [x] 新增 qwen3.6-35b-a3b 模型配置
  - [x] 新增 glm-5.1 模型配置
  - [x] 新增 qwen3.6-plus-2026-04-02 模型配置
  - [x] 新增 gui-plus-2026-02-26 模型配置
  - [x] 新增 qwen-flash-character-2026-02-26 模型配置
  - [x] 新增 qwen3.5-35b-a3b 模型配置

- [x] Task 2: 修改 config.yaml 默认模型配置
  - [x] 将 author.default_model 从 qwen3.6-plus 更换为 qwen3.6-flash
  - [x] 将 reviewer.default_model 从 qwen3.6-plus 更换为 qwen3.6-flash
  - [x] 将 reader_reviewer.default_model 从 qwen3.6-plus 更换为 qwen3.6-flash

- [x] Task 3: 更新 core/model_manager.py 模型分类
  - [x] 在 MODEL_CATEGORIES 中添加新模型分类映射
  - [x] 确保新模型能被正确识别和筛选

# Task Dependencies
- [Task 2] 依赖于 [Task 1] 完成
- [Task 3] 依赖于 [Task 1] 完成
