import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import os
from dotenv import load_dotenv
from typing import List, Dict, Optional

load_dotenv()

# 模型分类和优先级
MODEL_CATEGORIES = {
    "qwen-turbo": {"category": "cost_effective", "priority": 1, "display_name": "qwen-turbo（高性价比）"},
    "qwen-plus": {"category": "balanced", "priority": 2, "display_name": "qwen-plus（平衡推荐）"},
    "qwen-max": {"category": "premium", "priority": 3, "display_name": "qwen-max（高级）"},
    "qwen-long": {"category": "long_context", "priority": 4, "display_name": "qwen-long（长上下文）"},
}

# 默认定价（兜底用）
DEFAULT_PRICING = {
    "input": 0.0008,
    "output": 0.002,
}


def fetch_qwen_models() -> List[Dict]:
    """
    获取通义千问可用模型列表（混合方案）
    1. 优先尝试从配置文件加载
    2. 然后尝试自动发现
    3. 最后使用默认列表
    """
    # 1. 尝试从配置文件加载
    custom_models = _load_custom_models()
    if custom_models:
        print(f"[模型发现] 从配置文件加载了 {len(custom_models)} 个自定义模型")
        return custom_models

    # 2. 尝试自动发现
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key or len(api_key) < 10:
        print("[模型发现] DASHSCOPE_API_KEY 未配置，使用默认模型列表")
        print("[提示] 可以创建 data/custom_models.json 来添加你的试用模型")
        return _get_default_qwen_models()

    try:
        import requests

        print("[模型发现] 正在获取通义千问模型列表...")
        
        # 尝试使用 HTTP API 获取模型列表
        url = "https://dashscope.aliyuncs.com/api/v1/deployments/models"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        params = {
            "page_no": 1,
            "page_size": 100,
        }

        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code != 200:
            print(f"[模型发现] API 请求失败：{response.status_code}")
            print("[提示] 可以创建 data/custom_models.json 来添加你的试用模型")
            return _get_default_qwen_models()

        data = response.json()
        models_data = data.get('output', {}).get('models', [])
        
        if not models_data:
            print("[模型发现] 模型列表为空")
            print("[提示] 可以创建 data/custom_models.json 来添加你的试用模型")
            return _get_default_qwen_models()

        qwen_models = []
        for model in models_data:
            model_id = model.get('model_name', '')
            if 'qwen' in model_id.lower():
                model_info = _parse_model_info_from_api(model)
                if model_info:
                    qwen_models.append(model_info)

        if not qwen_models:
            print("[模型发现] 未找到通义千问模型，使用默认列表")
            print("[提示] 可以创建 data/custom_models.json 来添加你的试用模型")
            return _get_default_qwen_models()

        # 按优先级排序
        qwen_models.sort(key=lambda x: MODEL_CATEGORIES.get(x["model"], {}).get("priority", 999))

        print(f"[模型发现] 成功获取 {len(qwen_models)} 个通义千问模型")
        return qwen_models

    except ImportError:
        print("[模型发现] requests 库未安装，使用默认模型列表")
        print("[提示] 可以创建 data/custom_models.json 来添加你的试用模型")
        return _get_default_qwen_models()
    except Exception as e:
        print(f"[模型发现] 获取模型列表失败：{e}")
        print("[提示] 可以创建 data/custom_models.json 来添加你的试用模型")
        return _get_default_qwen_models()


def _load_custom_models() -> Optional[List[Dict]]:
    """
    从 data/custom_models.json 加载自定义模型列表
    """
    custom_file = os.path.join("data", "custom_models.json")
    if not os.path.exists(custom_file):
        return None
    
    try:
        with open(custom_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if not isinstance(data, list):
            print("[模型发现] custom_models.json 格式错误，应为数组")
            return None
        
        models = []
        for item in data:
            model_id = item.get("model")
            if not model_id:
                continue
            
            # 从已知分类获取信息，或使用自定义配置
            category_info = MODEL_CATEGORIES.get(model_id, {})
            
            models.append({
                "model": model_id,
                "name": item.get("name", category_info.get("display_name", model_id)),
                "category": item.get("category", category_info.get("category", "general")),
                "description": item.get("description", ""),
                "context_length": item.get("context_length", 32768),
                "input_price": item.get("input_price", category_info.get("input_price", DEFAULT_PRICING["input"])),
                "output_price": item.get("output_price", category_info.get("output_price", DEFAULT_PRICING["output"])),
                "has_free_quota": item.get("has_free_quota", True),
                "provider": "dashscope",
            })
        
        if models:
            return models
        return None
    except Exception as e:
        print(f"[模型发现] 加载 custom_models.json 失败：{e}")
        return None


def _parse_model_info_from_api(model_data: Dict) -> Optional[Dict]:
    """从 HTTP API 返回的数据解析单个模型信息"""
    model_id = model_data.get('model_name', '')
    if not model_id:
        return None

    # 从已知分类获取信息
    category_info = MODEL_CATEGORIES.get(model_id, {})

    # 如果是试用模型，添加友好的显示名称
    display_name = category_info.get("display_name", model_id)
    if not category_info and "qwen" in model_id.lower():
        # 未在预设列表中的 qwen 模型（如试用模型）
        display_name = model_id + "（试用）"

    # 获取定价信息（使用默认定价或从已知分类）
    if category_info:
        # 已知模型使用已知分类的定价
        input_price = DEFAULT_PRICING["input"]
        output_price = DEFAULT_PRICING["output"]
    else:
        # 未知模型使用通用定价
        input_price = DEFAULT_PRICING["input"]
        output_price = DEFAULT_PRICING["output"]

    # 假设所有从 API 获取的模型都可能有免费额度
    has_free_quota = True

    return {
        "model": model_id,
        "name": display_name,
        "category": category_info.get("category", "general") if category_info else "general",
        "description": "",
        "context_length": 32768,
        "input_price": input_price,
        "output_price": output_price,
        "has_free_quota": has_free_quota,
        "provider": "dashscope",
    }


def _parse_model_info(model_data: Dict) -> Optional[Dict]:
    """解析单个模型数据（备用函数）"""
    model_id = model_data.get('model_id', '')
    if not model_id:
        return None

    # 从已知分类获取信息
    category_info = MODEL_CATEGORIES.get(model_id, {})

    # 尝试获取模型描述
    description = model_data.get('description', '')

    # 尝试获取上下文长度
    context_length = model_data.get('max_context_length', 0)

    # 获取定价信息（如果有的话）
    pricing = model_data.get('pricing', {})
    input_price = pricing.get('input', {}).get('price_per_token', DEFAULT_PRICING['input'])
    output_price = pricing.get('output', {}).get('price_per_token', DEFAULT_PRICING['output'])

    # 检查是否有免费额度
    has_free_quota = False
    quota_info = model_data.get('quota', {})
    if quota_info:
        free_remaining = quota_info.get('free_remaining', 0)
        has_free_quota = free_remaining > 0

    return {
        "model": model_id,
        "name": category_info.get("display_name", model_id),
        "category": category_info.get("category", "general"),
        "description": description,
        "context_length": context_length,
        "input_price": input_price,
        "output_price": output_price,
        "has_free_quota": has_free_quota,
        "provider": "dashscope",
    }


def _get_default_qwen_models() -> List[Dict]:
    """返回默认模型列表（兜底方案）"""
    return [
        {
            "model": "qwen-plus",
            "name": "qwen-plus（平衡推荐）",
            "category": "balanced",
            "description": "通用推荐，推理能力与性价比平衡",
            "context_length": 32768,
            "input_price": 0.0008,
            "output_price": 0.002,
            "has_free_quota": True,
            "provider": "dashscope",
        },
        {
            "model": "qwen-turbo",
            "name": "qwen-turbo（高性价比）",
            "category": "cost_effective",
            "description": "速度快，成本低，适合审稿等任务",
            "context_length": 32768,
            "input_price": 0.0003,
            "output_price": 0.0006,
            "has_free_quota": True,
            "provider": "dashscope",
        },
        {
            "model": "qwen-max",
            "name": "qwen-max（高级）",
            "category": "premium",
            "description": "最强推理能力，适合复杂创作",
            "context_length": 32768,
            "input_price": 0.004,
            "output_price": 0.012,
            "has_free_quota": False,
            "provider": "dashscope",
        },
        {
            "model": "qwen-long",
            "name": "qwen-long（长上下文）",
            "category": "long_context",
            "description": "超长上下文，适合记忆管理",
            "context_length": 1000000,
            "input_price": 0.0005,
            "output_price": 0.002,
            "has_free_quota": False,
            "provider": "dashscope",
        },
    ]


def get_gemini_models() -> List[Dict]:
    """返回 Gemini 模型列表（静态）"""
    return [
        {
            "model": "gemini-1.5-flash",
            "name": "gemini-1.5-flash（免费）",
            "category": "free",
            "description": "Google Gemini，免费层，推荐用于审核",
            "context_length": 1000000,
            "input_price": 0.0,
            "output_price": 0.0,
            "has_free_quota": True,
            "provider": "gemini",
            "free_tier": True,
        },
        {
            "model": "gemini-1.5-flash-8b",
            "name": "gemini-1.5-flash-8b（免费）",
            "category": "free",
            "description": "Google Gemini 8B，更轻量的免费选择",
            "context_length": 1000000,
            "input_price": 0.0,
            "output_price": 0.0,
            "has_free_quota": True,
            "provider": "gemini",
            "free_tier": True,
        },
        {
            "model": "gemini-2.0-flash",
            "name": "gemini-2.0-flash（免费）",
            "category": "free",
            "description": "Google Gemini 2.0，最新免费模型",
            "context_length": 1000000,
            "input_price": 0.0,
            "output_price": 0.0,
            "has_free_quota": True,
            "provider": "gemini",
            "free_tier": True,
        },
    ]


def discover_all_models(refresh: bool = False) -> List[Dict]:
    """
    发现所有可用模型（通义千问 + Gemini）
    """
    # 先获取通义千问模型（可能动态）
    qwen_models = fetch_qwen_models()

    # 再获取 Gemini 模型（静态）
    gemini_models = get_gemini_models()

    return qwen_models + gemini_models


def filter_models_for_usage(models: List[Dict], usage: str, top_k: int = 5) -> List[Dict]:
    """
    根据用途筛选模型，返回最适合的top_k个
    
    usage: "author" | "reviewer" | "reader_reviewer"
    """
    usage_rules = {
        "author": {
            # 作者模型：优先高级、平衡模型，有免费额度的排在前面
            "categories": ["premium", "balanced"],
            "prefer_free": True,
            "exclude_vl": True,  # 排除多模态模型
            "exclude_math": True,  # 排除数学模型
            "exclude_coder": True,  # 排除编程模型
        },
        "reviewer": {
            # 审核模型：优先轻量、性价比高，有免费额度的排在前面
            "categories": ["cost_effective", "balanced"],
            "prefer_free": True,
            "exclude_vl": True,
            "exclude_math": False,  # 数学模型可能更严谨
            "exclude_coder": False,
        },
        "reader_reviewer": {
            # 读者视角模型：优先平衡、高级模型
            "categories": ["balanced", "premium"],
            "prefer_free": True,
            "exclude_vl": True,
            "exclude_math": True,
            "exclude_coder": True,
        },
    }

    rules = usage_rules.get(usage, usage_rules["author"])

    # 筛选模型
    filtered = []
    for m in models:
        # 检查分类
        if m["category"] not in rules["categories"]:
            continue
        
        # 排除特定类型模型
        model_name = m["model"].lower()
        if rules["exclude_vl"] and ("vl" in model_name or "vision" in model_name):
            continue
        if rules["exclude_math"] and "math" in model_name:
            continue
        if rules["exclude_coder"] and "coder" in model_name:
            continue
        
        filtered.append(m)

    # 排序：有免费额度的优先，然后按分类优先级
    def sort_key(m):
        free_priority = 0 if m.get("has_free_quota", False) else 1
        category_priority = rules["categories"].index(m["category"])
        return (free_priority, category_priority)

    filtered.sort(key=sort_key)

    # 返回前top_k个
    return filtered[:top_k]


def get_models_for_usage(usage: str, top_k: int = 5) -> List[Dict]:
    """
    直接获取指定用途的模型列表
    """
    all_models = discover_all_models()
    return filter_models_for_usage(all_models, usage, top_k)


def get_all_models_grouped() -> Dict[str, List[Dict]]:
    """
    获取按用途分组的模型列表
    """
    all_models = discover_all_models()
    return {
        "author": filter_models_for_usage(all_models, "author"),
        "reviewer": filter_models_for_usage(all_models, "reviewer"),
        "reader_reviewer": filter_models_for_usage(all_models, "reader_reviewer"),
    }


def model_list_to_menu_format(models: List[Dict]) -> Dict[str, Dict]:
    """
    将模型列表转换为菜单选择格式（与 api_client.py 兼容）
    """
    menu = {}
    for idx, model in enumerate(models, 1):
        key = str(idx)
        menu[key] = {
            "name": model["name"],
            "model": model["model"],
            "provider": model["provider"],
            "input_price": model.get("input_price", DEFAULT_PRICING["input"]),
            "output_price": model.get("output_price", DEFAULT_PRICING["output"]),
            "free_tier": model.get("free_tier", False),
            "has_free_quota": model.get("has_free_quota", False),
            "env_key": "DASHSCOPE_API_KEY" if model["provider"] == "dashscope" else "GEMINI_API_KEY",
        }
    return menu


def save_models_to_cache(models: List[Dict], cache_file: str = "data/models_cache.json"):
    """缓存模型列表到文件"""
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(models, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[模型缓存] 保存失败：{e}")


def load_models_from_cache(cache_file: str = "data/models_cache.json") -> Optional[List[Dict]]:
    """从文件加载模型列表缓存"""
    if not os.path.exists(cache_file):
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[模型缓存] 加载失败：{e}")
        return None


if __name__ == "__main__":
    # 测试模型发现
    print("=" * 60)
    print("  模型发现测试")
    print("=" * 60)
    models = discover_all_models()
    print(f"\n共发现 {len(models)} 个模型：\n")
    for m in models:
        price_info = ""
        if m["input_price"] == 0 and m["output_price"] == 0:
            price_info = "★ 免费"
        else:
            price_info = f"输入¥{m['input_price'] * 1000:.2f}/百万 输出¥{m['output_price'] * 1000:.2f}/百万"
        quota_tag = " [有免费额度]" if m.get("has_free_quota") else ""
        print(f"  - {m['name']}{quota_tag}")
        print(f"    {price_info}")
        if m.get("description"):
            print(f"    {m['description']}")
        print()
