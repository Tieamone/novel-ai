"""运行本机浏览器工作台。"""

import argparse
from threading import Timer
import webbrowser

class ChineseArguments(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "启动参数无效，请用 --help 查看可用参数。\n")


def main():
    parser = ChineseArguments(
        description="启动小说写作工作台",
        epilog="使用 python main.py --cli 进入原有命令行菜单。",
        add_help=False,
    )
    parser._positionals.title = "位置参数"
    parser._optionals.title = "选项"
    parser.add_argument("-h", "--help", action="help", help="显示启动说明")
    parser.add_argument("--port", type=int, default=8765, help="本机访问端口")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.exit(2, "端口应在 1 至 65535 之间。\n")
    try:
        from waitress import create_server
        from workbench.server import STATIC, create_app
        from workbench.jobs import ACTIVE_STATES
    except ModuleNotFoundError as error:
        if error.name not in ("flask", "waitress", "werkzeug"):
            raise
        parser.exit(2, "缺少网页依赖，请用项目 Python 执行 python -m pip install -r requirements-web.txt。\n")
    if not (STATIC / "index.html").is_file():
        parser.exit(2, "缺少网页构建文件，请先进入 web 目录执行 npm ci 和 npm run build。\n")
    app = create_app()
    try:
        server = create_server(app, host="127.0.0.1", port=args.port, threads=4)
    except OSError:
        parser.exit(1, f"无法监听本机端口 {args.port}，请检查占用或用 --port 指定其他端口。\n")
    url = f"http://127.0.0.1:{args.port}"
    print(f"小说写作工作台  {url}", flush=True)
    print("按 Ctrl+C 停止服务。", flush=True)
    if not args.no_browser:
        browser = Timer(0.4, webbrowser.open, args=(url,))
        browser.daemon = True
        browser.start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        jobs = app.extensions["workbench_jobs"]
        current = jobs.current()
        if current and current["status"] in ACTIVE_STATES:
            job = jobs.get(current["id"])
            print("正在停止后台任务，请等待当前步骤结束。", flush=True)
            job.cancel()
            with job.changed:
                job.changed.wait_for(lambda: job.status not in ACTIVE_STATES)


if __name__ == "__main__":
    main()
