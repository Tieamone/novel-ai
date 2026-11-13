"""本机工作台接口，网页和接口使用同一地址。"""

from pathlib import Path
from secrets import compare_digest, token_urlsafe

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import main as cli
from core import outline_manager, writer
from core.memory_manager import MemoryManager
from . import actions
from .jobs import Jobs


STATIC = Path(__file__).resolve().parents[1] / "web" / "dist"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def create_app():
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
    app.config["TRUSTED_HOSTS"] = sorted(LOOPBACK)
    app.json.ensure_ascii = False
    jobs = Jobs()
    app.extensions["workbench_jobs"] = jobs
    token = token_urlsafe(32)

    @app.before_request
    def protect_local_api():
        if not request.path.startswith("/api/"):
            return
        origin = request.headers.get("Origin")
        same_origin = not origin or origin == f"{request.scheme}://{request.host}"
        if not same_origin or request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403, "请从本机工作台发起请求")
        if request.method != "GET" and not compare_digest(
                request.headers.get("X-Workbench-Token", "").encode(), token.encode()):
            abort(403, "页面会话已失效，请刷新页面")

    @app.after_request
    def prevent_cache(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description if error.code < 500 else "工作台服务错误"), error.code

    def payload():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError("请提交有效的操作参数")
        return value

    def selected_book():
        novel = request.args.get("novel")
        actions.existing_book(novel)
        return novel

    @app.get("/")
    def index():
        if not (STATIC / "index.html").is_file():
            return "找不到网页构建文件，请先在 web 目录运行 npm ci 和 npm run build。", 503
        return send_from_directory(STATIC, "index.html", max_age=0)

    @app.get("/assets/<path:name>")
    def asset(name):
        return send_from_directory(STATIC / "assets", name)

    @app.get("/api/bootstrap")
    def bootstrap():
        def available(name):
            try:
                actions.existing_book(name)
            except ValueError:
                return False
            return True
        novels = [{"name": name, "genre": genre} for name, genre in cli._list_novels(available, readonly=True)]
        return jsonify(token=token, novels=novels, job=jobs.current())

    @app.get("/api/book")
    def book():
        novel = selected_book()
        fields = ("chapter_num", "title", "emotion_tag", "status", "retry_count",
                  "review_score_total", "reader_review_score")
        chapter_columns = actions.columns(novel, "chapters")
        selection = ", ".join(key if key in chapter_columns else f"NULL AS {key}" for key in fields)
        chars = "COALESCE(LENGTH(content), 0)" if "content" in chapter_columns else "0"
        chapters = actions.chapter_rows(novel, f"SELECT {selection}, {chars} AS chars FROM chapters "
                                       "ORDER BY chapter_num", readonly=True) if chapter_columns else []
        task_columns = actions.columns(novel, "chapter_tasks")
        if task_columns:
            stale = ("COALESCE(status='进行中' AND updated_at < "
                     "datetime('now','-30 minutes','localtime'),0)" if
                     {"status", "updated_at"} <= task_columns else "0")
            tasks = actions.chapter_rows(
                novel, f"SELECT *, {stale} AS stale FROM chapter_tasks ORDER BY chapter_num", readonly=True,
            )
        else:
            tasks = []
        outline = actions.chapter_rows(
            novel, "SELECT * FROM outline_foreshadowing WHERE novel_name=? ORDER BY plant_chapter",
            (novel,), readonly=True,
        ) if actions.columns(novel, "outline_foreshadowing") else []
        last_chapter = chapters[-1]["chapter_num"] if chapters else 0
        style_file = actions.book_file(novel, "style.txt")
        intent = {}
        for key, filename in actions.INTENT_FILES.items():
            path = actions.book_file(novel, filename)
            intent[key] = path.read_text(encoding="utf-8") if path.is_file() else ""
        return jsonify(
            name=novel, target=cli._get_target_chapters(novel), chapters=chapters, tasks=tasks,
            foreshadow=outline,
            health=MemoryManager(novel, readonly=True).get_foreshadow_report(last_chapter)
            if last_chapter and actions.columns(novel, "foreshadowing") else None,
            legacy=actions.requires_migration(novel),
            categories=outline_manager._CATEGORY_LIST,
            styles=writer.AUTHOR_STYLES, style=style_file.read_text(encoding="utf-8").strip()
            if style_file.is_file() else "1", intent=intent,
        )

    @app.get("/api/chapter/<int:chapter>")
    def chapter_detail(chapter):
        novel = selected_book()
        if not actions.columns(novel, "chapters"):
            abort(404, "章节不存在")
        rows = actions.chapter_rows(novel, "SELECT * FROM chapters WHERE chapter_num=?", (chapter,), readonly=True)
        if not rows:
            abort(404, "章节不存在")
        return jsonify(rows[0])

    @app.get("/api/checkpoint/<int:chapter>")
    def checkpoint(chapter):
        novel = selected_book()
        history = actions.chapter_rows(novel, """
            SELECT attempt, started_at, ended_at, end_reason, word_count, review_score
            FROM writing_sessions WHERE chapter_num=? ORDER BY attempt
        """, (chapter,), readonly=True) if actions.columns(novel, "writing_sessions") else []
        cache = actions.chapter_rows(novel, """
            SELECT SUBSTR(beats_text, 1, 200) AS preview, created_at
            FROM beats_cache WHERE chapter_num=?
        """, (chapter,), readonly=True) if actions.columns(novel, "beats_cache") else []
        return jsonify(history=history, cache=cache[0] if cache else None)

    @app.get("/api/models")
    def model_options():
        return jsonify(actions.models())

    @app.get("/api/template")
    def download_template():
        return send_from_directory(cli.get_project_root(), "newbook_template.txt", as_attachment=True)

    @app.get("/api/download/<name>")
    def download(name):
        import re
        novel = request.args.get("novel")
        actions.existing_book(novel)
        if not re.fullmatch(r"第[0-9]+章\.txt", name):
            abort(404, "导出文件不存在")
        directory = actions.export_directory(novel)
        if (directory / name).resolve() != directory / name:
            abort(404, "导出文件不存在")
        return send_from_directory(directory, name, as_attachment=True)

    @app.post("/api/jobs")
    def start_job():
        data = payload()
        kind, novel = data.get("kind"), data.get("novel")
        if not isinstance(kind, str) or kind not in actions.TITLES:
            raise ValueError("未知操作")
        if kind not in ("import", "models", "create"):
            actions.existing_book(novel)
        elif kind == "create":
            actions.book_directory(novel)
        else:
            novel = None
        return jsonify(jobs.start(actions.TITLES[kind], novel, lambda: actions.run(kind, novel, data))), 202

    def get_job(job_id):
        job = jobs.get(job_id)
        if job is None:
            abort(404, "任务已不存在")
        return job

    @app.get("/api/jobs/<job_id>")
    def job_snapshot(job_id):
        return jsonify(get_job(job_id).snapshot(request.args.get("after", 0, type=int)))

    @app.get("/api/jobs/current")
    def current_job():
        return jsonify(job=jobs.current(request.args.get("known")))

    @app.post("/api/jobs/<job_id>/answer")
    def answer(job_id):
        data = payload()
        job = get_job(job_id)
        job.answer(data.get("prompt_id"), actions.string(data, "value"))
        return jsonify(ok=True)

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel(job_id):
        get_job(job_id).cancel()
        return jsonify(ok=True)

    return app
