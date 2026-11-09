// CSS colors remain live in Leaflet SVG attributes and HTML swatches, so
// changing the application accent updates existing maps without remounting.
export function cardMapStyles(block, accent = 'var(--accent)') {
  const palette = [accent, ...[25, 45, 65].map(percent => `color-mix(in srgb, ${accent}, #000 ${percent}%)`),
    ...[20, 35, 50, 65].map(percent => `color-mix(in srgb, ${accent}, #fff ${percent}%)`)];
  const groups = (block.groups || []).map((group, index) => ({ ...group, color: palette[index % palette.length] }));
  const colors = new Map(groups.map(group => [group.group_id, group.color]));
  const places = new Map(block.places.map(place => [place.place_id, place]));
  return {
    groups,
    placeColor: place => colors.get(place.group_id) || accent,
    routeColor: route => {
      const from = places.get(route.from_place_id), to = places.get(route.to_place_id);
      const inherited = from?.group_id && from.group_id === to?.group_id ? from.group_id : null;
      return colors.get(route.group_id || inherited) || accent;
    },
  };
}
