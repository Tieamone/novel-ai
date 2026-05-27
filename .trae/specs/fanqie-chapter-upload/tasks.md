# 番茄小说章节自动上传 - 任务清单

- [ ] **Task 1: 安装依赖与环境准备**
  - [ ] 在 `requirements.txt` 中新增 `playwright>=1.45.0` 和 `playwright-stealth>=1.0.6`
  - [ ] 安装 Python 包：`pip install playwright playwright-stealth`
  - [ ] 安装 Chromium 浏览器：`python -m playwright install chromium`
  - [ ] 在 `.gitignore` 中新增 `tools/uploader/auth_state.json`、`tools/uploader/progress.json`、`tools/uploader/upload.log`
  - **验证**：`python -c "from playwright.sync_api import sync_playwright; print('OK')"` 不报错

- [ ] **Task 2: 创建 `tools/uploader/` 包骨架**
  - [ ] 创建 `tools/uploader/__init__.py`（空文件）
  - [ ] 创建 `tools/uploader/config.py`：定义番茄作家后台 URL、CSS 选择器、等待超时等常量
  - [ ] 创建 `tools/upload_to_fanqie.py` 入口脚本骨架（参数解析 + 帮助信息）
  - **验证**：`python tools/upload_to_fanqie.py --help` 输出帮助信息

- [ ] **Task 3: 实现浏览器管理模块 (`browser.py`)**
  - [ ] `launch_browser()`：启动 Chromium（有头模式）、设置 viewport、user-agent
  - [ ] 注入 `playwright-stealth` 反检测脚本
  - [ ] `create_context_with_auth()`：尝试加载 `auth_state.json`，不存在则创建新 context
  - [ ] `safe_close()`：安全的浏览器关闭（try/except 包裹，处理 KeyboardInterrupt）
  - **验证**：独立运行后能看到 Chromium 窗口打开，无报错

- [ ] **Task 4: 实现登录模块 (`login.py`)**
  - [ ] `ensure_logged_in(page)`：检测当前页面 URL 是否在登录页
  - [ ] `do_login(page, context)`：导航到登录页 → 等待用户扫码 → 轮询检测 URL 变化 → 保存 `storage_state`
  - [ ] 登录超时处理（5 分钟无操作则提示超时）
  - [ ] 登录态过期检测（导航后重定向到登录页 → 提示重新扫码）
  - **验证**：运行脚本 → 弹出浏览器 → 扫码 → 确认 auth_state.json 生成

- [ ] **Task 5: 实现单章上传模块 (`upload.py`)**
  - [ ] `navigate_to_editor(page)`：从作家后台首页导航到新建章节页
  - [ ] `fill_title(page, title)`：逐字符输入标题（50-150ms 随机延迟）
  - [ ] `paste_content(page, content)`：使用剪贴板粘贴正文，粘贴后滚动预览
  - [ ] `submit_chapter(page)`：点击发布按钮 → 等待成功提示 → 返回结果
  - [ ] `human_like_delay()`：随机等待工具函数
  - [ ] `upload_chapter(page, chapter_num, title, content)`：编排上述步骤的主函数
  - **验证**：手动准备一短章节测试上传，确认能成功提交

- [ ] **Task 6: 实现批量上传脚本 (`upload_to_fanqie.py`)**
  - [ ] 参数解析：`--novel`（必填）、`--start`（默认1）、`--end`（可选，默认最大）、`--interval-min`/`--interval-max`（章间间隔范围）
  - [ ] 从 `output/{novel}/` 读取章节文件
  - [ ] 断点续传：读取/写入 `tools/uploader/progress.json`
  - [ ] 章间随机等待 + 单次上限 20 章
  - [ ] 进度显示：`[3/50] 第3章 上传成功 (耗时 45s)`
  - **验证**：运行批量上传脚本，确认逐章上传、断点续传正常

- [ ] **Task 7: 实现日志模块**
  - [ ] `log_upload(result: dict)`：追加写入 `tools/uploader/upload.log`
  - [ ] 日志格式：`时间 | 章节号 | 标题 | 字数 | 结果 | 耗时 | 错误信息`
  - [ ] `show_upload_history(novel_name)`：查询并展示上传历史
  - **验证**：上传几章后查看 upload.log 格式正确

- [ ] **Task 8: 可选集成 - main.py 上传钩子**
  - [ ] 在 `generate_chapter_auto()` 中，`export_chapter()` 成功后增加询问：`是否上传到番茄小说？(y/n)`
  - [ ] 若选 y，调用 `upload_to_fanqie.py` 的单章上传函数
  - [ ] 确保上传失败不影响已有导出结果
  - **验证**：完整走一遍写作→审稿→导出→询问上传的流程

# Task Dependencies
- Task 2 依赖 Task 1
- Task 3 依赖 Task 2
- Task 4 依赖 Task 3
- Task 5 依赖 Task 3, Task 4
- Task 6 依赖 Task 5
- Task 7 可与 Task 3-6 并行
- Task 8 依赖 Task 5（不强制，可独立评估是否实施）