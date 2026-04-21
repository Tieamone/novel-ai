from core.model_manager import discover_all_models
from core.api_client import call_api

# 测试模型发现
print("=== 测试模型发现 ===")
models = discover_all_models()
print(f"发现了 {len(models)} 个模型")

# 查找标准格式的 qwen3.6-plus 模型
print("\n=== 查找标准格式的 qwen3.6-plus 模型 ===")
target_model = None
for m in models:
    if m['model'] == 'qwen3.6-plus':
        target_model = m
        print(f"找到模型: {m['model']} - {m['name']}")
        break

if not target_model:
    print("未找到 qwen3.6-plus 模型")

# 测试模型验证
print("\n=== 测试模型验证 ===")
if target_model:
    try:
        result = call_api(
            system_prompt="你是助手。",
            user_message="回复ok两个字",
            model_name=target_model['model'],
            provider=target_model['provider'],
            max_tokens=10,
            temperature=0.1,
            retry=1
        )
        print(f"验证成功: {result}")
    except Exception as e:
        print(f"验证失败: {e}")
