import React from 'react';
import { CardWeather } from './card-weather.jsx';
import { HomeToggle, HomePanel, HomeWeatherReport } from './home-disclosure.jsx';

// Hide legacy branding in saved overview titles without rewriting Agent content.
export function homeDisplayTitle(spec) {
  return spec.home_kind === 'overview'
    ? spec.title.replace(/\bCyrene\b\s*[·:：|—–-]?\s*/gi, '').trim() || '今日概览'
    : spec.title;
}

function foldable(kind, block) {
  return (block.type === 'action_list' && (block.collapsed || kind === 'results'))
    || (kind === 'resume' && block.type === 'timeline');
}

export function homePanelId(card, block) {
  return 'home-' + card.card_id + '-' + block.block_id;
}

export function AssistantHomeLayout({ card, renderBlock, disclosure, now = new Date() }) {
  const { spec } = card;
  const kind = spec.home_kind;
  const weather = kind === 'overview' ? spec.blocks.find(block => block.type === 'weather_observation' || block.type === 'weather') : null;
  const blocks = spec.blocks.filter(block => block !== weather
    && !(kind === 'overview' && block.type === 'action_list' && block.items.length === 0));
  const folds = blocks.filter(block => foldable(kind, block));
  const inHeader = kind !== 'overview' && folds.length === 1;
  const sectionLabel = { overview: '今日概览', resume: '继续进行', results: '最近成果' }[kind];
  function toggle(block) {
    const id = homePanelId(card, block);
    const fallback = block.type === 'timeline' ? '查看进度' : kind === 'results' ? '交付物' : '事项';
    const openTimeline = block.type === 'timeline' && disclosure[0] === id;
    const label = openTimeline ? (inHeader ? '收起进度' : '收起' + (block.title || '进度')) : block.title || fallback;
    return <HomeToggle key={id} id={id} label={label} count={openTimeline ? null : block.items.length} disclosure={disclosure} />;
  }
  function panel(block) {
    const id = homePanelId(card, block);
    return <HomePanel key={id} id={id} disclosure={disclosure}>{renderBlock(block, true)}</HomePanel>;
  }
  // Adjacent lists share a button row and full-width details at their authored position.
  const content = [];
  for (let i = 0; i < blocks.length;) {
    const block = blocks[i++];
    if (!foldable(kind, block)) { content.push(renderBlock(block)); continue; }
    const group = [block];
    while (i < blocks.length && foldable(kind, blocks[i])) group.push(blocks[i++]);
    if (!inHeader) content.push(<div key={'controls-' + block.block_id} className="wbc-home-toggles">{group.map(toggle)}</div>);
    content.push(...group.map(panel));
  }
  return <>
    <div className={'wbc-home-heading' + (weather ? ' with-weather' : '') + (inHeader ? ' with-toggle' : '')}>
      <div className="wbc-home-intro">
        <div className="wbc-home-section-label">{sectionLabel}</div>
        {kind === 'overview' && <div className="wbc-home-date">{new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(now)}</div>}
        <h3>{homeDisplayTitle(spec)}</h3>{!weather && spec.subtitle && <p>{spec.subtitle}</p>}
      </div>
      {weather && <CardWeather block={weather} now={now} homeArtwork showMetadata={false} />}
      {weather && spec.subtitle && <p className="wbc-home-subtitle">{spec.subtitle}</p>}
      {inHeader && toggle(folds[0])}
    </div>
    {weather?.advice && <HomeWeatherReport id={homePanelId(card, weather)} text={weather.advice} disclosure={disclosure} />}
    <div className="wbc-home-content">{content}</div>
  </>;
}
