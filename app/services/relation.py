"""关联企业分析（v2：按节点记源的跨源图谱）。

每个图节点记住它来自哪个数据源；展开该节点时使用**它自己数据源**支持
的策略，源不支持的角度自动落到支持的源（法人/电话搜索落到天眼查）：

  rb/aqc 节点 → enrich 拉股东/对外投资（真实股权）→ 子公司逐家收录
  任何节点   → 法人其它任职  → 天眼查“法定代表人匹配”（跨源补齐）
  任何节点   → 同电话        → 支持该角度的源

策略（用户可勾选）：
  legal       法人其它任职企业
  shareholder 股东参与的其他企业（依赖源的字段/角度）
  invest      对外投资（子公司/被投企业，有向箭头）
  phone       同电话疑似关联

预算保护：节点数 / 展开深度 / 请求次数三重兜底。
"""

from collections import deque

from .. import config, sources
from ..sources.base import SourceError
from ..utils import clean_text, is_person_name, phones_equal, record_person_hit
from .pacing import wait_interval

EDGE_LABELS = {
    "legal": "法人",
    "shareholder": "股东",
    "invest": "对外投资",
    "phone": "同电话",
    "position": "任职",
}

_CO_PAYLOAD_KEYS = [
    "name", "legalPersonName", "regStatus", "creditCode", "regNumber",
    "companyOrgType", "regCapital", "estiblishTime", "regLocation",
    "industry", "registerInstitute", "phone", "city", "district",
    "companyScore", "matchType", "source", "sources",
]


class _BudgetStop(Exception):
    """请求预算耗尽。"""


class RelationBuilder:
    def __init__(
        self,
        source_id: str,
        target: str,
        depth: int = 2,
        max_nodes: int = 40,
        strategies: list[str] | None = None,
        on_progress=None,
    ):
        self.source_param = source_id
        self._wanted = set(strategies) if strategies else None
        self.source = None
        self.strategies = []
        if source_id != "all":
            self._adopt(sources.get_source(source_id))
        self.on_progress = on_progress

        self.target = target.strip()
        self.depth = max(1, min(int(depth), config.RELATION_MAX_DEPTH))
        self.max_nodes = max(10, min(int(max_nodes), config.RELATION_MAX_NODES))

        self.nodes: list[dict] = []       # 按添加顺序排列，id = 下标
        self.edges: list[dict] = []
        self.node_index: dict[str, int] = {}
        self.edge_keys: set[str] = set()
        self._seen_co: set[str] = set()
        self._memo: dict[tuple[str, str, str], list] = {}
        self._searched_legal: set[str] = set()
        self._searched_holder: set[str] = set()
        self._searched_phone: set[str] = set()
        self._searched_invest: set[tuple] = set()
        self._enriched: set[str] = set()
        self._expanded_pids: set[str] = set()
        self.requests = 0
        self.truncated = False

    # ---------------- 基础 ----------------

    def _adopt(self, src) -> None:
        self.source = src
        self.strategies = [
            s for s in src.strategies
            if self._wanted is None or s in self._wanted
        ]

    def _wants(self, strategy: str) -> bool:
        return self._wanted is None or strategy in self._wanted

    def _request_budget(self) -> int:
        return max(40, self.max_nodes * 2)

    def _ssearch(self, src, angle: str, keyword: str, limit: int = 30) -> list[dict]:
        """按指定源的搜索；进程内记忆 + 礼貌间隔；预算耗尽抛 _BudgetStop。"""
        if self.requests >= self._request_budget():
            raise _BudgetStop()
        key = (src.id, angle, keyword.lower())
        if key not in self._memo:
            wait_interval(src)
            records = src.search(keyword, angle, limit=limit)
            self.requests += 1
            self._memo[key] = records
            if self.on_progress:
                self.on_progress(self.requests)
        return self._memo[key]

    def _enrich(self, rec: dict) -> dict:
        """Cookie 源（风鸟/爱企查）的补全钩子：填股东/对外投资明细。"""
        sid = rec.get("source") or (self.source.id if self.source else None)
        src = None
        try:
            src = sources.get_source(sid)
        except Exception:
            src = None
        fn = getattr(src, "enrich", None)
        if fn is None:
            return rec
        key = f"co:{self._company_key(rec)}"
        if key in self._enriched:
            return rec
        if self.requests >= self._request_budget():
            raise _BudgetStop()
        self._enriched.add(key)
        wait_interval(src)
        try:
            out = fn(rec)
        except Exception:
            return rec
        self.requests += 2
        if self.on_progress:
            self.on_progress(self.requests)
        return out if isinstance(out, dict) else rec

    def _add_node(self, type_: str, label: str, sub: str, payload: dict | None = None,
                  source: str | None = None, pid: str | None = None) -> int | None:
        key = f"{type_}:{label.lower()}"
        if key in self.node_index:
            return self.node_index[key]
        if len(self.nodes) >= self.max_nodes:
            self.truncated = True
            return None
        node = {
            "id": len(self.nodes),
            "type": type_,
            "label": label,
            "sub": sub,
            "source": source,
            **({"pid": pid} if pid else {}),
            **({"payload": payload} if payload else {}),
        }
        self.node_index[key] = node["id"]
        self.nodes.append(node)
        return node["id"]

    def _add_edge(self, s: int, t: int, etype: str, label: str = "") -> None:
        key = f"{s}|{t}|{etype}"
        if key in self.edge_keys:
            return
        self.edge_keys.add(key)
        self.edges.append({"s": s, "t": t, "type": etype, "label": label})

    def _company_key(self, rec: dict) -> str:
        code = clean_text(rec.get("creditCode", "")).replace(" ", "").lower()
        if code:
            return f"{code}@{rec.get('name', '').lower()}"
        return rec.get("name", "").lower()

    def _company_id(self, rec: dict) -> int | None:
        return self.node_index.get(f"co:{self._company_key(rec)}")

    # ---------------- 收录 ----------------

    def add_company(self, rec: dict, src) -> int | None:
        """收录一家企业（带来源）；自动补记录自带的法人/自然人股东关系边。"""
        key = f"co:{self._company_key(rec)}"
        if key in self.node_index:
            return self.node_index[key]
        if key in self._seen_co or len(self.nodes) >= self.max_nodes:
            if len(self.nodes) >= self.max_nodes:
                self.truncated = True
            return None
        self._seen_co.add(key)

        payload = {k: rec.get(k) for k in _CO_PAYLOAD_KEYS if rec.get(k) not in (None, "")}
        if len(self.nodes) >= self.max_nodes:
            self.truncated = True
            return None
        cid = len(self.nodes)
        self.nodes.append({
            "id": cid,
            "type": "co",
            "label": rec.get("name") or "未知企业",
            "sub": rec.get("regStatus") or rec.get("industry") or "",
            "source": src.id,
            "payload": payload,
        })
        self.node_index[key] = cid

        legal = clean_text(rec.get("legalPersonName", ""))
        if legal:
            pnode = self._add_node("p", legal, "", pid=rec.get("legalPersonId") or None)
            if pnode is not None:
                self._add_edge(cid, pnode, "legal")
        seen_p: set[str] = set()
        for holder in rec.get("shareholders") or []:
            name = clean_text(holder.get("name", "") if isinstance(holder, dict) else holder)
            if not name or name == legal or name in seen_p or not is_person_name(name):
                continue
            seen_p.add(name)
            if len(seen_p) > 5:
                break
            ph = holder.get("pid") if isinstance(holder, dict) else None
            pnode = self._add_node("p", name, "", pid=ph or None)
            if pnode is not None:
                pct = holder.get("percent", "") if isinstance(holder, dict) else ""
                self._add_edge(cid, pnode, "shareholder", pct or "")
        return cid

    # ---------------- 主流程 ----------------

    def build(self) -> dict:
        target = self.target
        if not target:
            raise ValueError("目标企业名不能为空")

        root: dict | None = None
        if self.source is None:
            # 聚合模式：按优先级找到能命中目标企业的源并采用它
            from .aggregate import REAL_ORDER
            low = target.lower()
            for cand in REAL_ORDER:
                try:
                    src = sources.get_source(cand)
                except Exception:
                    continue
                try:
                    wait_interval(src)
                    results = src.search(target, "企业名", limit=10)
                except Exception:
                    continue
                if not results:
                    continue
                exact = [r for r in results if r.get("name", "").lower() == low]
                root = (exact or results)[0]
                root["source"] = src.id
                self._adopt(src)
                self.requests += 1
                if self.on_progress:
                    self.on_progress(self.requests)
                break
            if self.source is None:
                raise ValueError(
                    f"所有数据源都未检索到【{target}】，请换关键词或指定具体数据源重试。"
                )
        else:
            for angle in ("企业名", "综合"):
                try:
                    results = self._ssearch(self.source, angle, target, limit=20)
                except _BudgetStop:
                    break
                if results:
                    low = target.lower()
                    exact = [r for r in results if r.get("name", "").lower() == low]
                    root = (exact or results)[0]
                    break
        if root is None:
            raise ValueError(f"未检索到目标企业【{target}】，请换关键词或数据源后重试。")

        root.setdefault("source", self.source.id)
        try:
            root = self._enrich(root)
        except _BudgetStop:
            pass

        root_id = self.add_company(root, sources.get_source(root["source"]))
        if root_id is None:
            raise ValueError("节点预算太小，无法完成分析。")

        queue: deque[tuple[int, dict, int]] = deque([(root_id, root, 1)])
        try:
            while queue and not self.truncated:
                me, rec, d = queue.popleft()
                rec = self._enrich(rec)
                self._expand(me, rec, d, queue)
        except _BudgetStop:
            self.truncated = True

        # 人员节点副标题：统计关联企业数（去重）
        person_cos: dict[int, set[int]] = {}
        for e in self.edges:
            if e["type"] in ("legal", "shareholder"):
                person_cos.setdefault(e["t"], set()).add(e["s"])
        for n in self.nodes:
            if n["type"] == "p":
                n["sub"] = f"关联 {len(person_cos.get(n['id'], set()))} 家企业"

        co_count = sum(1 for n in self.nodes if n["type"] == "co")
        p_count = len(self.nodes) - co_count
        return {
            "meta": {
                "target": target,
                "rootName": root.get("name", target),
                "rootNode": root_id,
                "source": self.source.id if self.source else None,
                "depth": self.depth,
                "maxNodes": self.max_nodes,
                "strategies": self.strategies,
                "requests": self.requests,
                "truncated": self.truncated,
                "stats": {
                    "companies": co_count,
                    "persons": p_count,
                    "edges": len(self.edges),
                },
            },
            "nodes": self.nodes,
            "edges": self.edges,
        }

    def _expand(
        self,
        me: int,
        rec: dict,
        d: int,
        queue: deque[tuple[int, dict, int]],
    ) -> None:
        """展开一个企业节点：使用该节点自己数据源支持的策略，缺的角度跨源补。"""
        co_name = rec.get("name", "")
        child_depth = d + 1
        do_child = child_depth <= self.depth
        legal = clean_text(rec.get("legalPersonName", ""))

        try:
            node_src = sources.get_source(rec.get("source") or (self.source.id if self.source else None))
        except Exception:
            return

        def absorb(result_rec: dict, src, person: str = "") -> None:
            """收录搜索命中；按人检索时只收确有任职/持股关系的企业。"""
            if result_rec.get("name", "").lower() == co_name.lower():
                return
            if person and not record_person_hit(result_rec, person):
                result_rec = self._enrich(result_rec)
                if not record_person_hit(result_rec, person):
                    return
            result_rec.setdefault("source", src.id)
            cid = self.add_company(result_rec, src)
            if cid is not None and do_child:
                queue.append((cid, result_rec, child_depth))

        def fallback_for(angle: str):
            """节点源不支持该角度时，落到支持的天眼查。"""
            if angle in node_src.angles:
                return node_src
            try:
                tyc = sources.get_source("tyc")
                if angle in tyc.angles:
                    return tyc
            except Exception:
                pass
            return None

        # 1) 同法人展开（节点源不支持“法人”角度时自动跨源到天眼查）
        if legal and self._wants("legal"):
            lsrc = fallback_for("法人")
            if lsrc and legal not in self._searched_legal:
                self._searched_legal.add(legal)
                try:
                    for r in self._ssearch(lsrc, "法人", legal):
                        absorb(r, lsrc, person=legal)
                except SourceError:
                    pass

        # 2) 股东展开（仅当源真的支持“股东”角度；综合搜索按人名是噪声，跳过）
        if self._wants("shareholder") and "股东" in node_src.angles:
            for holder in rec.get("shareholders") or []:
                hname = clean_text(holder.get("name", "") if isinstance(holder, dict) else holder)
                if not hname or hname == legal or hname in self._searched_holder:
                    continue
                if not is_person_name(hname):
                    continue
                self._searched_holder.add(hname)
                try:
                    for r in self._ssearch(node_src, "股东", hname):
                        absorb(r, node_src, person=hname)
                except SourceError:
                    pass

        # 3) 同电话展开（完整未脱敏且数字一致才算）
        phone = clean_text(rec.get("phone", ""))
        if phone and self._wants("phone"):
            psrc = fallback_for("电话")
            if psrc and phone not in self._searched_phone:
                self._searched_phone.add(phone)
                try:
                    phone_results = self._ssearch(psrc, "电话", phone)
                except SourceError:
                    phone_results = []
                for r in phone_results:
                    if phones_equal(phone, r.get("phone", "")):
                        cid = self.add_company(r, psrc)
                        if cid is not None:
                            if do_child and cid != me:
                                queue.append((cid, r, child_depth))
                            if cid != me:
                                self._add_edge(me, cid, "phone", phone)
                        else:
                            break

        # 4) 自然人/高管展开：节点带风鸟 personId 时，用“人员任职企业”精确展开
        #       （只加节点与角色边，不加深公司层级，因此不受 depth 限制）
        self._expand_persons(rec, me)

        # 5) 对外投资展开（依赖节点 enrich 出的 investCompanies；同一母公司的同一被投企业只查一次）
        if "invest" in node_src.strategies and self._wants("invest"):
            for child in rec.get("investCompanies") or []:
                child = clean_text(child if isinstance(child, str) else child.get("name", ""))
                if not child or (me, child) in self._searched_invest:
                    continue
                self._searched_invest.add((me, child))
                try:
                    results = self._ssearch(node_src, "企业名", child, limit=10)
                except SourceError:
                    continue
                best = next(
                    (r for r in results if r.get("name", "").lower() == child.lower()),
                    results[0] if results else None,
                )
                if best is None:
                    continue
                cid = self.add_company(best, node_src)
                if cid is not None and cid != me:
                    if do_child:
                        queue.append((cid, best, child_depth))
                    self._add_edge(me, cid, "invest", child)


    def _expand_persons(self, rec: dict, me: int) -> None:
        """把该公司记录里带 pid 的自然人（法代/股东）用风鸟“人员任职企业”展开。"""
        if not (self._wants("legal") or self._wants("shareholder") or self._wants("position")):
            return
        try:
            rb = sources.get_source("rb")
        except Exception:
            return
        if not hasattr(rb, "person_companies"):
            return

        legal = clean_text(rec.get("legalPersonName", ""))
        candidates: list[tuple[str, str, str]] = []  # (姓名, pid, 默认边型)
        if legal and rec.get("legalPersonId"):
            candidates.append((legal, rec["legalPersonId"], "legal"))
        for holder in rec.get("shareholders") or []:
            name = clean_text(holder.get("name", "") if isinstance(holder, dict) else holder)
            if not name or name == legal or not is_person_name(name):
                continue
            pid = holder.get("pid") if isinstance(holder, dict) else None
            if pid:
                candidates.append((name, pid, "shareholder"))
            if len(candidates) >= 4:
                break
        # 同一人员（pid）在多个公司节点出现时只展开一次
        candidates = [(n, p, ty) for n, p, ty in candidates
                      if p not in self._expanded_pids and not self._expanded_pids.add(p)]

        for pname, ppid, default_type in candidates:
            if self.requests >= self._request_budget():
                raise _BudgetStop()
            wait_interval(rb)
            try:
                companies = rb.person_companies(ppid, limit=15)
            except Exception:
                continue
            self.requests += 2
            if self.on_progress:
                self.on_progress(self.requests)
            pnode = self.node_index.get(f"p:{pname.lower()}")
            # 该人员在本公司的精确高管职务（如“董事长、董事”）→ 补任职边
            me_name = (rec.get("name") or "").lower()
            hit_me = next((c for c in companies if (c.get("name") or "").lower() == me_name), None)
            extra_role = ""
            if hit_me:
                role = (hit_me.get("role") or "")
                extra = role.replace("法定代表人", "").replace("股东", "").strip("、，, ")
                if extra:
                    extra_role = extra
            if extra_role and pnode is not None:
                self._add_edge(me, pnode, "position", extra_role)
            for co in companies:
                name = co.get("name", "")
                if not name:
                    continue
                cid = self._company_id(co)
                if cid is None:
                    co.setdefault("source", "rb")
                    cid = self.add_company(co, rb)
                    if cid is None:
                        continue
                if pnode is None:
                    pnode = self._add_node("p", pname, "")
                if pnode is not None:
                    role = co.get("role") or ""
                    if "法定代表人" in role or (co.get("legalPersonName") or "") == pname:
                        self._add_edge(cid, pnode, "legal")
                    elif "股东" in role:
                        self._add_edge(cid, pnode, "shareholder")
                    else:
                        self._add_edge(cid, pnode, "position", co.get("position") or role)

def build_relation_graph(
    source_id: str,
    target: str,
    depth: int = 2,
    max_nodes: int = 40,
    strategies: list[str] | None = None,
    on_progress=None,
) -> dict:
    return RelationBuilder(
        source_id, target, depth=depth, max_nodes=max_nodes,
        strategies=strategies, on_progress=on_progress,
    ).build()
