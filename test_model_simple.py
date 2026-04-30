from core.api_client import call_api


def main():
    # 手动连通性测试：pytest 导入本文件时不会发起真实 API 请求。
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


if __name__ == "__main__":
    main()
