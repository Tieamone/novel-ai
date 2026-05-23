# 番茄小说章节自动上传 Spec

## Why
目前系统只能将章节导出为本地 `.txt` 文件，用户需要手动登录番茄作家后台逐章粘贴上传。通过模拟浏览器自动化上传，可将「写作→审稿→导出→上传」串成完整闭环。

## What Changes
- **新增** `tools/uploader/` 包，提供浏览器自动化上传能力
- **新增** `playwright` + `playwright-stealth` 两个第三方依赖
- **新增** `tools/upload_to_fanqie.py` 独立入口脚本，支持批量/单章上传
- `main.py` 中**可选**新增上传钩子（不影响现有流程）
- `config.yaml` 中**新增** `upload` 配置节（可选）

## Impact
- Affected specs: 无（全新功能，不影响已有 spec）
- Affected code: 
  - `tools/uploader/`（新目录）
  - `tools/upload_to_fanqie.py`（新文件）
  - `requirements.txt`（新增2个依赖）
  - `config.yaml`（可选新增配置节）
  - `main.py`（仅新增可选钩子，约 5 行）

---

## 冲突分析报告

### ✅ 无冲突项

| 检查维度 | 现状 | 结论 |
|---------|------|------|
| 浏览器自动化库 | 全项目无任何 `playwright`/`selenium`/`webdriver` 导入 | 无冲突 |
| 异步代码 | 全项目 0 处 `async`/`await`/`asyncio`，纯同步 CLI | Playwright 同步 API 天然兼容 |
| 多线程/多进程 | 全项目 0 处 `threading`/`multiprocessing` | 无冲突 |
| tools/ 目录 | 仅有 `cleanup_foreshadow.py`，各自独立 | 无冲突 |
| 导出模块 | `exporter.py` 只负责文件写入，无上传逻辑 | 职责清晰，互补不冲突 |
| 数据库 | 不涉及新表或 schema 变更 | 无冲突 |
| 模块命名空间 | `core/` 下无 `uploader` 或类似包 | 无命名冲突 |

### ⚠️ 需注意项

| 检查维度 | 说明 | 应对 |
|---------|------|------|
| **依赖引入** | `CLAUDE.md` 规定"不要引入新的第三方依赖而不先确认"。需新增 `playwright` + `playwright-stealth` | 用户已明确批准此功能，依赖是必须的 |
| **Playwright 同步 vs 异步** | Playwright 提供 `sync_api`（阻塞式），与项目现有同步代码完全兼容。使用 `from playwright.sync_api import sync_playwright` | 选同步 API |
| **浏览器二进制** | `playwright install chromium` 会下载 ~300MB 的 Chromium。不参与版本管理，对应 `.gitignore` | 在 `.gitignore` 中排除 |
| **Signal Handler** | `main.py` 已注册 `SIGINT` 处理器做状态回滚。浏览器操作中 Ctrl+C 需同时关闭浏览器进程，防止孤儿进程 | 上传脚本中独立捕获 `KeyboardInterrupt`，确保 `browser.close()` |
| **阻塞时长** | 单章上传预计 30-90 秒（页面导航+等待+模拟输入）。若集成到 `generate_chapter_auto()` 会阻塞写作流水线 | 设计为独立脚本 + 可选钩子，默认走独立脚本模式 |
| **状态持久化** | 登录态（cookie）需跨会话复用，避免每次扫码登录 | 使用 Playwright 的 `storage_state` 机制 |
| **playwright-stealth 维护状态** | `playwright-stealth` 是社区库，Playwright 版本升级时可能滞后 | 锁定 Playwright 版本，不自动升级 |

---

## ADDED Requirements

### Requirement: 浏览器自动化基础设施
系统 SHALL 提供基于 Playwright Sync API 的浏览器启动、反检测注入、登录态管理能力。

#### Scenario: 首次启动浏览器
- **WHEN** 用户运行上传脚本且本地无浏览器 profile
- **THEN** 系统启动 Chromium（有头模式），注入 `playwright-stealth` 反检测脚本，隐藏 `navigator.webdriver` 等特征

#### Scenario: 复用登录态
- **WHEN** 用户之前已完成扫码登录且 `auth_state.json` 存在
- **THEN** 系统加载 `storage_state` 恢复登录态，无需重新登录

#### Scenario: Ctrl+C 安全退出
- **WHEN** 用户在浏览器操作中按下 Ctrl+C
- **THEN** 系统先关闭浏览器进程（`browser.close()`），再释放资源后退出，不留孤儿进程

### Requirement: 扫码登录与状态保存
系统 SHALL 支持用户手动扫码登录番茄作家后台，并将登录态持久化到本地文件。

#### Scenario: 首次登录
- **WHEN** auth_state.json 不存在
- **THEN** 系统打开番茄作家登录页，提示用户扫码，轮询检测登录成功后将 `context.storage_state()` 写入 `tools/uploader/auth_state.json`

#### Scenario: 登录态过期
- **WHEN** 加载 auth_state.json 后导航到作家后台，检测到重定向到登录页
- **THEN** 系统删除旧 auth_state.json，提示用户重新扫码登录

### Requirement: 单章上传
系统 SHALL 支持将指定的单章内容上传到番茄作家后台。

#### Scenario: 正常上传一章
- **WHEN** 用户指定小说名、章节号、标题、正文
- **THEN** 系统：
  1. 确保已登录（未登录则引导扫码）
  2. 导航到作家后台 → 新建章节页
  3. 模拟真人操作节奏填充标题、正文（逐字输入标题 + 粘贴正文）
  4. 提交前滚动预览正文
  5. 点击发布按钮
  6. 等待发布成功反馈
  7. 记录上传日志

#### Scenario: 上传失败重试
- **WHEN** 提交后页面无成功反馈或出现错误提示
- **THEN** 系统等待 30-60 秒后重试，最多 2 次；仍失败则记录失败日志并跳过

### Requirement: 批量上传
系统 SHALL 支持从 `output/{小说名}/` 目录批量读取已导出章节并上传。

#### Scenario: 批量上传指定范围
- **WHEN** 用户运行 `python tools/upload_to_fanqie.py --novel "时光当铺" --start 1 --end 50`
- **THEN** 系统按章节号顺序读取 `output/时光当铺/第XXX章.txt`，逐章上传，章间随机间隔 60-180 秒

#### Scenario: 断点续传
- **WHEN** 批量上传中途中断（Ctrl+C 或异常）
- **THEN** 系统记录最后成功上传的章节号到 `tools/uploader/progress.json`；下次运行时自动从下一章继续

### Requirement: 反检测策略
系统 SHALL 实施多层反检测措施，降低被平台识别为自动化操作的风险。

#### Scenario: 浏览器指纹伪装
- **WHEN** 浏览器启动
- **THEN** `playwright-stealth` 自动注入，隐藏 `webdriver` 属性、伪造 `chrome.runtime`、`plugins`、`languages` 等指纹

#### Scenario: 真人行为模拟
- **WHEN** 系统填充标题输入框
- **THEN** 逐字符键入，每字间隔 50-150ms 随机；正文区域使用剪贴板粘贴（模拟 Ctrl+V）

#### Scenario: 操作节奏控制
- **WHEN** 执行批量上传
- **THEN** 章间间隔 60-180 秒随机；单次运行上传不超过 20 章；每章操作后有随机滚动/停顿

### Requirement: 日志与进度追踪
系统 SHALL 记录每次上传操作的详细日志，支持查询上传历史和失败原因。

#### Scenario: 记录上传日志
- **WHEN** 每次上传完成（成功或失败）
- **THEN** 系统追加写入 `tools/uploader/upload.log`，包含：时间、章节号、标题、字数、结果（成功/失败/跳过）、耗时、错误信息

---

## 技术决策

### 为什么选 Playwright Sync API 而非 Async API
项目全链路是同步阻塞式 CLI（`main.py` 2000+ 行无一 `async`）。使用 `playwright.sync_api` 可无缝集成，无需引入 `asyncio` 事件循环，不改变项目编程范式。

### 为什么设计为独立脚本 + 可选钩子，而非强制集成
- 上传操作耗时（单章 30-90 秒），阻塞 `generate_chapter_auto()` 会打断写作+审稿流水线
- 用户可能需要先审阅章节内容再决定是否上传
- 独立脚本可灵活选择上传范围（如只上传 1-50 章）

### 为什么不用 Playwright Async API 的并发上传
- 反检测要求之一是"不并发操作"，真人不会同时操作多个浏览器标签页
- 并发上传会显著增加被检测风险

### 为什么只支持番茄小说
- 每个平台的选择器、流程、反爬策略完全不同
- 先聚焦一个平台做稳定，后续可抽象 Provider 接口支持多平台