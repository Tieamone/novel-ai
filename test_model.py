from core.model_manager import discover_all_models
from core.api_client import call_api


def main():
    # 手动连通性测试：pytest 导入本文件时不会发起真实 API 请求。
    print("=== 测试模型发现 ===")
    models = discover_all_models()
    print(f"发现了 {len(models)} 个模型")

    print("\n=== 查找 qwen3.6-plus-2026-04-02 模型 ===")
    target_model = None
    for m in models:
        if m['model'] == 'qwen3.6-plus-2026-04-02':
            target_model = m
            print(f"找到模型: {m['model']} - {m['name']}")
            break

    if not target_model:
        print("未找到 qwen3.6-plus-2026-04-02 模型")

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


if __name__ == "__main__":
    main()
