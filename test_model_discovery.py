from core.model_manager import discover_all_models


def test_discover_all_models_returns_configured_models():
    models = discover_all_models()
    assert isinstance(models, list)
    assert any(m.get("model") == "qwen3.6-flash" for m in models)


def main():
    print("=== 测试模型发现 ===")
    try:
        models = discover_all_models()
        print(f"成功发现 {len(models)} 个模型")
        print("\n所有模型:")
        for i, m in enumerate(models, 1):
            model_id = m['model']
            model_name = m['name'].split(' ')[0]
            print(f"  {i}. {model_id} - {model_name}")
    except Exception as e:
        print(f"模型发现失败: {e}")


if __name__ == "__main__":
    main()
