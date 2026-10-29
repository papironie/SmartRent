// SmartRent front-end. Plain JavaScript, no build step.

// Forms with data-confirm ask before submitting (delete, end lease).
document.addEventListener('submit', (ev) => {
  const msg = ev.target.dataset.confirm;
  if (msg && !window.confirm(msg)) ev.preventDefault();
});

const peso = (v) => '₱' + Number(v).toLocaleString('en-PH', { maximumFractionDigits: 0 });
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ---------- Charts: fetch JSON from our own API, then draw with Chart.js ----------
(async function charts() {
  const canvases = document.querySelectorAll('canvas[data-chart]');
  const api = document.body.dataset.apiAnalytics;
  if (!canvases.length || !window.Chart || !api) return;

  // The Analytics page filter (?property=ID) narrows the charts too.
  const pid = new URLSearchParams(location.search).get('property');
  const url = pid ? api + '?property=' + encodeURIComponent(pid) : api;

  let data;
  try {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    data = await res.json();
  } catch (err) {
    canvases.forEach((c) => (c.parentElement.textContent = 'Charts could not load. Refresh the page.'));
    return;
  }
  const C = { ink: '#1f3a5f', blue: '#3b82f6', grey: '#cbd5e1', amber: '#f59e0b', green: '#16a34a', red: '#dc3545' };
  Chart.defaults.plugins.legend.labels.boxWidth = 12;

  const builders = {
    monthly: () => ({
      type: 'bar',
      data: { labels: data.monthly.labels, datasets: [{ label: 'Billed', data: data.monthly.billed, backgroundColor: C.ink }] },
      options: { scales: { y: { ticks: { callback: peso } } },
                 plugins: { tooltip: { callbacks: { label: (c) => 'Billed: ' + peso(c.raw) } } } },
    }),
    property: () => ({
      type: 'bar',
      data: { labels: data.by_property.labels, datasets: [
        { label: 'Rent roll (occupied)', data: data.by_property.rent_roll, backgroundColor: C.green },
        { label: 'Vacancy loss', data: data.by_property.vacancy_loss, backgroundColor: C.amber },
      ] },
      options: { indexAxis: 'y', scales: { x: { stacked: true, ticks: { callback: peso } }, y: { stacked: true } } },
    }),
    occupancy: () => ({
      type: 'doughnut',
      data: { labels: data.occupancy.labels, datasets: [{ data: data.occupancy.values, backgroundColor: [C.ink, C.grey, C.amber], borderWidth: 0 }] },
      options: { cutout: '60%', plugins: { legend: { position: 'right' } } },
    }),
    trend: () => ({
      type: 'line',
      data: { labels: data.monthly.labels, datasets: [{ label: 'Occupancy %', data: data.monthly.occupancy,
              borderColor: C.ink, backgroundColor: 'rgba(31,58,95,.12)', fill: true, tension: 0.25 }] },
      options: { scales: { y: { min: 0, max: 100, ticks: { callback: (v) => v + '%' } } },
                 plugins: { legend: { display: false }, tooltip: { callbacks: {
                   label: (c) => `${c.raw}% (${data.monthly.occupied_units[c.dataIndex]} units billed)` } } } },
    }),
    forecast: () => ({
      type: 'bar',
      data: { labels: data.forecast.labels, datasets: [
        { label: 'Contracted', data: data.forecast.contracted, backgroundColor: C.green },
        { label: 'At risk (not renewed)', data: data.forecast.at_risk, backgroundColor: C.amber },
        { label: 'Vacant potential', data: data.forecast.vacant, backgroundColor: C.grey },
      ] },
      options: { scales: { x: { stacked: true }, y: { stacked: true, ticks: { callback: peso } } },
                 plugins: { tooltip: { callbacks: { label: (c) => c.dataset.label + ': ' + peso(c.raw) } } } },
    }),
    tenure: () => ({
      type: 'bar',
      data: { labels: data.tenure.labels, datasets: [{ label: 'Active leases', data: data.tenure.values, backgroundColor: C.blue }] },
      options: { scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }, plugins: { legend: { display: false } } },
    }),
    demand: () => ({
      type: 'bar',
      data: { labels: data.demand.labels, datasets: [
        { type: 'line', label: 'Listing views', data: data.demand.views, borderColor: C.blue, backgroundColor: C.blue, yAxisID: 'y', tension: 0.25 },
        { label: 'Inquiries', data: data.demand.inquiries, backgroundColor: C.amber, yAxisID: 'y1' },
      ] },
      options: { scales: { y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: 'Views' } },
                           y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, ticks: { precision: 0 },
                                 title: { display: true, text: 'Inquiries' } } } },
    }),
    expiry: () => ({
      type: 'bar',
      data: { labels: data.lease_expiry.labels, datasets: [{ label: 'Leases ending', data: data.lease_expiry.values, backgroundColor: C.blue }] },
      options: { scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    }),
  };
  canvases.forEach((c) => {
    const build = builders[c.dataset.chart];
    if (!build) return;
    const cfg = build();
    cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: false }, cfg.options);
    new Chart(c, cfg);
  });
})();

// ---------- Leaflet maps ----------
(function maps() {
  const el = document.getElementById('map');
  if (!el || !window.L) return;
  L.Icon.Default.imagePath = document.body.dataset.staticUrl + 'vendor/leaflet/images/';
  const map = L.map(el).setView([15.98, 120.57], 12);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; OpenStreetMap contributors',
  }).addTo(map);

  if (el.dataset.geojson) {                       // list page: all properties
    fetch(el.dataset.geojson, { credentials: 'same-origin' }).then((r) => r.json()).then((fc) => {
      if (!fc.features.length) return;
      const layer = L.geoJSON(fc, {
        onEachFeature: (f, l) => {
          const p = f.properties;
          l.bindPopup(`<b><a href="${el.dataset.detailBase}${p.id}">${esc(p.name)}</a></b><br>${esc(p.address)}<br>${p.units} units, ${p.vacant} vacant`);
        },
      }).addTo(map);
      map.fitBounds(layer.getBounds().pad(0.3), { maxZoom: 16 });
    });
  }

  let marker = null;
  const put = (a, b) => {
    if (marker) marker.setLatLng([a, b]); else marker = L.marker([a, b]).addTo(map);
  };
  if (el.dataset.pinLat) { put(+el.dataset.pinLat, +el.dataset.pinLng); map.setView([+el.dataset.pinLat, +el.dataset.pinLng], 15); }

  if (el.dataset.pick) {                          // property page: click to move the pin
    const lat = document.getElementById('lat'), lng = document.getElementById('lng');
    map.on('click', (e) => {
      put(e.latlng.lat, e.latlng.lng);
      lat.value = e.latlng.lat.toFixed(6); lng.value = e.latlng.lng.toFixed(6);
    });
  }
})();

// ---------- Site conditions (OpenTopoData + Open-Meteo, through our own API) ----------
(function site() {
  const box = document.getElementById('site');
  if (!box || !box.dataset.hasPin) return;
  const body = document.getElementById('site-body');
  fetch(box.dataset.url, { credentials: 'same-origin' }).then((r) => r.json()).then((d) => {
    const row = (k, v) => `<dt class="col-6">${k}</dt><dd class="col-6">${v}</dd>`;
    let html = row('Elevation', d.elevation_m == null ? '—' : esc(Math.round(d.elevation_m)) + ' m above sea level');
    if (d.weather) {
      const w = d.weather;
      html += row('Now', esc(w.condition)) + row('Temperature', esc(w.temperature_c) + ' °C') +
              row('Humidity', esc(w.humidity_pct) + ' %') + row('Wind', esc(w.wind_kmh) + ' km/h') +
              row('Rain, past 7 days', esc(w.rain_past_7d_mm) + ' mm') + row('Rain, today + 2 days', esc(w.rain_next_3d_mm) + ' mm');
    }
    (d.warnings || []).forEach((m) => (html += `<dd class="col-12 text-warning-emphasis">${esc(m)}</dd>`));
    body.innerHTML = html;
  }).catch(() => (body.innerHTML = '<dd class="col-12 text-warning-emphasis">Could not load site conditions.</dd>'));
})();
