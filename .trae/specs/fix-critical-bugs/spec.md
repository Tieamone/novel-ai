# 修复关键运行时Bug Spec

## Why
系统在生成第26章时崩溃，出现两个关键错误：
1. `TypeError: get() takes from 2 to 3 positional arguments but 4 were given` - reader_reviewer.py调用cfg()函数参数错误
2. `API错误: 400 - Range of input length should be [1, 3072]` - 审稿内容超出审核模型的token限制

## What Changes
- 修复 config_loader.py 的 cfg() 函数以支持嵌套配置读取（4个参数）
- 修复 reviewer.py 和 reader_reviewer.py 的审稿内容截断逻辑，确保不超过模型token限制
- 优化 writer.py 的结尾规则逻辑矛盾（笔误修正）

## Impact
- Affected specs: 配置管理、审稿流程、写作质量检查
- Affected code:
  - core/config_loader.py (cfg函数)
  - core/reader_reviewer.py (配置调用 + 内容截断)
  - core/reviewer.py (内容截断)
  - core/writer.py (提示词修正)

## ADDED Requirements

### Requirement: 嵌套配置支持
config_loader的get()函数 SHALL 支持嵌套字典访问，允许传入多个key参数或使用default关键字参数。

#### Scenario: 读取三层嵌套配置
- **WHEN** 调用 `cfg("model", "reader_reviewer", "enabled", True)`
- **THEN** 函数应返回 `config["model"]["reader_reviewer"]["enabled"]` 的值，如果不存在则返回默认值True

### Requirement: 审稿内容长度限制
系统 SHALL 在调用审稿API前自动截断内容，确保不超过模型的max_tokens限制。

#### Scenario: 超长章节审稿
- **WHEN** 章节内容超过4000字（约3000 tokens）
- **THEN** 系统应智能截断：保留开头和结尾各1500字，中间用省略号连接
- **AND** 应在控制台打印截断提示

### Requirement: 写作提示词准确性
writer.py中的写作约束提示词 SHALL 无逻辑矛盾，确保AI能正确理解要求。

#### Scenario: 结尾规则一致性
- **WHEN** AI阅读结尾铁律第4条和第5条
- **THEN** 规则应明确要求"应有画面感"和"应形成阅读驱动力"，而非矛盾表述

## MODIFIED Requirements

### Requirement: 配置函数签名
原cfg(section, key, default)函数修改为支持可变参数：
```python
def get(section: str, key: str, *args, default=None):
    """
    支持多层嵌套配置读取
    例: cfg("model", "reader_reviewer", "enabled", True)
    等价于: config["model"]["reader_reviewer"].get("enabled", True)
    """
```

### Requirement: 审稿流程健壮性
审稿流程 SHALL 增加输入验证：
1. 检查内容长度是否超过阈值（3000字）
2. 如果超长则执行智能截断
3. 记录截断日志供调试

## REMOVED Requirements
无

## Implementation Details

### Fix 1: config_loader.py - 扩展cfg()函数
位置: core/config_loader.py 第27-29行
修改: 使其支持*args接收额外的嵌套key，最后一个参数为default

### Fix 2: reviewer.py - 内容截断
位置: core/reviewer.py build_review_prompt() 和 review_chapter()
添加: 在构建prompt前检查内容长度，超长时截断

### Fix 3: reader_reviewer.py - 内容截断 + 配置修复
位置: core/reader_reviewer.py 第113行和第177-180行
修改1: 修复cfg()调用
修改2: 添加内容截断逻辑

### Fix 4: writer.py - 提示词修正
位置: core/writer.py 第687-691行
修改: 修正结尾规则的笔误
