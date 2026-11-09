import React from 'react';
import { CardWeather } from './card-weather.jsx';
import { WBC_ICONS } from './icons.jsx';

// Hide legacy branding in saved overview titles without rewriting Agent content.
export function homeDisplayTitle(spec) {
  return spec.home_kind === 'overview'
    ? spec.title.replace(/\bCyrene\b\s*[·:：|—–-]?\s*/gi, '').trim() || '今日概览'
    : spec.title;
}

// The Agent authors every headline and subtitle. This component only arranges them.
export function AssistantHomeLayout({ card, renderBlock, now = new Date() }) {
  const { spec } = card;
  const weather = spec.home_kind === 'overview' ? spec.blocks.find(block => block.type === 'weather') : null;
  const label = { resume: '继续进行', results: '最近成果' }[spec.home_kind];
  return <>
    {label && <div className="wbc-home-section-label">{label}</div>}
    <div className={'wbc-home-heading' + (weather ? ' with-weather' : '')}>
      <div className="wbc-home-intro">
        {spec.home_kind === 'overview' && <div className="wbc-home-date">{new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(now)}</div>}
        <h3>{homeDisplayTitle(spec)}</h3>{spec.subtitle && <p>{spec.subtitle}</p>}
      </div>
      {weather && <CardWeather block={weather} now={now} showMetadata={false} />}
    </div>
    <div className="wbc-home-content">{spec.blocks.filter(block => block !== weather).map(block => {
      if (spec.home_kind === 'resume' && block.type === 'timeline') return <details className="wbc-home-progress" key={block.block_id}>
        <summary className="wb-btn"><span>{block.title || '查看进度'}</span><span className="wbc-home-progress-count">{block.items.length} 项</span><span className="wbc-card-disclosure-icon" aria-hidden="true">{WBC_ICONS.chevronDown}</span></summary>
        {renderBlock(block)}
      </details>;
      return renderBlock(block);
    })}</div>
  </>;
}
