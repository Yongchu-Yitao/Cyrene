// WGS-84 → GCJ-02 (火星坐标) — AMap tiles use GCJ-02, so raw WGS pins must be
// shifted or they land ~500m off. Same math as the legacy map view.
function wbcWgs84ToGcj02(wgsLat, wgsLng) {
  if (wgsLng < 72.004 || wgsLng > 137.8347 || wgsLat < 0.8293 || wgsLat > 55.8271) return [wgsLat, wgsLng];
  var pi = 3.1415926535897932384626, a = 6378245.0, ee = 0.00669342162296594323;
  function tLat(x, y) {
    var r = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
    r += (20.0 * Math.sin(6.0 * x * pi) + 20.0 * Math.sin(2.0 * x * pi)) * 2.0 / 3.0;
    r += (20.0 * Math.sin(y * pi) + 40.0 * Math.sin(y / 3.0 * pi)) * 2.0 / 3.0;
    r += (160.0 * Math.sin(y / 12.0 * pi) + 320.0 * Math.sin(y * pi / 30.0)) * 2.0 / 3.0;
    return r;
  }
  function tLng(x, y) {
    var r = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
    r += (20.0 * Math.sin(6.0 * x * pi) + 20.0 * Math.sin(2.0 * x * pi)) * 2.0 / 3.0;
    r += (20.0 * Math.sin(x * pi) + 40.0 * Math.sin(x / 3.0 * pi)) * 2.0 / 3.0;
    r += (150.0 * Math.sin(x / 12.0 * pi) + 300.0 * Math.sin(x / 30.0 * pi)) * 2.0 / 3.0;
    return r;
  }
  var dlat = tLat(wgsLng - 105.0, wgsLat - 35.0);
  var dlng = tLng(wgsLng - 105.0, wgsLat - 35.0);
  var radlat = wgsLat / 180.0 * pi;
  var magic = Math.sin(radlat);
  magic = 1 - ee * magic * magic;
  var sqrtmagic = Math.sqrt(magic);
  dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi);
  dlng = (dlng * 180.0) / (a / sqrtmagic * Math.cos(radlat) * pi);
  return [wgsLat + dlat, wgsLng + dlng];
}

// Same provider setting as the legacy map ("direct" = CARTO, "amap" = 高德).
function wbcMapProvider() {
  try { return localStorage.getItem("cyrene-tweak-map-provider") || "direct"; } catch (e) { return "direct"; }
}

function wbcTileConfig(provider) {
  var isDark = document.documentElement.dataset.theme === "dark";
  if (provider === "amap") {
    return {
      url: "https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=" + (isDark ? 8 : 7) + "&x={x}&y={y}&z={z}",
      options: { keepBuffer: 4, updateWhenZooming: false, updateWhenIdle: true },
    };
  }
  return {
    url: "https://{s}.basemaps.cartocdn.com/" + (isDark ? "dark_all" : "light_all") + "/{z}/{x}/{y}{r}.png",
    options: { subdomains: "abcd", keepBuffer: 4, updateWhenZooming: false, updateWhenIdle: true },
  };
}


export { wbcWgs84ToGcj02, wbcMapProvider, wbcTileConfig };
