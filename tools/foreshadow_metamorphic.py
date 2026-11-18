"""只在指定预算和模型后，对合成章节执行伏笔变形验收。"""

import json

from core import api_client, reviewer
from core.utils import extract_json_obj

KINDS = ("真兑现", "只提物品", "否定或传闻", "主谓颠倒")
FIXTURES = [
    {"text": "\n\n".join((start, paragraph, end)), "paragraph": paragraph,
     "candidate": candidate, "role_reversal": role_reversal}
    for start, paragraph, end, candidate, role_reversal in (
        ("盘点结束，值守人员等在码头。", "林岚拿起铜牌，走到东侧库门前。",
         "外面的人收拾工具，等林岚回来。", "林岚用铜牌打开东侧库门",
         "值守员用铜牌打开东侧库门，林岚只在旁等着。"),
        ("库房的货还没登账。", "老周翻出了那一页收据，拿给主管。",
         "其他人停下手上的活。", "老周用收据证明主管重复收费",
         "主管出示收据，证明重复收费的是老周。"),
        ("夜班交接前要排除警报。", "何南指着操作台上的红灯，让陈桐留心。",
         "值班室里还坐着两个人。", "陈桐切断备用电源，警报解除",
         "何南切断备用电源，警报解除。陈桐没有碰开关。"),
        ("车队正在等待出发许可。", "叶青和赵启拿着提货单去见调度。",
         "车上的人重新检查了绳结。", "赵启把有效提货单交给叶青",
         "叶青把有效提货单交给了赵启。"),
        ("测量数据需要重新校正。", "许宁把旧表和新表一起放到了桌上。",
         "办公室里有人在等书面结果。", "许宁用新表证实旧表读数偏高",
         "许宁用旧表证实新表读数偏高。"),
    )
]


def replace_paragraph(text, original, replacement):
    paragraphs = text.split("\n\n")
    if (not isinstance(replacement, str) or not replacement.strip() or replacement == original
            or "\n" in replacement or "\r" in replacement or paragraphs.count(original) != 1):
        raise ValueError("必须且只能替换唯一目标段，改写段须非空")
    paragraphs[paragraphs.index(original)] = replacement
    return "\n\n".join(paragraphs)


def statistics(results, expected):
    scored = [item for item in results if item.get("scored", True)]
    positive = [item for item in scored if item["kind"] == KINDS[0]]
    negative = [item for item in scored if item["kind"] != KINDS[0]]
    recall = sum(item["confirmed"] for item in positive) / len(positive) if positive else None
    false_positive = sum(item["confirmed"] for item in negative) / len(negative) if negative else None
    return {"recall": recall, "false_positive_rate": false_positive, "valid": len(results),
            "scored": len(scored),
            "discarded": expected * 4 - len(results),
            "pass": len(positive) == expected and len(negative) == 3 * expected
            and recall is not None and recall >= 0.8 and false_positive <= 0.1}


def run(generator, judge, checker, count, progress=None):
    if not generator or generator in (judge, checker):
        raise ValueError("变体生成模型必须区别于两层判定模型")
    result = {} if progress is None else progress
    cases, generated = [], []
    result.update(models={"generator": generator, "judge": judge, "cross_checker": checker}, cases=cases,
                  generated=generated, **statistics(cases, count))
    for index, fixture in enumerate(FIXTURES[:count], 1):
        # 角色互换使用固定样本。主被动句换位不会改变动作的执行者和对象。
        variants = {KINDS[-1]: fixture["role_reversal"]}
        model_input = {key: fixture[key] for key in ("text", "paragraph", "candidate")}
        for attempt in range(1, 3):
            missing = [kind for kind in KINDS[:-1] if kind not in variants]
            if not missing:
                break
            raw = api_client.call_api(
                "只改指定的一段，每项只返回一个替换段落，保留人名和场景。"
                "真兑现：候选动作和结果由指定角色实际完成。只提物品：仅说明物品出现或被看到，删掉候选动作及其结果。"
                "否定或传闻：明确事件没有发生或尚未证实，不能同时写出实际发生。"
                "任何替换都不可照抄原段。仅返回JSON对象，只含所请求的类别，值为替换段文本。",
                json.dumps({**model_input, "类别": missing}, ensure_ascii=False),
                model_name=generator, max_tokens=2400, temperature=0.3, retry=1,
            )
            returned = extract_json_obj(raw)
            generated.append({"group": index, "attempt": attempt, "characters": len(raw),
                              "returned_categories": list(returned)})
            for kind in missing:
                replacement = returned.get(kind)
                if isinstance(replacement, str):
                    replacement = replacement.strip()
                try:
                    replace_paragraph(fixture["text"], fixture["paragraph"], replacement)
                except ValueError:
                    continue
                if replacement not in variants.values():
                    variants[kind] = replacement
        for kind in KINDS:
            if kind not in variants:
                continue
            replacement = variants[kind]
            text = replace_paragraph(fixture["text"], fixture["paragraph"], replacement)
            score = reviewer.score_foreshadowing(text, {"to_resolve": [{
                "fid": "OF001", "description": fixture["candidate"], "action": "resolve", "source": "大纲",
            }]})
            cases.append({"group": index, "kind": kind, "replacement": replacement,
                          "source": "固定角色互换样本" if kind == KINDS[-1] else generator,
                          "scored": score is not None and not score.get("crosscheck_error", False),
                          "confirmed": bool(score and score["confirmed"])})
            result.update(statistics(cases, count))
    return result


def main():
    from tools.acceptance_run import cli
    return cli("变形验收", only_metamorphic=True)


if __name__ == "__main__":
    raise SystemExit(main())
