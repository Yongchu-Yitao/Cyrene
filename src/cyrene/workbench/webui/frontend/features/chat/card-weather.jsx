import React from 'react';
import { weatherKind, weatherPeriod } from './card-weather-model.mjs';

// Distinct silhouettes stay legible at the compact home icon size.
const cloudShapes = {
  partly_cloudy: {
    back: 'M104 92a10 10 0 0 1 0-20 14 14 0 0 1 27 3 9 9 0 0 1 0 17Z',
    front: 'M39 105a16 16 0 0 1-2-32 22 22 0 0 1 41-9 18 18 0 0 1 24 18 12 12 0 0 1-1 23Z',
    highlight: 'M43 73a19 19 0 0 1 31-7',
  },
  cloudy: {
    back: 'M57 78a17 17 0 0 1 0-34 25 25 0 0 1 47-7 20 20 0 0 1 30 19 11 11 0 0 1-1 22Z',
    front: 'M29 105a17 17 0 0 1 0-34 22 22 0 0 1 39-10 23 23 0 0 1 36 17 14 14 0 0 1 17 14 13 13 0 0 1-13 13Z',
    highlight: 'M33 72a19 19 0 0 1 31-7',
  },
  rain: {
    back: 'M45 72a16 16 0 0 1 0-32 27 27 0 0 1 48-7 23 23 0 0 1 30 23 12 12 0 0 1-12 16Z',
    front: 'M30 91a18 18 0 0 1 0-36 25 25 0 0 1 45-7 25 25 0 0 1 38 15 14 14 0 0 1 19 14 14 14 0 0 1-14 14Z',
    highlight: 'M34 56a22 22 0 0 1 36-6',
  },
  snow: {
    back: 'M74 74a15 15 0 0 1 0-30 19 19 0 0 1 36-4 18 18 0 0 1 24 18 16 16 0 0 1-16 16Z',
    front: 'M38 94a20 20 0 0 1-6-39 24 24 0 0 1 45-10 21 21 0 0 1 33 17 17 17 0 0 1 8 32c-10 5-18 2-25 0-9 5-18 5-26 0-10 5-20 5-29 0Z',
    highlight: 'M38 55a20 20 0 0 1 33-9',
  },
  storm: {
    back: 'M46 65a17 17 0 0 1-3-33 20 20 0 0 1 37-12 22 22 0 0 1 34 14 18 18 0 0 1 17 18 13 13 0 0 1-13 13Z',
    front: 'M29 86a18 18 0 0 1-1-36 25 25 0 0 1 46-10 26 26 0 0 1 39 18 14 14 0 0 1 20 13 15 15 0 0 1-15 15Z',
    highlight: 'M32 51a21 21 0 0 1 36-8',
  },
};

export function CardWeather({ block, showMetadata = true, now = new Date() }) {
  const kind = weatherKind(block, now);
  const period = weatherPeriod(now);
  const sun = period === 'day' && ['clear', 'partly_cloudy'].includes(kind);
  const moon = period === 'night' && ['night', 'partly_cloudy'].includes(kind);
  const cloud = cloudShapes[kind];
  return <div className={'wbc-weather-scene weather-' + kind} data-period={period}>
    <div className="wbc-weather-main">
      <div className="wbc-weather-copy"><span className="wbc-weather-location">{block.location}</span><strong className="wbc-weather-temperature">{block.temperature}</strong><span>{block.condition}</span>{block.range && <small>{block.range}</small>}</div>
      <div className="wbc-weather-art" aria-hidden="true">
        <svg viewBox="0 0 160 140" focusable="false">
          {sun && <g className="weather-sun" transform={cloud ? undefined : "translate(2 22)"}><g className="weather-rays" stroke="currentColor" strokeWidth="3" strokeLinecap="round">{Array.from({length: 8}, (_, i) => <path key={i} d="M 78 13 L 78 20" transform={`rotate(${i * 45} 78 48)`} />)}</g><circle className="weather-sun-halo" cx="78" cy="48" r="29"/><circle className="weather-sun-disc" cx="78" cy="48" r="22"/></g>}
          {moon && <g transform={cloud ? "translate(28 -16) scale(.85)" : "translate(-9 15)"}><g className="weather-moon"><path d="M100 25a32 32 0 1 0 22 50A33 33 0 0 1 100 25Z"/><circle cx="47" cy="30" r="2"/><circle cx="130" cy="40" r="3"/><circle cx="115" cy="104" r="2"/></g></g>}
          <WeatherConditions kind={kind} cloud={cloud} />
        </svg>
      </div>
    </div>
    {block.advice && <p className="wbc-weather-advice">{block.advice}</p>}
    {showMetadata && <small className="wbc-weather-source">{[block.source, block.updated_at].filter(Boolean).join(' · ')}</small>}
  </div>;
}

function WeatherConditions({ kind, cloud }) {
  return <>
          <g className={cloud ? 'weather-cloud-system' : undefined}>
          {cloud && <g className="weather-cloud">
            <path className="weather-cloud-back" d={cloud.back}/>
            <path className="weather-cloud-front" d={cloud.front}/>
            <path className="weather-cloud-highlight" d={cloud.highlight}/>
          </g>}
          {(kind === 'rain' || kind === 'storm') && <g className="weather-rain" stroke="currentColor" strokeWidth="4" strokeLinecap="round">{(kind === 'storm' ? [44,120] : [52,80,108]).map((x,i)=><path key={x} className={'weather-drop drop-'+i} d={`M${x} 106 v10`} />)}</g>}
          {kind === 'storm' && <path className="weather-lightning" d="M82 74 66 103H81L72 132 105 94H88L100 74Z"/>}
          {kind === 'snow' && <g className="weather-snow">{[52,80,108].map((x,i)=><g key={x} className={'weather-flake drop-'+i} stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d={`M${x} 106v12m-5-9 10 6m-10 0 10-6`}/></g>)}</g>}
          </g>
          {kind === 'fog' && <g className="weather-fog" stroke="currentColor" strokeWidth="7" strokeLinecap="round"><path d="M36 48h88M26 68h104M42 88h75M32 108h92"/></g>}
          {kind === 'unknown' && <g className="weather-fog" fill="none" stroke="currentColor" strokeWidth="3"><circle cx="80" cy="70" r="34"/><path d="M80 51v25m0 8v5"/></g>}
  </>;
}
