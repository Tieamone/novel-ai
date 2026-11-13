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
