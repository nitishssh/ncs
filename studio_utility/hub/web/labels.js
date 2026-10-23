// Readable labels for raw CCAPI values - mirrors studio_utility/labels.py
const FRACTIONS = { "1/3": "⅓", "2/3": "⅔", "1/2": "½" };
const NAMED = {
  auto: "Auto", awbwhite: "AWB white", whitefluorescent: "Fluorescent", colortemp: "Color temp",
  spot: "Spot AF", "1point": "1-point", expand_cross: "Expand (cross)", expand_around: "Expand (around)",
  whole_area: "Whole area", single: "Single",
};
const SETTING_NAMES = {
  movie_size: "Movie size", iso: "ISO", tv: "Shutter", av: "Aperture", exposure: "Exposure comp.",
  wb: "White balance", af_method: "AF method", picture_style: "Picture style",
};

function label(key, value) {
  const v = String(value);
  if (key === "movie_size") {
    const m = v.match(/^(\d+)x(\d+)_(\d+)_([a-z]+)(?:_([a-z]+))?(?:_(crop))?/);
    if (m) {
      const res = { 3840: "4K", 1920: "FHD", 1280: "HD" }[m[1]] || `${m[1]}×${m[2]}`;
      return `${res}${m[6] ? " Crop" : ""} ${+m[3] / 100}p`;
    }
  }
  if (key === "exposure") {
    const m = v.match(/^([+-])(\d+)(?:\.0|_(\d\/\d))$/);
    if (m) {
      if (m[2] === "0" && !m[3]) return "±0";
      return `${m[1] === "-" ? "−" : "+"}${m[2] === "0" && m[3] ? "" : m[2]}${m[3] ? FRACTIONS[m[3]] || "" : ""}`;
    }
  }
  if (key === "av" && /^f\d/.test(v)) return "f/" + v.slice(1);
  if (key === "tv" && /^\d+"\d+$/.test(v)) return v.replace('"', ".") + " s";
  if (key === "tv" && v.endsWith('"')) return v.slice(0, -1) + " s";
  if (NAMED[v]) return NAMED[v];
  const z = v.match(/^flexible_zone_(\d)$/);
  if (z) return `Flexible zone ${z[1]}`;
  if (/^[a-z][a-z0-9_]*$/.test(v)) return v[0].toUpperCase() + v.slice(1).replace(/_/g, " ");
  return v;
}
