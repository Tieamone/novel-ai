# novel-ai

本仓库为本地 AI 网文写作自动化系统，提供命令行和浏览器工作台。

快速说明：

- 浏览器工作台：`python main.py`
- 命令行菜单：`python main.py --cli`
- 核心模块目录：`core/`（包括 `api_client.py`、`planner.py`、`writer.py`、`reviewer.py`、`memory_manager.py`、`db.py`、`exporter.py`）
- 数据目录：`data/`（每本小说一个子目录）
- 导出目录：`output/`

最新变更请参阅 `CHANGELOG.md`。

## 浏览器工作台

首次使用时，在项目目录打开 PowerShell，安装工作台依赖并构建前端。使用本机已安装的 Node.js 运行以下命令。

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-web.txt
npm.cmd --prefix web ci
npm.cmd --prefix web run build
.\venv\Scripts\python.exe main.py
```

日常使用可在 IDE 中直接运行 `main.py`，或执行 `python main.py`，默认自动打开 [本机工作台](http://127.0.0.1:8765)。Windows 下如果系统 Python 启动时发现项目已有 `venv`，会自动使用其中的解释器。端口占用时可追加 `--port 8766`，手动打开时可追加 `--no-browser`。`run_workbench.cmd` 也可作为快捷入口使用。

工作台读取与 CLI 相同的小说库和模型密钥。生成、审稿和策划在本机后台任务中执行，右下角可查看进度、提交确认或请求停止。浏览器刷新后可重新接入正在运行的任务。同步模型请求结束后，任务才会在检查点停止。需要停止服务时，先停止正在执行的任务，等状态更新后再在启动窗口按 Ctrl+C。

可用操作见 [工作台菜单与验收说明](docs/WORKBENCH.md)。

## 百炼免费额度自动同步

需要已安装并完成控制台登录的百炼 CLI（`bl auth login --console`）。程序通过 `bl usage free --all --console-region cn-beijing --output json` 读取账号额度，不发送模型推理请求。优先使用 PATH 中的 CLI，Windows 兼容 `D:\Tools\bailian-cli\bl.cmd`。

- 启动程序、进入网页“模型与费用”或 CLI 模型菜单时，检查上次成功同步时间。无缓存或超过 24 小时才自动查询；关闭程序期间不运行后台任务。关闭三天后再启动会刷新，当天短时间重开使用缓存。
- 网页点击“立即刷新免费额度”，CLI 输入 `R`，可随时强制更新。查询最多等待 30 秒。
- 额度保存到 `data/model_quota_cache.json`，候选列表只保留 CLI 返回的 Text 类型、剩余额度大于 0 且未过期的模型。`custom_models.json` 只为匹配的模型补充参数，不再增加候选，MiMo 和旧静态模型不会出现在列表中。
- 显示剩余额度、到期日期和用完即停状态；已耗尽、已过期或不在 CLI 结果中的模型移出候选；没有可用快照时列表为空。额度日期按北京时间判断，当日到期的精确时刻以平台为准。
- 查询失败保留上次成功快照和时间，并提示错误。登录失效时重新执行登录命令，再手动刷新。
- 刷新只更新候选列表，不自动切换当前模型；当前配置被移出候选时，页面要求重新选择并保存，不改写 config.yaml 或平台开关。实际调用兼容性仍通过“验证并保存”检查。余额为快照，不能替代平台“用完即停”。请使用与项目 API Key 相同账号的控制台登录。

CLI 登录配置由官方工具管理，默认位于用户目录 `.bailian`，不复制到项目。额度缓存不提交 Git。本次会话费用仍沿用原有估算逻辑，额度同步不代表对实际账单的核验。

## 模型密钥与离线测试

复制 `.env.example` 为 `.env`，按实际使用的服务商填入 `DASHSCOPE_API_KEY` 或 `MIMO_API_KEY`。密钥文件不进入 Git。

项目现有测试也支持标准库 unittest。在安装了 pytest 的开发环境，`python -m pytest` 默认只收集 `tests/`，并在测试期间断开 Python 网络连接。根目录 `test_mimo_models.py` 是真实模型连通检查，需要显式运行，可能产生费用。

写作记忆使用目标章之前的阶段摘要和所有尚未压缩的逐章摘要。旧动态伏笔提示最多 8 条，仅供体检与策划参考，不再自动变成本章执行任务。作者、节拍与审稿以本章大纲伏笔安排为准；允许无伏笔任务。审核默认失败后停止，可在 `config.yaml` 使用 `force_and_flag` 继续生成内容未达标的章节。强制通过正文会参与后续记忆。

### 写作方向与旧书兼容

默认剧情和人物行动优先，普通环境与转场简写，打磨合并常规办事过程并保留精简稿，低于字数下限时由后续检查报告。完整大纲留在策划层，作者根据本章任务、前情和大纲分配的伏笔写作。埋入章只收到需要呈现的事实，兑现章才收到兑现要求。现有小说与旧任务卡不自动改写；历史伏笔安排过密时先预览并精简计划。
