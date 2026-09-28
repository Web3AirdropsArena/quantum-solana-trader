// True 3D coordinates projected onto a native canvas; no remote renderer required.
export const signals = [
  ["market", "Market", -2.5, 0, 0],
  ["trend", "Momentum", -1.5, 1.3, 0.9],
  ["reversion", "Reversion", -1.6, -0.9, 1],
  ["breakout", "Breakout", -1.4, -0.1, -1.5],
  ["model", "Probability", 0, 0.65, 0],
  ["experts", "Expert votes", 0, -0.8, 0.7],
  ["risk", "Risk policy", 1.35, 0.35, -0.4],
  ["action", "Decision", 2.6, 0, 0.4],
  ["memory", "Memory", 0.1, -1.8, -0.8],
  ["jev", "Jev review", 1.1, 1.6, 1],
];
const edges = [
  [0, 1],
  [0, 2],
  [0, 3],
  [1, 4],
  [2, 4],
  [3, 4],
  [1, 5],
  [2, 5],
  [3, 5],
  [4, 6],
  [5, 6],
  [6, 7],
  [4, 8],
  [5, 8],
  [8, 4],
  [9, 6],
];

export class Scene {
  constructor(canvas, mini = false, onSelect = () => {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.mini = mini;
    this.angle = -0.12;
    this.pitch = -0.16;
    this.zoom = 1;
    this.data = {};
    this.motion = !matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.last = 0;
    this.visible = true;
    this.points = [];
    this.drag = null;
    new IntersectionObserver((entries) => {
      this.visible = entries[0].isIntersecting;
    }).observe(canvas);
    this.resize = new ResizeObserver(() => this.draw());
    this.resize.observe(canvas);
    if (!mini) {
      canvas.addEventListener("pointerdown", (e) => {
        this.drag = {
          x: e.clientX,
          y: e.clientY,
          startX: e.clientX,
          startY: e.clientY,
        };
        canvas.setPointerCapture(e.pointerId);
      });
      canvas.addEventListener("pointermove", (e) => {
        if (!this.drag) return;
        this.angle += (e.clientX - this.drag.x) * 0.008;
        this.pitch = Math.max(
          -1,
          Math.min(1, this.pitch + (e.clientY - this.drag.y) * 0.006),
        );
        this.drag.x = e.clientX;
        this.drag.y = e.clientY;
        this.draw();
      });
      canvas.addEventListener("pointerup", (e) => {
        if (
          this.drag &&
          Math.hypot(
            e.clientX - this.drag.startX,
            e.clientY - this.drag.startY,
          ) < 8
        ) {
          const rect = canvas.getBoundingClientRect();
          const x = e.clientX - rect.left,
            y = e.clientY - rect.top;
          const hit = this.points.find(
            (p) => Math.hypot(p.x - x, p.y - y) < 25,
          );
          if (hit) onSelect(hit.id);
        }
        this.drag = null;
      });
      canvas.addEventListener("pointercancel", () => (this.drag = null));
      canvas.addEventListener(
        "wheel",
        (e) => {
          e.preventDefault();
          this.zoom = Math.max(
            0.55,
            Math.min(1.8, this.zoom - e.deltaY * 0.001),
          );
          this.draw();
        },
        { passive: false },
      );
      canvas.addEventListener("keydown", (e) => {
        if (
          ![
            "ArrowLeft",
            "ArrowRight",
            "ArrowUp",
            "ArrowDown",
            "+",
            "-",
          ].includes(e.key)
        )
          return;
        e.preventDefault();
        if (e.key === "ArrowLeft") this.angle -= 0.12;
        if (e.key === "ArrowRight") this.angle += 0.12;
        if (e.key === "ArrowUp") this.pitch -= 0.1;
        if (e.key === "ArrowDown") this.pitch += 0.1;
        if (e.key === "+") this.zoom = Math.min(1.8, this.zoom + 0.1);
        if (e.key === "-") this.zoom = Math.max(0.55, this.zoom - 0.1);
        this.draw();
      });
    }
    this.frame = (t) => {
      if (this.visible && !document.hidden && t - this.last > 40) {
        if (this.motion && !this.drag) this.angle += 0.0015;
        this.draw();
        this.last = t;
      }
      requestAnimationFrame(this.frame);
    };
    requestAnimationFrame(this.frame);
  }
  update(data) {
    this.data = data;
    this.draw();
  }
  reset() {
    this.angle = -0.12;
    this.pitch = -0.16;
    this.zoom = 1;
    this.draw();
  }
  draw() {
    const { canvas: c, ctx, mini } = this;
    const rect = c.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const w = rect.width,
      h = rect.height,
      dpr = Math.min(devicePixelRatio || 1, 2);
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const scale = Math.min(w / (mini ? 6.5 : 7.5), h / 5.4) * this.zoom;
    const project = (x, y, z) => {
      const x1 = x * Math.cos(this.angle) + z * Math.sin(this.angle),
        z1 = -x * Math.sin(this.angle) + z * Math.cos(this.angle);
      const y1 = y * Math.cos(this.pitch) - z1 * Math.sin(this.pitch),
        z2 = y * Math.sin(this.pitch) + z1 * Math.cos(this.pitch);
      const perspective = 7 / (7 + z2);
      return {
        x: w / 2 + x1 * scale * perspective,
        y: h * 0.46 + y1 * scale * perspective,
        z: z2,
        s: perspective,
      };
    };
    ctx.lineWidth = 0.6;
    ctx.strokeStyle = "#35433955";
    for (let i = -5; i <= 5; i++) {
      let a = project(i, 1.9, -4),
        b = project(i, 1.9, 4);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      a = project(-5, 1.9, i);
      b = project(5, 1.9, i);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    this.points = signals.map(([id, label, x, y, z]) => ({
      ...project(x, y, z),
      id,
      label,
    }));
    edges.forEach(([a, b], i) => {
      const p = this.points[a],
        q = this.points[b];
      ctx.strokeStyle = "#a9e99039";
      ctx.lineWidth = mini ? 0.6 : 1;
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(q.x, q.y);
      ctx.stroke();
      if (this.motion) {
        const t = (this.last / 4000 + i * 0.17) % 1;
        ctx.fillStyle = "#c1f7a3aa";
        ctx.beginPath();
        ctx.arc(
          p.x + (q.x - p.x) * t,
          p.y + (q.y - p.y) * t,
          1.7,
          0,
          Math.PI * 2,
        );
        ctx.fill();
      }
    });
    [...this.points]
      .sort((a, b) => b.z - a.z)
      .forEach((p) => {
        const r = (p.id === "model" ? 12 : 7) * p.s * (mini ? 0.75 : 1);
        ctx.beginPath();
        ctx.arc(p.x, p.y, r * 2, 0, Math.PI * 2);
        ctx.fillStyle = "#9fdb8120";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fillStyle = p.id === "action" ? "#c1f7a3" : "#243b2a";
        ctx.fill();
        ctx.strokeStyle = "#b2e597";
        ctx.lineWidth = 1;
        ctx.stroke();
        if (!mini) {
          ctx.textAlign = "center";
          ctx.font = '10px "Segoe UI", sans-serif';
          ctx.fillStyle = "#d0dace";
          ctx.fillText(p.label.toUpperCase(), p.x, p.y + r + 20);
          ctx.fillStyle = "#91ab96";
          ctx.font = "11px monospace";
          ctx.fillText(this.data[p.id] ?? "—", p.x, p.y + r + 36);
        }
      });
  }
}
