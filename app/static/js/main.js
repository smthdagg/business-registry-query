/**
 * 工商企业聚合信息查询系统 - 前端（v2：页面导航式 SPA，hash 路由）。
 *
 * 路由：
 *   #/search                快速查询（默认）
 *   #/company?name=xxx      公司档案页（基本信息/股东/对外投资/分支机构/主要人员）
 *   #/person?name=xxx       人员页（名下企业）
 *   #/relation?target=xxx   关联图谱
 *   #/batch                 批量查询
 *   #/tasks                 任务中心
 *   #/task?id=N             任务详情（批量结果/图谱结果）
 *
 * 设计原则：所有实体都是页面，所有字段都是链接，浏览器前进后退可用。
 */
(function () {
  "use strict";

  // ---------------- 工具 ----------------
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const enc = encodeURIComponent;

  const state = {
    cfg: null,
    srcMeta: null,
    role: "admin",
    user: "admin",
    lastSearch: { records: [] },
    tasksTimer: null,
    relPoll: null,
    graphView: null,
  };

  const STATUS_CHIP = {
    存续: "tag-ok", 在业: "tag-ok", 开业: "tag-ok", 正常: "tag-ok", 在营: "tag-ok", 仍注册: "tag-ok",
    注销: "tag-err", 吊销: "tag-err", 迁出: "tag-err", 停业: "tag-err",
    已注销: "tag-err", 已吊销: "tag-err", 已告解散: "tag-err",
  };
  const TASK_STATUS = {
    queued: ["排队中", "tag-queued"], running: ["执行中", "tag-run"],
    done: ["已完成", "tag-done"], error: ["失败", "tag-err"],
    cancelled: ["已取消", "tag-stop"], cancelling: ["取消中", "tag-run"],
    interrupted: ["已中断", "tag-stop"],
  };
  const KIND_LABEL = { batch: ["批量查询", "kind-batch"], relation: ["关联图谱", "kind-relation"] };
  const SOURCE_SHORT = { rb: "风鸟", c88: "88查", tyc: "天眼查", aqc: "爱企查", mock: "演示", all: "聚合", search: "搜索层", profile: "档案" };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function fmtVal(v) {
    if (v == null || v === "") return "";
    if (Array.isArray(v)) return v.map((x) => (x && typeof x === "object" ? fmtObj(x) : String(x))).join("、");
    if (typeof v === "object") return fmtObj(v);
    return String(v);
  }
  function fmtObj(o) {
    if ("name" in o && "percent" in o) return `${o.name}（${o.percent}）`;
    try { return JSON.stringify(o); } catch (_) { return String(o); }
  }
  function labelOf(key) {
    const f = (state.cfg && state.cfg.fields || []).find((x) => x.key === key);
    return f ? f.label : key;
  }
  function sourceLabel(id) {
    const s = (state.cfg.sources || []).find((x) => x.id === id);
    return s ? s.label : id;
  }
  function sourceShort(id) { return SOURCE_SHORT[id] || sourceLabel(id); }
  function toast(msg, isErr) {
    const el = $("#toast");
    el.textContent = msg;
    el.className = "toast" + (isErr ? " err" : "");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 3000);
  }
  class ApiError extends Error {}

  async function api(path, opts = {}) {
    const o = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) {
      o.headers["Content-Type"] = "application/json";
      o.body = JSON.stringify(opts.body);
    }
    let res;
    try { res = await fetch(path, o); }
    catch (e) { throw new ApiError("网络错误：" + e.message); }
    let data = null;
    try { data = await res.json(); } catch (_) { /* 空响应 */ }
    if (res.status === 401 && !path.startsWith("/api/login")) {
      if (opts.noAuthUI !== true) showLogin();
      throw new ApiError((data && data.detail) || "未登录或会话已过期");
    }
    if (!res.ok) throw new ApiError((data && data.detail) || ("HTTP " + res.status));
    return data;
  }

  // 记录缓存：搜索结果 → 公司页秒开
  function cacheRecord(rec) {
    if (!rec || !rec.name) return;
    try { sessionStorage.setItem("gs:rec:" + rec.name, JSON.stringify(rec)); } catch (_) {}
  }
  function readCachedRecord(name) {
    try { return JSON.parse(sessionStorage.getItem("gs:rec:" + name) || "null"); } catch (_) { return null; }
  }

  // ---------------- 登录 ----------------
  function showLogin() {
    if (state.cfg && state.cfg.auth === false) return;
    $("#login-mask").classList.remove("hidden");
    setTimeout(() => $("#login-pass").focus(), 50);
  }


  // ---------------- 监控名单 ----------------
  async function renderWatchlistPage() {
    app(`<div class="card"><div class="card-title">监控名单 <span class="dim">（对关注企业定期检查，状态/法人/资本变化自动记录）</span></div>
      <form id="watch-form" class="form-row" style="margin-bottom:12px">
        <input id="watch-name" class="input grow" placeholder="输入要监控的企业名称">
        <input id="watch-note" class="input grow" placeholder="备注（可选）">
        <button class="btn btn-primary" type="submit">加入监控</button>
      </form>
      <div id="watch-list"></div></div>`);
    $("#watch-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const name = $("#watch-name").value.trim();
      if (!name) { toast("请输入企业名称"); return; }
      try {
        await api("/api/watchlist", { method: "POST", body: { company_name: name, note: $("#watch-note").value.trim() } });
        toast("已加入监控：" + name);
        $("#watch-name").value = ""; $("#watch-note").value = "";
        loadWatchlist();
      } catch (e) { toast("加入失败：" + e.message, true); }
    });
    loadWatchlist();
  }

  async function loadWatchlist() {
    let data;
    try { data = await api("/api/watchlist"); }
    catch (e) { $("#watch-list").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
    const items = data.items || [];
    if (!items.length) { $("#watch-list").innerHTML = '<div class="dim">暂无监控企业。</div>'; return; }
    $("#watch-list").innerHTML = items.map((w) => `
      <div class="task-item" data-id="${w.id}">
        <div class="task-head">
          <span class="task-title"><a class="table-link" href="#/company?name=${enc(w.company_name)}">${esc(w.company_name)}</a></span>
          ${w.last_status ? `<span class="tag-ok">已检查</span>` : `<span class="tag-queued">未检查</span>`}
          <span class="dim" style="font-size:12px">${esc(w.note || "")}</span>
        </div>
        <div class="task-meta">加入于 ${esc(w.created_at || "")}${w.last_checked_at ? " · 最近检查 " + esc(w.last_checked_at) : ""} · 变化次数 <b>${w.changes_count || 0}</b></div>
        <div class="task-actions">
          <button class="btn btn-sm btn-primary act-check">立即检查</button>
          <button class="btn btn-sm act-remove">移除</button>
          <span class="dim act-result"></span>
        </div>
      </div>`).join("");
    $("#watch-list").querySelectorAll(".task-item").forEach((card) => {
      const id = card.dataset.id;
      const result = card.querySelector(".act-result");
      card.querySelector(".act-check").addEventListener("click", async () => {
        result.textContent = "检查中…";
        try {
          const r = await api(`/api/watchlist/${id}/check`, { method: "POST" });
          result.textContent = r.changed ? `✅ 有变化（累计 ${r.changes_count} 次）` : `✅ 无变化（累计 ${r.changes_count} 次）`;
          loadWatchlist();
        } catch (e) { result.textContent = "❌ " + e.message; }
      });
      card.querySelector(".act-remove").addEventListener("click", async () => {
        try { await api(`/api/watchlist/${id}`, { method: "DELETE" }); loadWatchlist(); }
        catch (e) { toast("移除失败：" + e.message, true); }
      });
    });
  }

  // ---------------- 操作日志（管理员） ----------------
  async function renderLogsPage() {
    app(`<div class="card"><div class="card-title">操作日志 <span class="dim">（登录/查询/档案/任务/设置/监控）</span></div>
      <div id="logs-list"></div></div>`);
    let data;
    try { data = await api("/api/logs?limit=300"); }
    catch (e) { $("#logs-list").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
    const items = data.items || [];
    $("#logs-list").innerHTML = `<table class="kv-table">
      <thead><tr><th>时间</th><th>用户/角色</th><th>操作</th><th>详情</th></tr></thead>
      <tbody>${items.map((l) => `<tr>
        <td>${esc(l.ts)}</td><td>${esc(l.user || "")}${l.role ? "（" + esc(l.role) + "）" : ""}</td>
        <td>${esc(l.action)}</td><td>${esc(l.detail || "")}</td>
      </tr>`).join("")}</tbody></table>`;
  }

  // ---------------- 启动 ----------------
  async function boot() {
    bindStatic();
    try { state.cfg = await api("/api/config"); }
    catch (e) {
      if (e instanceof ApiError && e.message.includes("未登录")) { showLogin(); return; }
      toast("初始化失败：" + e.message, true);
      return;
    }
    if (state.cfg.auth) $("#logout-btn").classList.remove("hidden");
    try {
      const me = await api("/api/me");
      state.role = me.role || "admin";
      state.user = me.username || "";
    } catch (_) { state.role = "admin"; }
    const badge = $("#role-badge");
    if (badge) badge.textContent = `${state.user}（${state.role === "admin" ? "管理员" : "会员"}）`;
    applySource(state.cfg.currentSource);
    route();
  }

  function bindStatic() {
    $("#login-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const pass = $("#login-pass").value;
      const role = $("#login-role")?.value || "admin";
      const username = ($("#login-user")?.value || "").trim() || (role === "admin" ? "admin" : "member");
      try { await api("/api/login", { method: "POST", body: { username, password: pass, role } }); }
      catch (e) { $("#login-err").textContent = e.message; return; }
      // 登录成功：整页刷新，用新会话完整启动（原地换状态易残留登录遮罩）
      location.reload();
    });
    $("#logout-btn").addEventListener("click", async () => {
      await api("/api/logout", { method: "POST" });
      location.reload();
    });
    window.addEventListener("hashchange", route);
    window.addEventListener("resize", () => {
      if (state.graphView) { state.graphView._resize(); state.graphView.render(); }
    });
  }

  function applySource(sid) {
    const meta = state.cfg.sources.find((s) => s.id === sid) || state.cfg.sources[0];
    state.srcMeta = meta;
  }


  // ---------------- 筛选工具：省/市 提取 + 行业门类映射 ----------------
  const MUNI = ["北京市", "上海市", "天津市", "重庆市"];
  const PROV_OF_CITY = {
    "合肥市":"安徽省","芜湖市":"安徽省","蚌埠市":"安徽省","阜阳市":"安徽省","宿州市":"安徽省","安庆市":"安徽省","滁州市":"安徽省","六安市":"安徽省","宣城市":"安徽省","亳州市":"安徽省","池州市":"安徽省","黄山市":"安徽省","淮南市":"安徽省","淮北市":"安徽省","铜陵市":"安徽省","马鞍山市":"安徽省",
    "深圳市":"广东省","广州市":"广东省","东莞市":"广东省","佛山市":"广东省","珠海市":"广东省","惠州市":"广东省","中山市":"广东省","汕头市":"广东省","江门市":"广东省","湛江市":"广东省","茂名市":"广东省",
    "杭州市":"浙江省","宁波市":"浙江省","温州市":"浙江省","嘉兴市":"浙江省","绍兴市":"浙江省","金华市":"浙江省","台州市":"浙江省","湖州市":"浙江省","义乌市":"浙江省",
    "苏州市":"江苏省","南京市":"江苏省","无锡市":"江苏省","南通市":"江苏省","常州市":"江苏省","徐州市":"江苏省","扬州市":"江苏省","泰州市":"江苏省","昆山市":"江苏省","江阴市":"江苏省","张家港市":"江苏省","常熟市":"江苏省","太仓市":"江苏省","仪征市":"江苏省",
    "济南市":"山东省","青岛市":"山东省","烟台市":"山东省","潍坊市":"山东省","临沂市":"山东省","济宁市":"山东省","淄博市":"山东省","泰安市":"山东省","威海市":"山东省","莱芜市":"山东省",
    "成都市":"四川省","绵阳市":"四川省","宜宾市":"四川省","自贡市":"四川省","泸州市":"四川省",
    "武汉市":"湖北省","宜昌市":"湖北省","襄阳市":"湖北省","孝感市":"湖北省","荆州市":"湖北省","黄冈市":"湖北省",
    "长沙市":"湖南省","株洲市":"湖南省","常德市":"湖南省","岳阳市":"湖南省","衡阳市":"湖南省","郴州市":"湖南省","益阳市":"湖南省",
    "郑州市":"河南省","洛阳市":"河南省","开封市":"河南省","新乡市":"河南省","南阳市":"河南省","许昌市":"河南省","商丘市":"河南省","周口市":"河南省","驻马店市":"河南省","濮阳市":"河南省","平顶山市":"河南省",
    "福州市":"福建省","厦门市":"福建省","泉州市":"福建省","莆田市":"福建省","漳州市":"福建省",
    "西安市":"陕西省","咸阳市":"陕西省","宝鸡市":"陕西省","渭南市":"陕西省","榆林市":"陕西省","汉中市":"陕西省","延安市":"陕西省",
    "石家庄市":"河北省","唐山市":"河北省","保定市":"河北省","廊坊市":"河北省","邯郸市":"河北省","沧州市":"河北省","邢台市":"河北省","秦皇岛市":"河北省","张家口市":"河北省","衡水市":"河北省",
    "昆明市":"云南省","曲靖市":"云南省","玉溪市":"云南省","丽江市":"云南省","普洱市":"云南省","保山市":"云南省","昭通市":"云南省","临沧市":"云南省",
    "贵港市":"广西壮族自治区","南宁市":"广西壮族自治区","柳州市":"广西壮族自治区","桂林市":"广西壮族自治区","玉林市":"广西壮族自治区","百色市":"广西壮族自治区","贺州市":"广西壮族自治区","防城港市":"广西壮族自治区","钦州市":"广西壮族自治区","北海市":"广西壮族自治区",
    "南昌市":"江西省","赣州市":"江西省","九江市":"江西省","上饶市":"江西省","宜春市":"江西省","吉安市":"江西省","抚州市":"江西省","景德镇市":"江西省","萍乡市":"江西省","新余市":"江西省","鹰潭市":"江西省",
    "沈阳市":"辽宁省","大连市":"辽宁省","鞍山市":"辽宁省","营口市":"辽宁省","锦州市":"辽宁省","朝阳市":"辽宁省","铁岭市":"辽宁省","葫芦岛市":"辽宁省","盘锦市":"辽宁省","阜新市":"辽宁省","辽阳市":"辽宁省","本溪市":"辽宁省","抚顺市":"辽宁省","丹东市":"辽宁省",
    "太原市":"山西省","晋城市":"山西省","大同市":"山西省","运城市":"山西省","长治市":"山西省","临汾市":"山西省","吕梁市":"山西省","晋中市":"山西省","忻州市":"山西省","朔州市":"山西省","阳泉市":"山西省",
    "贵阳市":"贵州省","遵义市":"贵州省","六盘水市":"贵州省","安顺市":"贵州省","毕节市":"贵州省","铜仁市":"贵州省",
    "昆明市":"云南省",
    "哈尔滨市":"黑龙江省","齐齐哈尔市":"黑龙江省","大庆市":"黑龙江省","牡丹江市":"黑龙江省","绥化市":"黑龙江省","黑河市":"黑龙江省","鸡西市":"黑龙江省","鹤岗市":"黑龙江省","双鸭山市":"黑龙江省","伊春市":"黑龙江省","七台河市":"黑龙江省","佳木斯市":"黑龙江省",
    "长春市":"吉林省","吉林市":"吉林省","四平市":"吉林省","通化市":"吉林省","白山市":"吉林省","松原市":"吉林省","白城市":"吉林省","辽源市":"吉林省",
    "海口市":"海南省","三亚市":"海南省","儋州市":"海南省",
    "呼和浩特市":"内蒙古自治区","包头市":"内蒙古自治区","鄂尔多斯市":"内蒙古自治区","赤峰市":"内蒙古自治区","呼伦贝尔市":"内蒙古自治区","通辽市":"内蒙古自治区","巴彦淖尔市":"内蒙古自治区","乌兰察布市":"内蒙古自治区",
    "银川市":"宁夏回族自治区","石嘴山市":"宁夏回族自治区","吴忠市":"宁夏回族自治区","固原市":"宁夏回族自治区","中卫市":"宁夏回族自治区",
    "乌鲁木齐市":"新疆维吾尔自治区","克拉玛依市":"新疆维吾尔自治区","阿拉尔市":"新疆维吾尔自治区","库尔勒市":"新疆维吾尔自治区","喀什市":"新疆维吾尔自治区","伊宁市":"新疆维吾尔自治区","塔城市":"新疆维吾尔自治区","哈密市":"新疆维吾尔自治区","昌吉市":"新疆维吾尔自治区","阿克苏市":"新疆维吾尔自治区","和田市":"新疆维吾尔自治区","吐鲁番市":"新疆维吾尔自治区",
    "西宁市":"青海省","海东市":"青海省",
    "拉萨市":"西藏自治区","日喀则市":"西藏自治区","林芝市":"西藏自治区","山南市":"西藏自治区","昌都市":"西藏自治区",
    "兰州市":"甘肃省","天水市":"甘肃省","白银市":"甘肃省","庆阳市":"甘肃省","酒泉市":"甘肃省","张掖市":"甘肃省","武威市":"甘肃省","定西市":"甘肃省","陇南市":"甘肃省","嘉峪关市":"甘肃省","金昌市":"甘肃省","平凉市":"甘肃省",
    "北京市":"北京市","上海市":"上海市","天津市":"天津市","重庆市":"重庆市",
  };
  function extractRegion(rec) {
    const s = String(rec.regLocation || rec.regionIdCn || rec.city || "").trim();
    if (!s) return { province: "", city: "" };
    if (MUNI.some((m) => s.startsWith(m))) {
      const m0 = MUNI.find((m) => s.startsWith(m));
      return { province: m0, city: m0 };
    }
    let province = "", city = "";
    const pm = s.match(/([\u4e00-\u9fa5]{2,8}?(?:省|自治区))/);
    if (pm) province = pm[1];
    const cm = s.match(/([\u4e00-\u9fa5]{1,8}?(?:市|地区|盟|自治州))/);
    if (cm) city = cm[1];
    if (!province && city) province = PROV_OF_CITY[city] || "";
    if (!city && province) {
      const tail = s.slice(s.indexOf(province) + province.length);
      const cm2 = tail.match(/^([\u4e00-\u9fa5]{2,8}?(?:市|地区|盟|自治州))/);
      if (cm2) city = cm2[1];
    }
    return { province, city };
  }
  const INDUSTRY_TOP = [
    ["信息传输、软件和信息技术服务业", ["软件","信息技术","互联网","电信","数据处理","网络科技","电子商务","信息系统","物联网","人工智能","科技推广和应用服务"]],
    ["科学研究和技术服务业", ["科学研究","科技推广","技术服务","工程技术","检测","勘察","专业设计服务","研究与试验发展"]],
    ["建筑业", ["建筑","土木","装饰","装修","安装","工程勘察","房屋建筑","市政","园林绿化"]],
    ["批发和零售业", ["批发","零售","贸易","商贸","销售","超市","便利店","百货"]],
    ["制造业", ["制造","生产","加工","冶炼","纺织","印刷","医药制造","食品制造","家具","汽配","电子设备制造","装备制造"]],
    ["电力、热力、燃气及水生产和供应业", ["电力","热力","燃气","自来水","水生产和供应"]],
    ["交通运输、仓储和邮政业", ["运输","物流","仓储","邮政","快递","货运","铁路","公路","航空运输","水运","港口","多式联运","运输代理"]],
    ["住宿和餐饮业", ["住宿","餐饮","酒店","旅馆","饭店","民宿"]],
    ["金融业", ["银行","保险","证券","基金","金融","融资担保","小额贷款","典当","保理"]],
    ["房地产业", ["房地产","地产开发","物业管理","房屋租赁"]],
    ["租赁和商务服务业", ["租赁","商务服务","咨询","广告","会展","人力资源","企业管理","园区管理","供应链"]],
    ["水利、环境和公共设施管理业", ["水利","环境治理","公共设施","环卫","环保工程","市政设施"]],
    ["居民服务、修理和其他服务业", ["居民服务","修理","家政","理发","洗染","殡葬","美容美发"]],
    ["教育", ["教育","培训","学校","幼儿园","教育咨询"]],
    ["卫生和社会工作", ["医院","卫生","医疗","诊所","康复","养老","社会工作","健康管理"]],
    ["文化、体育和娱乐业", ["文化","体育","娱乐","影视","传媒","广播","艺术","体育场馆","健身"]],
    ["农林牧渔业", ["农业","林业","畜牧","渔业","种植","养殖","农产品"]],
    ["采矿业", ["采矿","开采","煤炭","石油和天然气","矿采选"]],
  ];
  function industryCate(ind) {
    const s = String(ind || "");
    if (!s) return "";
    for (const [cate, kws] of INDUSTRY_TOP) {
      for (const kw of kws) if (s.includes(kw)) return cate;
    }
    return "其他行业";
  }

  // ---------------- 路由 ----------------
  function parseHash() {
    const h = location.hash || "#/search";
    const [path, qs] = h.slice(1).split("?");
    const params = new URLSearchParams(qs || "");
    return { path: path || "/search", params };
  }

  function route() {
    const { path, params } = parseHash();
    if (state.tasksTimer) { clearInterval(state.tasksTimer); state.tasksTimer = null; }
    $$(".tab-btn").forEach((b) => b.classList.toggle("on", b.dataset.nav === path.slice(1)));
    window.scrollTo({ top: 0 });
    switch (path) {
      case "/company": return renderCompanyPage(params.get("name") || "");
      case "/person": return renderPersonPage(
        params.get("name") || "", params.get("pid") || "", params.get("region") || "");
      case "/relation": return renderRelationPage(params.get("target") || "");
      case "/batch": return renderBatchPage();
      case "/tasks": return renderTasksPage();
      case "/task": return renderTaskDetailPage(parseInt(params.get("id") || "0", 10));
      case "/settings": return renderSettingsPage();
      case "/watchlist": return renderWatchlistPage();
      case "/logs": return renderLogsPage();
      case "/": case "/home": return renderSearchPage();
      default: return renderSearchPage();
    }
  }

  function app(html) { $("#app").innerHTML = html; }
  function spinner(text) { return `<div class="card"><div class="meta-line"><span class="spinner"></span>${esc(text)}</div></div>`; }

  // ---------------- 快速查询页 ----------------
  function renderSearchPage() {
    const meta = state.srcMeta || {};
    const angles = meta.angles || ["综合"];
    const quick = [
      ["查企业", "企业名", "输入企业名称，如：华为技术有限公司"],
      ["查法人", "法人", "输入法定代表人姓名"],
      ["查股东", "股东", "输入股东姓名"],
      ["查电话", "电话", "输入联系电话"],
      ["查信用代码", "信用代码", "输入统一社会信用代码"],
    ];
    app(`
      <div class="hero card">
        <div class="hero-title">工商企业聚合信息查询系统</div>
        <div class="hero-sub">聚合 88查 · 天眼查 · 风鸟 · 爱企查，一次查询多源比对</div>
        <form id="search-form" class="hero-search form-row">
          <input id="q" class="input grow" placeholder="企业名 / 法人 / 信用代码 / 电话…" autocomplete="off">
          <select id="angle" class="select w140">${angles.map((a) => `<option value="${a}">${a}</option>`).join("")}</select>
          <button class="btn btn-primary btn-lg" type="submit">查 询</button>
        </form>
        <div class="quick-cats">
          ${quick.map(([label, angle, ph]) => `<button class="chip" data-angle="${angle}" data-ph="${esc(ph)}">${label}</button>`).join("")}
        </div>
      </div>
      <div id="search-result"></div>
      <div class="card">
        <div class="card-title" style="display:flex;justify-content:space-between;align-items:center">
          <span>最近查询 <span class="dim">（点条目可再次查询）</span></span>
          <button class="btn btn-sm" id="history-clear">清空历史</button>
        </div>
        <div id="history-list" class="chip-list"></div>
      </div>`);
    const hc = $("#history-clear");
    if (hc) hc.addEventListener("click", async () => {
      if (!confirm("确认清空全部历史查询记录？")) return;
      try { await api("/api/history", { method: "DELETE" }); toast("历史已清空"); refreshHistory(); }
      catch (e) { toast("清空失败：" + e.message, true); }
    });
    const hl = $("#history-list");
    if (hl) hl.addEventListener("click", async (ev) => {
      const del = ev.target.closest(".chip-del");
      if (del) {
        ev.stopPropagation();
        const id = del.dataset.id;
        try { await api("/api/history/" + id, { method: "DELETE" }); refreshHistory(); }
        catch (e) { toast("删除失败：" + e.message, true); }
        return;
      }
      const chip = ev.target.closest(".chip[data-kw]");
      if (!chip) return;
      $("#q").value = chip.dataset.kw;
      $("#angle").value = chip.dataset.angle || "综合";
      $("#search-form").requestSubmit();
    });
    $("#app").querySelectorAll(".quick-cats .chip").forEach((c) => {
      c.addEventListener("click", () => {
        $("#angle").value = c.dataset.angle;
        $("#q").placeholder = c.dataset.ph;
        $("#q").focus();
      });
    });
    $("#search-form").addEventListener("submit", doSearch);
    $("#app").addEventListener("click", onDrillClick);
    $("#history-list").addEventListener("click", (ev) => {
      const chip = ev.target.closest(".chip[data-kw]");
      if (!chip) return;
      $("#q").value = chip.dataset.kw;
      $("#angle").value = chip.dataset.angle || "综合";
      $("#search-form").requestSubmit();
    });
    const prefill = sessionStorage.getItem("gs:prefill");
    if (prefill) {
      sessionStorage.removeItem("gs:prefill");
      try {
        const p = JSON.parse(prefill);
        $("#q").value = p.kw || "";
        if (p.angle) $("#angle").value = p.angle;
        doSearch(new Event("submit"));
      } catch (_) {}
    }
    refreshHistory();
  }

  function prefillSearch(kw, angle) {
    sessionStorage.setItem("gs:prefill", JSON.stringify({ kw, angle }));
    location.hash = "#/search";
  }

  async function doSearch(ev) {
    ev.preventDefault();
    const kw = $("#q").value.trim();
    if (!kw) { toast("请输入查询关键词"); return; }
    const box = $("#search-result");
    box.innerHTML = `<div class="card"><div class="meta-line"><span class="spinner"></span>正在并行查询全部数据源…</div></div>`;
    try {
      const res = await api("/api/search", {
        method: "POST",
        body: { keyword: kw, angle: $("#angle").value, source: state.cfg.currentSource, limit: 100 },
      });
      renderSearchResult(res);
    } catch (e) {
      box.innerHTML = "";
      toast("查询失败：" + e.message, true);
    }
  }

  function statusChip(status) {
    const cls = STATUS_CHIP[String(status || "")];
    if (!cls) return esc(status || "");
    return `<span class="${cls}">${esc(status)}</span>`;
  }

  function renderSearchResult(res) {
    const box = $("#search-result");
    if (!res.ok) {
      box.innerHTML = `<div class="card banner err">查询失败：${esc(res.error || "未知错误")}</div>`;
      return;
    }
    const records = res.results || [];
    state.lastSearch.records = records;
    records.forEach(cacheRecord);
    const isAgg = res.source === "all";
    const srcLabel = isAgg ? "聚合查询" : (sourceLabel(res.source) || "");
    const cached = res.cached ? '<span class="tag-queued">缓存命中</span>' : "";
    let aggChips = "";
    if (isAgg && (res.sourcesDetail || []).length) {
      aggChips = `<div class="meta-line">` + (res.sourcesDetail || []).map((d) => {
        const name = sourceShort(d.id);
        if (d.ok) return `<span class="stats-chip" title="耗时 ${d.elapsed}s">${esc(name)} ${d.count}</span>`;
        return `<span class="stats-chip dim" title="${esc(d.error || "未配置 Cookie 已跳过")}">${esc(name)} 跳过</span>`;
      }).join(" ") + `</div>`;
    }
    const limitNote = !isAgg && res.total && res.total > records.length
      ? `<span class="tag-queued">接口单次仅显示前 ${records.length}/${res.total} 条</span>` : "";
    if (!records.length) {
      const totalHint = res.total ? `（总命中 ${res.total} 条，但按该角度过滤后无匹配）` : "";
      box.innerHTML = `<div class="card banner warn">未查询到【${esc(res.keyword)}】相关企业（角度：${esc(res.angle || "综合")} / ${esc(srcLabel)}）${totalHint}${cached}${aggChips}</div>`;
      return;
    }
    const cols = state.cfg.listColumns || [["name", "公司名称"]];
    const head = (isAgg ? "<th>来源</th>" : "") + cols.map(([k]) => `<th>${esc(labelOf(k))}</th>`).join("") + "<th>操作</th>";
    const body = records.map((r, i) => {
      const isPerson = r.type === "person";
      const srcCell = isAgg
        ? `<td>${(r.sources || []).map((x) => `<span class="kind-chip kind-batch" style="margin:0 3px 0 0">${esc(sourceShort(x))}</span>`).join("")}</td>` : "";
      if (isPerson) {
        // 人员条目（法人角度）：👤 姓名 + 任职数 + 点进名下企业
        return `<tr data-idx="${i}">${srcCell}
          <td class="c-name">👤 <a class="table-link" href="#/person?name=${enc(r.name || "")}&pid=${enc(r.pid || "")}">${esc(r.name)}</a></td>
          <td>${esc(r.matchType || "人员")}</td>
          <td>—</td>
          <td title="任职 ${r.count || 0} 家">任职 ${r.count || 0} 家</td>
          <td title="${esc(r.maxCompany || "")}">${esc((r.maxCompany || "").slice(0, 18))}</td>
          <td></td>
          <td><div class="row-actions">
            <a class="btn btn-sm btn-primary" href="#/person?name=${enc(r.name || "")}&pid=${enc(r.pid || "")}">名下企业</a>
          </div></td></tr>`;
      }
      const cells = cols.map(([k]) => {
        if (k === "regStatus") return `<td>${statusChip(r[k])}</td>`;
        if (k === "name") return `<td class="c-name"><a class="table-link" href="#/company?name=${enc(r[k] || "")}">${esc(r[k])}</a></td>`;
        return `<td title="${esc(fmtVal(r[k]))}">${esc(fmtVal(r[k]))}</td>`;
      }).join("");
      return `<tr data-idx="${i}">${srcCell}${cells}<td><div class="row-actions">
        <a class="btn btn-sm" href="#/company?name=${enc(r.name || "")}">档案</a>
        <a class="btn btn-sm" href="#/relation?target=${enc(r.name || "")}" title="围绕该企业分析关联关系">关联</a>
      </div></td></tr>`;
    }).join("");
    box.innerHTML = `<div class="card">
      <div class="meta-line">数据源 <b>${esc(srcLabel)}</b> · 角度 <b>${esc(res.angle || "综合")}</b> · 合并命中 <b>${records.length}</b> 条 ${limitNote}${cached}</div>
      ${aggChips}
      <div class="filter-bar" id="filter-bar">
        <select id="f-province" class="select w140"><option value="">全部省份</option></select>
        <select id="f-city" class="select w140"><option value="">全部城市</option></select>
        <select id="f-cate" class="select w180"><option value="">全部行业门类</option></select>
        <select id="f-industry" class="select w200"><option value="">全部行业细类</option></select>
        <select id="f-status" class="select w120"><option value="">全部状态</option></select>
        <span class="dim" id="f-count"></span>
      </div>
      <div class="result-box"><table class="list-table"><thead><tr>${head}</tr></thead><tbody id="result-tbody">${body}</tbody></table></div></div>`;
    // 筛选数据：从记录派生 省/市（地址提取）、门类/细类（行业映射）
    const rowsMeta = records.map((rec, idx) => {
      const reg = extractRegion(rec);
      if (rec.type === "person") {
        const preg = extractRegion({ regLocation: rec.region || rec.city || "" });
        return { rec, idx, province: preg.province || preg.city, city: preg.city,
                 cate: "人员", industry: "人员", isPerson: true };
      }
      return { rec, idx, province: reg.province, city: reg.city,
               cate: industryCate(rec.industry), industry: rec.industry || "" };
    });
    const provinces = [...new Set(rowsMeta.map((x) => x.province).filter(Boolean))].sort();
    const cates = [...new Set(rowsMeta.map((x) => x.cate).filter(Boolean))].sort();
    const statuses = [...new Set(records.map((r) => (r.regStatus || "").trim()).filter(Boolean))].sort();
    const fp = $("#f-province"), fc = $("#f-city"), fk = $("#f-cate"), fi = $("#f-industry"), fs = $("#f-status");
    fp.innerHTML = `<option value="">全部省份</option>` + provinces.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("")
      + (rowsMeta.some((x) => !x.province) ? `<option value="__none__">（未识别省份）</option>` : "");
    const hasPersons = rowsMeta.some((x) => x.isPerson);
    fk.innerHTML = `<option value="">全部行业门类</option>`
      + (hasPersons ? `<option value="人员">人员</option>` : "")
      + cates.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("")
      + (rowsMeta.some((x) => !x.cate) ? `<option value="__none__">（未分类）</option>` : "");
    fs.innerHTML = `<option value="">全部状态</option>` + statuses.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    const fillCity = () => {
      const prov = fp.value;
      const cities = [...new Set(rowsMeta.filter((x) => (!prov || (prov === "__none__" ? !x.province : x.province === prov)) && x.city).map((x) => x.city))].sort();
      fc.innerHTML = `<option value="">全部城市</option>` + cities.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    };
    const fillInd = () => {
      const cate = fk.value;
      const inds = [...new Set(rowsMeta.filter((x) => (!cate || (cate === "__none__" ? !x.cate : x.cate === cate)) && x.industry).map((x) => x.industry))].sort();
      fi.innerHTML = `<option value="">全部行业细类</option>` + inds.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    };
    const applyFilter = () => {
      const prov = fp.value, cty = fc.value, cate = fk.value, ind = fi.value, st = fs.value;
      const rows = rowsMeta.filter((x) =>
        (!prov || (prov === "__none__" ? !x.province : x.province === prov)) &&
        (!cty || x.city === cty) &&
        (!cate || (cate === "__none__" ? !x.cate : x.cate === cate)) &&
        (!ind || x.industry === ind) &&
        (!st || (x.rec.regStatus || "").trim() === st));
      $("#result-tbody").innerHTML = rows.map(({ rec }) => {
        const srcCell = isAgg
          ? `<td>${(rec.sources || []).map((x) => `<span class="kind-chip kind-batch" style="margin:0 3px 0 0">${esc(sourceShort(x))}</span>`).join("")}</td>` : "";
        if (rec.type === "person") {
          return `<tr>${srcCell}
            <td class="c-name">👤 <a class="table-link" href="#/person?name=${enc(rec.name || "")}&pid=${enc(rec.pid || "")}">${esc(rec.name)}</a></td>
            <td>${esc(rec.matchType || "人员")}</td>
            <td>—</td>
            <td title="任职 ${rec.count || 0} 家">任职 ${rec.count || 0} 家</td>
            <td title="${esc(rec.maxCompany || "")}">${esc((rec.maxCompany || "").slice(0, 18))}</td>
            <td></td>
            <td><div class="row-actions">
              <a class="btn btn-sm btn-primary" href="#/person?name=${enc(rec.name || "")}&pid=${enc(rec.pid || "")}">名下企业</a>
            </div></td></tr>`;
        }
        const cells = cols.map(([k]) => {
          if (k === "regStatus") return `<td>${statusChip(rec[k])}</td>`;
          if (k === "name") return `<td class="c-name"><a class="table-link" href="#/company?name=${enc(rec[k] || "")}">${esc(rec[k])}</a></td>`;
          return `<td title="${esc(fmtVal(rec[k]))}">${esc(fmtVal(rec[k]))}</td>`;
        }).join("");
        return `<tr>${srcCell}${cells}<td><div class="row-actions">
          <a class="btn btn-sm" href="#/company?name=${enc(rec.name || "")}">档案</a>
          <a class="btn btn-sm" href="#/relation?target=${enc(rec.name || "")}">关联</a>
        </div></td></tr>`;
      }).join("");
      $("#f-count").textContent = `筛选后 ${ rows.length } / ${ records.length } 条`;
    };
    fp.addEventListener("change", () => { fillCity(); applyFilter(); });
    fc.addEventListener("change", applyFilter);
    fk.addEventListener("change", () => { fillInd(); applyFilter(); });
    fi.addEventListener("change", applyFilter);
    fs.addEventListener("change", applyFilter);
    fillCity(); fillInd(); applyFilter();
    refreshHistory();
  }

  async function refreshHistory() {
    try {
      const data = await api("/api/history?limit=14");
      const items = data.items || [];
      const el = $("#history-list");
      if (!el) return;
      if (!items.length) { el.innerHTML = '<span class="dim">暂无查询记录</span>'; return; }
      el.innerHTML = items.map((h) => {
        const ok = h.ok ? "" : `<span class="tag-err">失败</span>`;
        return `<span class="chip" style="display:inline-flex;align-items:center" data-kw="${esc(h.keyword)}" data-angle="${esc(h.angle || "")}"
          title="${esc(h.angle)} · ${esc(h.source)} · ${esc(h.ts)}">
          ${esc(h.keyword)} <span class="dim">×${h.count || 0}</span> ${ok}
          <b class="chip-del" data-id="${h.id}" title="删除该条" style="margin-left:6px;color:var(--dim);cursor:pointer;font-weight:700;opacity:.65" onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=.65">✕</b>
        </span>`;
      }).join("");
    } catch (_) { /* 静默 */ }
  }

  // ---------------- 公司档案页 ----------------
  function personLink(name, pid) {
    const q = pid ? `?name=${enc(name)}&pid=${enc(pid)}` : `?name=${enc(name)}`;
    return `<a class="link-val" href="#/person${q}" title="查看 ${esc(name)} 名下企业">${esc(name)}</a>`;
  }
  function companyLink(name) {
    return `<a class="link-val" href="#/company?name=${enc(name)}" title="查看企业档案">${esc(name)}</a>`;
  }
  function phoneLink(phone) {
    return `<a class="link-val" data-phone="${esc(phone)}" title="按电话搜索疑似关联企业">${esc(phone)}</a>`;
  }
  function drillValue(key, value, pid) {
    if (key === "legalPersonName") {
      return personLink(fmtVal(value), pid) + '<span class="link-note">点击查看名下企业</span>';
    }
    if (key === "shareholders") {
      const arr = Array.isArray(value) ? value : [];
      return arr.map((h) => {
        const name = h && typeof h === "object" ? h.name : String(h);
        const pct = h && typeof h === "object" && h.percent ? `（${h.percent}）` : "";
        return personLink(name) + `<span class="link-note">${esc(pct)}</span>`;
      }).join("、") + '<span class="link-note">点击查看名下企业</span>';
    }
    if (key === "investCompanies") {
      const arr = Array.isArray(value) ? value : [value];
      return arr.map((n) => companyLink(String(n))).join("、") + '<span class="link-note">点击查看企业档案</span>';
    }
    if (key === "phone") {
      const arr = Array.isArray(value) ? value : String(value).split("、");
      return arr.filter(Boolean).map((p) => phoneLink(String(p))).join("、") + '<span class="link-note">点击查同电话企业</span>';
    }
    return null;
  }

  function fieldRow(label, html, srcs) {
    const chips = (srcs || []).map((s) => `<span class="src-mini">${esc(sourceShort(s))}</span>`).join("");
    return `<tr><th>${esc(label)}</th><td>${html}${chips}</td></tr>`;
  }
  function secBlock(title, count, rowsHtml, emptyText) {
    const cnt = count != null ? `<span class="cnt">${count}</span>` : "";
    const body = rowsHtml || `<div class="sec-empty">${esc(emptyText || "暂无数据")}</div>`;
    return `<div class="sec-title">${esc(title)}${cnt}</div>` + (rowsHtml ? `<table class="kv-table">${rowsHtml}</table>` : body);
  }

  function companyHeader(record) {
    const srcBadges = (record.sources || [])
      .map((s) => `<span class="kind-chip kind-batch" style="margin-right:4px">${esc(sourceShort(s))}</span>`).join("");
    return `<div class="card company-head">
      <h2>${esc(record.name || "")}</h2>
      <div class="head-meta">
        ${statusChip(record.regStatus)}
        ${record.estiblishTime ? `<span class="dim">成立 ${esc(record.estiblishTime)}</span>` : ""}
        ${record.creditCode ? `<span class="dim">信用代码 ${esc(record.creditCode)}</span>` : ""}
        ${record.legalPersonName ? `<span class="dim">法人 ${personLink(record.legalPersonName)}</span>` : ""}
        ${record.regCapital ? `<span class="dim">注册资本 ${esc(record.regCapital)}</span>` : ""}
      </div>
      <div class="head-meta" style="margin-top:6px">${srcBadges}</div>
      <div class="task-actions" style="margin-top:10px">
        <a class="btn btn-primary btn-sm" href="#/relation?target=${enc(record.name || "")}">查看关联图谱</a>
        <button class="btn btn-sm" id="co-watch">加入监控</button>
        <button class="btn btn-sm" id="co-copy">复制全部</button>
        <a class="btn btn-sm" href="javascript:history.back()">← 返回</a>
      </div>
    </div>`;
  }

  async function renderCompanyPage(name) {
    if (!name) { app('<div class="card banner warn">缺少公司名参数。</div>'); return; }
    const cached = readCachedRecord(name) || {};
    app(companyHeader(cached.name ? cached : { name }) + spinner("正在调取结构化档案（股东/对外投资/分支机构）并跨源比对…"));

    const watchBtn = $("#co-watch");
    if (watchBtn) watchBtn.addEventListener("click", async () => {
      try {
        await api("/api/watchlist", { method: "POST", body: { company_name: name } });
        toast("已加入监控名单");
        watchBtn.textContent = "已加入监控";
        watchBtn.disabled = true;
      } catch (e) { toast("加入失败：" + e.message, true); }
    });
    const bindCopy = (payload) => {
      const btn = $("#co-copy");
      if (btn) btn.addEventListener("click", async () => {
        const lines = [];
        for (const f of state.cfg.fields || []) {
          const v = payload[f.key];
          if (v != null && fmtVal(v) !== "") lines.push(`${f.label}：${fmtVal(v)}`);
        }
        try { await navigator.clipboard.writeText(lines.join("\n")); toast("已复制到剪贴板"); }
        catch (_) { toast("复制失败", true); }
      });
    };

    let profile;
    try {
      profile = await api("/api/profile", {
        method: "POST",
        body: { name, record: cached.name ? cached : undefined, source: state.cfg.currentSource === "all" ? null : state.cfg.currentSource },
      });
    } catch (e) {
      app(companyHeader(cached.name ? cached : { name })
        + `<div class="card banner warn">结构化档案获取失败：${esc(e.message)}（改用搜索层数据展示）</div>`
        + renderBasicSections(cached));
      bindCopy(cached);
      return;
    }
    app(companyHeader({ ...cached, ...profile.basic, sources: uniq((cached.sources || []).concat(profileSources(profile))) })
        + renderProfileSections(profile));
    document.querySelectorAll(".ctab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".ctab").forEach((x) => x.classList.toggle("on", x === tab));
        const id = tab.dataset.ctab;
        document.querySelectorAll(".ctab-panel").forEach((panel) => {
          panel.classList.toggle("hidden", panel.dataset.panel !== id);
        });
      });
    });
    bindCopy(profile.basic);
    cacheRecord(profile.basic);
  }

  function uniq(arr) { return [...new Set(arr.filter(Boolean))]; }
  function profileSources(p) {
    const srcs = [];
    Object.values(p.basicSources || {}).forEach((v) => srcs.push(...v));
    return uniq(srcs);
  }

  function renderBasicSections(record) {
    const rows = (state.cfg.fields || [])
      .filter((f) => fmtVal(record[f.key]) !== "")
      .map((f) => fieldRow(f.label, esc(fmtVal(record[f.key])), null))
      .join("");
    return `<div class="card">${secBlock("基本信息", null, `<table class="kv-table">${rows || '<tr><td class="dim">（无可用字段）</td></tr>'}</table>`)}</div>`;
  }

  function renderProfileSections(p) {
    const b = p.basic || {};
    const infoRows = (state.cfg.fields || [])
      .filter((f) => fmtVal(b[f.key]) !== "")
      .map((f) => {
        const pid = f.key === "legalPersonName" ? p.legalPersonId : null;
        const drilled = drillValue(f.key, b[f.key], pid);
        return fieldRow(f.label, drilled != null ? drilled : esc(fmtVal(b[f.key])), p.basicSources ? p.basicSources[f.key] : null);
      }).join("");

    const tabInfo = `<table class="kv-table">${infoRows || '<tr><td class="dim">（无字段）</td></tr>'}</table>`;

    const tabHolders = `<table class="kv-table">
      <thead><tr><th>股东</th><th>持股比例</th><th>认缴出资</th><th>类型</th></tr></thead>
      <tbody>${p.shareholders.map((h) => {
        const nameHtml = h.type === "person" ? personLink(h.name, h.pid) : companyLink(h.name);
        return `<tr><td>${nameHtml}</td><td><b class="pct">${esc(h.percent || "—")}</b></td><td>${esc(h.subscribed || "—")}</td><td>${esc(h.type === "person" ? "自然人" : "企业")}</td></tr>`;
      }).join("") || '<tr><td colspan="4" class="sec-empty">' + (p.sourceError ? esc("未取到股东明细：" + p.sourceError) : "该公司暂无股东记录") + '</td></tr>'}</tbody></table>`;

    const tabInvest = `<table class="kv-table">
      <thead><tr><th>企业名称</th><th>持股比例</th><th>状态</th></tr></thead>
      <tbody>${p.invest.map((c) => `<tr><td>${companyLink(c.name)}</td><td>${esc(c.percent || "—")}</td><td>${statusChip(c.status)}</td></tr>`).join("")
        || '<tr><td colspan="3" class="sec-empty">' + (p.sourceError ? esc("未取到对外投资明细：" + p.sourceError) : "该公司暂无对外投资记录（或风鸟未收录该区块）") + '</td></tr>'}</tbody></table>`;

    const tabPersons = `<table class="kv-table">
      <thead><tr><th>姓名</th><th>职务</th></tr></thead>
      <tbody>${(p.persons || []).map((x) => `<tr><td>${personLink(x.name, x.pid)}</td><td>${esc(x.position ? x.role + "（" + x.position + "）" : x.role)}</td></tr>`).join("")
        || '<tr><td colspan="2" class="sec-empty">暂无人员信息</td></tr>'}</tbody></table>`;

    const tabBranches = `<table class="kv-table">
      <thead><tr><th>分支机构</th><th>状态</th><th>负责人</th></tr></thead>
      <tbody>${p.branches.map((c) => `<tr><td>${companyLink(c.name)}</td><td>${statusChip(c.status)}</td><td>${esc(c.principal || "—")}</td></tr>`).join("")
        || '<tr><td colspan="3" class="sec-empty">' + (p.sourceError ? esc("未取到分支机构明细：" + p.sourceError) : "该公司暂无分支机构") + '</td></tr>'}</tbody></table>`;

    const tabs = [
      ["info", `工商信息`, tabInfo, null],
      ["holders", `股东信息`, tabHolders, p.shareholders.length],
      ["persons", `主要人员（高管）`, tabPersons, (p.persons || []).length],
      ["invest", `对外投资`, tabInvest, p.invest.length],
      ["branches", `分支机构`, tabBranches, p.branches.length],
    ];
    let html = `<div class="card company-tabs" data-tab="info">` +
      tabs.map(([id, label, _c, cnt], i) =>
        `<a class="ctab ${i === 0 ? "on" : ""}" data-ctab="${id}" href="javascript:void(0)">${esc(label)}${cnt != null ? `<span class="cnt">${cnt}</span>` : ""}</a>`).join("") +
      `</div>` +
      tabs.map(([id, _l, content], i) => `<div class="card ctab-panel${i === 0 ? "" : " hidden"}" data-panel="${id}">${content}</div>`).join("");

    if (p.sourceError) html += `<div class="card banner warn">${esc(p.sourceError)}</div>`;
    return html;
  }

  function onDrillClick(ev) {
    const t = ev.target.closest("[data-phone]");
    if (t) prefillSearch(t.dataset.phone, "电话");
  }

  // ---------------- 人员页 ----------------
  const PROVINCE_CODES = {
    "北京市":"110000","天津市":"120000","河北省":"130000","山西省":"140000",
    "内蒙古自治区":"150000","辽宁省":"210000","吉林省":"220000","黑龙江省":"230000",
    "上海市":"310000","江苏省":"320000","浙江省":"330000","安徽省":"340000",
    "福建省":"350000","江西省":"360000","山东省":"370000","河南省":"410000",
    "湖北省":"420000","湖南省":"430000","广东省":"440000","广西壮族自治区":"450000",
    "海南省":"460000","重庆市":"500000","四川省":"510000","贵州省":"520000",
    "云南省":"530000","西藏自治区":"540000","陕西省":"610000","甘肃省":"620000",
    "青海省":"630000","宁夏回族自治区":"640000","新疆维吾尔自治区":"650000",
  };

  // 省份参数归一化：全名（安徽省）/短名（安徽）/6 位代码 → 风鸟 regionId 代码。
  // 旧链接、收藏夹、历史 URL 可能携带短名，直接透传会导致风鸟返回 0 条。
  function provinceCode(v) {
    const s = String(v || "").trim();
    if (!s) return "";
    if (/^\d{6}$/.test(s)) return s;
    if (PROVINCE_CODES[s]) return PROVINCE_CODES[s];
    for (const [full, code] of Object.entries(PROVINCE_CODES)) {
      if (full.startsWith(s) || full.replace(/省$/, "").startsWith(s)) return code;
    }
    return "";
  }

  async function renderPersonPage(name, pid, regionId) {
    if (!name) {
      app(`<div class="card company-head"><h2>👤 人员搜索</h2>
        <form id="pp-form" class="form-row">
          <input id="pp-name" class="input grow" placeholder="输入人名，如：苏胜" autocomplete="off">
          <select id="pp-prov" class="select w140">
            <option value="">全部省份</option>
            ${Object.keys(PROVINCE_CODES).map((p) => `<option value="${p}">${p}</option>`).join("")}
          </select>
          <button class="btn btn-primary" type="submit">查 询</button>
        </form>
        <div class="meta-line">按姓名搜索人员（风鸟按人员档案精确区分同名），支持按任职企业所在省份筛选</div>
      </div>
      <div id="pp-result"></div>`);
      $("#pp-form").addEventListener("submit", (ev) => {
        ev.preventDefault();
        const nm = $("#pp-name").value.trim();
        if (!nm) { toast("请输入人名"); return; }
        location.hash = `#/person?name=${enc(nm)}&region=${enc($("#pp-prov").value)}`;
      });
      return;
    }

    // 情形 A：带 pid（从分组点选 / 公司档案点入）→ 精确任职企业
    if (pid) {
      app(`<div class="card company-head"><h2>👤 ${esc(name)}</h2>
        <div class="task-actions" style="margin-top:8px">
          <a class="btn btn-primary btn-sm" href="#/relation?target=${enc(name)}">在图谱中查看关联</a>
          <a class="btn btn-sm" href="javascript:history.back()">← 返回</a>
        </div></div>` + spinner("正在调取该人员全部任职企业（风鸟精确，含职务）…"));
      try {
        const data = await api("/api/person-companies", { method: "POST", body: { name, pid } });
        const records = (data.companies || []).filter((r) => r.name);
        if (!records.length) {
          app(`<div class="card company-head"><h2>👤 ${esc(name)}</h2></div>
            <div class="card banner warn">未检索到任职企业。${data.error ? "原因：" + esc(data.error) : ""}</div>`);
          return;
        }
        const srcChips = (data.source === "merged" || data.source === "rb")
          ? '<span class="stats-chip">风鸟 精确匹配</span>' : '<span class="stats-chip">天眼查口径</span>';
        app(`<div class="card company-head"><h2>👤 ${esc(name)}</h2>
          <div class="head-meta">名下企业 <b>${records.length}</b> 家 ${srcChips}</div>
          <div class="task-actions" style="margin-top:8px">
            <a class="btn btn-primary btn-sm" href="#/relation?target=${enc(name)}">在图谱中查看关联</a>
            <a class="btn btn-sm" href="javascript:history.back()">← 返回</a>
          </div></div>
          <div class="card"><table class="kv-table">
            <thead><tr><th>企业名称</th><th>职务</th><th>状态</th><th>地区</th><th>来源</th></tr></thead>
            <tbody>${records.map((r) => {
              const role = r.role || r.position || (r.legalPersonName === name ? "法定代表人" : "—");
              return `<tr>
                <td><a class="table-link" href="#/company?name=${enc(r.name)}">${esc(r.name)}</a></td>
                <td>${esc(role)}</td>
                <td>${statusChip(r.regStatus)}</td>
                <td>${esc(r.region || r.city || "—")}</td>
                <td>${(r.sources || [data.source]).map(sourceShort).map(esc).join("·")}</td>
              </tr>`;
            }).join("")}</tbody></table></div>`);
      } catch (e) {
        app(`<div class="card company-head"><h2>👤 ${esc(name)}</h2></div>
          <div class="card banner err">检索失败：${esc(e.message)}</div>`);
      }
      return;
    }

    // 情形 B：仅有姓名 → 风鸟同名人员分组（可选省份筛选）
    app(`<div class="card company-head"><h2>👤 ${esc(name)}</h2>
      <div class="task-actions" style="margin-top:8px">
        <a class="btn btn-sm" href="javascript:history.back()">← 返回</a>
      </div></div>`
      + spinner(`正在检索同名人员${regionId ? "（" + esc(regionId) + "）" : ""}（风鸟按人员档案精确分组）…`));
    let groups = null, err = "";
    try {
      const ps = await api("/api/person-search", {
        method: "POST",
        body: { name, region_id: provinceCode(regionId) },
      });
      if ((ps.items || []).length) groups = ps.items;
      else err = ps.error || "";
    } catch (e) { err = e.message; }

    if (groups && groups.length) {
      if (groups.length === 1) {
        location.replace(`#/person?name=${enc(name)}&pid=${enc(groups[0].pid)}`);
        return;
      }
      app(`<div class="card company-head"><h2>👤 ${esc(name)}</h2>
        <div class="head-meta">同名人员 <b>${groups.length}</b> 位（风鸟按人员档案精确区分，点选查看各自任职企业）</div>
        <div class="task-actions" style="margin-top:8px">
          <a class="btn btn-sm" href="javascript:history.back()">← 返回</a>
        </div></div>
        <div class="card">${groups.map((g, i) => `
          <a class="person-row" href="#/person?name=${enc(name)}&pid=${enc(g.pid)}">
            <span class="p-name">👥 ${esc(name)} <span class="dim">#${i + 1}</span></span>
            <span class="p-meta">任职 <b>${g.count}</b> 家</span>
            <span class="p-meta">${esc(g.region || (g.regions && g.regions[0] && g.regions[0].name) || "")}</span>
            <span class="p-meta">${esc((g.maxCompany || "").slice(0, 22))}</span>
            <span class="btn btn-sm">查看</span>
          </a>`).join("")}</div>`);
      return;
    }

    // 情形 C：风鸟不可用 → 天眼查口径兜底（明确告知原因，避免误读为最终结果）
    app(`<div class="card company-head"><h2>👤 ${esc(name)}</h2></div>`
      + `<div class="card banner warn">风鸟同名人员分组不可用${err ? "：" + esc(err) : ""}。` +
        `以下为兜底口径（天眼查按法定代表人姓名检索），非按人员档案精确分组。</div>`
      + spinner("正在按天眼查口径检索…"));
    try {
      const res = await api("/api/search", {
        method: "POST",
        body: { keyword: name, angle: "法人", source: state.cfg.currentSource, limit: 30, person: name },
      });
      const records = (res.results || []).filter((r) => r.name);
      if (!records.length) {
        app(`<div class="card company-head"><h2>👤 ${esc(name)}</h2></div>
          <div class="card banner warn">未检索到【${esc(name)}】名下企业。${res.error ? "原因：" + esc(res.error) : ""}</div>`);
        return;
      }
      app(`<div class="card company-head"><h2>👤 ${esc(name)}</h2>
        <div class="head-meta">名下企业 <b>${records.length}</b> 家 <span class="stats-chip">天眼查口径（法定代表人）</span></div>
        <div class="task-actions" style="margin-top:8px"><a class="btn btn-sm" href="javascript:history.back()">← 返回</a></div></div>
        <div class="card"><table class="kv-table">
          <thead><tr><th>企业名称</th><th>法定代表人</th><th>状态</th><th>来源</th></tr></thead>
          <tbody>${records.map((r) => `<tr>
            <td><a class="table-link" href="#/company?name=${enc(r.name)}">${esc(r.name)}</a></td>
            <td>${esc(r.legalPersonName || "—")}</td>
            <td>${statusChip(r.regStatus)}</td>
            <td>${(r.sources || []).map(sourceShort).map(esc).join("·")}</td>
          </tr>`).join("")}</tbody></table>
          <div class="dim" style="margin-top:8px">说明：天眼查口径按姓名检索法定代表人；同名人员可能合并显示。</div>
        </div>`);
    } catch (e) {
      app(`<div class="card banner err">检索失败：${esc(e.message)}</div>`);
    }
  }

  // ---------------- 关联图谱页 ----------------
  function renderRelationPage(target) {
    const meta = state.srcMeta || {};
    const strategies = meta.strategies || [];
    app(`
      <div class="card">
        <form id="relation-form">
          <div class="form-row">
            <input id="rel-target" class="input grow" placeholder="目标企业名称，如：星辰控股集团有限公司" value="${esc(target || "")}" autocomplete="off">
            <label class="inline-label">展开层数
              <select id="rel-depth" class="select w80">
                <option value="1">1 层</option>
                <option value="2" selected>2 层</option>
                <option value="3">3 层</option>
              </select>
            </label>
            <label class="inline-label">节点上限
              <input id="rel-nodes" type="number" class="input w80" value="40" min="10" max="60" step="5">
            </label>
            <button class="btn btn-primary" type="submit" id="rel-run-btn">开始分析</button>
          </div>
          <div id="rel-strategies" class="strategy-row">${strategies.map((s) => {
            const desc = (state.cfg.strategyMeta && state.cfg.strategyMeta[s]) || "";
            return `<label title="${esc(desc)}"><input type="checkbox" value="${s}" checked> ${esc(s)}
              <span class="dim" style="font-weight:400">（${esc(desc)}）</span></label>`;
          }).join("") || '<span class="dim">该数据源无扩展策略</span>'}</div>
        </form>
      </div>
      <div id="rel-status"></div>
      <div class="graph-wrap card hidden" id="graph-card">
        <div id="graph-toolbar" class="graph-toolbar"></div>
        <div class="graph-body">
          <div id="graph-canvas-box"><canvas id="graph-canvas"></canvas></div>
          <aside id="node-info" class="node-info">
            <div class="node-info-empty">点击图中节点查看详情</div>
          </aside>
        </div>
        <div id="graph-legend" class="graph-legend"></div>
      </div>`);
    $("#relation-form").addEventListener("submit", runRelation);
    if (target) $("#relation-form").requestSubmit();
  }

  function showRelStatus(status, extra) {
    const el = $("#rel-status");
    if (!el) return;
    if (status === "queued") el.innerHTML = '<div class="banner info"><span class="spinner"></span>任务已提交，排队中…</div>';
    else if (status === "running") el.innerHTML = `<div class="banner info"><span class="spinner"></span>正在分析关联关系（已发送 <b>${extra || 0}</b> 次请求）…</div>`;
    else if (status === "done") el.innerHTML = "";
    else if (status === "error") el.innerHTML = `<div class="banner err">分析失败：${esc(extra || "未知错误")}</div>`;
    else el.innerHTML = "";
  }

  async function runRelation(ev) {
    ev.preventDefault();
    const target = $("#rel-target").value.trim();
    if (!target) { toast("请输入目标企业名称"); return; }
    const btn = $("#rel-run-btn");
    btn.disabled = true;
    showRelStatus("queued");
    $("#graph-card").classList.add("hidden");
    if (state.relPoll) clearInterval(state.relPoll);
    const strategies = $$("#rel-strategies input:checked").map((c) => c.value);
    let nodes = parseInt($("#rel-nodes").value, 10);
    if (isNaN(nodes) || nodes < 10) nodes = 40;
    if (nodes > 60) nodes = 60;
    const depth = parseInt($("#rel-depth").value, 10) || 2;
    try {
      const task = await api("/api/tasks", {
        method: "POST",
        body: { kind: "relation", source: state.cfg.currentSource, target, depth, maxNodes: nodes, strategies },
      });
      state.relTaskId = task.id;
      state.relPoll = setInterval(() => pollRelation(task.id), 1200);
    } catch (e) {
      showRelStatus("error", e.message);
      btn.disabled = false;
    }
  }

  async function pollRelation(taskId) {
    let task;
    try { task = await api("/api/tasks/" + taskId); }
    catch (_) { clearInterval(state.relPoll); state.relPoll = null; return; }
    if (task.status === "running" || task.status === "queued") {
      showRelStatus("running", task.done || 0);
      return;
    }
    clearInterval(state.relPoll);
    state.relPoll = null;
    const btn = $("#rel-run-btn");
    if (btn) btn.disabled = false;
    if (task.status === "done") {
      const graph = task.result && task.result.graph;
      if (graph) renderGraph(graph, task);
      else showRelStatus("error", "任务结果为空");
    } else {
      showRelStatus("error", task.error || "任务" + (TASK_STATUS[task.status] || [task.status])[0]);
    }
  }

  function graphToolbarHTML(graph) {
    const m = graph.meta, st = m.stats;
    const source = sourceShort(m.source);
    const trunc = m.truncated
      ? '<span class="stats-chip warn" title="受节点/请求预算限制，图谱可能不完整">已截断（预算不足）</span>' : "";
    return `<span class="stats-chip">目标：${esc(m.rootName)}</span>
      <span class="stats-chip">主源：${esc(source)}</span>
      <span class="stats-chip">企业 ${st.companies} · 自然人 ${st.persons} · 关系 ${st.edges}</span>
      <span class="stats-chip">深度 ${m.depth} · 节点上限 ${m.maxNodes} · 请求 ${m.requests} 次</span>
      ${trunc}
      <span style="flex:1"></span>
      <button class="btn btn-sm" id="g-relayout">重新布局</button>
      <button class="btn btn-sm" id="g-fit">适应窗口</button>`;
  }

  function renderGraph(graph, task) {
    $("#graph-card").classList.remove("hidden");
    showRelStatus("done");
    const tb = $("#graph-toolbar");
    tb.innerHTML = graphToolbarHTML(graph);
    tb.querySelector("#g-relayout").addEventListener("click", () => state.graphView && state.graphView.setData(graph));
    tb.querySelector("#g-fit").addEventListener("click", () => state.graphView && state.graphView.fit());

    const used = {};
    (graph.edges || []).forEach((e) => (used[e.type] = true));
    const leg = Object.keys(window.GRAPH_STYLES)
      .filter((t) => used[t])
      .map((t) => {
        const st = window.GRAPH_STYLES[t];
        return `<span class="legend-item"><span class="legend-line ${st.dash ? "dash" : ""}" style="border-color:${st.color}"></span>${esc(st.label)}</span>`;
      }).join("");
    const legNode = $("#graph-legend");
    if (legNode) {
      legNode.innerHTML = leg + '<span class="legend-item"><span style="width:10px;height:10px;border-radius:50%;background:rgba(219,234,254,.9);display:inline-block;border:1.5px solid #2563eb"></span>企业</span>'
        + '<span class="legend-item"><span style="width:10px;height:10px;border-radius:50%;background:#fef3c7;display:inline-block;border:1.5px solid #f59e0b"></span>自然人</span>'
        + '<span class="legend-item">滚动缩放 · 拖空白平移 · 拖节点调整 · 点节点看详情</span>';
    }

    $("#node-info").innerHTML = '<div class="node-info-empty">点击图中节点查看详情</div>';
    if (state.graphView) state.graphView.destroy();
    state.graphView = new window.GraphView($("#graph-canvas"), {
      onSelect: (node) => showNodeInfo(graph, node),
    });
    state.graphView.setData(graph);
    const root = graph.nodes.find((n) => n.id === graph.meta.rootNode);
    if (root) showNodeInfo(graph, root);
  }

  function nodeEdges(graph, id) {
    return (graph.edges || []).filter((e) => e.s === id || e.t === id);
  }
  function relRows(graph, node) {
    const edges = nodeEdges(graph, node.id);
    if (!edges.length) return '<div class="dim">（无）</div>';
    const byNode = (id) => graph.nodes.find((n) => n.id === id);
    const typeName = (t) => (window.GRAPH_STYLES[t] || { label: t }).label;
    return edges.map((e) => {
      const other = byNode(e.s === node.id ? e.t : e.s);
      const dir = e.s === node.id ? "→" : "←";
      const extra = e.label ? ` ${e.label}` : "";
      return `<div class="rel-row">${esc(other ? other.label : "?")}
        <span class="dim"> ${dir} ${esc(typeName(e.type) + extra)}</span></div>`;
    }).join("");
  }

  function showNodeInfo(graph, node) {
    const panel = $("#node-info");
    if (!node) return;
    state.graphView.select(node.id);
    if (node.type === "co") {
      const payload = node.payload || {};
      const kv = (state.cfg.fields || [])
        .filter((f) => payload[f.key] != null && fmtVal(payload[f.key]) !== "" && f.key !== "name")
        .slice(0, 10)
        .map((f) => `<div class="kv"><b>${esc(f.label)}</b><span>${esc(fmtVal(payload[f.key]))}</span></div>`)
        .join("");
      panel.innerHTML = `<h4>🏢 ${esc(node.label)}</h4>
        <div class="kv" style="margin-bottom:6px"><b>类型</b><span>企业 ${node.sub ? "· " + esc(node.sub) : ""} · 来源 ${esc(sourceShort(node.source || ""))}</span></div>
        ${kv}
        <div class="rel-list"><b style="color:var(--dim)">关联关系：</b>${relRows(graph, node)}</div>
        <div class="task-actions">
          <a class="btn btn-sm btn-primary" href="#/company?name=${enc(node.label)}">查看完整档案</a>
          <a class="btn btn-sm" href="#/relation?target=${enc(node.label)}">以此为中心扩展</a>
        </div>`;
    } else {
      panel.innerHTML = `<h4>👤 ${esc(node.label)}</h4>
        <div class="kv" style="margin-bottom:6px"><b>身份</b><span>自然人 · ${esc(node.sub || "")}</span></div>
        <div class="rel-list"><b style="color:var(--dim)">关联企业：</b>${relRows(graph, node)}</div>
        <div class="task-actions">
          <a class="btn btn-sm btn-primary" href="#/person?name=${enc(node.label)}">查看名下企业</a>
        </div>`;
    }
  }

  // ---------------- 批量查询页 ----------------
  function renderBatchPage() {
    const meta = state.srcMeta || {};
    const angles = meta.angles || ["综合"];
    app(`
      <div class="card">
        <div class="card-title">提交批量查询</div>
        <form id="batch-form">
          <div class="form-row">
            <label class="inline-label">默认角度
              <select id="batch-angle" class="select w140">${angles.map((a) => `<option value="${a}">${a}</option>`).join("")}</select>
            </label>
            <label class="inline-label">请求间隔（秒）
              <input id="batch-delay" type="number" class="input w90" value="0" min="0" max="30" step="0.5">
            </label>
            <input id="batch-title" class="input grow" placeholder="任务标题（可选）">
          </div>
          <textarea id="batch-lines" class="textarea" rows="10"
            placeholder="每行一个查询词条。&#10;&#10;示例：&#10;华为技术有限公司&#10;法人：赵明路&#10;电话：0755-28780808&#10;&#10;行首加 # 可注释某行。"></textarea>
          <div class="meta-line">
            <span id="batch-note"></span> ·
            <span>每行可加前缀指定角度：<code>法人：</code> <code>股东：</code> <code>电话：</code> <code>信用代码：</code> <code>企业名：</code>，不加则用默认角度</span>
          </div>
          <button class="btn btn-primary" type="submit">提交批量任务</button>
        </form>
      </div>`);
    $("#batch-form").addEventListener("submit", submitBatch);
    $("#batch-lines").addEventListener("input", updateBatchNote);
    $("#batch-delay").addEventListener("input", updateBatchNote);
    updateBatchNote();
  }

  function batchLines() {
    return ($("#batch-lines").value || "").split(/\r?\n/)
      .map((s) => s.trim()).filter((s) => s && !s.startsWith("#"));
  }
  function updateBatchNote() {
    const meta = state.srcMeta;
    const el = $("#batch-note");
    if (!meta || !el) return;
    const n = batchLines().length;
    const delay = Math.max(parseFloat($("#batch-delay").value) || 0, meta.minInterval || 0);
    const est = delay > 0 ? `预计耗时约 ${(n * delay).toFixed(1)} 秒` : "无强制间隔";
    el.textContent = `${meta.label} · ${n} 行 · 实际间隔 ${delay.toFixed(1)}s · ${est}`;
  }
  async function submitBatch(ev) {
    ev.preventDefault();
    const lines = batchLines();
    if (!lines.length) { toast("请输入要查询的关键词，每行一个"); return; }
    const limit = state.cfg.limits.batchMaxLines;
    if (lines.length > limit) { toast(`最多 ${limit} 行，当前 ${lines.length} 行`, true); return; }
    const body = {
      kind: "batch",
      title: $("#batch-title").value.trim() || "",
      source: state.cfg.currentSource,
      angle: $("#batch-angle").value,
      delay: parseFloat($("#batch-delay").value) || 0,
      lines: lines,
    };
    const btn = ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const task = await api("/api/tasks", { method: "POST", body });
      toast(`批量任务已提交（#${task.id}，共 ${lines.length} 行）`);
      location.hash = "#/tasks";
    } catch (e) {
      toast("提交失败：" + e.message, true);
      btn.disabled = false;
    }
  }

  // ---------------- 任务中心 ----------------
  function renderTasksPage() {
    app(`<div class="card"><div class="card-title">任务中心 <span class="dim">（批量查询与关联分析任务）</span></div>
      <div id="task-list" class="task-list"></div></div>`);
    state.tasksTimer = setInterval(loadTasks, 2500);
    loadTasks();
  }

  async function loadTasks() {
    let data;
    try { data = await api("/api/tasks?limit=60"); }
    catch (_) { return; }
    const el = $("#task-list");
    if (!el) return;
    const tasks = data.items || [];
    if (!tasks.length) {
      el.innerHTML = '<div class="dim" style="padding:10px 0">暂无任务。可在“批量查询 / 关联图谱”页创建。</div>';
      return;
    }
    el.innerHTML = tasks.map((t) => {
      const [stLabel, stCls] = TASK_STATUS[t.status] || [t.status, "tag-stop"];
      const kind = KIND_LABEL[t.kind] || [t.kind, ""];
      const isBatch = t.kind === "batch";
      const pct = t.total > 0 ? Math.round((t.done / t.total) * 100) : (t.status === "done" ? 100 : 0);
      const progress = isBatch
        ? `<div class="progress"><span class="dim">${t.done}/${t.total}</span><span class="bar"><i style="width:${pct}%"></i></span><span class="dim">${pct}%</span></div>`
        : `<div class="progress"><span class="dim">${t.status === "running" || t.status === "queued" ? "分析中 · 已请求 " + t.done + " 次" : ""}</span></div>`;
      const errHtml = t.error ? `<div class="banner err" style="margin:8px 0 0">${esc(t.error)}</div>` : "";
      return `<div class="task-item" data-id="${t.id}">
        <div class="task-head">
          <span class="kind-chip ${kind[1]}">${kind[0]}</span>
          <span class="task-title">${esc(t.title)}</span>
          <span class="${stCls}">${stLabel}</span>
        </div>
        <div class="task-meta">#${t.id} · ${esc(t.created_at || "")}${t.finished_at ? " · 完成于 " + esc(t.finished_at) : ""}</div>
        ${progress}${errHtml}
        <div class="task-actions"></div>
      </div>`;
    }).join("");

    tasks.forEach((t) => {
      const box = el.querySelector(`.task-item[data-id="${t.id}"]`);
      if (!box) return;
      const acts = box.querySelector(".task-actions");
      const add = (label, cls, fn) => {
        const b = document.createElement("button");
        b.className = "btn btn-sm " + cls;
        b.textContent = label;
        b.addEventListener("click", fn);
        acts.appendChild(b);
      };
      const addLink = (label, cls, href) => {
        const a = document.createElement("a");
        a.className = "btn btn-sm " + cls;
        a.textContent = label;
        a.href = href;
        acts.appendChild(a);
      };
      if (t.status === "queued" || t.status === "running") {
        add("取消", "", async () => {
          try { await api(`/api/tasks/${t.id}/cancel`, { method: "POST" }); toast("已请求取消"); }
          catch (e) { toast(e.message, true); }
          loadTasks();
        });
      }
      if (t.status !== "queued" && t.status !== "running") {
        add("重跑", "", async () => {
          try { await api(`/api/tasks/${t.id}/rerun`, { method: "POST" }); toast("已创建重跑任务"); }
          catch (e) { toast(e.message, true); }
          loadTasks();
        });
      }
      if (t.status === "done" && t.kind === "batch") {
        ["csv", "xlsx", "json"].forEach((fmt) => {
          addLink("导出 " + fmt.toUpperCase(), "", `/api/tasks/${t.id}/export?fmt=${fmt}`);
        });
      }
      if (t.status === "done") {
        addLink("查看", "btn-primary", `#/task?id=${t.id}`);
      }
    });
  }

  // ---------------- 任务详情页 ----------------
  async function renderTaskDetailPage(taskId) {
    if (!taskId) { app('<div class="card banner warn">缺少任务 ID。</div>'); return; }
    app(spinner("正在加载任务结果…"));
    let task;
    try { task = await api("/api/tasks/" + taskId); }
    catch (e) { app(`<div class="card banner err">加载失败：${esc(e.message)}</div>`); return; }

    if (task.status !== "done" || !task.result) {
      app(`<div class="card banner warn">任务 #${taskId} 尚未完成（${(TASK_STATUS[task.status] || [task.status])[0]}）。
        ${task.error ? "原因：" + esc(task.error) : ""}</div>`);
      return;
    }
    if (task.kind === "batch") {
      renderBatchResult(task);
    } else if (task.kind === "relation" && task.result.graph) {
      renderRelationResultPage(task);
    }
  }

  function renderBatchResult(task) {
    const items = task.result.items || [];
    const head = "<tr><th>#</th><th>关键词</th><th>角度</th><th>结果</th><th>企业名（点击看档案）</th><th>法人</th><th>状态</th><th>信用代码</th><th>来源</th></tr>";
    const body = items.map((it, i) => {
      const b = it.best;
      const ok = it.ok && b;
      if (!ok) {
        return `<tr><td>${i + 1}</td><td>${esc(it.keyword)}</td><td>${esc(it.angle || "")}</td>
          <td><span class="tag-err">失败/无结果</span></td><td>-</td><td>-</td><td>-</td><td>-</td>
          <td class="dim">${esc(it.error || "无匹配")}</td></tr>`;
      }
      cacheRecord(b);
      return `<tr><td>${i + 1}</td><td>${esc(it.keyword)}</td><td>${esc(it.angle || "")}</td>
        <td><span class="tag-ok">${it.count || 0} 条</span></td>
        <td><a class="table-link" href="#/company?name=${enc(b.name || "")}">${esc(b.name || "-")}</a></td>
        <td>${esc(b.legalPersonName || "")}</td><td>${statusChip(b.regStatus)}</td>
        <td>${esc(b.creditCode || "")}</td><td class="dim">${(b.sources || []).map(sourceShort).join("·") || "-"}</td></tr>`;
    }).join("");
    app(`<div class="card">
      <div class="card-title">${esc(task.title)} <span class="dim">共 ${items.length} 行 · 完成于 ${esc(task.finished_at || "")}</span></div>
      <div class="result-export-row">
        <span class="dim">导出：</span>
        <a class="btn btn-sm" href="/api/tasks/${task.id}/export?fmt=csv">CSV</a>
        <a class="btn btn-sm" href="/api/tasks/${task.id}/export?fmt=xlsx">XLSX</a>
        <a class="btn btn-sm" href="/api/tasks/${task.id}/export?fmt=json">JSON</a>
        <a class="btn btn-sm" href="javascript:history.back()">← 返回</a>
      </div>
      <div class="result-box" style="overflow-x:auto"><table class="result-table">${head}${body}</table></div>
    </div>`);
  }

  function renderRelationResultPage(task) {
    const graph = task.result.graph;
    app(`<div class="card"><div class="card-title">${esc(task.title)} <span class="dim">完成于 ${esc(task.finished_at || "")}</span>
      <a class="btn btn-sm" style="float:right" href="javascript:history.back()">← 返回</a></div>
      <div id="rel-status"></div>
      <div class="graph-wrap" id="graph-card">
        <div id="graph-toolbar" class="graph-toolbar"></div>
        <div class="graph-body">
          <div id="graph-canvas-box"><canvas id="graph-canvas"></canvas></div>
          <aside id="node-info" class="node-info"><div class="node-info-empty">点击图中节点查看详情</div></aside>
        </div>
        <div id="graph-legend" class="graph-legend"></div>
      </div></div>`);
    renderGraph(graph, task);
  }



  // ---------------- 管理后台（设置） ----------------
  let adminTab = "cookies";

  async function renderSettingsPage() {
    const isAdmin = state.role === "admin";
    if (isAdmin) {
      const tabs = [
        ["cookies", "Cookie 管理"],
        ["sources", "数据源"],
        ["users", "用户管理"],
        ["logs", "操作日志"],
      ];
      app(`<div class="card" style="padding:8px 12px;margin-bottom:0">
          <div style="display:flex;gap:4px;flex-wrap:wrap">
            ${tabs.map(([id, label], i) => `<a class="ctab ${i === 0 ? "on" : ""}" data-atab="${id}" href="javascript:void(0)">${esc(label)}</a>`).join("")}
          </div></div>
        <div id="atab-panel"></div>`);
      document.querySelectorAll(".ctab[data-atab]").forEach((tab) => {
        tab.addEventListener("click", () => {
          adminTab = tab.dataset.atab;
          document.querySelectorAll(".ctab[data-atab]").forEach((x) => x.classList.toggle("on", x === tab));
          renderAdminPanel();
        });
      });
      renderAdminPanel();
    } else {
      // 会员：仅账号（修改自己密码）
      app(`<div class="card"><div class="card-title">账号 · 修改密码</div>
        <form id="pw-form" style="max-width:420px">
          <input type="password" id="pw-old" class="input" style="width:100%;margin-bottom:10px" placeholder="原密码">
          <input type="password" id="pw-new" class="input" style="width:100%;margin-bottom:10px" placeholder="新密码（至少 6 位）">
          <input type="password" id="pw-new2" class="input" style="width:100%;margin-bottom:10px" placeholder="确认新密码">
          <button class="btn btn-primary" type="submit">保存新密码</button>
          <span class="dim" id="pw-result" style="margin-left:10px"></span>
        </form></div>`);
      $("#pw-form").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const oldp = $("#pw-old").value, newp = $("#pw-new").value;
        if (newp !== $("#pw-new2").value) { $("#pw-result").textContent = "两次新密码不一致"; return; }
        try {
          await api("/api/me/password", { method: "POST", body: { old_password: oldp, new_password: newp } });
          $("#pw-result").textContent = "✅ 密码已修改";
          $("#pw-old").value = $("#pw-new").value = $("#pw-new2").value = "";
        } catch (e) { $("#pw-result").textContent = "❌ " + e.message; }
      });
    }
  }

  function renderAdminPanel() {
    const panel = $("#atab-panel");
    if (!panel) return;
    if (adminTab === "cookies") return renderAdminCookies(panel);
    if (adminTab === "sources") return renderAdminSources(panel);
    if (adminTab === "users") return renderAdminUsers(panel);
    if (adminTab === "logs") return renderAdminLogs(panel);
  }

  const COOKIE_SOURCES = [
    { id: "tyc", label: "天眼查", note: "浏览器登录 m.tianyancha.com 后复制 Cookie" },
    { id: "rb", label: "风鸟", note: "浏览器登录 riskbird.com 后复制 Cookie（SVIP 效果最佳）" },
    { id: "c88", label: "88查", note: "浏览器打开 88cha.com 随便搜一次后复制 Cookie（免注册）" },
    { id: "aqc", label: "爱企查", note: "浏览器登录 www.aiqicha.com 后复制 Cookie" },
  ];

  async function renderAdminCookies(panel) {
    panel.innerHTML = spinner("加载 Cookie 配置…");
    try {
      const data = await api("/api/admin/settings/cookies");
      const status = {};
      (data.items || []).forEach((s) => (status[s.id] = s));
      panel.innerHTML = COOKIE_SOURCES.map((src) => {
        const st = status[src.id] || {};
        const chip = st.configured
          ? `<span class="tag-ok">已配置${st.fromDb ? "（页面）" : "（环境变量）"}</span>`
          : `<span class="tag-queued">未配置</span>`;
        const at = st.configuredAt ? `<span class="dim">保存于 ${esc(st.configuredAt)}</span>` : "";
        return `<div class="card cookie-card" data-src="${src.id}">
          <div class="task-head">
            <span class="kind-chip kind-batch">${esc(src.label)}</span> ${chip} ${at}
            <span class="dim" style="font-size:12px">${esc(src.note)}</span>
          </div>
          <textarea class="textarea" rows="4" placeholder="粘贴完整 Cookie（name=value; ...）"></textarea>
          <div class="task-actions">
            <button class="btn btn-sm btn-primary act-save">保存</button>
            <button class="btn btn-sm act-import">导入 Cookie 文件</button>
            <input type="file" class="act-file hidden" accept=".txt,.json,.cookie,.har" multiple>
            <button class="btn btn-sm act-clear">清除（回退环境变量）</button>
            <button class="btn btn-sm act-probe">测试数据源</button>
            <span class="dim act-result"></span>
          </div>
        </div>`;
      }).join("");
    } catch (e) {
      panel.innerHTML = `<div class="banner err">${esc(e.message)}</div>`;
      return;
    }
    panel.querySelectorAll(".cookie-card").forEach((card) => {
      const srcId = card.dataset.src;
      const ta = card.querySelector("textarea");
      const result = card.querySelector(".act-result");
      card.querySelector(".act-save").addEventListener("click", async () => {
        try {
          const r = await api("/api/admin/settings/cookie", { method: "POST", body: { source: srcId, cookie: ta.value } });
          toast(`${srcId} Cookie 已保存（${r.length} 字符）`);
          renderAdminCookies(panel);
        } catch (e) { toast("保存失败：" + e.message, true); }
      });
      card.querySelector(".act-clear").addEventListener("click", async () => {
        try {
          await api("/api/admin/settings/cookie/clear", { method: "POST", body: { source: srcId } });
          toast(`${srcId} Cookie 已清除`);
          renderAdminCookies(panel);
        } catch (e) { toast("清除失败：" + e.message, true); }
      });
      card.querySelector(".act-probe").addEventListener("click", async () => {
        result.textContent = "测试中…";
        try {
          const r = await api("/api/source/probe", { method: "POST", body: { source: srcId } });
          result.textContent = r.ok ? "✅ " + r.message : "❌ " + r.message;
        } catch (e) { result.textContent = "❌ " + e.message; }
      });
      const fileInput = card.querySelector(".act-file");
      card.querySelector(".act-import").addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", async () => {
        const files = [...(fileInput.files || [])];
        if (!files.length) return;
        result.textContent = "导入中…";
        let okCount = 0;
        for (const f of files) {
          try {
            const text = await f.text();
            const r = await api("/api/admin/cookies/import", {
              method: "POST",
              body: { source: srcId, text },
            });
            okCount++;
            result.textContent = `✅ ${f.name}：${r.format} 格式，${r.count} 条已导入`;
          } catch (e) {
            result.textContent = `❌ ${f.name}：${e.message}`;
          }
        }
        fileInput.value = "";
        toast(`${srcId} 导入完成（${okCount}/${files.length} 个文件成功）`);
      });
    });
  }

  function renderAdminSources(panel) {
    panel.innerHTML = `<div class="form-row">
        <select id="settings-src" class="select w220">${(state.cfg.sources || []).map((s) => `<option value="${s.id}" ${s.id === state.cfg.currentSource ? "selected" : ""}>${esc(s.label)}</option>`).join("")}</select>
        <button class="btn btn-sm" id="settings-src-probe">测试数据源</button>
        <span class="dim" id="settings-src-result"></span>
      </div>
      <div class="meta-line" style="margin-top:10px">默认数据源用于：首页大搜索、批量任务、人员页兜底检索。切换后立即生效。</div>`;
    $("#settings-src").addEventListener("change", async (ev) => {
      const sid = ev.target.value;
      try { await api("/api/admin/source", { method: "POST", body: { source: sid } }); }
      catch (e) { toast(e.message, true); return; }
      state.cfg.currentSource = sid;
      toast("默认数据源已切换为：" + sid);
    });
    $("#settings-src-probe").addEventListener("click", async () => {
      const res = $("#settings-src-result");
      res.textContent = "测试中…";
      try {
        const r = await api("/api/source/probe", { method: "POST", body: { source: $("#settings-src").value } });
        res.textContent = r.ok ? "✅ " + r.message : "❌ " + r.message;
      } catch (e) { res.textContent = "❌ " + e.message; }
    });
  }

  async function renderAdminUsers(panel) {
    panel.innerHTML = spinner("加载用户…");
    let data;
    try { data = await api("/api/admin/users"); }
    catch (e) { panel.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
    const items = data.items || [];
    panel.innerHTML = `
      <table class="kv-table">
        <thead><tr><th>用户名</th><th>角色</th><th>备注</th><th>创建时间</th><th>最近登录</th><th>操作</th></tr></thead>
        <tbody>${items.map((u) => `<tr>
          <td><b>${esc(u.username)}</b>${u.username === state.user ? ' <span class="src-mini">当前</span>' : ""}</td>
          <td><select class="select act-role" data-u="${esc(u.username)}">
            <option value="admin" ${u.role === "admin" ? "selected" : ""}>管理员</option>
            <option value="member" ${u.role === "member" ? "selected" : ""}>会员</option>
          </select></td>
          <td>${esc(u.note || "")}</td>
          <td>${esc(u.created_at || "")}</td>
          <td>${esc(u.last_login || "—")}</td>
          <td><button class="btn btn-sm act-reset" data-u="${esc(u.username)}">重置密码</button>
              <button class="btn btn-sm btn-danger act-del" data-u="${esc(u.username)}">删除</button></td>
        </tr>`).join("")}</tbody></table>
      <div class="sec-title">新建用户</div>
      <form id="user-form" class="form-row">
        <input id="nu-name" class="input" placeholder="用户名（字母/数字/下划线）">
        <input id="nu-pass" class="input" type="password" placeholder="密码（≥6 位）">
        <select id="nu-role" class="select"><option value="member">会员</option><option value="admin">管理员</option></select>
        <input id="nu-note" class="input" placeholder="备注（可选）">
        <button class="btn btn-primary" type="submit">创建</button>
      </form>`;
    panel.querySelectorAll(".act-role").forEach((sel) => sel.addEventListener("change", async () => {
      try { await api(`/api/admin/users/${enc(sel.dataset.u)}/role`, { method: "POST", body: { role: sel.value } }); toast("角色已更新"); }
      catch (e) { toast(e.message, true); renderAdminUsers(panel); }
    }));
    panel.querySelectorAll(".act-reset").forEach((btn) => btn.addEventListener("click", async () => {
      const np = prompt(`为 ${btn.dataset.u} 设置新密码（≥6 位）：`);
      if (np === null) return;
      try { await api(`/api/admin/users/${enc(btn.dataset.u)}/password`, { method: "POST", body: { old_password: "", new_password: np } }); toast("密码已重置"); }
      catch (e) { toast(e.message, true); }
    }));
    panel.querySelectorAll(".act-del").forEach((btn) => btn.addEventListener("click", async () => {
      if (!confirm(`确认删除用户 ${btn.dataset.u}？`)) return;
      try { await api(`/api/admin/users/${enc(btn.dataset.u)}`, { method: "DELETE" }); toast("已删除"); renderAdminUsers(panel); }
      catch (e) { toast(e.message, true); }
    }));
    $("#user-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await api("/api/admin/users", { method: "POST", body: {
          username: $("#nu-name").value.trim(), password: $("#nu-pass").value,
          role: $("#nu-role").value, note: $("#nu-note").value.trim(),
        } });
        toast("用户已创建");
        renderAdminUsers(panel);
      } catch (e) { toast("创建失败：" + e.message, true); }
    });
  }

  async function renderAdminLogs(panel) {
    panel.innerHTML = `<div class="form-row" style="margin-bottom:10px">
        <select id="log-action" class="select w200"><option value="">全部操作</option></select>
        <button class="btn btn-sm btn-danger" id="log-clear">清空日志</button>
        <span class="dim" id="log-count"></span>
      </div><div id="log-table"></div>`;
    const load = async (action) => {
      const data = await api(`/api/logs?limit=500${action ? "&action=" + enc(action) : ""}`);
      const items = data.items || [];
      $("#log-count").textContent = `共 ${items.length} 条`;
      $("#log-table").innerHTML = `<table class="kv-table">
        <thead><tr><th>时间</th><th>用户</th><th>角色</th><th>操作</th><th>详情</th></tr></thead>
        <tbody>${items.map((l) => `<tr>
          <td>${esc(l.ts)}</td><td>${esc(l.user || "")}</td><td>${esc(l.role || "")}</td>
          <td>${esc(l.action)}</td><td>${esc(l.detail || "")}</td></tr>`).join("")
          || '<tr><td colspan="5" class="sec-empty">暂无日志</td></tr>'}</tbody></table>`;
    };
    const actions = ["登录", "查询", "查看档案", "创建任务", "加入监控", "监控检查", "更新Cookie", "创建用户", "重置密码", "删除用户", "调整角色", "清空日志"];
    $("#log-action").innerHTML = `<option value="">全部操作</option>` + actions.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join("");
    $("#log-action").addEventListener("change", () => load($("#log-action").value));
    $("#log-clear").addEventListener("click", async () => {
      if (!confirm("确认清空全部操作日志？")) return;
      try { await api("/api/admin/logs", { method: "DELETE" }); toast("日志已清空"); load(""); }
      catch (e) { toast("清空失败：" + e.message, true); }
    });
    await load("");
  }

  // ---------------- 启动 ----------------
  document.addEventListener("DOMContentLoaded", boot);
})();
