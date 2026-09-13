/**
 * 轻量力导向图谱渲染器（零依赖）。
 * 节点：公司=蓝色圆，自然人=橙色圆，根企业带金色环。
 * 边：法人=橙、股东=紫、对外投资=蓝(带箭头)、同电话=灰虚线。
 * 交互：拖节点 / 拖空白平移 / 滚轮缩放 / 点节点回调。
 */
(function () {
  "use strict";

  const EDGE_STYLE = {
    legal:      { color: "#d97706", label: "法人", dash: false, arrow: false },
    shareholder:{ color: "#7c3aed", label: "股东", dash: false, arrow: false },
    invest:     { color: "#2563eb", label: "对外投资", dash: false, arrow: true },
    phone:      { color: "#64748b", label: "同电话", dash: true, arrow: false },
  };
  const TYPE_COLOR = { co: "#2563eb", p: "#f59e0b" };
  const TYPE_FILL = { co: "rgba(219,234,254,.92)", p: "rgba(254,243,199,.95)" };

  class GraphView {
    constructor(canvas, { onSelect, onDbl } = {}) {
      this.cv = canvas;
      this.ctx = canvas.getContext("2d");
      this.onSelect = onSelect || null;
      this.onDbl = onDbl || null;
      this.nodes = [];      // {id,type,label,sub,x,y,vx,vy,r,isRoot,selected}
      this.edges = [];      // {s,t,type,label, sx,sy,tx,ty}
      this.byId = new Map();
      this.view = { x: 0, y: 0, k: 1 };   // 世界->屏幕: sx=x*k+vx
      this.animating = false;
      this.stepLeft = 0;
      this._raf = 0;
      this._drag = null;    // {mode:'pan'|'node', id, sx, sy, wx, wy}
      this._bind();
      this._resize();
      this.fit = this.fit.bind(this);
    }

    destroy() {
      cancelAnimationFrame(this._raf);
      this._unbind();
    }

    // ---------- 尺寸 ----------
    _resize() {
      const dpr = window.devicePixelRatio || 1;
      const rect = this.cv.getBoundingClientRect();
      this.cv.width = Math.max(200, rect.width) * dpr;
      this.cv.height = Math.max(200, rect.height) * dpr;
      this.dpr = dpr;
    }

    // ---------- 数据 ----------
    setData(data) {
      this.nodes = [];
      this.edges = [];
      this.byId = new Map();
      const stats = data.meta && data.meta.stats;
      const total = (stats ? stats.companies + stats.persons : 0) || data.nodes.length;
      const n = data.nodes.length || 1;
      // 初始化：按层半径环形 + 少量抖动
      const cx = 0, cy = 0;
      const R = Math.max(180, n * 26);
      for (let i = 0; i < data.nodes.length; i++) {
        const nd = data.nodes[i];
        const ang = (i / n) * Math.PI * 2;
        const rr = R * (0.45 + 0.55 * Math.abs(Math.sin(i * 2.399)));
        this.nodes.push({
          id: nd.id,
          type: nd.type,
          label: nd.label || "",
          sub: nd.sub || "",
          payload: nd.payload || null,
          isRoot: nd.id === (data.meta && data.meta.rootNode),
          r: nd.type === "co" ? 18 : 12,
          x: cx + Math.cos(ang) * rr * 0.5 + (Math.random() - .5) * 40,
          y: cy + Math.sin(ang) * rr * 0.5 + (Math.random() - .5) * 40,
          vx: 0, vy: 0, selected: false,
          degree: 0,
        });
        this.byId.set(nd.id, this.nodes[this.nodes.length - 1]);
      }
      for (const e of data.edges || []) {
        const sn = this.byId.get(e.s), tn = this.byId.get(e.t);
        if (!sn || !tn) continue;
        sn.degree++;
        tn.degree++;
        this.edges.push({ s: e.s, t: e.t, type: e.type, label: e.label || "" });
      }
      this.stepLeft = Math.min(700, 160 + total * 9);
      this._tick();
      this.fit();
    }

    _physics() {
      const nodes = this.nodes, edges = this.edges;
      const K = 900, L = 150;
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          let dx = b.x - a.x, dy = b.y - a.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 1) { dx = (Math.random() - .5); dy = (Math.random() - .5); d2 = 1; }
          const d = Math.sqrt(d2);
          const f = Math.min(K / d2, 14) * (d < L ? 1 : 0.35);
          const fx = (dx / d) * f, fy = (dy / d) * f;
          a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
        }
      }
      for (const e of edges) {
        const a = nodes[e.s] || this.byId.get(e.s);
        const b = nodes[e.t] || this.byId.get(e.t);
        if (!a || !b) continue;
        let dx = b.x - a.x, dy = b.y - a.y;
        let d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - L) * 0.035;
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
      // 向心约束
      for (const a of nodes) {
        a.vx -= a.x * 0.008;
        a.vy -= a.y * 0.008;
      }
      for (const a of nodes) {
        if (this._drag && this._drag.id === a.id) { a.vx = 0; a.vy = 0; continue; }
        a.vx *= 0.82; a.vy *= 0.82;
        a.x += a.vx; a.y += a.vy;
      }
    }

    _tick = () => {
      if (!this.animating) return;
      const steps = 6;
      for (let i = 0; i < steps && this.stepLeft > 0; i++, this.stepLeft--) this._physics();
      this.render();
      if (this.stepLeft > 0) {
        this._raf = requestAnimationFrame(this._tick);
      } else {
        this.animating = false;
        this.render();
      }
    };

    // ---------- 坐标 ----------
    _w2s(p) { return { x: p.x * this.view.k + this.view.x, y: p.y * this.view.k + this.view.y }; }
    _s2w(x, y) { return { x: (x - this.view.x) / this.view.k, y: (y - this.view.y) / this.view.k }; }

    fit() {
      const W = this.cv.width / this.dpr, H = this.cv.height / this.dpr;
      if (!this.nodes.length) return;
      let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
      for (const a of this.nodes) {
        x0 = Math.min(x0, a.x); y0 = Math.min(y0, a.y);
        x1 = Math.max(x1, a.x); y1 = Math.max(y1, a.y);
      }
      const pad = 90;
      const k = Math.min((W - pad * 2) / Math.max(1, x1 - x0), (H - pad * 2) / Math.max(1, y1 - y0), 2.2);
      this.view.k = Math.max(0.12, Math.min(3, k));
      this.view.x = (W - (x0 + x1) * this.view.k) / 2;
      this.view.y = (H - (y0 + y1) * this.view.k) / 2;
      this.render();
    }

    // ---------- 渲染 ----------
    render() {
      const ctx = this.ctx, dpr = this.dpr;
      const W = this.cv.width / dpr, H = this.cv.height / dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      // 边（同对节点的多条边按类型错开，避免重叠）
      const keyByPair = new Map();
      this.edges.forEach((e) => {
        const k = e.s < e.t ? e.s + "|" + e.t : e.t + "|" + e.s;
        if (!keyByPair.has(k)) keyByPair.set(k, []);
        keyByPair.get(k).push(e);
      });
      const pairIdx = new Map();
      for (const e of this.edges) {
        const a = this.byId.get(e.s), b = this.byId.get(e.t);
        if (!a || !b) continue;
        const k = e.s < e.t ? e.s + "|" + e.t : e.t + "|" + e.s;
        const idx = pairIdx.get(k) || 0;
        pairIdx.set(k, idx + 1);
        const st = EDGE_STYLE[e.type] || EDGE_STYLE.phone;
        const p1 = this._w2s(a), p2 = this._w2s(b);
        let off = 0;
        const cnt = keyByPair.get(k).length;
        if (cnt > 1) off = (idx - (cnt - 1) / 2) * 6;
        if (off) {
          const dx = p2.x - p1.x, dy = p2.y - p1.y;
          const len = Math.sqrt(dx * dx + dy * dy) || 1;
          const nx = -dy / len, ny = dx / len;
          p1.x += nx * off; p1.y += ny * off;
          p2.x += nx * off; p2.y += ny * off;
        }
        ctx.strokeStyle = st.color;
        ctx.globalAlpha = 0.85;
        ctx.lineWidth = 1.6;
        ctx.setLineDash(st.dash ? [6, 5] : []);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y);
        ctx.stroke();
        ctx.setLineDash([]);
        if (st.arrow) {
          const ang = Math.atan2(p2.y - p1.y, p2.x - p1.x);
          const tip = { x: p2.x - Math.cos(ang) * (b.r + 4), y: p2.y - Math.sin(ang) * (b.r + 4) };
          const s1 = 7, sp = Math.PI * .42;
          ctx.fillStyle = st.color;
          ctx.beginPath();
          ctx.moveTo(tip.x, tip.y);
          ctx.lineTo(tip.x - Math.cos(ang - sp) * s1, tip.y - Math.sin(ang - sp) * s1);
          ctx.lineTo(tip.x - Math.cos(ang + sp) * s1, tip.y - Math.sin(ang + sp) * s1);
          ctx.closePath(); ctx.fill();
        }
        // 边标签（放大后可见）
        if (this.view.k > 1.05) {
          const mx = (p1.x + p2.x) / 2, my = (p1.y + p2.y) / 2;
          ctx.font = "10px sans-serif";
          const text = e.label || st.label;
          const tw = ctx.measureText(text).width + 8;
          ctx.fillStyle = "rgba(255,255,255,.92)";
          ctx.fillRect(mx - tw / 2, my - 8, tw, 15);
          ctx.fillStyle = st.color;
          ctx.fillText(text, mx - tw / 2 + 4, my + 3.5);
        }
      }
      ctx.globalAlpha = 1;

      // 边颜色不透明后画节点
      const selected = this._selected;
      for (const a of this.nodes) {
        if (a === selected) continue;
        this._drawNode(ctx, a);
      }
      if (selected) this._drawNode(ctx, selected);

      // 图例提示
      ctx.fillStyle = "rgba(100,116,139,.55)";
      ctx.font = "12px sans-serif";
    }

    _drawNode(ctx, a) {
      const p = this._w2s(a);
      const R = Math.max(5, a.r * this.view.k);
      ctx.save();
      // 高亮关联边淡色
      if (a.selected) {
        ctx.strokeStyle = "#f59e0b";
        ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.arc(p.x, p.y, R + 7, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.shadowColor = "rgba(15,23,42,.18)";
      ctx.shadowBlur = 6 * this.view.k;
      ctx.fillStyle = TYPE_FILL[a.type] || "#fff";
      ctx.beginPath(); ctx.arc(p.x, p.y, R, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
      ctx.lineWidth = a.isRoot ? 3 : 1.6;
      ctx.strokeStyle = a.isRoot ? "#f59e0b" : TYPE_COLOR[a.type] || "#64748b";
      ctx.stroke();
      if (a.isRoot) {
        ctx.beginPath(); ctx.arc(p.x, p.y, R + 3, 0, Math.PI * 2);
        ctx.setLineDash([4, 4]); ctx.stroke(); ctx.setLineDash([]);
      }
      if (a.type === "co") {
        ctx.fillStyle = "#1e40af";
        ctx.font = `700 ${Math.max(9, 13 * this.view.k)}px sans-serif`;
      } else {
        ctx.fillStyle = "#92400e";
        ctx.font = `600 ${Math.max(9, 12 * this.view.k)}px sans-serif`;
      }
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const label = a.type === "co" ? this._short(a.label, a.isRoot ? 16 : 12) : this._short(a.label, 8);
      ctx.fillText(label, p.x, p.y + 1);
      // 名称下的小字
      if (this.view.k >= 0.7) {
        ctx.font = `${Math.max(8.5, 10.5 * this.view.k)}px sans-serif`;
        ctx.fillStyle = a.type === "co" ? "#475569" : "#a16207";
        ctx.textBaseline = "top";
        const sub = a.type === "co" ? (a.sub || "") : a.sub;
        const short = this._short(sub, 14);
        if (short) ctx.fillText(short, p.x, p.y + R + 5);
        ctx.textBaseline = "alphabetic";
      }
      ctx.restore();
    }

    _short(text, n) {
      if (!text) return "";
      return text.length > n ? text.slice(0, n) + "…" : text;
    }

    // ---------- 交互 ----------
    _bind() {
      const cv = this.cv;
      this._h = {
        down: (e) => this._down(e),
        move: (e) => this._move(e),
        up: (e) => this._up(e),
        wheel: (e) => this._wheel(e),
        dbl: (e) => this._dbl(e),
        leave: () => { this._downPos = null; },
      };
      cv.addEventListener("pointerdown", this._h.down);
      window.addEventListener("pointermove", this._h.move);
      window.addEventListener("pointerup", this._h.up);
      cv.addEventListener("pointerleave", this._h.leave);
      cv.addEventListener("wheel", this._h.wheel, { passive: false });
      cv.addEventListener("dblclick", this._h.dbl);
    }
    _unbind() {
      const cv = this.cv;
      cv.removeEventListener("pointerdown", this._h.down);
      window.removeEventListener("pointermove", this._h.move);
      window.removeEventListener("pointerup", this._h.up);
      cv.removeEventListener("pointerleave", this._h.leave);
      cv.removeEventListener("wheel", this._h.wheel);
      cv.removeEventListener("dblclick", this._h.dbl);
    }
    _pos(e) {
      const rect = this.cv.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }
    _hit(wx, wy) {
      let best = null, bestD = 26;
      for (const a of this.nodes) {
        const dx = a.x - wx, dy = a.y - wy;
        const d = Math.sqrt(dx * dx + dy * dy);
        const limit = Math.max(a.r, 14) * 1.15;
        if (d < Math.min(bestD, limit) && d < limit) { best = a; bestD = d; }
      }
      return best;
    }
    _down(e) {
      const p = this._pos(e);
      const w = this._s2w(p.x, p.y);
      const hit = this._hit(w.x, w.y);
      this._downPos = p;
      this._moved = false;
      if (hit) {
        this._drag = { mode: "node", id: hit.id };
        this.cv.classList.add("dragging");
      } else {
        this._drag = { mode: "pan" };
        this.cv.classList.add("dragging");
      }
      try { this.cv.setPointerCapture && this.cv.setPointerCapture(e.pointerId); } catch (_) { /* noop */ }
    }
    _move(e) {
      const p = this._pos(e);
      if (!this._drag) return;
      const dx = p.x - this._downPos.x, dy = p.y - this._downPos.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) this._moved = true;
      if (this._drag.mode === "pan") {
        this.view.x += dx; this.view.y += dy;
        this._downPos = p;
        this.render();
      } else {
        const w = this._s2w(p.x, p.y);
        const a = this.byId.get(this._drag.id);
        if (a) { a.x = w.x; a.y = w.y; this._dragLast = p; }
        this.render();
      }
    }
    _up() {
      this.cv.classList.remove("dragging");
      if (!this._drag) return;
      const wasNode = this._drag.mode === "node";
      const wasClick = !this._moved;
      const w = this._s2w(this._downPos.x, this._downPos.y);
      const hit = this._hit(w.x, w.y);
      this._drag = null;
      if (wasClick && hit && this.onSelect) {
        this.select(hit.id);
        this.onSelect(hit);
      }
    }
    _dbl(e) {
      const p = this._pos(e);
      const w = this._s2w(p.x, p.y);
      const hit = this._hit(w.x, w.y);
      if (hit && this.onDbl) this.onDbl(hit);
    }
    _wheel(e) {
      e.preventDefault();
      const rect = this.cv.getBoundingClientRect();
      const px = e.clientX - rect.left, py = e.clientY - rect.top;
      const w = this._s2w(px, py);
      const factor = Math.pow(1.0015, -e.deltaY);
      const k = Math.max(0.12, Math.min(3, this.view.k * factor));
      this.view.x = px - w.x * k;
      this.view.y = py - w.y * k;
      this.view.k = k;
      this.render();
    }
    select(id) {
      for (const a of this.nodes) a.selected = a.id === id;
      this._selected = this.byId.get(id) || null;
      this.render();
    }
  }

  window.GraphView = GraphView;
  window.GRAPH_STYLES = EDGE_STYLE;
  window.GRAPH_TYPE_COLOR = TYPE_COLOR;
})();
