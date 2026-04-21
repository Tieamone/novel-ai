import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dashscope import Generation

print('Dashscope version:', getattr(Generation, '__version__', 'unknown'))
print('Generation Models:', dir(Generation.Models) if hasattr(Generation, 'Models') else 'No Models')

# 检查具体的模型值
if hasattr(Generation, 'Models'):
    for attr in dir(Generation.Models):
        if not attr.startswith('_'):
            value = getattr(Generation.Models, attr, 'N/A')
            print(f'{attr}: {value}')

# 尝试列出所有可能的模型
print('\n=== 尝试使用 qwen3.6 系列模型 ===')
try:
    from dashscope import Text2Text
    print('Text2Text available')
except Exception as e:
    print('Text2Text not available:', e)
