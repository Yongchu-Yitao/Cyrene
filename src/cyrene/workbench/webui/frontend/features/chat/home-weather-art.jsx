import React, { useEffect, useRef } from 'react';
import { createWeatherPainter } from './home-weather-painter.mjs';

export function HomeWeatherArt({ kind, period }) {
  const canvas = useRef(null);
  useEffect(() => {
    const element = canvas.current;
    const ctx = element.getContext('2d');
    if (!ctx) return;
    const paint = createWeatherPainter(ctx, kind, period);
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    let elapsed = 1.1;
    let previous = null;
    let frame;
    let visible = false;
    function tick(now) {
      if (previous != null) elapsed += Math.min((now - previous) / 1000, 0.1);
      previous = now;
      paint(elapsed);
      frame = requestAnimationFrame(tick);
    }
    function sync() {
      cancelAnimationFrame(frame);
      previous = null;
      paint(reduced.matches ? 1.1 : elapsed);
      if (visible && !document.hidden && !reduced.matches && kind !== 'unknown') frame = requestAnimationFrame(tick);
    }
    const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; sync(); });
    observer.observe(element);
    reduced.addEventListener('change', sync);
    document.addEventListener('visibilitychange', sync);
    sync();
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      reduced.removeEventListener('change', sync);
      document.removeEventListener('visibilitychange', sync);
    };
  }, [kind, period]);
  return <canvas ref={canvas} width="320" height="232" className="wbc-home-sky" aria-hidden="true" />;
}
