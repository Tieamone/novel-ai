#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mimo模型调用测试脚本
测试所有Mimo模型是否能正常调用
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv('.env')

from core.api_client import call_api, _get_api_key

def test_mimo_models():
    print("=" * 60)
    print("Mimo模型调用测试")
    print("=" * 60)

    # 检查API Key
    api_key = _get_api_key('MIMO_API_KEY')
    if not api_key:
        print("[ERROR] MIMO_API_KEY未设置")
        print("请在.env文件中设置: MIMO_API_KEY=你的Key")
        return False

    print(f"[OK] MIMO_API_KEY: {api_key[:10]}...")

    # 测试模型列表
    models = [
        ('mimo-v2.5-pro', '旗舰模型'),
        ('mimo-v2.5', '标准模型'),
        ('mimo-v2-pro', '高级模型'),
        ('mimo-v2-omni', '全能模型'),
        ('mimo-v2-flash', '快速版'),
    ]

    results = []
    for model_name, description in models:
        print(f"\n测试 {model_name} ({description})...")
        try:
            response = call_api(
                system_prompt='你是一个助手',
                user_message='请回复"测试成功"四个字',
                model_name=model_name,
                provider='mimo',
                max_tokens=10,
                temperature=0.1,
                retry=1
            )
            print(f"  [OK] 成功: {response.strip()[:50]}")
            results.append((model_name, True, response.strip()[:50]))
        except Exception as e:
            error_msg = str(e)[:100]
            print(f"  [FAIL] 失败: {error_msg}")
            results.append((model_name, False, error_msg))

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    success_count = sum(1 for _, success, _ in results if success)
    total_count = len(results)

    for model_name, success, msg in results:
        status = "[OK] 成功" if success else "[FAIL] 失败"
        print(f"{model_name}: {status}")

    print(f"\n总计: {success_count}/{total_count} 个模型可用")

    if success_count == total_count:
        print("\n所有Mimo模型测试通过！")
        return True
    else:
        print(f"\n有 {total_count - success_count} 个模型测试失败")
        return False

if __name__ == "__main__":
    success = test_mimo_models()
    sys.exit(0 if success else 1)