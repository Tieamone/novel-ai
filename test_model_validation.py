from core.api_client import select_model_interactive

# 测试模型选择和验证
print("=== 测试模型选择和验证 ===")
try:
    selected_model = select_model_interactive()
    print(f"成功选择模型: {selected_model['model']} - {selected_model['name']}")
except Exception as e:
    print(f"模型选择失败: {e}")
