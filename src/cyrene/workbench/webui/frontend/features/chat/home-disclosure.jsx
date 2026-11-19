import React, { useLayoutEffect, useRef, useState } from 'react';
import { WBC_ICONS } from './icons.jsx';

export function HomeToggle({ id, label, count, disclosure }) {
  const [active, setActive] = disclosure;
  return <button type="button" className="wb-btn wbc-home-toggle" aria-controls={id}
    aria-expanded={active === id} onClick={() => setActive(previous => previous === id ? null : id)}>
    <span>{label}</span>{count != null && <small>{count} 项</small>}
    <span aria-hidden="true" className="wbc-card-disclosure-icon">{WBC_ICONS.chevronDown}</span>
  </button>;
}

export function HomePanel({ id, disclosure, peek, children }) {
  const open = disclosure[0] === id;
  const hidden = !open && peek == null;
  return <div id={id} className="wbc-home-panel" data-open={open}
    aria-hidden={hidden || undefined} inert={hidden ? '' : undefined}
    style={peek != null ? { '--home-peek': peek + 'px' } : undefined}>
    <div className="wbc-home-panel-clip">{children}</div>
  </div>;
}

export function HomeWeatherReport({ id, text, disclosure }) {
  const paragraph = useRef(null);
  const [size, setSize] = useState(null);
  const [, setActive] = disclosure;
  useLayoutEffect(() => {
    const element = paragraph.current;
    function measure() {
      const height = element.getBoundingClientRect().height;
      if (!height) return;
      const limit = 2 * parseFloat(getComputedStyle(element).lineHeight);
      const overflow = height > limit + 0.5;
      const peek = Math.min(limit, height);
      setSize(previous => previous?.peek === peek && previous.overflow === overflow ? previous : { peek, overflow });
      if (!overflow) setActive(previous => previous === id ? null : previous);
    }
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [id, text, setActive]);
  return <div className="wbc-home-weather-report">
    {size?.overflow && <HomeToggle id={id} label="天气详情" disclosure={disclosure} />}
    <HomePanel id={id} disclosure={disclosure} peek={size?.peek ?? 0}>
      <p ref={paragraph}>{text}</p>
    </HomePanel>
  </div>;
}
