import os
import time
from dotenv import load_dotenv

load_dotenv()

# 三种独立模型：作者模型、审核模型、读者视角模型
_author_model = "qwen-plus"
_author_provider = "dashscope"

_reviewer_model = "qwen-turbo"
_reviewer_provider = "dashscope"

_reader_reviewer_model = "qwen-plus"
_reader_reviewer_provider = "dashscope"

_session_stats = {
    "total_calls": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_cost_yuan": 0.0,
    "model_used": "",
}

# 失败计数
_failure_stats = {
    "author_failures": 0,
    "reviewer_failures": 0,
    "reader_reviewer_failures": 0,
}

# 模型切换历史
_switch_history = []

# 动态加载的可用模型（启动时从 model_manager 获取）
_available_models = None


def get_available_models(refresh: bool = False, usage: str = None) -> dict:
    """
    获取可用模型列表（自动发现 + 缓存）
    
    usage: 可选，"author" | "reviewer" | "reader_reviewer"，按用途筛选
    """
    global _available_models
    if _available_models is None or refresh:
        try:
            from core.model_manager import discover_all_models, model_list_to_menu_format
            models = discover_all_models(refresh=refresh)
            _available_models = model_list_to_menu_format(models)
        except Exception as e:
            print(f"[警告] 动态模型发现失败，使用默认模型：{e}")
            _available_models = AVAILABLE_MODELS
    
    # 如果指定了用途，返回筛选后的模型
    if usage:
        try:
            from core.model_manager import get_models_for_usage, model_list_to_menu_format
            filtered_models = get_models_for_usage(usage, top_k=5)
            return model_list_to_menu_format(filtered_models)
        except Exception as e:
            print(f"[警告] 模型筛选失败，返回所有模型：{e}")
            return _available_models
    
    return _available_models


def get_current_author_model() -> dict:
    """
    获取当前作者模型的详细信息（包含context_length等）。
    
    返回:
        dict: {
            "model": "qwen3.6-plus",
            "provider": "dashscope",
            "context_length": 131072,
            ...
        }
        或 None（如果未设置）
    """
    models = get_available_models()
    
    # 方法1: 通过模型ID精确匹配
    for key, info in models.items():
        if info.get("model") == _author_model:
            return info
    
    # 方法2: 模糊匹配
    for key, info in models.items():
        if _author_model in info.get("model", ""):
            return info
    
    # 兜底：返回基本信息
    return {
        "model": _author_model,
        "provider": _author_provider,
        "context_length": 0,
    }


def get_model_pricing(model_name: str) -> dict:
    """
    获取模型定价（从动态发现的模型信息或默认定价表）
    """
    models = get_available_models()
    for key, info in models.items():
        if info.get("model") == model_name:
            return {
                "input": info.get("input_price", 0.0008),
                "output": info.get("output_price", 0.002),
            }
    # 兜底：从默认定价表
    if model_name in MODEL_PRICING:
        return MODEL_PRICING[model_name]
    # 最终兜底
    return {"input": 0.0008, "output": 0.002}


MODEL_PRICING = {
    "qwen-turbo":           {"input": 0.0003,  "output": 0.0006},
    "qwen-plus":            {"input": 0.0008,  "output": 0.002},
    "qwen-max":             {"input": 0.04,    "output": 0.12},
    "qwen-long":            {"input": 0.0005,  "output": 0.002},
    "gemini-1.5-flash":     {"input": 0.0,     "output": 0.0},   # 免费层 0 成本
    "gemini-1.5-flash-8b":  {"input": 0.0,     "output": 0.0},   # 免费层 0 成本
    "gemini-2.0-flash":     {"input": 0.0,     "output": 0.0},
}

# 免费层模型：RPM较低，遇到429时需大幅延长等待
FREE_TIER_MODELS = {"gemini-1.5-flash", "gemini-1.5-flash-8b", "gemini-2.0-flash"}

AVAILABLE_MODELS = {
    "1": {
        "name": "通义千问 Plus（推荐，质量好）",
        "model": "qwen-plus",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
        "free_tier": False,
    },
    "2": {
        "name": "通义千问 Turbo（速度快，适合批量）",
        "model": "qwen-turbo",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
        "free_tier": False,
    },
    "3": {
        "name": "通义千问 Max（质量最高，消耗快）",
        "model": "qwen-max",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
        "free_tier": False,
    },
    "4": {
        "name": "Qwen Long（超长上下文，适合长篇）",
        "model": "qwen-long",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
        "free_tier": False,
    },
    "5": {
        "name": "Gemini 1.5 Flash【免费】（谷歌，限速15次/分钟）",
        "model": "gemini-1.5-flash",
        "provider": "gemini",
        "env_key": "GEMINI_API_KEY",
        "free_tier": True,
    },
    "6": {
        "name": "Gemini 1.5 Flash-8B【免费·超轻量】（速度更快，质量略低）",
        "model": "gemini-1.5-flash-8b",
        "provider": "gemini",
        "env_key": "GEMINI_API_KEY",
        "free_tier": True,
    },
    "7": {
        "name": "Gemini 2.0 Flash【免费】（最新，需科学上网）",
        "model": "gemini-2.0-flash",
        "provider": "gemini",
        "env_key": "GEMINI_API_KEY",
        "free_tier": True,
    },
}


def _format_api_error(error, api_name="API", attempt=1, max_attempts=3):
    """格式化API错误为用户友好的消息"""
    error_str = str(error)

    if "401" in error_str or "Unauthorized" in error_str or "invalid_api_key" in error_str:
        category = "认证失败"
        suggestion = "请检查 config.yaml 中的 API_KEY 是否正确"
    elif "429" in error_str or "rate" in error_str.lower() or "limit" in error_str.lower():
        category = "请求频率超限"
        suggestion = f"等待{10*(attempt)}秒后重试，或降低调用频率"
    elif "400" in error_str:
        category = "请求参数错误"
        suggestion = "检查输入内容长度是否超过模型限制"
    elif "500" in error_str or "502" in error_str or "503" in error_str:
        category = "服务端错误"
        suggestion = "稍后重试，或切换到其他可用模型"
    elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
        category = "请求超时"
        suggestion = "检查网络连接，或增加超时时间"
    else:
        category = "未知错误"
        suggestion = "查看详细错误信息并联系支持"

    return {
        "category": category,
        "message": error_str,
        "suggestion": suggestion,
        "attempt_info": f"第 {attempt}/{max_attempts} 次尝试",
        "full_error": repr(error)
    }


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = get_model_pricing(model)
    cost = (input_tokens * pricing["input"] +
            output_tokens * pricing["output"]) / 1000
    return round(cost, 6)


def _update_stats(model: str, input_tokens: int,
                  output_tokens: int, cost: float):
    _session_stats["total_calls"] += 1
    _session_stats["total_input_tokens"] += input_tokens
    _session_stats["total_output_tokens"] += output_tokens
    _session_stats["total_cost_yuan"] += cost
    _session_stats["model_used"] = model


def get_session_stats() -> dict:
    return _session_stats.copy()


def print_session_stats():
    s = _session_stats
    total_tokens = s["total_input_tokens"] + s["total_output_tokens"]
    cost = s["total_cost_yuan"]
    print("\n" + "=" * 50)
    print("  本次会话用量统计")
    print("=" * 50)
    print(f"  模型：{s['model_used']}")
    print(f"  调用次数：{s['total_calls']} 次")
    print(f"  输入 token：{s['total_input_tokens']:,}")
    print(f"  输出 token：{s['total_output_tokens']:,}")
    print(f"  总 token：{total_tokens:,}")
    print(f"  预估费用：¥{cost:.4f} 元")
    if cost < 0.01:
        print("  （费用极低，基本免费）")
    print("=" * 50)


# ==================== 三种独立模型管理 ====================

def get_current_model() -> str:
    return _author_model


def get_author_model() -> str:
    return _author_model


def get_reviewer_model() -> str:
    return _reviewer_model


def get_reader_reviewer_model() -> str:
    return _reader_reviewer_model


def set_author_model(model_name: str, provider: str = None):
    global _author_model, _author_provider
    old_model = _author_model
    _author_model = model_name
    if provider is None:
        provider = "gemini" if "gemini" in model_name else "dashscope"
    _author_provider = provider
    _switch_history.append({
        "type": "author",
        "old_model": old_model,
        "new_model": model_name,
        "timestamp": time.time()
    })
    print(f"[模型切换] 作者模型已切换：{old_model} → {model_name}")


def set_reviewer_model(model_name: str, provider: str = None):
    global _reviewer_model, _reviewer_provider
    old_model = _reviewer_model
    _reviewer_model = model_name
    if provider is None:
        provider = "gemini" if "gemini" in model_name else "dashscope"
    _reviewer_provider = provider
    _switch_history.append({
        "type": "reviewer",
        "old_model": old_model,
        "new_model": model_name,
        "timestamp": time.time()
    })
    print(f"[模型切换] 审核模型已切换：{old_model} → {model_name}")


def set_reader_reviewer_model(model_name: str, provider: str = None):
    global _reader_reviewer_model, _reader_reviewer_provider
    old_model = _reader_reviewer_model
    _reader_reviewer_model = model_name
    if provider is None:
        provider = "gemini" if "gemini" in model_name else "dashscope"
    _reader_reviewer_provider = provider
    _switch_history.append({
        "type": "reader_reviewer",
        "old_model": old_model,
        "new_model": model_name,
        "timestamp": time.time()
    })
    print(f"[模型切换] 读者视角模型已切换：{old_model} → {model_name}")


def increment_failure_counter(counter_type: str):
    """增加失败计数器：author/reviewer/reader_reviewer"""
    if counter_type in _failure_stats:
        _failure_stats[counter_type] += 1


def reset_failure_counter(counter_type: str = None):
    """重置失败计数器"""
    if counter_type:
        if counter_type in _failure_stats:
            _failure_stats[counter_type] = 0
    else:
        for k in _failure_stats:
            _failure_stats[k] = 0


def get_failure_stats() -> dict:
    return _failure_stats.copy()


def get_switch_history() -> list:
    return _switch_history.copy()


def check_switch_needed(counter_type: str) -> bool:
    """检查是否需要触发模型切换"""
    from core.config_loader import get as cfg
    threshold = cfg("novel", "failure_switch_threshold", 3)
    return _failure_stats.get(counter_type, 0) >= threshold


# ==================== 统一调用入口 ====================

def call_api(system_prompt: str, user_message: str,
             model_name: str = None,
             provider: str = None,
             max_tokens: int = None,
             temperature: float = None,
             retry: int = 3) -> str:
    from core.config_loader import get as cfg
    if max_tokens is None:
        max_tokens = cfg("model", "max_tokens", 4096)
    if temperature is None:
        temperature = 0.85

    if model_name is None:
        model_name = _author_model
        provider = _author_provider
    elif provider is None:
        provider = "gemini" if "gemini" in model_name else "dashscope"

    if provider == "dashscope":
        return _call_dashscope(system_prompt, user_message,
                               model_name, max_tokens, temperature, retry)
    elif provider == "gemini":
        return _call_gemini(system_prompt, user_message,
                            model_name, max_tokens, temperature, retry)
    else:
        raise ValueError(f"未知的provider: {provider}")


def _call_dashscope(system_prompt, user_message, model_name,
                    max_tokens, temperature, retry):
    from dashscope import Generation

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("未找到 DASHSCOPE_API_KEY，请检查 .env 文件")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for attempt in range(retry):
        try:
            response = Generation.call(
                api_key=api_key,
                model=model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                result_format="message",
            )
            if response.status_code == 200:
                usage = response.usage or {}
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                output_tokens = getattr(usage, "output_tokens", 0) or 0
                cost = _calc_cost(model_name, input_tokens, output_tokens)
                _update_stats(model_name, input_tokens, output_tokens, cost)
                return response.output.choices[0].message.content
            elif response.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"  [限速] API限速，等待{wait}秒后重试...")
                time.sleep(wait)
            else:
                raise RuntimeError(
                    f"API错误: {response.status_code} - {response.message}"
                )
        except RuntimeError:
            raise
        except Exception as e:
            err_info = _format_api_error(e, "DashScope", attempt + 1, retry)
            print(f"\n❌ [{err_info['category']}] {api_name if 'api_name' in dir() else 'DashScope'}")
            print(f"   详情: {err_info['message'][:200]}")
            print(f"   建议: {err_info['suggestion']}")
            if attempt < retry - 1:
                print(f"   {err_info['attempt_info']}...")
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"API连续失败{retry}次: {e}")

    raise RuntimeError(f"API连续失败{retry}次")


def _call_gemini(system_prompt, user_message, model_name,
                 max_tokens, temperature, retry):
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("未找到 GEMINI_API_KEY，请检查 .env 文件")

    is_free = model_name in FREE_TIER_MODELS
    client = genai.Client(api_key=api_key)
    full_prompt = f"{system_prompt}\n\n---\n\n{user_message}"

    for attempt in range(retry):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config=genai.types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
            )
            try:
                input_tokens = (
                    response.usage_metadata.prompt_token_count or 0
                )
                output_tokens = (
                    response.usage_metadata.candidates_token_count or 0
                )
                cost = _calc_cost(model_name, input_tokens, output_tokens)
                _update_stats(model_name, input_tokens, output_tokens, cost)
            except Exception:
                pass
            # 免费层：成功后主动等待，避免下次立即触发RPM限制
            if is_free:
                time.sleep(4)
            return response.text

        except Exception as e:
            err_str = str(e).lower()
            # 处理限速错误（429 / quota / resource_exhausted）
            if any(kw in err_str for kw in
                   ("429", "quota", "resource_exhausted", "rate")):
                if is_free:
                    wait = 65  # 免费层：等满1分钟以重置RPM窗口
                else:
                    wait = 30 * (attempt + 1)
                print(f"  [限速] Gemini API限速（免费层），等待{wait}秒后重试...")
                time.sleep(wait)
                continue

            err_info = _format_api_error(e, "Gemini", attempt + 1, retry)
            print(f"\n❌ [{err_info['category']}] Gemini")
            print(f"   详情: {err_info['message'][:200]}")
            print(f"   建议: {err_info['suggestion']}")
            if attempt < retry - 1:
                print(f"   {err_info['attempt_info']}...")
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"Gemini API连续失败{retry}次: {e}")

    raise RuntimeError(f"Gemini API连续失败{retry}次")


# ==================== 三种独立调用入口 ====================

def call_author_api(system_prompt: str, user_message: str,
                   max_tokens: int = None,
                   temperature: float = None,
                   retry: int = 3) -> str:
    """调用作者模型"""
    return call_api(
        system_prompt=system_prompt,
        user_message=user_message,
        model_name=_author_model,
        provider=_author_provider,
        max_tokens=max_tokens,
        temperature=temperature,
        retry=retry
    )


def call_reviewer_api(system_prompt: str, user_message: str,
                     max_tokens: int = None,
                     temperature: float = None,
                     retry: int = 3) -> str:
    """调用审核模型"""
    return call_api(
        system_prompt=system_prompt,
        user_message=user_message,
        model_name=_reviewer_model,
        provider=_reviewer_provider,
        max_tokens=max_tokens,
        temperature=temperature,
        retry=retry
    )


def call_reader_reviewer_api(system_prompt: str, user_message: str,
                            max_tokens: int = None,
                            temperature: float = None,
                            retry: int = 3) -> str:
    """调用读者视角模型"""
    return call_api(
        system_prompt=system_prompt,
        user_message=user_message,
        model_name=_reader_reviewer_model,
        provider=_reader_reviewer_provider,
        max_tokens=max_tokens,
        temperature=temperature,
        retry=retry
    )


# ==================== 交互式模型选择（支持三种模型分别选择） ====================

def select_model_interactive() -> dict:
    global _author_model, _author_provider
    global _reviewer_model, _reviewer_provider
    global _reader_reviewer_model, _reader_reviewer_provider

    print("\n" + "=" * 50)
    print("  请选择写作模型（作者模型）")
    print("=" * 50)

    models = get_available_models(usage="author")
    for key, info in models.items():
        api_key = os.getenv(info["env_key"], "")
        has_key = api_key and len(api_key) > 10
        status = "✓ Key已填写" if has_key else "✗ Key未填写"
        pricing = get_model_pricing(info["model"])
        inp = pricing.get("input", 0)
        out = pricing.get("output", 0)
        if inp == 0.0 and out == 0.0:
            price_str = "★ 免费"
        else:
            price_str = (
                f"输入¥{inp * 1000:.2f}/百万token  "
                f"输出¥{out * 1000:.2f}/百万token"
            )
        print(f"  {key}. {info['name']:<38} [{status}]")
        print(f"      {price_str}")

    print()
    choice = input("请选择作者模型（直接回车默认选1）：").strip() or "1"

    if choice not in models:
        print("[提示] 无效选择，使用默认模型")
        choice = "1"

    selected = models[choice]

    api_key = os.getenv(selected["env_key"], "")
    if not api_key or len(api_key) < 10:
        print(f"\n[错误] 未找到 {selected['env_key']}，请检查 .env 文件")
        print("请重新选择\n")
        return select_model_interactive()

    _author_model = selected["model"]
    _author_provider = selected["provider"]
    _session_stats["model_used"] = selected["model"]

    # 为审核模型和读者视角模型设置默认值
    _reviewer_model = "qwen-turbo"
    _reviewer_provider = "dashscope"
    _reader_reviewer_model = "qwen-plus"
    _reader_reviewer_provider = "dashscope"

    print(f"\n[OK] 作者模型已选择：{selected['name']}")
    print(f"     模型代码：{selected['model']}")
    print(f"     审核模型默认：qwen-turbo")
    print(f"     读者视角模型默认：qwen-plus")
    if selected.get("free_tier"):
        print("     [免费层] 限速约15次/分钟，遇到429错误会自动等待60秒重试")
    print("     正在验证模型可用性...")

    try:
        call_author_api(
            system_prompt="你是助手。",
            user_message="回复ok两个字",
            max_tokens=10,
            temperature=0.1,
            retry=1,
        )
        print("     [✓ 验证通过] 模型响应正常\n")
    except Exception as e:
        print(f"     [✗ 验证失败] {e}")
        print("     该模型当前不可用，请重新选择\n")
        return select_model_interactive()

    return selected


def select_all_models_interactive():
    """交互式分别选择三种模型"""
    print("\n" + "=" * 60)
    print("  高级模型配置 - 分别选择三种模型")
    print("=" * 60)

    print("\n【1/3】选择作者模型（用于生成章节内容）")
    author_choice = _select_single_model("作者模型", default="1", usage="author")
    set_author_model(author_choice["model"], author_choice["provider"])

    print("\n【2/3】选择审核模型（用于责任编辑审稿）")
    reviewer_choice = _select_single_model("审核模型", default="2", usage="reviewer")
    set_reviewer_model(reviewer_choice["model"], reviewer_choice["provider"])

    print("\n【3/3】选择读者视角模型（用于读者视角评估）")
    reader_choice = _select_single_model("读者视角模型", default="1", usage="reader_reviewer")
    set_reader_reviewer_model(reader_choice["model"], reader_choice["provider"])

    print("\n" + "=" * 60)
    print("  模型配置完成")
    print("=" * 60)
    print(f"  作者模型：{_author_model}")
    print(f"  审核模型：{_reviewer_model}")
    print(f"  读者视角模型：{_reader_reviewer_model}")


def _select_single_model(prompt_title: str, default: str, usage: str = None) -> dict:
    """辅助函数：选择单个模型
    
    usage: 可选，"author" | "reviewer" | "reader_reviewer"
    """
    print("-" * 60)
    models = get_available_models(usage=usage)
    for key, info in models.items():
        api_key = os.getenv(info["env_key"], "")
        has_key = api_key and len(api_key) > 10
        status = "✓ Key已填写" if has_key else "✗ Key未填写"
        pricing = get_model_pricing(info["model"])
        inp = pricing.get("input", 0)
        out = pricing.get("output", 0)
        if inp == 0.0 and out == 0.0:
            price_str = "★ 免费"
        else:
            price_str = (
                f"输入¥{inp * 1000:.2f}/百万  "
                f"输出¥{out * 1000:.2f}/百万"
            )
        print(f"  {key}. {info['name']:<32} [{status}]")
        print(f"      {price_str}")

    print()
    choice = input(f"请选择{prompt_title}（直接回车默认选{default}）：").strip() or default

    if choice not in models:
        print("[提示] 无效选择，使用默认")
        choice = default

    selected = models[choice]

    api_key = os.getenv(selected["env_key"], "")
    if not api_key or len(api_key) < 10:
        print(f"\n[错误] 未找到 {selected['env_key']}")
        return _select_single_model(prompt_title, default)

    # 验证可用性
    print(f"  正在验证 {selected['name']}...")
    try:
        call_api(
            system_prompt="你是助手。",
            user_message="回复ok",
            model_name=selected["model"],
            provider=selected["provider"],
            max_tokens=10,
            temperature=0.1,
            retry=1,
        )
        print(f"  [✓] {selected['name']} 验证通过")
    except Exception as e:
        print(f"  [✗] 验证失败：{e}")
        return _select_single_model(prompt_title, default)

    return selected
