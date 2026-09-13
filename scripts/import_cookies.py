#!/usr/bin/env python3
"""把浏览器导出的 Netscape Cookie 文件转成 .env 里的数据源 Cookie。

用法（文件来自 Cookie 导出类浏览器插件，格式为 Netscape HTTP Cookie File）：
  python scripts/import_cookies.py 文件1.txt 文件2.txt ...

不带参数时，自动扫描 ~/Downloads 下文件名/域名包含已知站点的 *.txt。

站点 → .env 变量映射：
  tianyancha → TYC_COOKIE     aiqicha → AQC_COOKIE
  riskbird   → RB_COOKIE      88cha   → CHA88_COOKIE

Cookie 会过期：过期后在浏览器重新导出，再跑一次本脚本即可。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"

TARGETS = {
    "tianyancha": "TYC_COOKIE",
    "aiqicha": "AQC_COOKIE",
    "riskbird": "RB_COOKIE",
    "88cha": "CHA88_COOKIE",
}


def parse_netscape(path: Path) -> tuple[str, str] | None:
    """解析 Netscape Cookie 文件，返回 (命中的站点关键词, Cookie 头字符串)。"""
    pairs: list[str] = []
    domains: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, name, value = parts[0].strip(), parts[5].strip(), parts[6].strip()
        if not name or value in ("", "undefined"):
            continue
        domains.add(domain.lower())
        pairs.append(f"{name}={value}")
    if not pairs:
        return None
    blob = " ".join(domains)
    for keyword, env_key in TARGETS.items():
        if keyword in blob:
            return keyword, "; ".join(pairs)
    return None


def update_env(key: str, value: str) -> None:
    lines = []
    if ENV_FILE.exists():
        lines = [
            ln for ln in ENV_FILE.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
    replaced = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> None:
    if argv:
        files = [Path(p) for p in argv]
    else:
        downloads = Path.home() / "Downloads"
        files = sorted(
            p for p in downloads.glob("*.txt")
            if any(k in p.name.lower() for k in TARGETS)
        )
    if not files:
        print("没有找到 Cookie 文件。用法：python scripts/import_cookies.py 文件1.txt ...")
        return
    done = []
    for path in files:
        if not path.exists():
            print(f"[跳过] {path} 不存在")
            continue
        result = parse_netscape(path)
        if result is None:
            print(f"[跳过] {path.name}：不是 Netscape Cookie 文件或未识别站点")
            continue
        keyword, cookie = result
        update_env(TARGETS[keyword], cookie)
        done.append(TARGETS[keyword])
        print(f"[写入] {path.name} → .env 的 {TARGETS[keyword]}（{len(cookie)} 字符）")
    if done:
        print(f"\n完成：{', '.join(done)} 已写入 {ENV_FILE}")
        print("重启服务后生效：kill 8000 端口进程后重新运行 run_local.sh（或 docker compose restart）")


if __name__ == "__main__":
    main(sys.argv[1:])
