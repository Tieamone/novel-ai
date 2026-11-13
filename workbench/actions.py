"""网页命令的参数验证和业务调用。"""

from pathlib import Path
import os

import main as cli
from core import api_client, outline_manager, writer
from core.config_loader import get_data_dir, get_output_dir
from core.db import ensure_database
from core.exporter import export_all, export_chapter, get_safe_output_dir
from core.interaction import ask, check_cancelled, say
from core.style_analyzer import analyze_style, save_fingerprint
from core.utils import with_db_connection


INTENT_FILES = {"intent": "author_intent.md", "focus": "current_focus.md"}
ROLES = {
    "author": ("作者模型", api_client.get_author_model, api_client.set_author_model),
    "reviewer": ("责任编辑", api_client.get_reviewer_model, api_client.set_reviewer_model),
    "reader_reviewer": ("读者审稿", api_client.get_reader_reviewer_model, api_client.set_reader_reviewer_model),
}
TITLES = {
    "create": "新建小说", "import": "导入小说", "generate": "生成章节",
    "recover": "恢复审稿", "export": "导出章节", "delete_chapters": "删除章节",
    "delete_book": "删除小说", "task": "保存任务卡", "style": "保存写作风格",
    "fingerprint": "生成文法指纹", "intent": "保存创作资料", "models": "设置模型",
    "foreshadow": "保存大纲伏笔", "delete_foreshadow": "删除大纲伏笔",
    "suggest": "生成伏笔建议", "reset": "重置任务", "rewrite": "重置正文",
    "clear_beats": "清除节拍缓存",
}


def book_directory(novel):
    if not isinstance(novel, str):
        raise ValueError("请选择小说")
    error = cli._validate_novel_name(novel)
    if error:
        raise ValueError(error)
    base = get_data_dir().resolve()
    directory = base / novel
    if (directory.resolve() != directory
            or (directory / "novel.db").resolve() != directory / "novel.db"):
        raise ValueError("小说目录或数据库指向其他位置")
    return directory


def existing_book(novel):
    directory = book_directory(novel)
    if not (directory / "novel.db").is_file():
        raise ValueError("找不到该小说")
    return directory


def book_file(novel, name):
    path = existing_book(novel) / name
    if path.resolve() != path:
        raise ValueError("资料文件指向其他位置")
    return path


def export_directory(novel):
    directory = get_output_dir().resolve() / get_safe_output_dir(novel).name
    if directory.resolve() != directory:
        raise ValueError("小说导出目录指向其他位置")
    return directory


def protect_exports(novel):
    directory = export_directory(novel)
    if any(path.resolve() != path for path in directory.glob("第*章.txt")):
        raise ValueError("有导出文件指向其他位置")


def number(value, label="章节号", maximum=1000000, minimum=1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label}应为 {minimum} 至 {maximum} 的整数")
    return value


def string(data, key, maximum=1000000):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("输入内容的格式或长度无效")
    return value


def models():
    return {
        "roles": {
            key: {
                "label": label, "current": getter(),
                "options": [
                    {"id": info["model"], "name": info["name"], "provider": info["provider"],
                     "configured": len(os.getenv(info["env_key"], "")) > 10}
                    for info in api_client.get_available_models(usage=key).values()
                ],
            }
            for key, (label, getter, _) in ROLES.items()
        },
        "usage": api_client.get_session_stats(),
        "failures": api_client.get_failure_stats(),
        "history": api_client.get_switch_history(),
    }


def set_models(data):
    changes = []
    for role, (label, getter, setter) in ROLES.items():
        model = string(data, role, 160)
        if not model or model == getter():
            continue
        selected = next((info for info in api_client.get_available_models(usage=role).values()
                         if info["model"] == model), None)
        if not selected or len(os.getenv(selected["env_key"], "")) <= 10:
            raise ValueError(f"{label}不可用或缺少密钥")
        say(f"正在验证{label}：{selected['name']}")
        api_client.call_api("你是助手。", "回复ok", model_name=model,
                            provider=selected["provider"], max_tokens=10, temperature=0.1, retry=1)
        changes.append((setter, model, selected["provider"]))
    for setter, model, provider in changes:
        setter(model, provider)
    return {"message": "会话模型已保存"}


def chapter_rows(novel, query, parameters=(), *, readonly=False):
    with with_db_connection(novel, readonly=readonly) as conn:
        return [dict(row) for row in conn.execute(query, parameters)]


def columns(novel, table):
    with with_db_connection(novel, readonly=True) as conn:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def requires_migration(novel):
    return (not {"status", "updated_at", "original_plot_goal", "rewrite_count"} <= columns(novel, "chapter_tasks")
            or not {"review_score_total", "review_failure_attribution", "reader_review_score"} <= columns(novel, "chapters")
            or any(not columns(novel, table) for table in (
                "outline_foreshadowing", "writing_sessions", "beats_cache", "novel_info",
            )))


def generate(novel, count):
    protect_exports(novel)
    chapters = []
    for index in range(count):
        check_cancelled()
        say(f"批次进度 {index + 1}/{count}")
        chapter = cli.generate_chapter_auto(novel)
        if chapter is None:
            break
        if cli._get_chapter_status(novel, chapter) not in (
            cli.CHAPTER_STATUS_APPROVED, cli.CHAPTER_STATUS_FORCE_APPROVED
        ):
            return {"chapter": chapter, "message": f"第{chapter}章待处理，请查看任务记录"}
        chapters.append(chapter)
    return {"chapter": chapters[-1] if chapters else None,
            "message": f"已生成 {len(chapters)} 章" if chapters else "没有生成新章节，请查看任务记录"}


def delete_chapters(novel, data):
    first = number(data.get("first"))
    last = number(data.get("last", first))
    if last < first or data.get("confirm") != "YES":
        raise ValueError("请核对章节范围并输入 YES")
    rows = chapter_rows(novel, """
        SELECT c.chapter_num, c.status, t.status AS task_status
        FROM chapters c LEFT JOIN chapter_tasks t ON c.chapter_num=t.chapter_num
        WHERE c.chapter_num BETWEEN ? AND ? ORDER BY c.chapter_num
    """, (first, last))
    if len(rows) != last - first + 1:
        raise ValueError("选定范围内有章节不存在")
    if any(row["status"] == "writing" or row["task_status"] == cli.TASK_IN_PROGRESS for row in rows):
        raise ValueError("选定范围内有章节正在写作")
    protect_exports(novel)
    for row in rows:
        cli.delete_chapter(novel, row["chapter_num"])
    return {"message": f"已删除 {len(rows)} 章，任务卡已重置"}


def save_foreshadow(novel, data):
    description = string(data, "description").strip()
    category = string(data, "category", 40)
    status = string(data, "status", 20)
    if not description or category not in outline_manager._CATEGORY_LIST:
        raise ValueError("请输入伏笔描述并选择分类")
    fields = dict(description=description, category=category,
                  plant_chapter=number(data.get("plant_chapter") or 0, "埋入章", minimum=0),
                  resolve_chapter=number(data.get("resolve_chapter") or 0, "兑现章", minimum=0),
                  importance=number(data.get("importance"), "重要度", 5), notes=string(data, "notes"))
    fid = string(data, "fid", 40)
    if fid:
        if status not in outline_manager._STATUS_LIST:
            raise ValueError("请选择有效的伏笔状态")
        if not outline_manager.update_outline_foreshadow(novel, fid, status=status, **fields):
            raise ValueError("找不到该伏笔")
    else:
        if not fields["plant_chapter"] or not fields["resolve_chapter"]:
            raise ValueError("新增伏笔需要填写埋入章和兑现章")
        fid = outline_manager.add_outline_foreshadow(novel, **fields)
    return {"message": f"已保存伏笔 {fid}"}


def run(kind, novel, data):
    if kind == "models":
        return set_models(data)
    if kind == "create":
        book_directory(novel)
        name = cli.create_novel(novel, string(data, "genre", 100), string(data, "keywords", 3000))
        return {"novel": name, "message": "小说策划已完成"}
    if kind == "import":
        content = string(data, "content")
        if not content:
            path = cli.get_project_root() / "newbook.txt"
            if not path.is_file():
                raise ValueError("项目目录中没有 newbook.txt，请上传文本")
            content = path.read_text(encoding="utf-8-sig")
        parsed = cli._import_from_text_file(content.lstrip("\ufeff"))
        if parsed is None:
            return {"message": "未导入小说，请查看任务记录"}
        book_directory(parsed["novel_name"])
        return {"novel": cli.create_novel(import_data=parsed), "message": "小说已导入"}

    existing_book(novel)
    if kind != "delete_book" and requires_migration(novel):
        approved = ask("这本小说使用旧版数据库。继续操作会运行项目现有的兼容迁移。",
                       options=(("yes", "迁移并继续"), ("no", "取消操作")))
        if approved != "yes":
            return {"message": "已取消操作"}
    if kind != "delete_book":
        ensure_database(novel)
    if kind == "generate":
        count = number(data.get("count", 1), "生成章数", 1000)
        if count > 50 and data.get("confirm") != "YES":
            raise ValueError("单次生成超过 50 章，请输入 YES 确认")
        return generate(novel, count)
    if kind == "recover":
        chapter = number(data.get("chapter"))
        action = string(data, "method", 1)
        if action not in ("1", "2", "3"):
            raise ValueError("请选择恢复方式")
        protect_exports(novel)
        cli.recover_review_failed(novel, chapter, action)
        return {"chapter": chapter, "message": f"第{chapter}章 · {cli._get_chapter_status(novel, chapter)}"}
    if kind == "export":
        protect_exports(novel)
        chapter = data.get("chapter")
        paths = [export_chapter(novel, number(chapter))] if chapter is not None else export_all(novel)
        return {"downloads": [Path(path).name for path in paths if path], "message": "导出操作已完成"}
    if kind == "delete_book":
        if data.get("name_confirmation") != novel or data.get("confirm") != "YES":
            raise ValueError("请输入完整书名及 YES 确认删除")
        output_path = export_directory(novel)
        for other, _ in cli._list_novels(readonly=True):
            if other != novel and os.path.normcase(str(get_safe_output_dir(other).resolve())) == os.path.normcase(str(output_path)):
                raise ValueError("其他小说共用此导出目录，请先整理导出文件")
        cli.delete_novel(novel)
        return {"deleted_novel": novel, "message": "小说已删除"}
    if kind == "delete_chapters":
        return delete_chapters(novel, data)
    if kind == "task":
        cli.edit_task_card(novel, number(data.get("chapter")),
                           string(data, "plot_goal").strip(), string(data, "emotion_tag", 20))
    elif kind == "style":
        style = string(data, "style", 40)
        custom = string(data, "custom", 4000).strip()
        if style not in writer.AUTHOR_STYLES and not (style == "custom" and custom):
            raise ValueError("请选择写作风格或填写自定义描述")
        book_file(novel, "style.txt").write_text(
            f"custom:{custom}" if style == "custom" else style, encoding="utf-8")
    elif kind == "fingerprint":
        reference = string(data, "content")
        if len(reference.strip()) < 200:
            raise ValueError("参考文本至少需要 200 字")
        book_file(novel, "style_fingerprint.json")
        save_fingerprint(novel, analyze_style(reference))
    elif kind == "intent":
        field = string(data, "field", 20)
        if field not in INTENT_FILES:
            raise ValueError("请选择创作资料")
        book_file(novel, INTENT_FILES[field]).write_text(string(data, "content"), encoding="utf-8")
    elif kind == "foreshadow":
        return save_foreshadow(novel, data)
    elif kind == "delete_foreshadow":
        if data.get("confirm") != "YES":
            raise ValueError("请确认删除伏笔")
        if not outline_manager.delete_outline_foreshadow(novel, string(data, "fid", 40)):
            raise ValueError("找不到该伏笔")
    elif kind == "suggest":
        outline = book_file(novel, "master_outline.md")
        if not outline.is_file() or not outline.read_text(encoding="utf-8").strip():
            raise ValueError("请先为这本小说准备总大纲")
        outline_manager.ai_suggest_outline_foreshadow(novel)
    elif kind in ("reset", "rewrite", "clear_beats"):
        chapter = number(data.get("chapter"))
        if data.get("confirm") != "YES":
            raise ValueError("请确认断点操作")
        tasks = chapter_rows(novel, "SELECT status FROM chapter_tasks WHERE chapter_num=?", (chapter,))
        chapters = chapter_rows(novel, "SELECT status FROM chapters WHERE chapter_num=?", (chapter,))
        if kind == "reset":
            if not tasks:
                raise ValueError("任务卡不存在")
            cli.reset_writing_checkpoint(novel, chapter)
        else:
            if tasks and tasks[0]["status"] == cli.TASK_IN_PROGRESS:
                raise ValueError("章节正在写作，请先检查断点")
            if kind == "rewrite":
                if not chapters:
                    raise ValueError("章节不存在")
                cli.rewrite_writing_checkpoint(novel, chapter)
            else:
                cli.clear_writing_beats(novel, chapter)
    return {"message": f"{TITLES[kind]}完成"}
