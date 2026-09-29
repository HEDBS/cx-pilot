# -*- coding: utf-8 -*-
"""cx-pilot M0 sidecar：FastAPI 薄封装 core/ 引擎（REST + SSE，127.0.0.1 only）。

禁改面纪律：本包只 import core 的数据层模块，绝不 import flet / core.theme / app_v2 / ui。
"""
import os

# server 进程内永不允许引擎走 stdin 交互回退（缺凭据必须走 NoCredentials → 428）
os.environ["CXPilot_GUI"] = "1"

# 版本单一常量：与界面角标 / app_v2 --version 同源（v0.3.1）。app_v2 禁改面里是字符串字面量，
# server 侧以本常量为唯一事实源；升版三处同步纪律见 HANDOFF §4.7。
VERSION = "0.3.1"
ENGINE = VERSION
