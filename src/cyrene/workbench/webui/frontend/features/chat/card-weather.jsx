import React from 'react';
import { weatherKind } from './card-weather-model.mjs';

export function CardWeather({ block, showMetadata = true }) {
  const kind = weatherKind(block);
  const sun = ['clear', 'partly_cloudy'].includes(kind);
  const cloud = ['partly_cloudy', 'cloudy', 'rain', 'snow', 'storm'].includes(kind);
  return <div className={'wbc-weather-scene weather-' + kind}>
    <div className="wbc-weather-main">
      <div className="wbc-weather-copy"><span className="wbc-weather-location">{block.location}</span><strong className="wbc-weather-temperature">{block.temperature}</strong><span>{block.condition}</span>{block.range && <small>{block.range}</small>}</div>
      <div className="wbc-weather-art" aria-hidden="true">
        <svg viewBox="0 0 160 140" focusable="false">
          {sun && <g className="weather-sun"><g className="weather-rays" stroke="currentColor" strokeWidth="3" strokeLinecap="round">{Array.from({length: 8}, (_, i) => <path key={i} d="M 78 13 L 78 20" transform={`rotate(${i * 45} 78 48)`} />)}</g><circle className="weather-sun-halo" cx="78" cy="48" r="29"/><circle className="weather-sun-disc" cx="78" cy="48" r="22"/></g>}
          {kind === 'night' && <g className="weather-moon"><path d="M100 25a32 32 0 1 0 22 50A33 33 0 0 1 100 25Z"/><circle cx="47" cy="30" r="2"/><circle cx="130" cy="40" r="3"/><circle cx="115" cy="104" r="2"/></g>}
          <g className={cloud ? 'weather-cloud-system' : undefined}>
          {cloud && <g className="weather-cloud"><path d="M43 96a20 20 0 0 1-1-40 29 29 0 0 1 55-10 23 23 0 0 1 27 24 14 14 0 0 1-3 27Z"/><path className="weather-cloud-highlight" d="M48 57a26 26 0 0 1 44-7"/></g>}
          {(kind === 'rain' || kind === 'storm') && <g className="weather-rain" stroke="currentColor" strokeWidth="4" strokeLinecap="round">{[52,80,108].map((x,i)=><path key={x} className={'weather-drop drop-'+i} d={`M${x} 106 v10`} />)}</g>}
          {kind === 'storm' && <path className="weather-lightning" d="m87 83-13 23h13l-7 24 25-33H91l7-14Z"/>}
          {kind === 'snow' && <g className="weather-snow">{[52,80,108].map((x,i)=><g key={x} className={'weather-flake drop-'+i} stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d={`M${x} 106v12m-5-9 10 6m-10 0 10-6`}/></g>)}</g>}
          </g>
          {kind === 'fog' && <g className="weather-fog" stroke="currentColor" strokeWidth="7" strokeLinecap="round"><path d="M36 48h88M26 68h104M42 88h75M32 108h92"/></g>}
          {kind === 'unknown' && <g className="weather-fog" fill="none" stroke="currentColor" strokeWidth="3"><circle cx="80" cy="70" r="34"/><path d="M80 51v25m0 8v5"/></g>}
        </svg>
      </div>
    </div>
    {block.advice && <p className="wbc-weather-advice">{block.advice}</p>}
    {showMetadata && <small className="wbc-weather-source">{[block.source, block.updated_at].filter(Boolean).join(' · ')}</small>}
  </div>;
}
