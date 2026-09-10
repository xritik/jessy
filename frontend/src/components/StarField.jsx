import { useMemo } from "react";

const STATIC_STAR_COUNT = 140;
const TWINKLE_STAR_COUNT = 18;

function buildStaticShadow(count) {
  const shadows = [];
  for (let i = 0; i < count; i++) {
    const x = (Math.random() * 100).toFixed(2);
    const y = (Math.random() * 100).toFixed(2);
    const opacity = (0.35 + Math.random() * 0.5).toFixed(2);
    shadows.push(`${x}vw ${y}vh 0 rgba(255,255,255,${opacity})`);
  }
  return shadows.join(", ");
}

function buildTwinkleStars(count) {
  return Array.from({ length: count }, (_, i) => ({
    id: i,
    top: Math.random() * 100,
    left: Math.random() * 100,
    delay: (Math.random() * 6).toFixed(2),
    duration: (3 + Math.random() * 4).toFixed(2),
  }));
}

/**
 * Lightweight CSS-only star field. The bulk of the stars are painted
 * via a single 1px div's box-shadow list (no extra DOM nodes, no JS
 * animation loop). A small subset of stars are real elements with a
 * CSS opacity-pulse animation to add gentle twinkle without touching
 * the majority of dots.
 */
export default function StarField() {
  const staticShadow = useMemo(() => buildStaticShadow(STATIC_STAR_COUNT), []);
  const twinkleStars = useMemo(() => buildTwinkleStars(TWINKLE_STAR_COUNT), []);

  return (
    <div className="starfield" aria-hidden="true">
      <div className="starfield-static" style={{ boxShadow: staticShadow }} />
      {twinkleStars.map((s) => (
        <span
          key={s.id}
          className="starfield-twinkle"
          style={{
            top: `${s.top}%`,
            left: `${s.left}%`,
            animationDelay: `${s.delay}s`,
            animationDuration: `${s.duration}s`,
          }}
        />
      ))}
    </div>
  );
}
