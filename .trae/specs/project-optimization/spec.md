# 项目全面优化 Spec

## Why
经过全面代码审查，发现项目存在多个性能、安全性和代码质量问题需要优化，以提升系统稳定性、可维护性和用户体验。

## What Changes
- 修复数据库连接泄漏风险（多处未使用上下文管理器）
- 优化错误处理和异常信息质量
- 改进内存管理和性能
- 增强安全性（SQL注入防护、输入验证）
- 提升代码质量和可维护性

## Impact
- Affected specs: 数据库管理、API调用、错误处理、写作流程、审稿系统
- Affected code: 全部核心模块

---

## ADDED Requirements

### Requirement: 数据库连接安全管理
系统 SHALL 使用上下文管理器或try-finally确保所有数据库连接正确关闭。

**优先级**: P0 (严重)

#### Scenario: 异常情况下连接关闭
- **WHEN** 数据库操作过程中发生异常
- **THEN** 连接必须被正确关闭，不会导致连接池耗尽

### Requirement: 错误信息增强
系统 SHALL 提供清晰、可操作的错误信息，包含足够的上下文。

**优先级**: P1 (重要)

#### Scenario: API调用失败
- **WHEN** API调用失败
- **THEN** 错误信息应包含：错误类型、HTTP状态码、建议操作、重试次数

### Requirement: 内存使用优化
系统 SHALL 避免不必要的内存占用和大对象重复加载。

**优先级**: P1 (重要)

#### Scenario: 大文本处理
- **WHEN** 处理超过10KB的文本内容
- **THEN** 应使用流式处理或分块读取，避免一次性加载到内存

### Requirement: 输入验证加强
系统 SHALL 对所有用户输入进行严格验证和清理。

**优先级**: P1 (重要)

#### Scenario: 恶意输入处理
- **WHEN** 用户输入包含特殊字符或超长字符串
- **THEN** 系统应安全处理，不崩溃且不执行危险操作

## MODIFIED Requirements

### Requirement: 审稿流程健壮性
审稿流程 SHALL 增加更完善的错误恢复机制和数据一致性保证。

**当前问题**:
- 审稿失败时状态可能不一致
- 并发访问时可能产生竞态条件
- 缺少事务保护

### Requirement: API调用容错性
API调用模块 SHALL 提供更智能的重试策略和降级方案。

**当前问题**:
- 重试逻辑不够灵活
- 特定错误类型处理不足
- 缺少熔断机制

### Requirement: 写作流程可靠性
写作流程 SHALL 确保数据完整性和状态一致性。

**当前问题**:
- force_approved状态处理有风险
- 任务认领存在竞态条件
- 内容注入风险

## REMOVED Requirements
无

---

## Implementation Details

### Phase 1: 关键修复（P0 - 必须立即修复）

#### Fix 1.1: 数据库连接管理统一化
**位置**: 
- main.py (约20处get_connection()调用)
- memory_manager.py (全部方法)
- reviewer.py (review_chapter)
- reader_reviewer.py (reader_review_chapter)

**方案**: 
- 创建数据库连接上下文管理器装饰器
- 或在所有函数中使用try-finally确保conn.close()
- 优先使用 `with` 语句

#### Fix 1.2: 内存管理优化
**位置**:
- writer.py (_save_chapter_memory)
- memory_manager.py (compress_old_summaries)
- exporter.py (clean_for_export)

**方案**:
- 避免大字符串重复拼接（使用列表+join）
- 流式处理超大文本
- 及时释放不再需要的变量

### Phase 2: 重要改进（P1 - 应该尽快修复）

#### Fix 2.1: 错误处理增强
**位置**:
- api_client.py (_call_dashscope, _call_gemini)
- reviewer.py (review_chapter)
- main.py (generate_chapter_auto, _recover_review_failed)

**方案**:
- 统一异常处理模式
- 提供结构化的错误信息
- 添加错误码和建议操作
- 记录详细的调试日志

#### Fix 2.2: 输入验证加强
**位置**:
- main.py (_validate_novel_name, 所有input()调用)
- planner.py (用户输入的大纲、角色名等)
- writer.py (外部数据导入)

**方案**:
- 长度限制（防止DoS）
- 字符白名单/黑名单
- SQL特殊字符转义
- 路径遍历防护

#### Fix 2.3: 并发安全改进
**位置**:
- main.py (_claim_task_for_writing)
- db.py (所有写操作)
- memory_manager.py (状态更新)

**方案**:
- 使用数据库事务包裹关键操作
- 添加适当的锁机制
- 使用原子操作替代读-改-写模式

### Phase 3: 质量提升（P2 - 建议优化）

#### Fix 3.1: 代码去重和重构
**位置**:
- 多个文件中的_extract_json_obj函数（3处重复）
- 多个文件中的_to_int函数（2处重复）
- 截断函数可以统一

**方案**:
- 提取到公共工具模块 core/utils.py
- 统一接口和行为
- 减少维护成本

#### Fix 3.2: 配置管理优化
**位置**:
- config_loader.py
- config.yaml

**方案**:
- 添加配置验证
- 支持环境变量覆盖
- 配置热重载（可选）
- 默认值集中管理

#### Fix 3.3: 日志系统完善
**位置**: 全局

**方案**:
- 引入logging模块替代print
- 分级别日志（DEBUG/INFO/WARNING/ERROR）
- 日志轮转和持久化
- 敏感信息脱敏

#### Fix 3.4: 性能监控
**位置**:
- api_client.py (统计信息)
- main.py (进度显示)

**方案**:
- 添加性能指标收集
- API响应时间统计
- 内存使用监控
- 瓶颈识别工具

---

## 验证标准

### 功能验证
- [ ] 所有现有功能正常工作
- [ ] 数据库连接无泄漏（长时间运行测试）
- [ ] 错误场景优雅降级
- [ ] 边界条件正确处理

### 性能验证
- [ ] 内存使用稳定（无明显增长）
- [ ] API调用效率合理
- [ ] 大文本处理流畅
- [ ] 启动时间可接受

### 安全验证
- [ ] 无SQL注入风险
- [ ] 无路径遍历风险
- [ ] 输入验证充分
- [ ] 敏感信息保护

### 代码质量验证
- [ ] 无重复代码
- [ ] 函数长度合理（<50行）
- [ ] 命名规范一致
- [ ] 注释适当且准确
