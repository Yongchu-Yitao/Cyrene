const kinds = new Set(['clear', 'partly_cloudy', 'cloudy', 'rain', 'snow', 'storm', 'fog', 'night']);
export function weatherKind(block) {
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
