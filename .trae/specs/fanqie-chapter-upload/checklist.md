# 番茄小说章节自动上传 - 验收清单

## 依赖与环境
- [ ] `playwright` 和 `playwright-stealth` 已加入 `requirements.txt`
- [ ] Chromium 浏览器二进制已通过 `playwright install chromium` 安装
- [ ] `.gitignore` 已排除 `auth_state.json`、`progress.json`、`upload.log`

## 浏览器基础设施
- [ ] `launch_browser()` 能正常启动 Chromium 有头模式
- [ ] `playwright-stealth` 注入后 `navigator.webdriver` 为 `false`
- [ ] Ctrl+C 时浏览器进程被正确关闭，不留孤儿进程
- [ ] `auth_state.json` 不存在时不会报错，走正常登录流程

## 登录模块
- [ ] 首次运行能引导用户打开登录页扫码
- [ ] 扫码成功后 `auth_state.json` 被正确保存
- [ ] 再次运行时自动加载 `auth_state.json`，无需重新登录
- [ ] 登录态过期时提示用户重新扫码，不会静默失败
- [ ] 登录超时（5分钟）有明确提示

## 单章上传
- [ ] 能正确导航到新建章节页面
- [ ] 标题以逐字符方式输入，不是瞬间填充
- [ ] 正文通过剪贴板粘贴方式填入
- [ ] 提交后能检测到成功/失败反馈
- [ ] 上传失败自动重试最多 2 次，间隔 30-60 秒

## 批量上传
- [ ] `--novel --start --end` 参数解析正确
- [ ] 按章节号顺序从 `output/` 目录读取文件
- [ ] 章间随机间隔 60-180 秒
- [ ] 单次运行不超过 20 章的上限生效
- [ ] 断点续传：中断后能从 `progress.json` 恢复

## 反检测策略
- [ ] 浏览器指纹伪装生效（webdriver、plugins、languages 等）
- [ ] 标题输入节奏模拟真人（50-150ms 随机）
- [ ] 操作间有随机停顿和滚动行为
- [ ] 不会在 1 分钟内连续操作超过正常人频率

## 日志
- [ ] 每次上传操作有日志记录（时间、章节、结果、耗时）
- [ ] `tools/uploader/` 下所有文件（auth_state、progress、log）都有内容且格式正确

## 集成（可选）
- [ ] `main.py` 中导出成功后询问上传的钩子不阻塞主流程
- [ ] 上传钩子失败不影响章节导出结果