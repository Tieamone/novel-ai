"""使用现有流水线对隔离合成书验收，默认只估算请求。"""

import argparse
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

from core import api_client, config_loader, model_quota, reviewer, writer
from core.book_health import inspect_book
from core.memory_manager import MemoryManager
from core.outline_manager import add_outline_foreshadow, list_outline_foreshadow
from core.utils import extract_json_obj, with_db_connection
from tools import foreshadow_metamorphic


class Budget:
    """发送前按 UTF-8 输入字节数加输出上限预留，成功后按服务商用量结算。"""

    def __init__(self, limit, models):
        self.limit = limit
        self.models = set(models)
        self.tokens = 0
        self.requests = 0
        self.by_model = Counter()
        self.stopped = None

    def call(self, transport, system, user, model, maximum, temperature):
        if self.stopped:
            raise RuntimeError(self.stopped)
        if model not in self.models:
            raise ValueError("调用了未批准的验收模型")
        reserve = len((system + user).encode("utf-8")) + maximum + 256
        if self.limit and self.tokens + reserve > self.limit:
            self.stopped = "剩余 token 预算低于下一次请求预留量"
            raise RuntimeError(self.stopped)
        self.requests += 1
        try:
            response, usage = transport(system, user, model, maximum, temperature)
            if type(usage) is not int or usage <= 0:
                raise ValueError("服务商未返回有效 token 用量")
        except Exception:
            self.tokens += reserve
            self.by_model[model] += reserve
            self.stopped = "请求失败或用量未知，已按预留量计入并中止"
            raise
        self.tokens += usage
        self.by_model[model] += usage
        if self.limit and self.tokens > self.limit:
            self.stopped = "服务商用量超出预算"
            raise RuntimeError(self.stopped)
        return response


def estimate(chapters, samples, max_retry, only_metamorphic=False, split=False):
    n = 0 if only_metamorphic else chapters
    compression = 3 if n == 12 else 0
    # 一次首稿、节拍、打磨、事实核验、两审、记忆、两次对照；两章有伏笔。
    normal = {"新书策划估算": 10 if n else 0,
              "流水线": (9 + int(split)) * n + (4 if n else 0) + compression,
              "变形": 5 * samples, "变形复核最多追加": 4 * samples}
    # 每次生成可能分两段、补写三次、精简一次，并有多轮审稿。
    # 额外给裁切复审、一次任务卡重写和对应重试留出余量。
    upper = n * (2 * max_retry * 30 + 6) + compression + 10 * samples
    return {"chapters": n, "samples": samples, "normal": normal,
            "typical_requests": normal["新书策划估算"] + normal["流水线"] + normal["变形"],
            "writing_and_metamorphic_retry_estimate": upper, "provider_retries": 0}


def pair_judgments(original, polished, goal, model):
    results = []
    for swapped, (first, second) in enumerate(((original, polished), (polished, original))):
        result = extract_json_obj(api_client.call_api(
            "你是独立网文编辑。基于任务对照 A/B，先核验打磨稿保留的原稿关键事实数，"
            "关注人物、因果、证据和结尾，再判断哪个稿件阅读节奏更好。"
            '只返回JSON：{"total":整数,"kept":整数,"preferred":"A/B/平局"}。',
            f"任务：{goal}\n打磨稿位置：{'A' if swapped else 'B'}\n【A】\n{first}\n【B】\n{second}",
            model_name=model, max_tokens=650, temperature=0.1, retry=1,
        ))
        total, kept, preferred = result.get("total"), result.get("kept"), result.get("preferred")
        if (type(total) is not int or type(kept) is not int or not 0 <= kept <= total or not total
                or preferred not in ("A", "B", "平局")):
            raise ValueError("成对裁判返回无效")
        polish_at = "A" if swapped else "B"
        results.append({"polished_at": polish_at, "fact_retention": kept / total,
                        "preferred": "平局" if preferred == "平局" else ("打磨稿" if preferred == polish_at else "原稿")})
    return results


def _seed_regression_book(book, chapters):
    import main as cli

    mm = MemoryManager(book)
    (mm.data_dir / "target_chapters.txt").write_text(str(chapters), encoding="utf-8")
    (mm.data_dir / "master_outline.md").write_text(
        "林岚在旧码头清点货物、试机和结账，之后逐次协调不同货主的交接困难。", encoding="utf-8")
    add_outline_foreshadow(
        book, "埋入：林岚收好老周开具的盖章借用单；兑现：借用单证明接头只是借用并撤销误扣款；必要性：核账凭证",
        "情节伏笔", 2, 3,
    )
    tasks = [
        "林岚与老陈核对水泵维修约定，面对试机档期冲突争取早班空档。正文自然出现一行【系统提示】试机预约已登记。约好试机就停。",
        "试机缺接头，林岚找到老周并借到备用接头，收好老周开具的盖章借用单。试机通过后双方约好对账，本章停在约定对账，工钱尚未支付。",
        "老陈误将接头记作采购费用，林岚出示盖章借用单证明只是借用。双方核对条目，撤销误扣款并完成结算，到结清为止。",
    ]
    obstacles = ("货箱标签错贴", "买家改变交付地点", "装卸用具短缺", "交货清单漏记一箱",
                 "两名货主同时要求先验收", "退换货缺少签收记录", "对方拒绝承担运费", "交货时间与船班冲突", "漏记一笔押金")
    tasks += [f"林岚处理新委托中的{item}，展开双方协商、可选办法、林岚的决定和具体后果，到本次交接完成为止。"
              for item in obstacles]
    for number, goal in enumerate(tasks[:chapters], 1):
        cli._upsert_task_status(book, number, goal, "铺垫", cli.TASK_PENDING)


def _plan_book(book, chapters, report):
    import main as cli
    from core import outline_manager
    from core.interaction import using_interaction

    target = max(10, chapters)
    review = outline_manager.review_outline_foreshadow
    audits = []

    class AutomaticChoices:
        def ask(self, prompt, options, multiline, context):
            choices = dict(options)
            if "5" in choices and choices["5"] == "自定义":
                return "5"
            if "请输入目标章数" in prompt:
                return str(target)
            if "分批生成" in str(options):
                return "2"
            return ""

        @staticmethod
        def write(message):
            print(message, end="", flush=True)

        @staticmethod
        def check_cancelled():
            pass

    def traced_review(*args, **kwargs):
        result = review(*args, **kwargs)
        audits.append(None if result is None else {
            "reviewed": len(result), "passed": sum(item["passed"] for item in result.values()),
        })
        return result

    with (using_interaction(AutomaticChoices()),
          patch.object(outline_manager, "review_outline_foreshadow", side_effect=traced_review)):
        cli.create_novel(book, "都市职业网文·轻系统流",
                         "旧码头维修工靠选择和交涉解决交接难题。系统只登记工作节点。"
                         "第一章在预约试机成功时安排自然的一行【系统提示】。剧情紧凑，伏笔服从主线需要。")
    with with_db_connection(book) as conn:
        saved = [r["chapter_num"] for r in conn.execute(
            "SELECT chapter_num FROM chapter_tasks WHERE LENGTH(TRIM(plot_goal)) > 0 ORDER BY chapter_num")]
    report["planning"] = {"target": target, "cards": saved, "macro_reviews": audits,
                          "initial_foreshadow": list_outline_foreshadow(book)}
    report["checks"]["策划前置完成"] = saved[:chapters] == list(range(1, chapters + 1)) and None not in audits
    return saved[:chapters] == list(range(1, chapters + 1))


def run_pipeline(directory, chapters, pair_model, report, *, seeded=False):
    import main as cli

    book = "__acceptance_" + directory.name
    if seeded:
        _seed_regression_book(book, chapters)
    elif not _plan_book(book, chapters, report):
        return
    mm = MemoryManager(book)
    initial_plan = {item["fid"]: item for item in list_outline_foreshadow(book)}
    rows, polishes = report["chapters"], report["polishes"]
    stats = report["checks"]
    polish = writer._polish_chapter_ex

    def recorded(original, *args, **kwargs):
        candidate, detail = polish(original, *args, **kwargs)
        event = {"chapter": number, **detail}
        polishes.append(event)
        if detail["accepted"]:
            try:
                event["pair"] = pair_judgments(original, candidate, kwargs.get("plot_goal", ""), pair_model)
            except ValueError as exc:
                event["pair"], event["pair_error"] = [], str(exc)
        return candidate, detail

    next_context = []
    export_rows, plans = [], []

    def capture_context(chapter):
        window = [x["chapter_num"] for x in mm.load_context(chapter)["recent_summaries"]]
        with with_db_connection(book) as conn:
            source = list(conn.execute(
                "SELECT chapter_num, is_compressed, summary FROM summaries WHERE chapter_num<? ORDER BY chapter_num",
                (chapter,)))
        stages = [x["chapter_num"] for x in source if x["is_compressed"] and x["summary"].startswith("[阶段摘要")]
        stage = max(stages, default=0)
        expected = ([stage] if stage else []) + [x["chapter_num"] for x in source
                                                if not x["is_compressed"] and x["chapter_num"] > stage]
        next_context.append({"chapter": chapter, "stage": stage, "actual": window, "expected": expected})

    with patch.object(writer, "_polish_chapter_ex", side_effect=recorded):
        for number in range(1, chapters + 1):
            capture_context(number)
            error = None
            try:
                chapter = cli.generate_chapter_auto(book)
            except Exception as exc:
                chapter, error = None, type(exc).__name__
            saved = mm.load_chapter(number)
            body = saved.get("content") or ""
            with with_db_connection(book) as conn:
                sessions = [dict(x) for x in conn.execute(
                    "SELECT attempt,end_reason FROM writing_sessions WHERE chapter_num=? ORDER BY id", (number,))]
            rows.append({"chapter": number, "status": saved.get("status"), "length": len(body),
                         "length_issue": writer.chapter_length_issue(body), "sessions": sessions, "error": error})
            plans.append({"chapter": number, "items": list_outline_foreshadow(book)})
            with with_db_connection(book) as conn:
                task = conn.execute("SELECT status FROM chapter_tasks WHERE chapter_num=?", (number,)).fetchone()
            if chapter != number or not task or task["status"] != cli.TASK_COMPLETED:
                break
            path = config_loader.get_output_dir(book) / f"第{number:03d}章.txt"
            exported = path.read_text(encoding="utf-8")
            markers = re.findall(r"【[^】\r\n]+】", body)
            export_rows.append({"chapter": number, "source_length": len(body), "export_length": len(exported),
                                "same_body": body == exported,
                                "marker_present": bool(markers),
                                "marker_kept": all(exported.count(marker) >= count
                                                   for marker, count in Counter(markers).items())})
    if len(export_rows) == chapters:
        capture_context(chapters + 1)
    report.update(context=next_context, exports=export_rows, foreshadow=plans,
                  stale_planned=inspect_book(book)["stale_planned"])
    stats["长度及完章"] = len(rows) == len(export_rows) == chapters and all(not r["length_issue"] for r in rows)
    context_ok = all(row["actual"] == row["expected"] for row in next_context)
    stats["摘要上下文及章号上界"] = context_ok
    stats["压缩后重新读取上下文"] = (
        "不适用" if chapters == 3 else context_ok and len({r["stage"] for r in next_context if r["stage"]}) >= 2
    )
    stats["导出正文"] = len(export_rows) == chapters and all(row["same_body"] for row in export_rows)
    stats["系统流标记"] = (all(row["marker_kept"] for row in export_rows if row["marker_present"])
                               if any(row["marker_present"] for row in export_rows) else "未覆盖：正文未出现标记")
    final_plan = {item["fid"]: item for item in plans[-1]["items"]} if plans else {}
    covered = [item for item in initial_plan.values() if 0 < item["plant_chapter"] <= len(export_rows)]
    stats["伏笔流转"] = (not report["stale_planned"] and all(
        final_plan.get(item["fid"], {}).get("status") == "resolved"
        for item in covered if item["resolve_chapter"] and item["resolve_chapter"] <= len(export_rows)
    ) if covered else "未覆盖：写作范围无计划埋入项")
    report["deferrals"] = sum(max(0, item["plant_chapter"] - initial_plan[fid]["plant_chapter"])
                              for fid, item in final_plan.items() if fid in initial_plan)
    paired = [x for x in polishes if x["accepted"]]
    stats["成对比较"] = bool(paired) and all(len(x["pair"]) == 2 and min(
        r["fact_retention"] for r in x["pair"]) >= 0.9 for x in paired)
    report["metrics"] = {
        "first_pass_rate": sum(len(r["sessions"]) == 1 and r["status"] == "已审核" for r in rows) / max(1, len(rows)),
        "average_retries": sum(max(0, len(r["sessions"]) - 1) for r in rows) / max(1, len(rows)),
        "statuses": dict(Counter(r["status"] or "未生成" for r in rows)),
        "polish_acceptance": len(paired) / len(polishes) if polishes else None,
        "polish_rejections": dict(Counter(x["reason"] for x in polishes if not x["accepted"])),
    }


def write_report(directory, report):
    data = json.dumps(report, ensure_ascii=False, indent=2)
    (directory / "report.json").write_text(data + "\n", encoding="utf-8")
    lines = [f"# {report['mode']}验收报告", "", f"结果 **{report['result']}**", ""]
    lines.extend(f"- {name}　{'PASS' if val is True else 'FAIL' if val is False else val}"
                 for name, val in report["checks"].items())
    lines += ["", "```json", data, "```"]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cli(label="流水线与变形验收", *, only_metamorphic=False):
    ap = argparse.ArgumentParser(description=label)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="默认，只估算")
    mode.add_argument("--yes", action="store_true", help="执行已确认的真实模型请求")
    ap.add_argument("--chapters", type=int, choices=(3, 12), default=3)
    ap.add_argument("--samples", type=int, choices=range(1, len(foreshadow_metamorphic.FIXTURES) + 1), default=5)
    ap.add_argument("--max-tokens", type=int, help="总 token 上限；显式填写 0 表示无人工上限")
    ap.add_argument("--request-timeout", type=float, default=240, help="单次真实请求超时秒数")
    ap.add_argument("--stream", action="store_true", help="流式接收真实模型结果并汇总用量")
    for role in ("author", "reviewer", "reader", "cross-checker", "generator", "pair"):
        ap.add_argument(f"--{role}-model")
    args = ap.parse_args()
    cfg = deepcopy(config_loader.load_config())
    models = {"author": args.author_model or api_client.get_author_model(),
              "reviewer": args.reviewer_model or api_client.get_reviewer_model(),
              "reader": args.reader_model or api_client.get_reader_reviewer_model(),
              "cross_checker": args.cross_checker_model or cfg.get("model", {}).get("cross_checker", {}).get("default_model"),
              "generator": args.generator_model, "pair": args.pair_model}
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S-%f")
    directory = config_loader.get_project_root() / "output" / "acceptance" / stamp
    directory.mkdir(parents=True)
    split = any(part in models["author"].lower() for part in ("flash", "turbo", "lite", "mini"))
    report = {"mode": "真实" if args.yes else "Dry-run", "result": "DRY-RUN", "models": models,
              "stream": args.stream,
              "estimate": estimate(args.chapters, args.samples, cfg["novel"].get("max_retry", 3), only_metamorphic, split),
              "checks": {}, "chapters": [], "polishes": [], "max_tokens": args.max_tokens}
    budget = None
    try:
        if args.yes:
            if args.max_tokens is None or args.max_tokens < 0:
                raise ValueError("真实验收须提供 --max-tokens；0 表示无人工上限")
            if not 0 < args.request_timeout <= 600:
                raise ValueError("请求超时须大于 0 且不超过 600 秒")
            families = {api_client.model_family(models[r]) for r in ("author", "reviewer")}
            for role in (("cross_checker",) if only_metamorphic else ("cross_checker", "pair")):
                if not models[role] or api_client.model_family(models[role]) in families:
                    raise ValueError(f"{role} 需要明确指定与作者、主审不同家族的模型")
            if not models["generator"] or models["generator"] in (models["reviewer"], models["cross_checker"]):
                raise ValueError("变体生成模型必须区别于被测两层判定模型")
            active = {models[r] for r in (("reviewer", "cross_checker", "generator") if only_metamorphic else models)}
            today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
            quota = {item["model"]: item for item in model_quota.query_cli()}
            if any(m not in quota or not quota[m]["autoStop"] or quota[m]["expires"] <= today
                   or quota[m]["type"].lower() != "text" or quota[m]["remaining"] < max(1, args.max_tokens) for m in active):
                raise ValueError("需要每个验收模型具有足够百炼额度、未到期并开启用完即停")
            from openai import OpenAI
            key = api_client._get_api_key("DASHSCOPE_API_KEY")
            if not key:
                raise ValueError("未找到百炼 API 密钥")
            budget = Budget(args.max_tokens, active)
            client = OpenAI(api_key=key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                            max_retries=0, timeout=args.request_timeout)

            def guarded_create(**kwargs):
                messages = kwargs["messages"]

                def transport(*unused):
                    if not args.stream:
                        response = client.chat.completions.create(**kwargs)
                        return response, getattr(response.usage, "total_tokens", None)
                    parts, usage, finish_reason = [], None, None
                    with client.chat.completions.create(
                        **kwargs, stream=True, stream_options={"include_usage": True},
                    ) as stream:
                        for event in stream:
                            if event.usage:
                                usage = event.usage
                            if event.choices:
                                choice = event.choices[0]
                                if choice.delta.content:
                                    parts.append(choice.delta.content)
                                finish_reason = choice.finish_reason or finish_reason
                    response = SimpleNamespace(usage=usage, choices=[SimpleNamespace(
                        message=SimpleNamespace(content="".join(parts)), finish_reason=finish_reason,
                    )])
                    return response, getattr(usage, "total_tokens", None)

                guarded_args = [messages[0]["content"], messages[1]["content"], kwargs["model"],
                                kwargs["max_tokens"], kwargs["temperature"]]
                response = budget.call(transport, *guarded_args)
                print(f"  [验收用量] 第{budget.requests}次 {kwargs['model']}，累计{budget.tokens}/{budget.limit or '无人工上限'} token",
                      flush=True)
                return response

            guarded_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=guarded_create)))
            system_call = api_client.call_api

            def guarded_api(*positional, **kwargs):
                if budget.stopped:
                    raise RuntimeError(budget.stopped)
                kwargs.update(provider="dashscope", retry=1, timeout=args.request_timeout)
                return system_call(*positional, **kwargs)

            cfg["paths"] = {"data_dir": str(directory / "data"), "output_dir": str(directory / "exports")}
            cfg["model"]["cross_checker"] = {"default_model": models["cross_checker"]}
            cfg["novel"]["on_review_exhausted"] = "stop"
            if args.chapters == 12:
                cfg["novel"].update(compress_after_chapters=6, recent_summary_count=2)
            with ExitStack() as stack:
                stack.enter_context(client)
                for owner, attribute, value in (
                    (config_loader, "_config", cfg), (api_client, "call_api", guarded_api),
                    (api_client, "_author_model", models["author"]), (api_client, "_reviewer_model", models["reviewer"]),
                    (api_client, "_reader_reviewer_model", models["reader"]),
                    (api_client, "get_available_models", lambda *a, **kw: {}),
                    (reviewer, "input", lambda *a, **kw: "1"),
                    (reviewer, "check_switch_needed", lambda *a: False),
                    (api_client, "check_switch_needed", lambda *a: False),
                ):
                    stack.enter_context(patch.object(owner, attribute, value))
                stack.enter_context(patch("openai.OpenAI", return_value=guarded_client))
                if not only_metamorphic:
                    run_pipeline(directory, args.chapters, models["pair"], report)
                report["metamorphic"] = {}
                report["checks"]["变形基线"] = False
                foreshadow_metamorphic.run(models["generator"], models["reviewer"], models["cross_checker"],
                                          args.samples, progress=report["metamorphic"])
                report["checks"]["变形基线"] = report["metamorphic"]["pass"]
            report["result"] = "FAIL" if budget.stopped or False in report["checks"].values() else "PASS"
    except Exception as exc:
        report["result"] = "FAIL"
        report["error"] = f"{type(exc).__name__}: {exc}" if budget is None else type(exc).__name__
    finally:
        if budget:
            report.update(requests=budget.requests, tokens=budget.tokens,
                          tokens_by_model=dict(budget.by_model), budget_stop=budget.stopped)
        write_report(directory, report)
        print(json.dumps({"result": report["result"], "estimate": report["estimate"],
                          "report": str(directory / "report.md")}, ensure_ascii=False, indent=2))
    return int(report["result"] == "FAIL")


if __name__ == "__main__":
    raise SystemExit(cli())
