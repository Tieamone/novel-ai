import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.api_client import call_api
from core.memory_manager import MemoryManager

WORLD_PROMPT = """你是一位专业的中文网络小说策划师，擅长玄幻、仙侠、都市等类型。
请根据用户提供的基本信息，生成一份详细的世界观设定。

要求：
1. 世界背景要有独特性，避免套路化
2. 必须包含：地理环境、力量体系、社会结构、核心规则
3. 语言简洁，总字数500字以内
4. 直接输出设定内容，不要加任何前缀说明"""

CHARACTER_PROMPT = """你是一位专业的中文网络小说策划师。
请根据用户提供的信息，为每个角色生成详细档案。

要求：
1. 每个角色必须包含：外貌、性格核心、隐藏秘密、致命弱点、初始位置、初始状态
2. 隐藏秘密和致命弱点要能为后续伏笔服务
3. 角色之间要有关联和冲突点
4. 严格按照以下JSON格式输出，不要加任何其他内容：

[
  {
    "name": "角色名",
    "role": "主角/配角/反派",
    "appearance": "外貌描述",
    "personality": "性格描述",
    "secret": "隐藏秘密",
    "weakness": "致命弱点",
    "current_location": "初始位置",
    "current_status": "初始状态",
    "relationships": {"其他角色名": "关系描述"}
  }
]"""

OUTLINE_PROMPT = """你是一位专业的中文网络小说策划师。
请根据已有的世界观和人物设定，生成一份总大纲。

要求：
1. 包含三幕结构：开局（1-30章）、发展（31-100章）、高潮结局（101章以后）
2. 每幕列出3-5个关键转折点
3. 指出主要伏笔的埋设和兑现位置
4. 结局要有反转，不能太俗套
5. 总字数600字以内，直接输出大纲内容"""


def generate_world(novel_name: str, genre: str, keywords: str,
                   mm: MemoryManager) -> str:
    print("  正在生成世界观...")
    user_msg = f"小说类型：{genre}\n关键词：{keywords}\n小说名：{novel_name}"
    world = call_api(WORLD_PROMPT, user_msg, temperature=0.9)
    mm.save_world_settings(world)
    print("  [OK] 世界观已生成并保存")
    return world


def generate_characters(characters_input: list, world: str,
                        mm: MemoryManager) -> list:
    print("  正在生成人物档案...")
    import re, json
    names_str = "、".join(characters_input)
    user_msg = f"角色名单：{names_str}\n\n世界观背景：\n{world}"
    raw = call_api(CHARACTER_PROMPT, user_msg, temperature=0.8)

    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        raise ValueError(f"AI返回格式错误，无法解析人物JSON:\n{raw}")

    characters = json.loads(match.group())
    for char in characters:
        mm.save_character(char["name"], char)

    print(f"  [OK] {len(characters)} 个人物档案已生成并保存")
    return characters


def generate_outline(world: str, characters: list,
                     mm: MemoryManager) -> str:
    print("  正在生成总大纲...")
    char_summary = "\n".join(
        [f"- {c['name']}（{c['role']}）：{c['personality']}"
         for c in characters]
    )
    user_msg = f"世界观：\n{world}\n\n主要角色：\n{char_summary}"
    outline = call_api(OUTLINE_PROMPT, user_msg, temperature=0.9)

    data_dir = mm.data_dir
    (data_dir / "master_outline.md").write_text(
        f"# 总大纲\n\n{outline}", encoding="utf-8"
    )
    print("  [OK] 总大纲已生成并保存")
    return outline


def run_planner(novel_name: str, genre: str,
                keywords: str, character_names: list):
    print(f"\n开始策划《{novel_name}》...")
    print("=" * 50)

    mm = MemoryManager(novel_name)
    world = generate_world(novel_name, genre, keywords, mm)
    characters = generate_characters(character_names, world, mm)
    outline = generate_outline(world, characters, mm)

    print("=" * 50)
    print(f"\n策划完成！文件已保存到 data/{novel_name}/ 目录")
    print(f"\n【世界观预览】\n{world[:200]}...")
    print(f"\n【大纲预览】\n{outline[:200]}...")
    return mm