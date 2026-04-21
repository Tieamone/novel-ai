from core.model_manager import discover_all_models

# 测试模型发现
print("=== 测试模型发现 ===")
try:
    models = discover_all_models()
    print(f"成功发现 {len(models)} 个模型")
    print("\n所有模型:")
    for i, m in enumerate(models, 1):
        # 只打印模型ID和基本名称，避免Unicode编码问题
        model_id = m['model']
        model_name = m['name'].split(' ')[0]  # 只取名称的第一部分
        print(f"  {i}. {model_id} - {model_name}")
except Exception as e:
    print(f"模型发现失败: {e}")
