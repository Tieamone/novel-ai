from core.api_client import _is_thinking_model

# 测试模型名称格式
print("=== 测试模型名称格式 ===")

model_names = [
    "qwen3.6-plus",
    "qwen3.6-plus-2026-04-02",
    "qwen3.6-35b-a3b",
    "glm-5.1",
    "qwen-turbo"
]

for model_name in model_names:
    is_thinking = _is_thinking_model(model_name)
    print(f"{model_name}: is_thinking={is_thinking}")

# 检查自定义模型配置
print("\n=== 检查自定义模型配置 ===")
import json
import os

custom_file = os.path.join("data", "custom_models.json")
if os.path.exists(custom_file):
    with open(custom_file, "r", encoding="utf-8") as f:
        models = json.load(f)
    print(f"加载了 {len(models)} 个自定义模型")
    for model in models:
        if "qwen3.6-plus" in model.get("model", ""):
            print(f"模型: {model['model']} - {model['name']}")
