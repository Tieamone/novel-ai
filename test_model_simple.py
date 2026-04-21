from core.api_client import call_api

# 测试标准模型验证
print("=== 测试标准模型验证 ===")

test_models = [
    "qwen3.6-plus",
    "qwen3.6-flash",
    "qwen3.6-35b-a3b"
]

for model_name in test_models:
    print(f"\n测试模型: {model_name}")
    try:
        result = call_api(
            system_prompt="你是助手。",
            user_message="回复ok",
            model_name=model_name,
            provider="dashscope",
            max_tokens=10,
            temperature=0.1,
            retry=1
        )
        print(f"  验证成功: {result}")
    except Exception as e:
        print(f"  验证失败: {e}")
