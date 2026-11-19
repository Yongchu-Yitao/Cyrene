const conditionLabels = {
  clear: '晴', partly_cloudy: '多云', cloudy: '阴', rain: '雨', snow: '雪', storm: '雷雨', fog: '雾', night: '晴',
};
const kinds = new Set(Object.keys(conditionLabels));

export function weatherReadout(block) {
  if (block.type !== 'weather_observation') return block;
  const condition = block.condition_label || conditionLabels[block.condition_code];
  const temperature = block.temperature_c == null ? null : block.temperature_c + '°C';
  const range = block.range_c && '今天 ' + block.range_c.low + '–' + block.range_c.high + '°C';
  if (temperature == null) return { location: [block.location, condition].filter(Boolean).join(' · '), range };
  return { location: block.location, temperature, condition, range };
}

// Match the home clock; saved observations must not freeze the display in daytime.
export function weatherPeriod(now = new Date()) {
  const hour = now.getHours();
  return hour < 6 || hour >= 18 ? 'night' : 'day';
}

export function weatherKind(block, now = new Date()) {
  const kind = block.type === 'weather_observation' ? block.condition_code ?? 'unknown' : conditionKind(block);
  if (kind === 'clear' || kind === 'night') return weatherPeriod(now) === 'night' ? 'night' : 'clear';
  return kind;
}

function conditionKind(block) {
  if (kinds.has(block.icon)) return block.icon;
  const condition = String(block.condition || '').toLowerCase();
  if (/雷|storm|thunder/.test(condition)) return 'storm';
  if (/雪|snow|sleet/.test(condition)) return 'snow';
  if (/雨|rain|drizzle|shower/.test(condition)) return 'rain';
  if (/雾|霾|fog|mist|haze/.test(condition)) return 'fog';
  if (/多云|少云|partly|mostly sunny/.test(condition)) return 'partly_cloudy';
  if (/阴|cloud|overcast/.test(condition)) return 'cloudy';
  if (/晴|clear|sunny/.test(condition)) return 'clear';
  return 'unknown';
}
