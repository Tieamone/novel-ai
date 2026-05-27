"""
任务卡质量审核模块

在章节策划（生成任务卡）之后、正式写作之前，
对任务卡的可执行性进行四维评估，提前发现规划问题。
"""

from core.utils import extract_json_obj, to_int, with_db_connection
from core.config_loader import get as cfg


# ──────────────────────────────────────────────────────────────────
#  审核 System Prompt
# ──────────────────────────────────────────────────────────────────

TASK_CARD_REVIEWER_SYSTEM = """你是一位资深的小说策划师，专门审核章节任务卡的可执行性。

你的职责是在正式写作之前，检查任务卡是否真正"可执行"，
提前发现空泛目标、前后矛盾、伏笔生硬、节奏单调等问题。

请从以下四个维度逐条评估，给出1-5的整数评分：

【具体性】（specificity）1-5分
- 1分：空泛描述，如"继续推进剧情"、"角色成长"、"主角踏上旅程"
- 3分：有大致方向，但缺少场景或行动细节
- 5分：场景级目标，明确指出地点、具体行动和预期结果
  示例："主角在废弃图书馆找到一本残缺日记，发现其中写有自己的名字"

【一致性】（consistency）1-5分
- 1分：明显偏离大纲走向，或任务卡前后矛盾（如主角同时出现在两个地方）
- 3分：基本沿大纲方向，但有个别跳跃或衔接不自然
- 5分：与大纲自然衔接，逻辑自洽，前后章因果关系清晰

【伏笔融合度】（fs_integration）1-5分
- 1分：伏笔要求生硬插入、牺牲情节自然度，读起来像"为了埋伏笔而埋伏笔"
- 3分：伏笔能被容纳但略显刻意
- 5分：伏笔自然融入情节，不破坏叙事流畅度，读者察觉不到"此处有伏笔"

【情绪节奏】（rhythm）1-5分
- 1分：连续≥4章同一情绪标签，无任何变化
- 3分：有变化但节奏不均匀（如突然堆积5个冲突章）
- 5分：情绪起伏合理分布，高低交错，高潮节点间隔适当（5-8章一个高潮）

综合评分规则：
- total_score = 具体性×3 + 一致性×2 + 伏笔融合度×2 + 情绪节奏×1（满分40）
- 通过条件：total_score >= 28 且 四个单项均 >= 2

严格只输出JSON，不要解释，不要Markdown：
{
  "total_score": 0-40整数,
  "scores": {
    "specificity": 1-5,
    "consistency": 1-5,
    "fs_integration": 1-5,
    "rhythm": 1-5
  },
  "issues": [
    {
      "chapter_num": 章节号,
      "dimension": "specificity|consistency|fs_integration|rhythm",
      "problem": "具体问题描述（引用任务卡原文）",
      "suggestion": "修改建议"
    }
  ],
  "pass": true/false
}"""


# ──────────────────────────────────────────────────────────────────
#  构建审核 Prompt
# ──────────────────────────────────────────────────────────────────

def build_task_card_review_prompt(tasks: list, outline: str,
                                   batch_start: int = 1) -> str:
    """
    组装任务卡审核输入 prompt。

    Args:
        tasks: 任务卡列表，每项至少含 chapter_num、plot_goal、emotion_tag
        outline: 小说大纲全文
        batch_start: 本批起始章节号

    Returns:
        完整的审核 prompt 字符串
    """
    # 大纲截取前500字
    outline_snippet = outline[:500] if outline else "（无大纲）"
    if len(outline) > 500:
        outline_snippet += "\n...（大纲总长{}字，此处仅展示开头部分）".format(
            len(outline)
        )

    # 任务卡列表摘要（只保留核心字段）
    task_lines = []
    for t in tasks:
        cn = t.get("chapter_num", "?")
        goal = t.get("plot_goal", "").strip()
        tag = t.get("emotion_tag", "铺垫").strip()
        if not goal:
            goal = "（空）"
        task_lines.append(f"  第{cn}章 [{tag}] {goal}")

    tasks_text = "\n".join(task_lines) if task_lines else "（无任务卡）"

    # 统计情绪标签分布，供审核参考
    tag_counts = {}
    for t in tasks:
        tag = t.get("emotion_tag", "铺垫").strip()
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
    tag_dist = "、".join(f"{k}×{v}" for k, v in tag_counts.items())

    return f"""=== 任务卡审核任务 ===
本批起始章节：第{batch_start}章
本批任务卡数量：{len(tasks)} 章

=== 小说大纲（开头部分） ===
{outline_snippet}

=== 任务卡列表 ===
情绪标签分布：{tag_dist}
{tasks_text}

=== 四维评分标准 ===
1. 具体性（1-5）：1=空泛（如"继续推进剧情"），5=场景级目标（有地点、行动、结果）
2. 一致性（1-5）：1=偏离大纲/前后矛盾，5=与大纲自然衔接且逻辑自洽
3. 伏笔融合度（1-5）：1=生硬插入、牺牲情节自然度，5=自然融入情节
4. 情绪节奏（1-5）：1=连续≥4章同一标签，5=有起伏且合理分布

请逐条评估后输出最终JSON结论。"""


# ──────────────────────────────────────────────────────────────────
#  审核任务卡
# ──────────────────────────────────────────────────────────────────

def review_task_cards(novel_name: str, tasks: list, outline: str,
                      batch_start: int = 1) -> dict:
    """
    调用审核模型对任务卡进行四维质量评估。

    Args:
        novel_name: 小说名称
        tasks: 任务卡列表
        outline: 小说大纲全文
        batch_start: 本批起始章节号

    Returns:
        {
            "score_total": int,          # 加权总分 0-40
            "scores": {                  # 四维子分
                "specificity": 1-5,
                "consistency": 1-5,
                "fs_integration": 1-5,
                "rhythm": 1-5,
            },
            "issues": [                  # 问题列表
                {"chapter_num": int, "dimension": str,
                 "problem": str, "suggestion": str}
            ],
            "pass": bool,               # 是否通过
            "review_error": bool,        # 审核过程是否出错
        }
    """
    # 延迟导入，避免循环依赖
    from core.api_client import call_reviewer_api

    prompt = build_task_card_review_prompt(tasks, outline, batch_start)

    try:
        raw = call_reviewer_api(
            system_prompt=TASK_CARD_REVIEWER_SYSTEM,
            user_message=prompt,
            temperature=0.25,
            max_tokens=2000,
        )
    except Exception as e:
        print(f"  [任务卡审核] ⚠️ API调用失败：{e}")
        return {
            "score_total": 0,
            "scores": {
                "specificity": 1,
                "consistency": 1,
                "fs_integration": 1,
                "rhythm": 1,
            },
            "issues": [{
                "chapter_num": batch_start,
                "dimension": "consistency",
                "problem": f"审核API调用失败：{str(e)[:100]}",
                "suggestion": "请检查API配置后重试",
            }],
            "pass": False,
            "review_error": True,
        }

    parsed = extract_json_obj(raw)
    if not parsed or not isinstance(parsed, dict):
        print("  [任务卡审核] ⚠️ 返回格式异常，无法解析JSON")
        return {
            "score_total": 0,
            "scores": {
                "specificity": 1,
                "consistency": 1,
                "fs_integration": 1,
                "rhythm": 1,
            },
            "issues": [{
                "chapter_num": batch_start,
                "dimension": "consistency",
                "problem": "审核结果格式异常，无法解析JSON",
                "suggestion": "请重新审核",
            }],
            "pass": False,
            "review_error": True,
        }

    # ── 解析四维子分 ──────────────────────────────────────
    raw_scores = parsed.get("scores", {})
    if not isinstance(raw_scores, dict):
        raw_scores = {}

    specificity = to_int(raw_scores.get("specificity"), default=3,
                         min_value=1, max_value=5)
    consistency = to_int(raw_scores.get("consistency"), default=3,
                         min_value=1, max_value=5)
    fs_integration = to_int(raw_scores.get("fs_integration"), default=3,
                            min_value=1, max_value=5)
    rhythm = to_int(raw_scores.get("rhythm"), default=3,
                    min_value=1, max_value=5)

    scores = {
        "specificity": specificity,
        "consistency": consistency,
        "fs_integration": fs_integration,
        "rhythm": rhythm,
    }

    # ── 按公式计算加权总分 ────────────────────────────────
    # 公式：total = 具体性×3 + 一致性×2 + 伏笔融合度×2 + 情绪节奏×1
    score_total = (
        specificity * 3 +
        consistency * 2 +
        fs_integration * 2 +
        rhythm * 1
    )

    # 也接受模型返回的 total_score（用于校验）
    model_total = to_int(parsed.get("total_score"), default=score_total,
                         min_value=0, max_value=40)
    # 如果模型返回值和计算结果差距>5，使用计算结果
    if abs(model_total - score_total) > 5:
        score_total = score_total
    else:
        score_total = max(score_total, model_total)  # 取高者

    # ── 解析问题列表 ──────────────────────────────────────
    raw_issues = parsed.get("issues", [])
    if not isinstance(raw_issues, list):
        raw_issues = []
    issues = []
    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        cn = to_int(item.get("chapter_num"), default=batch_start)
        dim = str(item.get("dimension", "")).strip().lower()
        if dim not in ("specificity", "consistency", "fs_integration", "rhythm"):
            dim = "consistency"
        problem = str(item.get("problem", "")).strip()
        suggestion = str(item.get("suggestion", "")).strip()
        if not problem:
            continue
        issues.append({
            "chapter_num": cn,
            "dimension": dim,
            "problem": problem,
            "suggestion": suggestion,
        })

    # ── 判定通过条件 ──────────────────────────────────────
    # pass_score 从 config 读取，默认 28
    pass_score = cfg("novel", "task_card_review_pass_score", 28)
    model_pass = bool(parsed.get("pass", False))
    rule_pass = (
        score_total >= pass_score
        and specificity >= 2
        and consistency >= 2
        and fs_integration >= 2
        and rhythm >= 2
    )
    final_pass = model_pass and rule_pass

    # 显示结果
    status = "通过" if final_pass else "不通过"
    print(
        f"  [任务卡审核] {status} | "
        f"总分：{score_total}/40 "
        f"（具体性:{specificity}/5 "
        f"一致性:{consistency}/5 "
        f"伏笔:{fs_integration}/5 "
        f"节奏:{rhythm}/5）"
    )
    if issues:
        print(f"  [任务卡审核] 发现 {len(issues)} 个问题：")
        for iss in issues[:5]:
            print(
                f"    第{iss['chapter_num']}章 [{iss['dimension']}] "
                f"{iss['problem'][:60]}"
            )

    return {
        "score_total": score_total,
        "scores": scores,
        "issues": issues,
        "pass": final_pass,
        "review_error": False,
    }


# ──────────────────────────────────────────────────────────────────
#  修正不通过的任务卡
# ──────────────────────────────────────────────────────────────────

def revise_task_cards(novel_name: str, tasks: list,
                      review_result: dict, outline: str) -> list:
    """
    根据审核结果修正问题章节的任务卡。

    只修正 review_result["issues"] 中标记的章节，
    其他章节保持原样。

    Args:
        novel_name: 小说名称
        tasks: 原始任务卡列表
        review_result: review_task_cards 的返回结果
        outline: 小说大纲全文

    Returns:
        修正后的完整 tasks 列表（结构与输入一致）
    """
    # 延迟导入，避免循环依赖
    from core.api_client import call_author_api

    issues = review_result.get("issues", [])
    if not issues:
        print("  [任务卡修正] 无问题项，无需修正")
        return tasks

    # 需要修正的章节号集合
    target_chapters = set()
    for iss in issues:
        cn = iss.get("chapter_num")
        if cn:
            target_chapters.add(cn)

    if not target_chapters:
        print("  [任务卡修正] 问题项未标注章节号，无法定位")
        return tasks

    # 建立章节号 → tasks 索引的映射
    tasks_by_chapter = {}
    for i, t in enumerate(tasks):
        cn = t.get("chapter_num")
        if cn is not None:
            tasks_by_chapter[cn] = i

    outline_snippet = outline[:800] if outline else "（无大纲）"

    # 最多2次修正尝试
    max_attempts = 2
    for attempt in range(max_attempts):
        if not target_chapters:
            break

        # 构建当前批次的修正问题描述
        current_issues = [
            iss for iss in issues
            if iss.get("chapter_num") in target_chapters
        ]

        for cn in sorted(target_chapters):
            idx = tasks_by_chapter.get(cn)
            if idx is None:
                print(f"  [任务卡修正] ⚠️ 第{cn}章不在任务卡列表中，跳过")
                target_chapters.discard(cn)
                continue

            task = tasks[idx]
            chapter_issues = [
                iss for iss in current_issues
                if iss.get("chapter_num") == cn
            ]

            if not chapter_issues:
                continue

            # 构建修正 prompt
            old_task_json = {
                "chapter_num": task.get("chapter_num"),
                "plot_goal": task.get("plot_goal", ""),
                "emotion_tag": task.get("emotion_tag", "铺垫"),
            }

            problem_text = "\n".join(
                f"- [{iss['dimension']}] {iss['problem']}\n  建议：{iss['suggestion']}"
                for iss in chapter_issues
            )

            revise_prompt = f"""=== 任务卡修正任务 ===

【原任务卡】
{old_task_json}

【审核发现的问题】
{problem_text}

【小说大纲（供参考，控制推进幅度）】
{outline_snippet}

请为第{cn}章重新生成一张任务卡，要求：
1. 修正上述所有问题
2. plot_goal 必须具体到场景级别（40-80字）
3. 人物行为要合理，逻辑要自洽
4. 与大纲走向一致，不超前推进
5. 保持原情绪标签（{task.get('emotion_tag', '铺垫')}），除非问题明确要求修改

严格按以下JSON格式输出，不要任何其他内容：
{{"chapter_num": {cn}, "plot_goal": "新的情节目标", "emotion_tag": "{task.get('emotion_tag', '铺垫')}"}}"""

            try:
                raw = call_author_api(
                    system_prompt=(
                        "你是专业的小说策划师，"
                        "擅长修正有问题的章节任务卡，"
                        "在保持故事连贯性的同时解决具体问题。"
                        "只输出JSON，不要任何其他内容。"
                    ),
                    user_message=revise_prompt,
                    temperature=0.6,
                    max_tokens=500,
                )
            except Exception as e:
                print(f"  [任务卡修正] ⚠️ 第{cn}章修正API调用失败：{e}")
                target_chapters.discard(cn)
                continue

            parsed = extract_json_obj(raw)
            if not parsed or not isinstance(parsed, dict):
                print(f"  [任务卡修正] ⚠️ 第{cn}章修正结果解析失败，保留原任务卡")
                target_chapters.discard(cn)
                continue

            new_goal = str(parsed.get("plot_goal", "")).strip()
            new_tag = str(parsed.get("emotion_tag",
                                     task.get("emotion_tag", "铺垫"))).strip()

            # 校验情绪标签合法性
            valid_tags = {"铺垫", "冲突", "爽点", "低谷", "反转"}
            if new_tag not in valid_tags:
                new_tag = task.get("emotion_tag", "铺垫")

            if not new_goal or len(new_goal) < 10:
                print(f"  [任务卡修正] ⚠️ 第{cn}章修正目标过短，保留原任务卡")
                target_chapters.discard(cn)
                continue

            # 更新任务卡
            tasks[idx] = {
                **task,
                "plot_goal": new_goal,
                "emotion_tag": new_tag,
            }
            print(f"  [任务卡修正] 第{cn}章已修正：{new_goal[:50]}...")
            target_chapters.discard(cn)

        # 检查是否还有未修正的
        if target_chapters:
            if attempt < max_attempts - 1:
                print(
                    f"  [任务卡修正] 还有 {len(target_chapters)} 章待修正，"
                    f"第{attempt + 2}次尝试..."
                )
            else:
                remaining = sorted(target_chapters)
                print(
                    f"  [任务卡修正] 经{max_attempts}次尝试，"
                    f"仍有 {len(remaining)} 章未修正：{remaining}，保留原任务卡"
                )

    return tasks


# ──────────────────────────────────────────────────────────────────
#  情绪节奏报告
# ──────────────────────────────────────────────────────────────────

def generate_rhythm_report(novel_name: str) -> str:
    """
    从数据库读取全部任务卡，生成情绪节奏分析报告。

    分析内容：
    - 情绪标签总体分布
    - 连续超过3章同一标签的位置
    - 高潮节点（冲突/爽点/反转）的间隔

    Args:
        novel_name: 小说名称

    Returns:
        格式化的报告字符串
    """
    # 从数据库读取全部任务卡
    with with_db_connection(novel_name) as conn:
        rows = conn.execute(
            "SELECT chapter_num, plot_goal, emotion_tag "
            "FROM chapter_tasks "
            "ORDER BY chapter_num"
        ).fetchall()

    if not rows:
        return "暂无任务卡数据，无法生成节奏报告。"

    # 转换为列表
    task_list = [
        {
            "chapter_num": r["chapter_num"],
            "plot_goal": r["plot_goal"] or "",
            "emotion_tag": r["emotion_tag"] or "铺垫",
        }
        for r in rows
    ]

    # 统一情绪标签
    valid_tags = {"铺垫", "冲突", "爽点", "低谷", "反转"}
    for t in task_list:
        if t["emotion_tag"] not in valid_tags:
            t["emotion_tag"] = "铺垫"

    total = len(task_list)

    # ── 1. 情绪标签分布 ──────────────────────────────────
    tag_counts = {}
    for t in task_list:
        tag = t["emotion_tag"]
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    dist_lines = ["  " + "-".join(
        f"{k}：{v}章（{v * 100 / total:.1f}%）"
        for k, v in sorted(tag_counts.items(),
                           key=lambda x: -x[1])
    )]

    # ── 2. 检测连续 >3 章同一标签 ────────────────────────
    streak_issues = []
    i = 0
    while i < len(task_list):
        current_tag = task_list[i]["emotion_tag"]
        streak_start = i
        while i < len(task_list) and task_list[i]["emotion_tag"] == current_tag:
            i += 1
        streak_len = i - streak_start
        if streak_len > 3:
            start_ch = task_list[streak_start]["chapter_num"]
            end_ch = task_list[i - 1]["chapter_num"]
            streak_issues.append(
                f"  第{start_ch}-{end_ch}章 连续{streak_len}章 [{current_tag}]"
            )

    if streak_issues:
        streak_text = "\n".join(streak_issues)
    else:
        streak_text = "  无（所有标签分布均匀，未出现连续超过3章的情况）"

    # ── 3. 高潮节点间隔检测 ──────────────────────────────
    climax_tags = {"冲突", "爽点", "反转"}
    climax_chapters = [
        t["chapter_num"] for t in task_list
        if t["emotion_tag"] in climax_tags
    ]

    gap_issues = []
    if len(climax_chapters) >= 2:
        for i in range(1, len(climax_chapters)):
            gap = climax_chapters[i] - climax_chapters[i - 1]
            if gap > 8:
                gap_issues.append(
                    f"  第{climax_chapters[i-1]}章 → 第{climax_chapters[i]}章 "
                    f"间隔{gap}章（超过建议的5-8章）"
                )
        # 检查开头到第一个高潮
        if climax_chapters[0] > 5:
            gap_issues.insert(0,
                f"  第1章 → 第{climax_chapters[0]}章（首个高潮节点）"
                f"间隔{climax_chapters[0]}章"
            )

    if gap_issues:
        gap_text = "\n".join(gap_issues)
    elif not climax_chapters:
        gap_text = "  ⚠️ 未检测到任何高潮节点（冲突/爽点/反转），建议在任务卡中增加节奏变化"
    else:
        gap_text = "  ✓ 高潮节点间隔合理（均在5-8章范围内）"

    # ── 组装报告 ──────────────────────────────────────────
    report_lines = [
        "=" * 50,
        f"  情绪节奏分析报告 - 《{novel_name}》",
        "=" * 50,
        "",
        f"  总章节数：{total} 章",
        "",
        "【情绪标签分布】",
        dist_lines[0],
        "",
        "【连续重复标签检测（>3章）】",
        streak_text,
        "",
        "【高潮节点间隔检查（冲突/爽点/反转）】",
        gap_text,
        "",
        "=" * 50,
    ]

    return "\n".join(report_lines)