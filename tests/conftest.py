"""pytest 共享配置：让测试能以包路径导入插件模块。

插件使用相对导入（from .core.models import ...），直接 import 会失败。
本文件把插件目录与父目录加入 sys.path，使 maibot_plugin_minecraft_adapter
作为命名空间包可被导入（core/services/handlers 均为纯逻辑，不依赖 maibot_sdk）。
"""

import os
import sys

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT_DIR = os.path.dirname(_PLUGIN_DIR)
for _p in (_PARENT_DIR, _PLUGIN_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
