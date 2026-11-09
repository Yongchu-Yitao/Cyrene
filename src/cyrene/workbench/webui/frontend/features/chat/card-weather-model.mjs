const kinds = new Set(['clear', 'partly_cloudy', 'cloudy', 'rain', 'snow', 'storm', 'fog', 'night']);
// Match the home clock; saved observations must not freeze the display in daytime.
export function weatherPeriod(now = new Date()) {
  const hour = now.getHours();
  return hour < 6 || hour >= 18 ? 'night' : 'day';
}

export function weatherKind(block, now = new Date()) {
  const kind = conditionKind(block);
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
