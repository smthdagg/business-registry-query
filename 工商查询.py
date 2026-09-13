import re
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


class 工商查询系统:
    """企业信息查询工具。"""

    基础URL = "https://m.tianyancha.com/proxyPeers/getCompanyPhone.json"

    基础字段 = {
        "公司名称": "name",
        "统一社会信用代码": "creditCode",
        "注册号": "regNumber",
        "组织机构代码": "orgNumber",
        "法定代表人": "legalPersonName",
        "注册资本": "regCapital",
        "成立日期": "estiblishTime",
        "经营状态": "regStatus",
        "公司类型": "companyOrgType",
        "注册地址": "regLocation",
        "行业": "categoryStr",
        "登记机关": "registerInstitute",
        "经营范围": "businessScope",
        "企业规模": "companyScale",
        "曾用名": "historyNames",
        "联系电话": "phone",
        "所在城市": "city",
        "所在区县": "district",
        "企业评分": "companyScore",
    }

    def __init__(self) -> None:
        self.会话 = requests.Session()
        self.会话.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                ),
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
        )

    @staticmethod
    def 清洗文本(文本: Any) -> str:
        """移除 HTML 标签并合并多余空白。"""
        if 文本 is None:
            return ""

        文本 = str(文本)
        文本 = re.sub(r"<[^>]+>", "", 文本)
        文本 = 文本.replace("\t", " ").replace("\n", " ")
        return re.sub(r"\s+", " ", 文本).strip()

    def 查询企业(self, 关键词: str) -> dict[str, Any] | None:
        """调用查询接口并返回原始数据。"""
        参数 = {
            "cate": "",
            "baseCode": "",
            "base": "",
            "key": 关键词,
        }

        try:
            响应 = self.会话.get(self.基础URL, params=参数, timeout=10)
            响应.raise_for_status()
            数据 = 响应.json()
        except requests.Timeout:
            print("查询超时，请稍后重试。")
            return None
        except requests.RequestException as 错误:
            print(f"接口请求失败：{错误}")
            return None
        except ValueError:
            print("接口返回的内容不是有效的 JSON 数据。")
            return None

        if 数据.get("state") != "ok":
            print(f"接口返回状态异常：{数据.get('state', '未知状态')}")
            return None

        企业列表 = 数据.get("data", {}).get("items", [])
        if not 企业列表:
            print("未查询到相关企业数据。")
            return None

        return 数据

    def 解析企业信息(self, 企业数据: dict[str, Any]) -> dict[str, str]:
        """将接口数据整理成适合展示的企业信息。"""
        信息: dict[str, str] = {}

        for 显示名, 字段名 in self.基础字段.items():
            值 = 企业数据.get(字段名)
            if isinstance(值, list):
                值 = "、".join(str(项目) for 项目 in 值 if 项目)

            清洗后的值 = self.清洗文本(值)
            if 清洗后的值:
                信息[显示名] = 清洗后的值

        标签列表 = 企业数据.get("labelListV2", [])
        if isinstance(标签列表, list) and 标签列表:
            标签 = [
                self.清洗文本(标签)
                for 标签 in 标签列表
                if self.清洗文本(标签)
            ]
            if 标签:
                信息["标签"] = "、".join(标签)

        return 信息

    @staticmethod
    def 显示完整信息(企业信息: dict[str, str]) -> None:
        """在终端中显示企业完整信息。"""
        print("\n" + "=" * 80)
        for 字段, 值 in 企业信息.items():
            print(f"{字段}: {值}")
        print("=" * 80)

    @staticmethod
    def 保存到文件(
        企业信息: dict[str, str],
        文件名: str = "企业查询结果.txt",
    ) -> bool:
        """将企业信息追加保存到 UTF-8 文本文件。"""
        文件路径 = Path(文件名).expanduser().resolve()

        try:
            with 文件路径.open("a", encoding="utf-8") as 文件:
                文件.write("\n" + "=" * 60 + "\n")
                文件.write(f"查询时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
                文件.write("-" * 40 + "\n")
                for 字段, 值 in 企业信息.items():
                    文件.write(f"{字段}: {值}\n")

            print(f"数据已保存到：{文件路径}")
            return True
        except OSError as 错误:
            print(f"保存文件失败：{错误}")
            return False

    def 询问并保存(self, 企业信息: dict[str, str]) -> None:
        """询问用户是否保存当前企业信息。"""
        选择 = input("是否将该企业信息保存到 TXT？(y/n): ").strip().lower()
        if 选择 == "y":
            self.保存到文件(企业信息)

    def 运行(self) -> None:
        """执行一次查询流程。"""
        print("\n===== 企业信息查询工具 =====")
        关键词 = input(
            "请输入查询关键词（企业名/信用代码/法人/电话等）: "
        ).strip()
        if not 关键词:
            print("关键词不能为空。")
            return

        print(f"\n正在查询【{关键词}】相关信息……")
        原始数据 = self.查询企业(关键词)
        if not 原始数据:
            return

        企业列表 = 原始数据["data"]["items"]
        print(f"共查询到 {len(企业列表)} 条结果。")

        for 序号, 企业 in enumerate(企业列表, start=1):
            简要信息 = self.解析企业信息(企业)
            公司名称 = 简要信息.get("公司名称", "未知企业")
            法定代表人 = 简要信息.get("法定代表人", "未知")
            print(f"[{序号}] {公司名称} | 法定代表人: {法定代表人}")

        用户输入 = input(
            "\n请输入要查看的企业序号（输入 0 查看全部）: "
        ).strip()

        try:
            选择序号 = int(用户输入)
        except ValueError:
            print("请输入有效的数字序号。")
            return

        if 选择序号 == 0:
            for 企业 in 企业列表:
                详情 = self.解析企业信息(企业)
                self.显示完整信息(详情)
                self.询问并保存(详情)
            return

        if 1 <= 选择序号 <= len(企业列表):
            目标企业 = 企业列表[选择序号 - 1]
            详情 = self.解析企业信息(目标企业)
            self.显示完整信息(详情)
            self.询问并保存(详情)
            return

        print("输入序号超出范围。")

    def 关闭(self) -> None:
        """关闭 HTTP 会话。"""
        self.会话.close()


def main() -> None:
    系统 = 工商查询系统()

    try:
        while True:
            系统.运行()
            是否继续 = input("\n是否继续查询？(y/n): ").strip().lower()
            if 是否继续 != "y":
                print("程序已退出。")
                break
    except KeyboardInterrupt:
        print("\n程序已取消。")
    finally:
        系统.关闭()


if __name__ == "__main__":
    main()
