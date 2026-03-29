import os
import time
from dotenv import load_dotenv

load_dotenv()

_current_model = "qwen-plus"
_current_provider = "dashscope"

_session_stats = {
    "total_calls": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_cost_yuan": 0.0,
    "model_used": "",
}

MODEL_PRICING = {
    "qwen-turbo":       {"input": 0.0003, "output": 0.0006},
    "qwen-plus":        {"input": 0.0008, "output": 0.002},
    "qwen-max":         {"input": 0.04,   "output": 0.12},
    "qwen-long":        {"input": 0.0005, "output": 0.002},
    "gemini-2.0-flash": {"input": 0.0,    "output": 0.0},
}

AVAILABLE_MODELS = {
    "1": {
        "name": "通义千问 Plus（推荐，质量好）",
        "model": "qwen-plus",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
    },
    "2": {
        "name": "通义千问 Turbo（速度快，字数少）",
        "model": "qwen-turbo",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
    },
    "3": {
        "name": "通义千问 Max（质量最高，消耗快）",
        "model": "qwen-max",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
    },
    "4": {
        "name": "Qwen Long（超长上下文，适合长篇）",
        "model": "qwen-long",
        "provider": "dashscope",
        "env_key": "DASHSCOPE_API_KEY",
    },
    "5": {
        "name": "Gemini 2.0 Flash（谷歌，需科学上网）",
        "model": "gemini-2.0-flash",
        "provider": "gemini",
        "env_key": "GEMINI_API_KEY",
    },
}


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, {"input": 0.001, "output": 0.002})
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


def select_model_interactive() -> dict:
    global _current_model, _current_provider

    print("\n" + "=" * 50)
    print("  请选择写作模型")
    print("=" * 50)

    for key, info in AVAILABLE_MODELS.items():
        api_key = os.getenv(info["env_key"], "")
        has_key = api_key and len(api_key) > 10
        status = "✓ Key已填写" if has_key else "✗ Key未填写"
        pricing = MODEL_PRICING.get(info["model"], {})
        price_str = (
            f"输入¥{pricing.get('input', 0) * 1000:.2f}/百万token  "
            f"输出¥{pricing.get('output', 0) * 1000:.2f}/百万token"
        )
        print(f"  {key}. {info['name']:<30} [{status}]")
        print(f"      {price_str}")

    print()
    choice = input("请输入编号（直接回车默认选1）：").strip() or "1"

    if choice not in AVAILABLE_MODELS:
        print("[提示] 无效选择，使用默认模型")
        choice = "1"

    selected = AVAILABLE_MODELS[choice]

    api_key = os.getenv(selected["env_key"], "")
    if not api_key or len(api_key) < 10:
        print(f"\n[错误] 未找到 {selected['env_key']}，请检查 .env 文件")
        print("请重新选择\n")
        return select_model_interactive()

    _current_model = selected["model"]
    _current_provider = selected["provider"]
    _session_stats["model_used"] = selected["model"]

    print(f"\n[OK] 已选择：{selected['name']}")
    print(f"     模型代码：{selected['model']}")
    print("     正在验证模型可用性...")

    try:
        call_api(
            system_prompt="你是助手。",
            user_message="回复ok两个字",
            model_name=selected["model"],
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


def call_api(system_prompt: str, user_message: str,
             model_name: str = None,
             max_tokens: int = None,
             temperature: float = None,
             retry: int = 3) -> str:
    # 从 config 读取默认值
    from core.config_loader import get as cfg
    if max_tokens is None:
        max_tokens = cfg("model", "max_tokens", 4096)
    if temperature is None:
        temperature = cfg("model", "temperature", 0.85)

    if model_name is None:
        model_name = _current_model
        provider = _current_provider
    else:
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
            else:
                raise RuntimeError(
                    f"API错误: {response.status_code} - {response.message}"
                )
        except RuntimeError:
            raise
        except Exception as e:
            print(f"  [警告] 调用失败 (第{attempt+1}次): {e}")
            if attempt < retry - 1:
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"API连续失败{retry}次: {e}")


def _call_gemini(system_prompt, user_message, model_name,
                 max_tokens, temperature, retry):
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("未找到 GEMINI_API_KEY，请检查 .env 文件")

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
            return response.text
        except Exception as e:
            print(f"  [警告] 调用失败 (第{attempt+1}次): {e}")
            if attempt < retry - 1:
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"API连续失败{retry}次: {e}")


def get_current_model() -> str:
    return _current_model