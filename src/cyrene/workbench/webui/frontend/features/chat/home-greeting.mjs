// Only the leading salutation is time-dependent. Agent-authored task copy stays intact.
export function currentHomeGreeting(text, now = new Date()) {
  if (typeof text !== 'string') return text;
  const hour = now.getHours();
  if (!Number.isFinite(hour)) return text;
  const greeting = hour < 5 || hour >= 23 ? '夜深了' : hour < 11 ? '早上好' : hour < 13 ? '中午好' : hour < 18 ? '下午好' : '晚上好';
  return text.replace(/^(\s*(?:#{1,6}\s+)?(?:\*\*|__)?)(早上好|早安|上午好|中午好|下午好|傍晚好|晚上好|夜深了)(?=(?:\*\*|__)?(?:$|[\s，,。.!！：:、]))/u,
    (_, prefix) => prefix + greeting);
}

export function presentHomeSpec(spec, now = new Date()) {
  if (spec.home_kind !== 'overview') return spec;
  return {...spec, title:currentHomeGreeting(spec.title,now), subtitle:currentHomeGreeting(spec.subtitle,now),
    blocks:spec.blocks.map(block => block.type !== 'text' ? block : {
      ...block, title:currentHomeGreeting(block.title,now), text:currentHomeGreeting(block.text,now),
    })};
}
