import re
import json
from core.api_client import call_author_api
from core.memory_manager import MemoryManager
from core.config_loader import get as cfg

# ==================== 篇幅选项 ====================

NOVEL_LENGTH_OPTIONS = {
    "1": {"name": "短篇",   "chapters": 40,  "desc": "30-50章，故事紧凑，单一主线"},
    "2": {"name": "中篇",   "chapters": 80,  "desc": "60-100章，标准网文，情节均衡"},
    "3": {"name": "长篇",   "chapters": 150, "desc": "100-200章，多条支线，慢热叙事"},
    "4": {"name": "超长篇", "chapters": 300, "desc": "200章以上，史诗规模，多弧线"},
    "5": {"name": "自定义", "chapters": None, "desc": "自己输入目标章数"},
}

# ==================== 策划提示词 ====================

WORLD_PROMPT = """你是一位专业的中文网络小说策划师，擅长构建让读者沉浸其中的世界。

请根据用户提供的大纲和主要角色，生成一份详细且有质感的世界观设定。

要求：
1. 世界观必须与大纲内容高度契合，服务于故事的核心冲突
2. 参考角色背景，让世界观能合理容纳这些角色的存在和行动
3. 必须包含四个维度：
   - 地理/物理环境：这个世界长什么样，有什么独特的感官特征（光线、气味、声音）
   - 力量体系/核心规则：这个世界运作的底层逻辑，有什么代价和限制
   - 社会结构与权力格局：谁掌握权力，谁被压迫，普通人过着什么样的生活
   - 世界的伤口：这个世界出了什么问题，或者曾经发生过什么影响至今的事
4. 写出这个世界独有的"气质"——是阴暗压抑、是奇异梦幻、是热血残酷，让读者一眼就感受到
5. 语言简洁但要有细节，总字数500字以内
6. 直接输出设定内容，不加前缀"""

CHARACTER_PROMPT = """你是一位专业的中文网络小说策划师，擅长塑造让读者过目不忘的人物。

请根据用户提供的大纲、世界观和角色名单，为每个角色生成详细档案。

要求：
1. 性格和背景必须与大纲情节逻辑一致，人物是为了推动冲突而存在的
2. 每个角色必须有内在矛盾：他们渴望的东西和他们恐惧的东西之间存在张力
3. 角色之间的关系要有层次：不只是"朋友/敌人"，而是有具体的历史、误解和潜在冲突
4. 每个角色要有独特的说话方式或行为习惯（1-2个具体细节），让读者能辨认出他是谁
5. 每个角色必须包含：
   - 外貌（有特征，不要通用模板）
   - 核心性格（用具体行为而非标签描述，例如"遇到危险会先观察出口"比"谨慎"更有信息量）
   - 隐藏秘密（与主线冲突相关）
   - 致命弱点（真实的，不能靠降智来体现）
   - 初始位置与状态
   - 与其他角色的关系（有具体张力，不只是情感标签）
6. 严格按以下JSON格式输出，不要加任何其他内容：

[
  {
    "name": "角色名",
    "role": "主角/配角/反派",
    "appearance": "外貌（包含1-2个辨识度高的特征）",
    "personality": "核心性格（用行为描述，包含内在矛盾：渴望X但恐惧Y）",
    "secret": "隐藏秘密（与主线冲突相关）",
    "weakness": "致命弱点（真实的，非降智）",
    "current_location": "初始位置",
    "current_status": "初始状态（包含行动和情绪）",
    "relationships": {"其他角色名": "具体关系（包含历史和当前张力）"}
  }
]"""

CHARACTER_EXTRACT_PROMPT = """你是一位专业的小说策划师。
请根据以下大纲，提取或推断出主要角色名单。

要求：
1. 列出大纲中明确提到的角色
2. 如有需要可补充1-2个大纲中暗示但未命名的关键配角（但要在名字后注明"推断"）
3. 每个角色一行，格式：角色名（角色定位）
4. 只输出角色列表，不要其他内容"""


def _build_outline_prompt(target_chapters: int) -> str:
    act1_end = max(10, target_chapters // 5)
    act2_end = max(act1_end + 10, int(target_chapters * 0.75))
    return f"""你是一位专业的中文网络小说策划师，擅长设计让读者停不下来的故事结构。

请根据用户提供的小说基本信息，生成一份完整的总大纲。

要求：
1. 包含三幕结构，与{target_chapters}章的总篇幅严格对应：
   - 开局（1-{act1_end}章）：建立世界感和人物，植入核心悬念，让读者对主角有情感投入
   - 发展（{act1_end+1}-{act2_end}章）：主线冲突层层升级，每段都有新的转折，伏笔交织，支线推进
   - 高潮结局（{act2_end+1}-{target_chapters}章）：矛盾总爆发，伏笔全部兑现，给读者一个意外却合理的结局
2. 每幕列出3-5个关键转折点，注明大致发生在第几章
3. 指出主要伏笔的埋设位置和兑现位置（格式：埋于第X章→兑现于第Y章）
4. 结局要有反转，但反转必须是"读者翻回去看才发现线索一直都在"的那种
5. 节奏要与{target_chapters}章的篇幅匹配：短篇紧凑，长篇有铺陈，不人为压缩或注水
6. 必须给故事一个独特的核心主题句（一句话概括这个故事的灵魂）
7. 总字数800字以内，直接输出大纲内容"""


def _extract_tasks_json(raw: str) -> list | None:
    """从 AI 返回中提取任务卡 JSON 数组，含截断自动修复。

    返回解析成功的 task list，失败返回 None。
    """
    if not raw or not raw.strip():
        print("  [诊断] AI 返回为空")
        return None

    # 1. 正则提取 JSON 数组
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        print(f"  [诊断] 未在返回中找到 JSON 数组。开头100字: {raw[:100]}")
        return None

    json_str = match.group()

    # 2. 尝试直接解析
    try:
        tasks = json.loads(json_str)
        if isinstance(tasks, list):
            return tasks
    except json.JSONDecodeError:
        pass

    # 3. 截断恢复：JSON 不完整，尝试修复
    print(f"  [诊断] JSON 解析失败（{len(json_str)} 字），尝试截断恢复...")
    tasks = _repair_truncated_json(json_str)
    if tasks is not None:
        print(f"  [OK] 截断恢复成功，找回 {len(tasks)} 条任务卡")
        return tasks

    # 4. 全部失败，输出诊断信息
    print(f"  [诊断] JSON 尾部 200 字: ...{json_str[-200:]}")
    return None


def _repair_truncated_json(json_str: str) -> list | None:
    """尝试修复被 max_tokens 截断的 JSON 数组。

    常见截断模式：
    - 在字符串中间截断：{"chapter_num": 37, "plot_goal": "林风在废墟中
    - 在对象中间截断：{"chapter_num": 37, "plot_goal": "...",
    - 数组未闭合：...}  ← 缺少 ]
    """
    stripped = json_str.strip()

    # 策略1：只缺结尾的 ]（最后一个对象完整）
    if stripped.endswith('}') and not stripped.endswith(']'):
        repaired = stripped + '\n]'
        try:
            tasks = json.loads(repaired)
            if isinstance(tasks, list):
                return tasks
        except json.JSONDecodeError:
            pass

    # 策略2：在对象/字符串内部被截断 —— 找到最后一个完整的 "},"
    # "}," 是 JSON 数组中完整对象的可靠标记（非最后一个元素）
    last_complete = stripped.rfind('},')
    if last_complete > 0:
        truncated = stripped[:last_complete + 1]  # 保留到 "}"
        truncated = truncated.rstrip(', \t\n\r')
        repaired = truncated + '\n]'
        try:
            tasks = json.loads(repaired)
            if isinstance(tasks, list):
                return tasks
        except json.JSONDecodeError:
            pass

    # 策略3：最后一条刚好完整（以 } 结尾但没有逗号），但缺 ]
    # 找到倒数第二个 }（跳过最后一个完整对象的 }）
    last_brace = stripped.rfind('}')
    if last_brace > 0:
        second_last = stripped.rfind('},', 0, last_brace)
        if second_last > 0:
            truncated = stripped[:second_last + 1]
            truncated = truncated.rstrip(', \t\n\r')
            repaired = truncated + '\n]'
            try:
                tasks = json.loads(repaired)
                if isinstance(tasks, list):
                    return tasks
            except json.JSONDecodeError:
                pass

    # 策略4：激进修复 —— 移除拖尾碎片，尝试加 "}]" + "]"
    # 去掉最后一行（通常是被截断的不完整行），补全结尾
    lines = stripped.split('\n')
    if len(lines) >= 3:
        # 去掉最后不完整的行
        while lines and not lines[-1].strip().rstrip(',').endswith('}'):
            lines.pop()
        if lines:
            truncated = '\n'.join(lines).rstrip(', \t\n\r')
            repaired = truncated + '\n]'
            try:
                tasks = json.loads(repaired)
                if isinstance(tasks, list):
                    return tasks
            except json.JSONDecodeError:
                pass

    return None


def _build_task_split_prompt(first_batch: int, full_target: int, outline: str,
                             start: int = 1,
                             prev_chapter_ending: str = "",
                             urgent_foreshadowing: list = None,
                             novel_name: str = "") -> str:
    if start == 1:
        chapter_range = f"前{first_batch}章"
    else:
        chapter_range = f"第{start}章到第{start + first_batch - 1}章"

    ending_block = ""
    ending_rule = ""
    if prev_chapter_ending:
        ending_block = f"""
【上章结尾悬念】
{prev_chapter_ending}

"""
        ending_rule = "\n6. 如果提供了【上章结尾悬念】，第一章任务卡必须与该悬念逻辑衔接，确保情节不断裂"

    # 动态编号的新增规则（接在 ending_rule 之后）
    ban_phrase = '禁止使用"继续推进剧情"、"进一步发展"、"主角有了新发现"等空泛表述作为 plot_goal'
    fs_advice = '如果 prompt 中包含伏笔建议，请将其自然融入对应章节的 plot_goal，不要为此改变原有的剧情逻辑和节奏'
    new_rule_base = 6 if not ending_rule else 7
    new_rules = f"\n{new_rule_base}. {ban_phrase}\n{new_rule_base + 1}. {fs_advice}"

    prompt = f"""根据以下小说大纲，生成{chapter_range}的章节任务卡。

全书目标总章数为{full_target}章，请据此控制每章推进幅度，不要赶进度。

任务卡规则：
1. 每章只安排一个核心情节节点（主角做了什么、发现了什么、与谁发生了什么）
2. plot_goal要具体到场景级别：不是"主角继续调查"，而是"主角在废弃图书馆找到一本残缺日记，发现其中有自己名字"
3. 情绪标签的分布要有节奏：不要连续超过3章都是同一个标签，要有起伏
4. 前5章的任务卡要重点建立世界感和人物关系，不要急着上大冲突
5. 每隔5-8章要安排一个"高潮/转折"节点（冲突/爽点/反转），保持读者追读动力{ending_rule}{new_rules}
{ending_block}大纲：
{outline}

严格按JSON格式输出，不要任何其他内容：
[
  {{
    "chapter_num": {start},
    "plot_goal": "本章情节目标（40-80字，具体到场景和行动）",
    "emotion_tag": "铺垫"
  }}
]

情绪标签只能从以下5个中选1个：铺垫 / 冲突 / 爽点 / 低谷 / 反转"""

    if urgent_foreshadowing:
        fs_lines = []
        for f in urgent_foreshadowing:
            fid = f.get("fid", "?")
            desc = f.get("description", "")
            plant = f.get("plant_chapter", "?")
            fs_lines.append(f" - {fid}: {desc}（埋于第{plant}章）")
        fs_block = "\n".join(fs_lines)
        prompt += f"""

【本批必须兑现的伏笔】
以下伏笔沉睡过久，请在本批任务卡中安排至少1-2个章节明确兑现：
{fs_block}"""

    if novel_name:
        prompt = _add_outline_fs_to_prompt(prompt, novel_name, start,
                                           start + first_batch - 1)

    return prompt


def _build_extend_task_prompt(from_chapter: int, end_chapter: int,
                              full_target: int, outline: str,
                              prev_chapter_ending: str = "",
                              urgent_foreshadowing: list = None,
                              novel_name: str = "") -> str:
    ending_block = ""
    ending_rule = ""
    if prev_chapter_ending:
        ending_block = f"""
【上章结尾悬念】
{prev_chapter_ending}

"""
        ending_rule = "\n5. 如果提供了【上章结尾悬念】，第一章任务卡必须与该悬念逻辑衔接，确保情节不断裂"

    prompt = f"""根据以下大纲，生成第{from_chapter}章到第{end_chapter}章的任务卡。
全书目标总章数为{full_target}章，当前进行到中段，请据此控制推进节奏。

任务卡规则：
1. 每章只安排一个核心情节节点，具体到场景级别
2. 这一批任务卡处于故事中段，要有新的冲突升级，要有伏笔回收，要推进人物关系变化
3. 每隔5-8章安排一个高潮节点，保持追读驱动力
4. 情绪标签要有起伏，不要连续同一标签超过3章{ending_rule}
{ending_block}大纲：
{outline}

JSON格式：
[
  {{
    "chapter_num": {from_chapter},
    "plot_goal": "情节目标（40-80字，具体到场景）",
    "emotion_tag": "铺垫"
  }}
]
情绪标签只能从：铺垫 / 冲突 / 爽点 / 低谷 / 反转 中选1个"""

    if urgent_foreshadowing:
        fs_lines = []
        for f in urgent_foreshadowing:
            fid = f.get("fid", "?")
            desc = f.get("description", "")
            plant = f.get("plant_chapter", "?")
            fs_lines.append(f" - {fid}: {desc}（埋于第{plant}章）")
        fs_block = "\n".join(fs_lines)
        prompt += f"""

【本批必须兑现的伏笔】
以下伏笔沉睡过久，请在本批任务卡中安排至少1-2个章节明确兑现：
{fs_block}"""

    if novel_name:
        prompt = _add_outline_fs_to_prompt(prompt, novel_name, from_chapter,
                                           end_chapter)

    return prompt


def _add_outline_fs_to_prompt(prompt: str, novel_name: str,
                               chapter_from: int, chapter_to: int,
                               inject_style: str = None) -> str:
    """从 outline_foreshadowing 表查询批次范围内的伏笔，追加到 prompt 中。

    批次超过 60 章时仅注入前 60 章的伏笔，避免 prompt 溢出。
    inject_style: "forced" 强制语气 / "guided" 引导语气（默认从 config 读取）
    """
    try:
        from collections import defaultdict
        from core.outline_manager import get_chapter_outline_tasks

        if inject_style is None:
            inject_style = cfg("novel", "foreshadow_injection_style", "guided")

        actual_to = min(chapter_to, chapter_from + 59)
        all_plant = []
        all_resolve = []
        for ch in range(chapter_from, actual_to + 1):
            tasks = get_chapter_outline_tasks(novel_name, ch)
            for item in tasks.get("to_plant", []):
                item["_chapter"] = ch
                all_plant.append(item)
            for item in tasks.get("to_resolve", []):
                item["_chapter"] = ch
                all_resolve.append(item)

        if not all_plant and not all_resolve:
            return prompt

        # ── 伏笔密度控制 ──
        max_per_ch = cfg("novel", "max_foreshadow_per_chapter", 2)
        ch_items = defaultdict(list)
        for item in all_plant:
            ch_items[item["_chapter"]].append(item)
        for item in all_resolve:
            ch_items[item["_chapter"]].append(item)

        for ch, items in ch_items.items():
            total = len(items)
            if total > max_per_ch:
                items_sorted = sorted(
                    items, key=lambda x: x.get("importance", 0), reverse=True
                )
                dropped = items_sorted[max_per_ch:]
                dropped_fids = [d["fid"] for d in dropped]
                print(
                    f"  [伏笔密度] 第{ch}章超限({total}个)，"
                    f"保留重要度最高的{max_per_ch}个，"
                    f"降级: {', '.join(dropped_fids)}"
                )
                dropped_keys = {(d["fid"], d["_chapter"]) for d in dropped}
                all_plant = [
                    t for t in all_plant
                    if (t["fid"], t["_chapter"]) not in dropped_keys
                ]
                all_resolve = [
                    t for t in all_resolve
                    if (t["fid"], t["_chapter"]) not in dropped_keys
                ]

        if not all_plant and not all_resolve:
            return prompt

        # ── 构建提示 ──
        is_forced = (inject_style == "forced")
        if is_forced:
            lines = ["\n【大纲伏笔任务（各章必须执行的埋入/兑现）】"]
            if all_plant:
                lines.append("以下为各章需要埋入的伏笔：")
                for t in all_plant:
                    lines.append(
                        f"  第{t['_chapter']}章必须埋入: {t['fid']} "
                        f"{t['description']} (重要度{t['importance']})"
                    )
            if all_resolve:
                lines.append("以下为各章需要兑现的伏笔：")
                for t in all_resolve:
                    lines.append(
                        f"  第{t['_chapter']}章必须兑现: {t['fid']} "
                        f"{t['description']} (重要度{t['importance']})"
                    )
        else:
            lines = ["\n【大纲伏笔任务（各章建议执行的埋入/兑现）】"]
            if all_plant:
                lines.append("以下为各章建议埋入的伏笔：")
                for t in all_plant:
                    lines.append(
                        f"  第{t['_chapter']}章建议埋入: {t['fid']} "
                        f"{t['description']} (★重要度{t['importance']})"
                    )
                    lines.append(
                        "  融入提示：可以在场景描写或角色对话中不经意地提及"
                    )
            if all_resolve:
                lines.append("以下为各章建议兑现的伏笔：")
                for t in all_resolve:
                    lines.append(
                        f"  第{t['_chapter']}章建议兑现: {t['fid']} "
                        f"{t['description']} (★重要度{t['importance']})"
                    )
                    lines.append(
                        "  融入提示：可以在场景描写或角色对话中不经意地提及"
                    )

        if actual_to < chapter_to:
            lines.append(f"(第{actual_to + 1}章及之后的伏笔将在后续批次中注入)")
        lines.append("请务必将上述伏笔要求写入对应章节的 plot_goal。")
        if not is_forced:
            lines.append(
                "请以情节自然流畅为优先，不得为埋入伏笔而强行改变情节走向。"
            )
        return prompt + "\n" + "\n".join(lines)
    except Exception:
        return prompt


# ==================== 交互工具 ====================

def _read_block_input() -> str:
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _choose_draft_review_mode() -> bool:
    print("\n" + "=" * 50)
    print("  策划结果确认模式")
    print("=" * 50)
    print("  1. 关闭（默认，直接采用AI结果）")
    print("  2. 开启（世界观/人物档案/任务卡先看草稿再确认）")
    print()
    choice = input("请选择（默认1）：").strip() or "1"
    enabled = (choice == "2")
    if enabled:
        print("  [OK] 已开启 AI草稿后人工编辑确认模式")
    return enabled


def _confirm_text_draft(step_name: str, draft_text: str) -> str:
    print(f"\n【{step_name} AI草稿预览】")
    preview = (draft_text or "").strip()
    if len(preview) > 300:
        print(preview[:300] + "...")
    else:
        print(preview or "（空）")
    print()
    print("  1. 直接采用草稿（默认）")
    print("  2. 手动编辑后采用")
    choice = input("请选择：").strip() or "1"
    if choice != "2":
        return draft_text
    print("\n  请粘贴修改后的完整内容。")
    print("  输入 END 单独一行结束。")
    print("-" * 50)
    edited = _read_block_input()
    if not edited:
        print("  [提示] 未输入内容，继续使用AI草稿")
        return draft_text
    print(f"  [OK] 已采用手动编辑版，共 {len(edited)} 字")
    return edited


def _confirm_json_draft(step_name: str, draft_obj, preview_items: int = 3):
    print(f"\n【{step_name} AI草稿预览】")
    preview_obj = draft_obj
    total_items = None
    if isinstance(draft_obj, list):
        total_items = len(draft_obj)
        if len(draft_obj) > preview_items:
            preview_obj = draft_obj[:preview_items]
    try:
        print(json.dumps(preview_obj, ensure_ascii=False, indent=2))
    except Exception:
        print(str(preview_obj))
    if total_items is not None and total_items > preview_items:
        print(f"\n  （仅预览前{preview_items}条，共{total_items}条）")
    print()
    print("  1. 直接采用草稿（默认）")
    print("  2. 手动编辑JSON后采用")
    choice = input("请选择：").strip() or "1"
    if choice != "2":
        return draft_obj

    while True:
        print("\n  请粘贴修改后的完整JSON。")
        print("  输入 END 单独一行结束。")
        print("-" * 50)
        edited_raw = _read_block_input()
        if not edited_raw:
            print("  [提示] 未输入内容，继续使用AI草稿")
            return draft_obj
        try:
            edited_obj = json.loads(edited_raw)
        except Exception as e:
            print(f"  [错误] JSON解析失败：{e}")
            retry = input("  输入 r 重试，其他键使用AI草稿：").strip().lower()
            if retry == "r":
                continue
            return draft_obj
        print("  [OK] 已采用手动编辑JSON")
        return edited_obj


# ==================== Step 0：篇幅选择 ====================

def _choose_novel_length() -> int:
    print("\n" + "=" * 50)
    print("  第零步：确定小说目标篇幅")
    print("=" * 50)
    for k, v in NOVEL_LENGTH_OPTIONS.items():
        chapters_str = f"约{v['chapters']}章" if v["chapters"] else "自定义"
        print(f"  {k}. {v['name']:<6} （{chapters_str}）  {v['desc']}")
    print()
    choice = input("请选择（默认2）：").strip() or "2"
    if choice not in NOVEL_LENGTH_OPTIONS:
        choice = "2"

    opt = NOVEL_LENGTH_OPTIONS[choice]
    if opt["chapters"] is None:
        while True:
            try:
                n = int(input("  请输入目标章数（建议20-500）：").strip())
                if 10 <= n <= 1000:
                    target = n
                    break
                print("  [提示] 请输入10-1000之间的整数")
            except ValueError:
                print("  [错误] 请输入数字")
    else:
        target = opt["chapters"]

    name = opt["name"] if opt["chapters"] else "自定义"
    print(f"\n  [OK] 目标篇幅：{target}章（{name}）")
    return target


# ==================== Step 1：大纲 ====================

def get_outline_choice(genre: str, keywords: str, novel_name: str,
                       target_chapters: int = 100) -> str:
    print("\n" + "=" * 50)
    print("  第一步：确定大纲")
    print("=" * 50)
    print("  1. 由AI根据类型和关键词自动生成")
    print("  2. 我自己提供大纲（粘贴文本）")
    print()
    choice = input("请选择（默认1）：").strip() or "1"

    if choice == "2":
        outline = _get_outline_from_user()
        if outline:
            return outline
        print("  [提示] 未收到内容，改为AI生成")

    print(f"\n  正在生成总大纲（目标篇幅：{target_chapters}章）...")
    outline = call_author_api(
        system_prompt=_build_outline_prompt(target_chapters),
        user_message=f"小说名：{novel_name}\n类型：{genre}\n关键词/基本设定：{keywords}",
        temperature=0.9,
    )
    print("  [OK] 总大纲已生成")
    return outline


def _get_outline_from_user() -> str:
    print()
    print("  请将大纲内容粘贴到下方。")
    print("  粘贴完成后，在新的一行单独输入 END 并回车结束。")
    print("-" * 50)
    outline = _read_block_input()
    if outline:
        print(f"  [OK] 已接收大纲，共 {len(outline)} 字")
    return outline


# ==================== Step 2：角色 ====================

def get_characters_choice(outline: str) -> list:
    print("\n" + "=" * 50)
    print("  第二步：确定角色名单")
    print("=" * 50)
    print("  1. AI从大纲中自动提取角色")
    print("  2. 我自己输入角色名字")
    print()
    choice = input("请选择（默认1）：").strip() or "1"

    if choice == "2":
        return _manual_input_characters()

    print("\n  正在从大纲提取角色...")
    raw = call_author_api(
        system_prompt=CHARACTER_EXTRACT_PROMPT,
        user_message=f"大纲内容：\n{outline}",
        temperature=0.5,
        max_tokens=300,
    )

    names = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        match = re.match(r'^[\d\.\s]*([^\s（(【]+)', line)
        if match:
            name = match.group(1).strip()
            if name and len(name) <= 10:
                names.append(name)

    if names:
        print(f"  [OK] 从大纲提取到 {len(names)} 个角色：{' / '.join(names)}")
        confirm = input(
            "  确认使用以上角色？(回车确认 / 输入n重新手动输入)："
        ).strip()
        if confirm.lower() == "n":
            return _manual_input_characters()
        return names

    print("  [警告] 未能提取到角色，请手动输入")
    return _manual_input_characters()


def _manual_input_characters() -> list:
    names_input = input("  请输入角色名字，用逗号分隔：").strip()
    names = [
        n.strip() for n in names_input.replace(",", "，").split("，")
        if n.strip()
    ]
    if not names:
        names = ["主角"]
    print(f"  [OK] 已设置 {len(names)} 个角色：{' / '.join(names)}")
    return names


# ==================== Step 3：世界观 ====================

def generate_world(novel_name: str, genre: str, outline: str,
                   character_names: list, mm: MemoryManager,
                   review_mode: bool = False) -> str:
    print("\n  正在根据大纲和角色生成世界观...")
    names_str = "、".join(character_names)
    world = call_author_api(
        system_prompt=WORLD_PROMPT,
        user_message=(
            f"小说类型：{genre}\n\n"
            f"主要角色：{names_str}\n\n"
            f"大纲内容：\n{outline}"
        ),
        temperature=0.85,
    )
    if review_mode:
        world = _confirm_text_draft("世界观", world)
    mm.save_world_settings(world)
    print("  [OK] 世界观已生成并保存")
    return world


# ==================== Step 4：人物档案 ====================

def generate_characters(character_names: list, outline: str,
                        world: str, mm: MemoryManager,
                        review_mode: bool = False) -> list:
    print("\n  正在生成人物档案...")
    names_str = "、".join(character_names)
    raw = call_author_api(
        system_prompt=CHARACTER_PROMPT,
        user_message=(
            f"角色名单：{names_str}\n\n"
            f"大纲内容：\n{outline}\n\n"
            f"世界观：\n{world}"
        ),
        temperature=0.8,
    )

    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        print("  [警告] 人物档案解析失败，使用基础档案")
        for name in character_names:
            mm.save_character(name, {
                "role": "待确认", "appearance": "", "personality": "",
                "secret": "", "weakness": "",
                "current_location": "", "current_status": "",
                "relationships": {}
            })
        return [{"name": n} for n in character_names]

    try:
        characters = json.loads(match.group())
    except Exception:
        print("  [警告] JSON解析失败，使用基础档案")
        for name in character_names:
            mm.save_character(name, {
                "role": "待确认", "appearance": "", "personality": "",
                "secret": "", "weakness": "",
                "current_location": "", "current_status": "",
                "relationships": {}
            })
        return [{"name": n} for n in character_names]

    if review_mode:
        characters = _confirm_json_draft("人物档案JSON", characters, 2)

    if not isinstance(characters, list):
        characters = []

    normalized = []
    used_names = set()
    fallback_names = iter(character_names)

    for char in characters:
        if not isinstance(char, dict):
            continue

        name = str(char.get("name", "")).strip()
        if not name:
            while True:
                try:
                    candidate = str(next(fallback_names)).strip()
                except StopIteration:
                    candidate = ""
                if candidate and candidate not in used_names:
                    name = candidate
                    break
                if not candidate:
                    break
        if not name or name in used_names:
            continue
        used_names.add(name)

        relationships = char.get("relationships", {})
        if not isinstance(relationships, dict):
            relationships = {}

        cleaned = {
            "role": str(char.get("role", "待确认") or "待确认"),
            "appearance": str(char.get("appearance", "") or ""),
            "personality": str(char.get("personality", "") or ""),
            "secret": str(char.get("secret", "") or ""),
            "weakness": str(char.get("weakness", "") or ""),
            "current_location": str(char.get("current_location", "") or ""),
            "current_status": str(char.get("current_status", "") or ""),
            "relationships": relationships,
        }
        mm.save_character(name, cleaned)
        normalized.append({"name": name, **cleaned})

    if not normalized:
        print("  [警告] 人物档案缺少有效姓名，使用基础档案")
        for name in character_names:
            mm.save_character(name, {
                "role": "待确认", "appearance": "", "personality": "",
                "secret": "", "weakness": "",
                "current_location": "", "current_status": "",
                "relationships": {}
            })
        return [{"name": n} for n in character_names]

    print(f"  [OK] {len(normalized)} 个人物档案已生成并保存")
    return normalized


# ==================== Step 5：风格 ====================

def get_style_choice() -> str:
    from core.writer import AUTHOR_STYLES

    print("\n" + "=" * 50)
    print("  第五步：选择写作风格")
    print("=" * 50)
    for key, style in AUTHOR_STYLES.items():
        print(f"  {key}. {style['name']:<12} {style['desc']}")
    print("  7. 自定义风格（自己描述写作风格）")
    print()

    choice = input("请输入编号（默认1）：").strip() or "1"

    if choice == "7":
        print()
        custom_desc = input("  请描述你想要的写作风格：").strip()
        if custom_desc:
            print("  [OK] 已设置自定义风格")
            return f"custom:{custom_desc}"
        print("  [提示] 未输入描述，使用默认风格")
        return "1"

    if choice not in AUTHOR_STYLES:
        choice = "1"

    print(f"\n  [OK] 已选择风格：{AUTHOR_STYLES[choice]['name']}\n")
    return choice


# ==================== 伏笔紧急度查询 ====================

def _get_urgent_foreshadowing(mm: MemoryManager, current_chapter: int,
                              sleep_threshold: int = 20, limit: int = 5) -> list:
    """获取沉睡超过 sleep_threshold 章且 status='active' 的伏笔，取前 limit 个。"""
    all_active = mm.load_active_foreshadowing()
    urgent = []
    for f in all_active:
        plant = max(f.get("plant_chapter", 1) or 1, 1)
        age = current_chapter - plant
        if age >= sleep_threshold:
            urgent.append(f)
    urgent.sort(key=lambda x: -((current_chapter - max(x.get("plant_chapter", 1) or 1, 1))))
    return urgent[:limit]


# ==================== Step 6：任务卡 ====================

def _get_model_max_output() -> int:
    """获取当前作者模型的最大输出 tokens，用于计算每批任务卡数量。
    取不到则回退到 16000。
    """
    try:
        from core.api_client import get_current_author_max_tokens
        val = get_current_author_max_tokens()
        if val and val > 0:
            return val
    except Exception:
        pass
    return 16000


def _compute_batch_params(chapter_count: int) -> tuple:
    """根据模型能力和章节数计算 (每批章数, 每批 max_tokens, 总批次数)。
    - 每章约需 200 tokens
    - 每批 ≥ 10 章
    - 总批次数做向上取整
    """
    import math
    model_max = _get_model_max_output()
    per_batch = max(model_max // 200, 10)
    batch_max_tokens = max(8000, model_max)
    total_batches = math.ceil(chapter_count / per_batch)
    return per_batch, batch_max_tokens, total_batches


def split_outline_to_tasks(outline: str, novel_name: str,
                           review_mode: bool = False,
                           target_chapters: int = 0,
                           full_batch: bool = False,
                           start: int = 1):
    """
    将大纲拆分为章节任务卡。

    full_batch=True: 一次性生成全书任务卡。若模型吞吐量不足，提示用户
                     换模型或降级为分批。
    full_batch=False: 分批生成，每批大小由模型能力动态决定。
    """
    from core.utils import with_db_connection
    import math

    if target_chapters > 0:
        if full_batch:
            first_batch = target_chapters
        else:
            first_batch = min(target_chapters, cfg("novel", "pre_split_chapters", 50))
        full_target = target_chapters
    else:
        first_batch = cfg("novel", "pre_split_chapters", 50)
        full_target = first_batch

    # ── 模型能力检查 ──────────────────────────────────
    per_batch, batch_max_tokens, total_batches = _compute_batch_params(first_batch)

    if full_batch and first_batch > per_batch:
        model_max = _get_model_max_output()
        print(f"\n  {'='*55}")
        print(f"  [模型吞吐量不足]")
        print(f"  当前模型最大输出: {model_max} tokens")
        print(f"  全量生成 {first_batch} 章约需: {first_batch * 200} tokens")
        print(f"  最大单批能力: {per_batch} 章/批")
        print(f"  {'='*55}")
        print(f"  1. 切换模型（选择吞吐量更大的模型）")
        print(f"  2. 改为分批生成（每批 {per_batch} 章，共 {total_batches} 批）")
        print(f"  3. 取消")
        choice = input("\n  请选择（默认2）：").strip() or "2"

        if choice == "1":
            from core.api_client import select_all_models_interactive, _select_single_model
            print("\n  [提示] 请选择 max_output_tokens 较大的模型（如 qwen3.6-plus 或 glm-5.1）")
            author_choice = _select_single_model("作者模型", default="1", usage="author")
            from core.api_client import set_author_model
            set_author_model(author_choice["model"], author_choice["provider"])
            # 重新计算
            per_batch, batch_max_tokens, total_batches = _compute_batch_params(first_batch)
            if first_batch > per_batch:
                print(f"\n  切换后仍不够（{first_batch}章 > 每批{per_batch}章），将自动分批生成。")
                full_batch = False
            else:
                print(f"\n  [OK] 切换后可以一次性生成全部 {first_batch} 章")
        elif choice == "3":
            print("  [取消] 跳过任务卡生成")
            return 0
        else:
            print(f"\n  将分批生成，每批 {per_batch} 章，共 {total_batches} 批")
            full_batch = False

    # ── 生成任务卡 ─────────────────────────────────────
    if full_batch:
        print(f"\n  正在一次性生成全部{first_batch}章任务卡（全书目标：{full_target}章）...")
        batch_tokens = max(8000, min(first_batch * 200, batch_max_tokens))
        print(f"  max_tokens={batch_tokens}，模型上限={_get_model_max_output()}")
        saved = _generate_single_batch(
            outline, novel_name, first_batch, full_target,
            start=start, max_tokens=batch_tokens,
            novel_name_for_fs=novel_name,
            review_mode=review_mode,
        )
        if saved < first_batch:
            saved = _supplement_tasks(
                outline, novel_name, first_batch, full_target, saved
            )
    else:
        # 分批生成：把章节分成多批，每批用独立 API 调用
        saved = 0
        for batch_idx in range(total_batches):
            batch_start = start + batch_idx * per_batch
            batch_count = min(per_batch, first_batch - batch_idx * per_batch)
            batch_end = batch_start + batch_count - 1

            print(f"\n  [{batch_idx+1}/{total_batches}] 正在生成第{batch_start}-{batch_end}章任务卡...")
            batch_saved = _generate_single_batch(
                outline, novel_name, batch_count, full_target,
                start=batch_start, max_tokens=batch_max_tokens,
                novel_name_for_fs=novel_name,
                review_mode=review_mode,
            )
            saved += batch_saved
            if batch_saved < batch_count:
                saved = _supplement_tasks(
                    outline, novel_name, batch_count, full_target,
                    saved + (batch_start - start),
                    batch_start=batch_start,
                )

        if total_batches > 1:
            print(f"\n  [OK] 分批生成完成！总计 {saved} 个章节任务卡（{total_batches}批）")
        else:
            print(f"\n  [OK] 已生成 {saved} 个章节任务卡")

    # ── 任务卡序列整体节奏报告 ──────────────────────────────
    if saved > 0 and cfg("novel", "task_card_review_enabled", True):
        from core.task_card_reviewer import generate_rhythm_report
        try:
            report = generate_rhythm_report(novel_name)
            print("\n" + report)
        except Exception as e:
            print(f"  [节奏报告] 生成失败（非致命）：{e}")

    return saved


def _generate_single_batch(outline: str, novel_name: str,
                           batch_count: int, full_target: int,
                           start: int = 1, max_tokens: int = 8000,
                           novel_name_for_fs: str = "",
                           review_mode: bool = False) -> int:
    """单次 API 调用生成一批任务卡，返回保存数量。"""
    from core.utils import with_db_connection

    prev_ending = ""
    urgent_fs = []
    if start > 1:
        mm_tmp = MemoryManager(novel_name)
        prev_ending = mm_tmp.get_last_chapter_ending(start, chars=400)
        urgent_fs = _get_urgent_foreshadowing(mm_tmp, start)
        if urgent_fs:
            print(f"  [伏笔] 检测到 {len(urgent_fs)} 个沉睡伏笔，将在任务卡中强制安排兑现")

    for attempt in range(2):
        raw = call_author_api(
            system_prompt="你是小说策划师，将大纲拆解为章节任务。只输出JSON，不要任何其他内容。",
            user_message=_build_task_split_prompt(
                batch_count, full_target, outline, start=start,
                prev_chapter_ending=prev_ending,
                urgent_foreshadowing=urgent_fs,
                novel_name=novel_name_for_fs,
            ),
            temperature=0.7,
            max_tokens=max_tokens,
        )
        tasks = _extract_tasks_json(raw)
        if tasks is not None:
            break
        if attempt == 0:
            print("  [重试] 首次解析失败，尝试重新生成...")
    else:
        print("  [警告] 解析失败（已重试），跳过本批")
        return 0

    # ── 任务卡质量审核 ──────────────────────────────────────
    if cfg("novel", "task_card_review_enabled", True):
        try:
            from core.task_card_reviewer import review_task_cards, revise_task_cards
            print(f"  [任务卡审核] 第{start}章-第{start + len(tasks) - 1}章 审核中...")
            review_result = review_task_cards(novel_name, tasks, outline, start)

            if review_result.get("pass"):
                pass  # 审核通过，继续入库
            else:
                # 审核不通过，自动修正一次
                print(f"  [修正] 审核未通过({review_result.get('score_total', 0)}/40)，自动修正中...")
                tasks = revise_task_cards(novel_name, tasks, review_result, outline)
                # 重新审核
                review_result2 = review_task_cards(novel_name, tasks, outline, start)
                if not review_result2.get("pass"):
                    if review_mode:
                        # 交互模式：让用户选择
                        print(f"  [警告] 修正后审核仍未通过({review_result2.get('score_total', 0)}/40)")
                        print("  1. 接受当前任务卡（强制入库）")
                        print("  2. 丢弃本批任务卡")
                        choice = input("  请选择（默认1）：").strip() or "1"
                        if choice == "2":
                            return 0
                    else:
                        print(
                            f"  [警告] 审核未通过({review_result2.get('score_total', 0)}/40)，"
                            f"修正后仍未达标，继续入库"
                        )
        except Exception as e:
            print(f"  [警告] 任务卡审核异常（非致命）：{e}，跳过审核直接入库")

    valid_tags = ["铺垫", "冲突", "爽点", "低谷", "反转"]
    with with_db_connection(novel_name) as conn:
        saved = 0
        for task in tasks:
            chapter_num = task.get("chapter_num")
            plot_goal = task.get("plot_goal", "").strip()
            emotion_tag = task.get("emotion_tag", "铺垫").strip()
            if emotion_tag not in valid_tags:
                emotion_tag = "铺垫"
            if chapter_num and plot_goal:
                conn.execute("""
                    INSERT OR REPLACE INTO chapter_tasks
                    (chapter_num, plot_goal, emotion_tag, status)
                    VALUES (?, ?, ?, '待处理')
                """, (chapter_num, plot_goal, emotion_tag))
                saved += 1
        conn.commit()
    return saved


def _supplement_tasks(outline: str, novel_name: str,
                      batch_count: int, full_target: int,
                      already_saved: int, batch_start: int = 1) -> int:
    """补充缺失的任务卡（AI 返回数量不足时）。返回新的 total saved。"""
    from core.utils import with_db_connection

    shortage = batch_count - already_saved
    if shortage <= 0:
        return already_saved

    start_chapter = batch_start + already_saved
    print(f"\n  [提示] 任务卡数量不足（{already_saved}/{batch_count}），正在补充{shortage}章...")

    _sup_max_tokens = max(8000, min(shortage * 200, 12000))
    supplement_raw = call_author_api(
        system_prompt="你是小说策划师，将大纲拆解为章节任务。只输出JSON，不要任何其他内容。",
        user_message=_build_task_split_prompt(
            shortage, full_target, outline, start=start_chapter,
            novel_name=novel_name,
        ),
        temperature=0.7,
        max_tokens=_sup_max_tokens,
    )

    sup_tasks = _extract_tasks_json(supplement_raw)
    if sup_tasks is None or not isinstance(sup_tasks, list):
        print(f"  [警告] 补充任务卡解析失败")
        return already_saved

    if not sup_tasks:
        print(f"  [警告] AI 返回了空的补充任务卡列表，跳过")
        return already_saved

    # ── 任务卡质量审核（补充路径，非交互式）──────────────
    if cfg("novel", "task_card_review_enabled", True):
        try:
            from core.task_card_reviewer import review_task_cards, revise_task_cards
            print(f"  [任务卡审核] 第{start_chapter}章-第{start_chapter + len(sup_tasks) - 1}章 审核中...")
            review_result = review_task_cards(novel_name, sup_tasks, outline, start_chapter)
            if not review_result.get("pass"):
                print(f"  [修正] 审核未通过({review_result.get('score_total', 0)}/40)，自动修正中...")
                sup_tasks = revise_task_cards(novel_name, sup_tasks, review_result, outline)
                review_result2 = review_task_cards(novel_name, sup_tasks, outline, start_chapter)
                if not review_result2.get("pass"):
                    print(
                        f"  [警告] 修正后仍不达标({review_result2.get('score_total', 0)}/40)，"
                        f"继续入库"
                    )
        except Exception as e:
            print(f"  [警告] 补充任务卡审核异常（非致命）：{e}，跳过审核直接入库")

    valid_tags = ["铺垫", "冲突", "爽点", "低谷", "反转"]
    with with_db_connection(novel_name) as conn:
        for task in sup_tasks:
            chapter_num = task.get("chapter_num")
            plot_goal = task.get("plot_goal", "").strip()
            emotion_tag = task.get("emotion_tag", "铺垫").strip()
            if emotion_tag not in valid_tags:
                emotion_tag = "铺垫"
            if chapter_num and plot_goal:
                conn.execute("""
                    INSERT OR REPLACE INTO chapter_tasks
                    (chapter_num, plot_goal, emotion_tag, status)
                    VALUES (?, ?, ?, '待处理')
                """, (chapter_num, plot_goal, emotion_tag))
                already_saved += 1
        conn.commit()
    print(f"  [OK] 补充完成！总计 {already_saved} 个章节任务卡")
    return already_saved


def extend_tasks(novel_name: str, from_chapter: int):
    """运行时扩展任务卡。根据模型能力动态决定扩展多少章。
    每次写作前自动触发：当 get_next_chapter_goal() 发现任务卡用完时调用。
    """
    from core.utils import with_db_connection
    from core.config_loader import get as cfg, get_data_dir

    data_dir = get_data_dir(novel_name)
    outline_path = data_dir / "master_outline.md"
    if not outline_path.exists():
        return

    outline = outline_path.read_text(encoding="utf-8")
    target_path = data_dir / "target_chapters.txt"
    full_target = 0
    if target_path.exists():
        try:
            full_target = int(target_path.read_text(encoding="utf-8").strip())
        except Exception:
            pass

    # 计算剩余章节数
    remaining = (full_target - from_chapter + 1) if full_target > 0 else 50
    # 根据模型能力决定这批扩展多少章
    per_batch, batch_max_tokens, _ = _compute_batch_params(remaining)
    batch_size = min(per_batch, remaining)
    end_chapter = from_chapter + batch_size - 1

    model_max = _get_model_max_output()
    print(f"  [扩展] 任务卡不足，模型上限={model_max} tokens，本批扩展 {batch_size} 章"
          f"（第{from_chapter}-{end_chapter}章）")

    if full_target > 0:
        print(f"  （全书目标 {full_target} 章，剩余 {remaining} 章未规划）")

    mm_tmp = MemoryManager(novel_name)
    prev_ending = mm_tmp.get_last_chapter_ending(from_chapter, chars=400)
    urgent_fs = _get_urgent_foreshadowing(mm_tmp, from_chapter)
    if urgent_fs:
        print(f"  [伏笔] 检测到 {len(urgent_fs)} 个沉睡伏笔，将在任务卡中强制安排兑现")

    # 最多重试一次
    for attempt in range(2):
        raw = call_author_api(
            system_prompt="你是小说策划师，将大纲拆解为章节任务。只输出JSON，不要任何其他内容。",
            user_message=_build_extend_task_prompt(
                from_chapter, end_chapter, full_target or end_chapter, outline,
                prev_chapter_ending=prev_ending,
                urgent_foreshadowing=urgent_fs,
                novel_name=novel_name,
            ),
            temperature=0.7,
            max_tokens=batch_max_tokens,
        )

        tasks = _extract_tasks_json(raw)
        if tasks is not None:
            break
        if attempt == 0:
            print(f"  [重试] 扩展任务卡首次解析失败，重试...")
    else:
        print(f"  [警告] 扩展任务卡解析失败（已重试），跳过扩展")
        return

    # ── 任务卡质量审核（运行时自动模式，不中断写作）──────
    if cfg("novel", "task_card_review_enabled", True):
        try:
            from core.task_card_reviewer import review_task_cards, revise_task_cards
            batch_end = from_chapter + len(tasks) - 1 if tasks else end_chapter
            print(f"  [任务卡审核] 第{from_chapter}章-第{batch_end}章 审核中...")
            review_result = review_task_cards(novel_name, tasks, outline, from_chapter)

            if not review_result.get("pass"):
                print(f"  [修正] 审核未通过({review_result.get('score_total', 0)}/40)，自动修正中...")
                tasks = revise_task_cards(novel_name, tasks, review_result, outline)
                # 重新审核
                review_result2 = review_task_cards(novel_name, tasks, outline, from_chapter)
                if not review_result2.get("pass"):
                    print(
                        f"  [警告] 修正后仍不达标({review_result2.get('score_total', 0)}/40)，"
                        f"继续入库（不中断写作流程）"
                    )
        except Exception as e:
            print(f"  [警告] 任务卡审核异常（非致命）：{e}，跳过审核直接入库")

    valid_tags = ["铺垫", "冲突", "爽点", "低谷", "反转"]
    with with_db_connection(novel_name) as conn:
        for task in tasks:
            chapter_num = task.get("chapter_num")
            plot_goal = task.get("plot_goal", "").strip()
            emotion_tag = task.get("emotion_tag", "铺垫").strip()
            if emotion_tag not in valid_tags:
                emotion_tag = "铺垫"
            if chapter_num and plot_goal:
                conn.execute("""
                    INSERT OR IGNORE INTO chapter_tasks
                    (chapter_num, plot_goal, emotion_tag, status)
                    VALUES (?, ?, ?, '待处理')
                """, (chapter_num, plot_goal, emotion_tag))
        conn.commit()
    print(f"  [OK] 任务卡已扩展至第{end_chapter}章")


# ==================== 任务卡重写（冲突修复） ====================

def rewrite_task_for_chapter(novel_name: str, chapter_num: int,
                              veto_reasons: list,
                              current_goal: str,
                              current_emotion_tag: str = "铺垫") -> dict:
    """
    基于大纲、人物设定、前情，为指定章节重新生成任务卡。

    调用时机：连续 ≥2 次因相同 veto_code 失败，且失败层均为 L1/L2。
    veto_reasons : 审稿器连续命中的否决原因描述列表（用于约束新目标）
    返回 {"plot_goal": ..., "emotion_tag": ...}，并同步更新数据库。
    """
    from core.utils import extract_json_obj, with_db_connection

    mm = MemoryManager(novel_name)

    # 读取大纲
    outline = ""
    outline_path = mm.data_dir / "master_outline.md"
    if outline_path.exists():
        try:
            outline = outline_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    # 人物设定摘要（最多 6 人，每人截取 80 字）
    char_lines = []
    try:
        characters = mm.load_characters()
        for c in characters[:6]:
            name = c.get("name", "")
            personality = (c.get("personality") or "")[:80]
            if name:
                char_lines.append(f"- {name}：{personality}")
    except Exception:
        pass
    char_summary = "\n".join(char_lines) if char_lines else "（无人物档案）"

    # 近期摘要（最近 3 章）
    recent_text = "（无摘要）"
    try:
        recent = mm.load_recent_summaries(3)
        if recent:
            recent_text = "\n".join(
                f"第{s['chapter_num']}章：{(s['summary'] or '')[:120]}"
                for s in recent
            )
    except Exception:
        pass

    veto_text = "\n".join(f"- {r}" for r in veto_reasons) if veto_reasons else "（无）"

    # 提取原始任务卡的情绪标签，作为结局基调约束
    _end_state_hint = f"情绪基调仍须为「{current_emotion_tag}」" if current_emotion_tag else ""

    prompt = f"""当前正在创作第{chapter_num}章，原任务卡连续审稿失败，根本原因如下：

【连续命中的否决原因】
{veto_text}

【原任务卡目标（已失败，请勿照搬）】
{current_goal}

【故事总大纲（节选，请据此控制推进幅度）】
{outline[:900] if outline else "（无大纲）"}

【主要人物设定】
{char_summary}

【近期剧情摘要】
{recent_text}

请为第{chapter_num}章重新设计一张任务卡，要求：
1. 严格遵守大纲整体走向，不超前推进
2. 人物行为必须符合其性格设定，杜绝 OOC
3. 不得再触发上述否决原因
4. 只安排一个核心情节节点，具体到场景和行动
5. plot_goal 40-80 字
6. 【关键约束】只修改实现路径和过程细节，章节结尾的核心状态／情节终态必须与原任务卡保持一致。
   例如：原目标是"众人陷入绝望"，新目标的结尾仍须是"众人陷入绝望"，只改"如何走到这一步"。
   {_end_state_hint}
   这是为了确保后续章节的任务卡不需要联动修改。

严格按以下 JSON 格式输出，不要任何其他内容：
{{"plot_goal": "新的情节目标", "emotion_tag": "铺垫"}}

emotion_tag 只能从以下 5 个中选 1 个：铺垫 / 冲突 / 爽点 / 低谷 / 反转"""

    # 大纲伏笔强制任务注入
    try:
        from core.outline_manager import get_chapter_outline_tasks
        outline_tasks = get_chapter_outline_tasks(novel_name, chapter_num)
        to_plant = outline_tasks.get("to_plant", [])
        to_resolve = outline_tasks.get("to_resolve", [])
        if to_plant or to_resolve:
            fs_lines = []
            if to_plant:
                fs_lines.append("【本章必须埋入】")
                for t in to_plant:
                    stars = "★" * (t.get("importance") or 3)
                    fs_lines.append(
                        f"- {t['fid']}: {t['description']}（重要度{stars}）"
                    )
            if to_resolve:
                fs_lines.append("【本章必须兑现】")
                for t in to_resolve:
                    stars = "★" * (t.get("importance") or 3)
                    fs_lines.append(
                        f"- {t['fid']}: {t['description']}（重要度{stars}）"
                    )
            prompt += "\n\n=== 大纲伏笔强制任务 ===\n" + "\n".join(fs_lines)
    except Exception:
        pass

    raw = call_author_api(
        system_prompt=(
            "你是专业的中文网络小说策划师，"
            "擅长在保持故事连贯性的同时化解情节矛盾，"
            "为卡壳的章节找到符合人物逻辑的新出路。"
        ),
        user_message=prompt,
        temperature=0.85,
        max_tokens=300,
    )

    from core.utils import extract_json_obj
    parsed = extract_json_obj(raw)

    valid_tags = {"铺垫", "冲突", "爽点", "低谷", "反转"}
    new_goal = (parsed.get("plot_goal") or "").strip()
    new_tag  = (parsed.get("emotion_tag") or current_emotion_tag).strip()

    if not new_goal or len(new_goal) < 10:
        print("  [警告] AI 重写任务卡结果异常，保留原目标")
        new_goal = current_goal
        new_tag  = current_emotion_tag
    if new_tag not in valid_tags:
        new_tag = current_emotion_tag

    # 写回数据库
    try:
        with with_db_connection(novel_name) as conn:
            row = conn.execute(
                "SELECT original_plot_goal, rewrite_count "
                "FROM chapter_tasks WHERE chapter_num=?",
                (chapter_num,)
            ).fetchone()
            original = (
                (row["original_plot_goal"] or current_goal)
                if row else current_goal
            )
            rewrite_count = ((row["rewrite_count"] or 0) + 1) if row else 1
            conn.execute("""
                UPDATE chapter_tasks
                SET plot_goal=?, emotion_tag=?,
                    original_plot_goal=?, rewrite_count=?,
                    updated_at=datetime('now','localtime')
                WHERE chapter_num=?
            """, (new_goal, new_tag, original, rewrite_count, chapter_num))
            conn.commit()
    except Exception as e:
        print(f"  [警告] 任务卡数据库更新失败（非致命）：{e}")

    return {"plot_goal": new_goal, "emotion_tag": new_tag}


# ==================== 主入口 ====================

def run_planner(novel_name: str, genre: str, keywords: str) -> tuple:
    """
    策划顺序：
    篇幅选择 → 大纲 → 角色名单 → 世界观（含角色信息）→ 人物档案 → 风格 → 任务卡
    """
    print(f"\n开始策划《{novel_name}》...")
    print("=" * 50)

    mm = MemoryManager(novel_name)

    # Step 0：篇幅选择
    target_chapters = _choose_novel_length()
    (mm.data_dir / "target_chapters.txt").write_text(
        str(target_chapters), encoding="utf-8"
    )

    # Step 1：大纲
    outline = get_outline_choice(genre, keywords, novel_name, target_chapters)
    (mm.data_dir / "master_outline.md").write_text(
        f"# 总大纲\n\n{outline}", encoding="utf-8"
    )
    print("  [OK] 大纲已保存")

    # Step 1.5：确定草稿审阅模式（影响伏笔生成和后续步骤的交互方式）
    review_mode = _choose_draft_review_mode()

    # Step 1.6：大纲伏笔生成（基于大纲自动设计伏笔并录入）
    from core.outline_manager import generate_outline_foreshadow
    generate_outline_foreshadow(novel_name, target_chapters, review_mode=review_mode)

    # Step 2：角色名单
    character_names = get_characters_choice(outline)

    # Step 3：世界观
    world = generate_world(
        novel_name, genre, outline, character_names, mm,
        review_mode=review_mode
    )

    # Step 4：人物档案
    generate_characters(
        character_names, outline, world, mm,
        review_mode=review_mode
    )

    # Step 5：写作风格
    style_key = get_style_choice()

    # Step 6：任务卡（默认全量生成，模型不够时自动提示换模型或分批）
    split_outline_to_tasks(
        outline, novel_name,
        review_mode=review_mode,
        target_chapters=target_chapters,
        full_batch=True,
    )

    print("\n" + "=" * 50)
    print(f"策划完成！文件已保存到 data/{novel_name}/")
    print(f"目标篇幅：{target_chapters}章")
    print(f"\n【世界观预览】\n{world[:150]}...")
    print(f"\n【大纲预览】\n{outline[:150]}...")

    return character_names, style_key
