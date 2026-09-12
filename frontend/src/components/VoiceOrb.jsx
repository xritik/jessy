import { useEffect, useRef } from "react";

const COLORS = {
  idle: [56, 217, 255],
  thinking: [168, 108, 255],
  speaking: [255, 90, 190],
  listening: [173, 226, 255],
  error: [255, 82, 82],
};

export default function VoiceOrb({ state = "idle", detail = "", size = 560 }) {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext("2d");
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let rotation = 0;
    let started = performance.now();

    // Fibonacci-distributed points create a clean surface-only neural shell.
    const nodes = Array.from({ length: 190 }, (_, i) => {
      const phi = Math.acos(1 - (2 * (i + 0.5)) / 190);
      const theta = Math.PI * (1 + Math.sqrt(5)) * i;
      return {
        x: Math.sin(phi) * Math.cos(theta),
        y: Math.cos(phi),
        z: Math.sin(phi) * Math.sin(theta),
        pulse: Math.random() * Math.PI * 2,
      };
    });

    const stars = Array.from({ length: 220 }, () => ({
      x: Math.random() * 2 - 1,
      y: Math.random() * 2 - 1,
      z: Math.random() * 2 - 1,
      s: Math.random() * 1.6 + 0.3,
    }));

    // Displayed color eases toward the target instead of snapping, so state
    // changes read as a mood shift rather than a hard cut.
    const liveColor = [...(COLORS[state] || COLORS.idle)];

    // Spin speed stays constant across all states — only color and the pump
    // (below) change. livePump eases toward its target so switching in or
    // out of "speaking" ramps smoothly rather than jumping.
    const SPIN_SPEED = 0.0016;
    const livePump = { amt: 0.015, speed: 0.0012 };
    let pumpPhase = 0;
    let lastTime = performance.now();

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const drawOrbit = (cx, cy, radius, index, time, r, g, b) => {
      const tilt = [-0.32, 0.16, 0.58][index];
      const vertical = [0.43, 0.25, 0.54][index];
      const angle = [0.12, -0.9, 0.62][index];
      const rx = radius * [1.72, 1.48, 1.62][index];
      const ry = radius * vertical;
      const phase = time * (0.00022 + index * 0.000055) + index * 1.8;

      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(angle);
      ctx.scale(1, 1 + tilt * 0.08);
      ctx.beginPath();
      for (let i = 0; i <= 100; i++) {
        const t = (i / 100) * Math.PI * 2;
        const x = Math.cos(t) * rx;
        const y = Math.sin(t) * ry;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.lineWidth = 0.9;
      ctx.strokeStyle = `rgba(${r},${g},${b},${state === "error" ? 0.24 : 0.15})`;
      ctx.shadowBlur = 8;
      ctx.shadowColor = `rgba(${r},${g},${b},.18)`;
      ctx.stroke();
      ctx.restore();

      // Two moving data nodes per orbit, trailed by a short fading tail.
      [phase, phase + Math.PI].forEach((t, particleIndex) => {
        const c = Math.cos(angle);
        const s = Math.sin(angle);
        const pulse = 0.72 + 0.28 * Math.sin(time * 0.004 + index + particleIndex);

        for (let trail = 3; trail >= 0; trail--) {
          const tt = t - trail * 0.05;
          const x0 = Math.cos(tt) * rx;
          const y0 = Math.sin(tt) * ry;
          const x = cx + x0 * c - y0 * s;
          const y = cy + x0 * s + y0 * c;
          const fade = 1 - trail / 4;
          ctx.save();
          ctx.fillStyle = `rgba(${r},${g},${b},${0.85 * pulse * fade})`;
          if (trail === 0) {
            ctx.shadowBlur = 13 + 7 * pulse;
            ctx.shadowColor = `rgba(${r},${g},${b},.95)`;
          }
          ctx.beginPath();
          ctx.arc(x, y, (2.15 + pulse * 0.8) * fade, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
        }
      });
    };

    const draw = (now) => {
      const time = reduceMotion ? 0 : now;
      const rect = canvas.getBoundingClientRect();
      const w = rect.width;
      const h = rect.height;
      const cx = w / 2;
      const cy = h / 2 - 8;
      const target = COLORS[state] || COLORS.idle;
      const ease = reduceMotion ? 1 : 0.025;
      for (let i = 0; i < 3; i++) liveColor[i] += (target[i] - liveColor[i]) * ease;
      const [r, g, b] = liveColor.map((v) => Math.round(v));

      // Medium-speed pump: a bigger, slower breath only while speaking.
      // Phase is accumulated from elapsed time (dt), never from time * speed —
      // multiplying a large absolute timestamp by an easing speed is what
      // caused the rapid flicker while the pump ramped up or down.
      const dt = reduceMotion ? 0 : Math.min(now - lastTime, 64);
      lastTime = now;
      const targetPump = state === "speaking" ? { amt: 0.1, speed: 0.0032 } : { amt: 0, speed: 0.0012 };
      livePump.amt += (targetPump.amt - livePump.amt) * ease;
      livePump.speed += (targetPump.speed - livePump.speed) * ease;
      pumpPhase += livePump.speed * dt;
      const breathe = reduceMotion ? 1 : 1 + livePump.amt * Math.sin(pumpPhase);

      // baseRadius stays fixed for the orbits and outer glow. coreRadius is
      // the only pumped value — it drives the sphere's body, its neural
      // mesh, and its own center light, and nothing else.
      const baseRadius = Math.min(size * 0.32, Math.min(w, h) * 0.30);
      const coreRadius = baseRadius * breathe;

      const activity =
        state === "thinking" ? 1.7 :
        state === "speaking" ? 1.45 :
        state === "listening" ? 1.25 :
        state === "error" ? 1.55 : 0.65;
      rotation += reduceMotion ? 0 : SPIN_SPEED;

      // The outer ambient glow's color still eases smoothly, but it no
      // longer scales with the pump — only the sphere itself pumps.
      if (containerRef.current) {
        containerRef.current.style.boxShadow =
          `inset 0 0 60px rgba(${r},${g},${b},0.05), 0 0 40px rgba(${r},${g},${b},0.08)`;
      }

      ctx.clearRect(0, 0, w, h);

      // Faint atmosphere sets the orb in space before anything else is drawn.
      const atmosphere = ctx.createRadialGradient(cx, cy, baseRadius * 0.2, cx, cy, baseRadius * 2.1);
      atmosphere.addColorStop(0, `rgba(${r},${g},${b},.07)`);
      atmosphere.addColorStop(0.6, `rgba(${r},${g},${b},.03)`);
      atmosphere.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = atmosphere;
      ctx.fillRect(0, 0, w, h);

      // Space dust, drifting slightly with rotation for a sense of parallax.
      stars.forEach((star) => {
        const drift = reduceMotion ? 0 : rotation * 6;
        const sx = cx + (star.x * Math.cos(drift * 0.02) - star.y * Math.sin(drift * 0.02)) * w * 0.48;
        const sy = cy + (star.x * Math.sin(drift * 0.02) + star.y * Math.cos(drift * 0.02)) * h * 0.48;
        const twinkle = 0.1 + 0.2 * Math.sin(time * 0.001 + star.z * 8);
        ctx.fillStyle = `rgba(110,210,255,${twinkle})`;
        ctx.fillRect(sx, sy, star.s, star.s);
      });

      // Soft translucent body gives the shell volume without hiding the
      // wireframe inside it.
      const body = ctx.createRadialGradient(
        cx - coreRadius * 0.22, cy - coreRadius * 0.28, coreRadius * 0.02,
        cx, cy, coreRadius * 1.05
      );
      body.addColorStop(0, `rgba(${r},${g},${b},.10)`);
      body.addColorStop(0.55, `rgba(${r},${g},${b},.05)`);
      body.addColorStop(0.85, `rgba(${r},${g},${b},.025)`);
      body.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = body;
      ctx.beginPath();
      ctx.arc(cx, cy, coreRadius, 0, Math.PI * 2);
      ctx.fill();

      // Three orbital rings with two glowing, trailed particles on every orbit.
      // These stay on baseRadius so they never pump with the sphere.
      [0, 1, 2].forEach((index) => drawOrbit(cx, cy, baseRadius, index, time, r, g, b));

      // Surface-only neural network. Back-facing points and edges are discarded.
      const projected = nodes.map((n) => {
        const x1 = n.x * Math.cos(rotation) - n.z * Math.sin(rotation);
        const z1 = n.x * Math.sin(rotation) + n.z * Math.cos(rotation);
        const perspective = 1 / (1.92 - z1 * 0.38);
        return { x: cx + x1 * coreRadius * perspective, y: cy + n.y * coreRadius * perspective, z: z1 };
      });

      ctx.lineWidth = 0.65;
      for (let i = 0; i < projected.length; i++) {
        const a = projected[i];
        if (a.z < 0.02) continue;
        for (let j = i + 1; j < projected.length; j++) {
          const b2 = projected[j];
          if (b2.z < 0.02) continue;
          const d = Math.hypot(a.x - b2.x, a.y - b2.y);
          if (d < coreRadius * 0.24) {
            ctx.strokeStyle = `rgba(${r},${g},${b},${0.06 + Math.min(0.1, ((coreRadius * 0.24 - d) / coreRadius) * 0.6)})`;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b2.x, b2.y);
            ctx.stroke();
          }
        }
      }

      projected.forEach((p) => {
        if (p.z < 0.02) return;
        const alpha = 0.65 + p.z * 0.35;
        ctx.fillStyle = `rgba(${r},${g},${b},${alpha})`;
        ctx.shadowBlur = 8 + p.z * 12;
        ctx.shadowColor = `rgba(${r},${g},${b},1)`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 1.0 + p.z * 1.4, 0, Math.PI * 2);
        ctx.fill();
      });
      ctx.shadowBlur = 0;

      // Animated surface data pulses, traveling between visible nodes.
      const visible = projected.filter((p) => p.z > 0.12);
      for (let k = 0; k < 10; k++) {
        const a = visible[(k * 7 + Math.floor(time / 720)) % Math.max(1, visible.length)];
        const b2 = visible[(k * 11 + 9) % Math.max(1, visible.length)];
        if (!a || !b2) continue;
        const t = (time / (1250 + k * 65) + k * 0.17) % 1;
        const px = a.x + (b2.x - a.x) * t;
        const py = a.y + (b2.y - a.y) * t;
        ctx.fillStyle = `rgba(${r},${g},${b},${0.48 + 0.28 * Math.sin(t * Math.PI)})`;
        ctx.shadowBlur = 12;
        ctx.shadowColor = `rgba(${r},${g},${b},.95)`;
        ctx.beginPath();
        ctx.arc(px, py, 1.35 + activity * 0.35, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
      }

      // One breathing core light at the center, blended additively for glow.
      const corePulse = reduceMotion ? 0.85 : 0.72 + 0.28 * Math.sin(time * 0.0035);
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreRadius * 0.22 * corePulse);
      core.addColorStop(0, `rgba(${r},${g},${b},${0.5 * corePulse})`);
      core.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, coreRadius * 0.22 * corePulse, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      if (!reduceMotion) raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    if (reduceMotion) draw(started);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [state, detail, size]);

  const initialGlow = (COLORS[state] || COLORS.idle).join(",");

  return (
    <canvas
      ref={canvasRef}
      aria-label={`JARVIS ${state}`}
      style={{ width: "100%", height: "100%", display: "block" }}
    />
  );
}