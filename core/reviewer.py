import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import re
from core.api_client import call_api
from core.memory_manager import MemoryManager

REVIEWER_SYSTEM = """你是一位严格的网络小说责任编辑，职责是找出章节中的问题。

你需要从以下三个维度审查：

【L1 逻辑审查】
- 人物行为是否符合已建立的性格设定？
- 时间线是否自洽？
- 是否有已死亡角色复活等硬伤？

【L2 伏笔审查】
- 是否遗忘了需要兑现的历史伏笔？
- 新埋的伏笔是否自然？

【L3 质量审查】
- 是否有大段注水内容？
- 是否有连续多句废话？
- 文风是否前后一致？

严格按以下JSON格式输出，不要加任何其他内容：
{
  "pass": true或false,
  "score": 1到10的整数,
  "l1_issues": ["问题描述"列表，没有则为空列表],
  "l2_issues": ["问题描述"列表，没有则为空列表],
  "l3_issues": ["问题描述"列表，没有则为空列表],
  "suggestions": "总体修改建议，如果pass为true则写'质量合格'"
}"""


def build_review_prompt(ctx: dict, chapter_num: int,
                        content: str, plot_goal: str) -> str:
    chars = ctx.get("characters", [])
    chars_str = "\n".join([
        f"【{c['name']}】性格：{c['personality']}｜状态：{c['current_status']}"
        for c in chars
    ])

    foreshadow = ctx.get("active_foreshadowing", [])
    f_str = "\n".join(
        [f"- {f['fid']}: {f['description']}" for f in foreshadow]
    ) if foreshadow else "（暂无）"

    return f"""=== 审稿任务 ===
章节：第{chapter_num}章
本章目标：{plot_goal}

=== 人物设定 ===
{chars_str}

=== 未兑现伏笔 ===
{f_str}

=== 待审正文 ===
{content}

请输出JSON审稿报告。"""


def review_chapter(novel_name: str, chapter_num: int,
                   content: str, plot_goal: str) -> dict:
    mm = MemoryManager(novel_name)
    ctx = mm.load_context(chapter_num)

    print(f"  正在审稿第{chapter_num}章...")
    prompt = build_review_prompt(ctx, chapter_num, content, plot_goal)

    raw = call_api(
        system_prompt=REVIEWER_SYSTEM,
        user_message=prompt,
        temperature=0.3,
        max_tokens=1024,
    )

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        print("  [警告] 审稿返回格式异常，默认通过")
        return {"pass": True, "score": 7, "suggestions": "格式异常，默认通过",
                "l1_issues": [], "l2_issues": [], "l3_issues": []}

    try:
        result = json.loads(match.group())
    except json.JSONDecodeError:
        print("  [警告] JSON解析失败，默认通过")
        return {"pass": True, "score": 7, "suggestions": "解析失败，默认通过",
                "l1_issues": [], "l2_issues": [], "l3_issues": []}

    status = "通过" if result.get("pass") else "不通过"
    score = result.get("score", 0)
    print(f"  [审稿结果] {status} | 评分：{score}/10")

    if not result.get("pass"):
        issues = (result.get("l1_issues", []) +
                  result.get("l2_issues", []) +
                  result.get("l3_issues", []))
        for issue in issues:
            print(f"    - {issue}")

    return result


def write_and_review(novel_name: str, chapter_num: int,
                     plot_goal: str, emotion_tag: str = "铺垫",
                     max_retry: int = 3) -> str:
    from core.writer import write_chapter

    for attempt in range(max_retry):
        print(f"\n  第{attempt+1}次写作尝试...")
        content = write_chapter(novel_name, chapter_num,
                                plot_goal, emotion_tag)

        result = review_chapter(novel_name, chapter_num,
                                content, plot_goal)

        if result.get("pass"):
            print(f"  [OK] 第{chapter_num}章审稿通过！")
            mm = MemoryManager(novel_name)
            mm.update_chapter_status(chapter_num, "approved")
            return content
        else:
            if attempt < max_retry - 1:
                suggestions = result.get("suggestions", "")
                plot_goal = f"{plot_goal}\n\n上次问题：{suggestions}"
                print(f"  [重写] 准备第{attempt+2}次写作...")
            else:
                print(f"  [警告] 连续{max_retry}次未通过，保留最后版本")
                mm = MemoryManager(novel_name)
                mm.update_chapter_status(chapter_num, "force_approved")
                return content

    return ""
