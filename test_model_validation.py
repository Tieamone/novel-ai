from core.api_client import select_model_interactive


def main():
    # 手动交互测试：pytest 导入本文件时不会等待用户输入。
    print("=== 测试模型选择和验证 ===")
    try:
        selected_model = select_model_interactive()
        print(f"成功选择模型: {selected_model['model']} - {selected_model['name']}")
    except Exception as e:
        print(f"模型选择失败: {e}")


if __name__ == "__main__":
    main()
