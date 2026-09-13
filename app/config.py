"""全局配置：全部从环境变量读取，未设置时使用默认值。

本地开发可直接运行；容器部署通过 .env / compose environment 注入。
"""

import os
from pathlib import Path


def _load_env_file() -> None:
    """极简 .env 加载（项目根目录），已存在的环境变量优先，不覆盖。"""
    try:
        path = Path(__file__).resolve().parents[1] / ".env"
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_env_file()

# 数据目录：数据库、密钥、缓存都放这里（compose 中挂载为 volume）
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).expanduser().resolve()

# 登录口令：留空 = 不启用登录（仅限本地调试）
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
# 普通会员口令（可选）：留空则只有管理员账号
MEMBER_PASSWORD = os.environ.get("MEMBER_PASSWORD", "")

# Cookie 签名密钥：留空则首次启动自动生成到 data/secret.key
SECRET_KEY = os.environ.get("SECRET_KEY", "")

# 会话有效期（天）
LOGIN_TTL_DAYS = int(os.environ.get("LOGIN_TTL_DAYS", "7"))

# 默认数据源：all = 聚合查询（并行打全部真实源）；也可指定 tyc/aqc/rb/c88/mock
DEFAULT_SOURCE = os.environ.get("DATA_SOURCE", "all")

# 天眼查：请求头 / 间隔
TYC_COOKIE = os.environ.get("TYC_COOKIE", "")          # 浏览器登录后复制的 Cookie
TYC_DELAY = float(os.environ.get("TYC_DELAY", "2.0"))  # 每次真实请求的最小间隔（秒）
TYC_URL = os.environ.get("TYC_URL", "")                # 接口地址覆盖（接口改版/走代理时用）
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "15"))

# 爱企查（百度，免登录配额极低，日常用需浏览器登录 Cookie）
AQC_COOKIE = os.environ.get("AQC_COOKIE", "")
AQC_DELAY = float(os.environ.get("AQC_DELAY", "1.5"))

# 风鸟（riskbird.com，免费注册，接口需登录 Cookie）
RB_COOKIE = os.environ.get("RB_COOKIE", "")
RB_DELAY = float(os.environ.get("RB_DELAY", "1.5"))

# 88查（88cha.com，阿里出品，免注册账号；浏览器 Cookie 过滑块风控）
CHA88_COOKIE = os.environ.get("CHA88_COOKIE", "")
CHA88_DELAY = float(os.environ.get("CHA88_DELAY", "1.0"))

# 搜索结果缓存（秒）：只对实时源生效
CACHE_TTL = int(os.environ.get("CACHE_TTL", "21600"))
CACHE_MAX_ROWS = int(os.environ.get("CACHE_MAX_ROWS", "2000"))

# 历史记录保留条数
HISTORY_MAX_ROWS = int(os.environ.get("HISTORY_MAX_ROWS", "200"))

# 批量/关联任务：行数上限与预算保护
BATCH_MAX_LINES = int(os.environ.get("BATCH_MAX_LINES", "500"))
RELATION_MAX_NODES = int(os.environ.get("RELATION_MAX_NODES", "60"))
RELATION_MAX_DEPTH = int(os.environ.get("RELATION_MAX_DEPTH", "3"))


# 浏览器采集（Camoufox 指纹浏览器，可选组件）：HTTP 通道被风控/失效时自动降级到浏览器渲染
# 需先安装：pip install camoufox[geoip] && python -m camoufox fetch
BROWSER_FALLBACK = os.environ.get("BROWSER_FALLBACK", "off") == "on"
BROWSER_HEADLESS = os.environ.get("BROWSER_HEADLESS", "on") == "on"
BROWSER_GEOIP = os.environ.get("BROWSER_GEOIP", "off") == "on"

# Cookie 自动保活间隔（分钟；对已配置平台做轻量访问，平台自动续会话并回写）
COOKIE_KEEPALIVE_MIN = int(os.environ.get("COOKIE_KEEPALIVE_MIN", "30"))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
