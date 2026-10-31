"""Smoke test for Arvan Weather with mocked APIs.
Usage: python3 dev/tests/smoke.py [outdir]   (run from repo root)
Serves the app locally, fakes Open-Meteo / RainViewer / map tiles, opens every tab and saves screenshots.
"""
import json, math, sys, threading, http.server, functools, os, io, random, urllib.parse
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'dev', 'shots')
os.makedirs(OUT, exist_ok=True)
random.seed(4)
NOW = datetime(2026, 10, 31, 14, 0)

def forecast(days=10, model_bias=0.0, with_current=True):
    hours = [NOW.replace(hour=0) + timedelta(hours=i) for i in range(days * 24)]
    H = {k: [] for k in ['time','temperature_2m','apparent_temperature','relative_humidity_2m','dew_point_2m','precipitation_probability','precipitation','weather_code','cloud_cover','visibility','wind_speed_10m','wind_direction_10m','wind_gusts_10m','uv_index','is_day','pressure_msl']}
    for i, t in enumerate(hours):
        d = i // 24; hr = t.hour
        base = 18 - d * 0.9 + model_bias * (d / 3)
        tmp = base + 6 * math.sin((hr - 9) / 24 * 2 * math.pi)
        rain = d in (2, 3) and 10 <= hr <= 20
        code = 63 if rain else (95 if d == 4 and hr == 16 else (2 if hr % 5 else 1))
        if d == 6: code = 71 if hr > 3 else 3
        H['time'].append(t.strftime('%Y-%m-%dT%H:%M'))
        H['temperature_2m'].append(round(tmp, 1)); H['apparent_temperature'].append(round(tmp - 2, 1))
        H['relative_humidity_2m'].append(55 + (25 if rain else 0)); H['dew_point_2m'].append(round(tmp - 9, 1))
        H['precipitation_probability'].append(80 if rain else 5); H['precipitation'].append(2.4 if rain else 0)
        H['weather_code'].append(code); H['cloud_cover'].append(90 if rain else 35)
        H['visibility'].append(24000); H['wind_speed_10m'].append(12 + (d == 4) * 30); H['wind_direction_10m'].append(250)
        H['wind_gusts_10m'].append(25 + (d == 4) * 45); H['uv_index'].append(max(0, round(5 * math.sin((hr - 6) / 12 * math.pi), 1)) if 6 <= hr <= 18 else 0)
        H['is_day'].append(1 if 6 <= hr < 18 else 0); H['pressure_msl'].append(1016 - d)
    Dd = {k: [] for k in ['time','weather_code','temperature_2m_max','temperature_2m_min','apparent_temperature_max','apparent_temperature_min','sunrise','sunset','daylight_duration','uv_index_max','precipitation_sum','precipitation_probability_max','precipitation_hours','wind_speed_10m_max','wind_gusts_10m_max','wind_direction_10m_dominant']}
    for d in range(days):
        sl = slice(d * 24, d * 24 + 24); t = (NOW + timedelta(days=d)).strftime('%Y-%m-%d')
        Dd['time'].append(t); Dd['weather_code'].append(max(H['weather_code'][sl]))
        Dd['temperature_2m_max'].append(max(H['temperature_2m'][sl])); Dd['temperature_2m_min'].append(min(H['temperature_2m'][sl]))
        Dd['apparent_temperature_max'].append(max(H['apparent_temperature'][sl])); Dd['apparent_temperature_min'].append(min(H['apparent_temperature'][sl]))
        Dd['sunrise'].append(t + 'T06:21'); Dd['sunset'].append(t + 'T17:02'); Dd['daylight_duration'].append(38460)
        Dd['uv_index_max'].append(max(H['uv_index'][sl])); Dd['precipitation_sum'].append(round(sum(H['precipitation'][sl]), 1))
        Dd['precipitation_probability_max'].append(max(H['precipitation_probability'][sl])); Dd['precipitation_hours'].append(sum(1 for x in H['precipitation'][sl] if x > 0))
        Dd['wind_speed_10m_max'].append(max(H['wind_speed_10m'][sl])); Dd['wind_gusts_10m_max'].append(max(H['wind_gusts_10m'][sl])); Dd['wind_direction_10m_dominant'].append(250)
    out = {'latitude': 35.7, 'longitude': 51.4, 'timezone': 'Asia/Tehran', 'hourly': H, 'daily': Dd}
    if with_current:
        i = 14
        out['current'] = {'time': '2026-10-31T14:00', **{k: H[k][i] for k in H if k not in ('time', 'precipitation_probability')}}
    return out

def aqi():
    hrs = [NOW.replace(hour=0) + timedelta(hours=i) for i in range(96)]
    return {'current': {'time': '2026-10-31T14:00', 'us_aqi': 128, 'european_aqi': 58, 'pm10': 88, 'pm2_5': 41.5, 'carbon_monoxide': 520, 'nitrogen_dioxide': 38, 'sulphur_dioxide': 9, 'ozone': 61, 'dust': 140, 'aerosol_optical_depth': .4},
            'hourly': {'time': [t.strftime('%Y-%m-%dT%H:%M') for t in hrs], 'us_aqi': [int(90 + 50 * math.sin(i / 9)) for i in range(96)], 'european_aqi': [int(45 + 20 * math.sin(i / 9)) for i in range(96)], 'pm2_5': [30] * 96, 'pm10': [70] * 96, 'dust': [int(100 + 60 * math.sin(i / 7)) for i in range(96)]}}

def png(color):
    b = io.BytesIO(); Image.new('RGBA', (256, 256), color).save(b, 'PNG'); return b.getvalue()
TILE = png((40, 44, 56, 255)); RADAR = png((0, 120, 200, 90))
BIAS = {'gfs_seamless': 2.5, 'icon_seamless': -1, 'gem_seamless': 1, 'ukmo_seamless': -2, 'ecmwf_aifs025_single': 0.5}

def handle(route):
    u = urllib.parse.urlparse(route.request.url); q = urllib.parse.parse_qs(u.query)
    J = lambda o: route.fulfill(status=200, content_type='application/json', body=json.dumps(o))
    if u.hostname == 'api.open-meteo.com':
        lats = q.get('latitude', ['0'])[0].split(',')
        if len(lats) > 1:
            return J([{'current': {'temperature_2m': 10 + 8 * math.sin(i / 7) + i % 5, 'cloud_cover': (i * 13) % 100, 'wind_speed_10m': (i * 7) % 70, 'wind_direction_10m': (i * 30) % 360, 'precipitation': (i % 9) * .6}} for i in range(len(lats))])
        if 'models' in q:
            m = q['models'][0]
            if m == 'knmi_seamless':  # outside domain -> nulls
                f = forecast(7, with_current=False); f['daily'] = {k: ([None] * 7 if k != 'time' else v) for k, v in f['daily'].items()}; return J(f)
            if m == 'kma_seamless':
                return route.fulfill(status=400, content_type='application/json', body=json.dumps({'error': True, 'reason': 'Invalid model'}))
            return J(forecast(7, BIAS.get(m, 0), with_current=False))
        return J(forecast())
    if u.hostname == 'air-quality-api.open-meteo.com': return J(aqi())
    if u.hostname == 'geocoding-api.open-meteo.com': return J({'results': [{'name': 'شیراز', 'admin1': 'استان فارس', 'country': 'ایران', 'latitude': 29.61, 'longitude': 52.53}]})
    if u.hostname == 'api.rainviewer.com': return J({'host': 'https://tilecache.rainviewer.com', 'radar': {'past': [{'time': 1793446800 + i * 600, 'path': f'/v2/radar/{i}'} for i in range(12)]}})
    if 'tilecache.rainviewer.com' in u.hostname: return route.fulfill(status=200, content_type='image/png', body=RADAR)
    if 'cartocdn' in u.hostname: return route.fulfill(status=200, content_type='image/png', body=TILE)
    if u.hostname == 'cdn.jsdelivr.net': return route.fulfill(status=200, content_type='text/css', body='')
    return route.continue_()

class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 8765), functools.partial(Q, directory=ROOT))
threading.Thread(target=srv.serve_forever, daemon=True).start()

errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 400, 'height': 860}, device_scale_factor=2)
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.on('console', lambda m: m.type == 'error' and errors.append(m.text))
    pg.route('**/*', lambda r: handle(r) if '127.0.0.1' not in r.request.url else r.continue_())
    pg.goto('http://127.0.0.1:8765/index.html'); pg.wait_for_timeout(1200)
    pg.screenshot(path=f'{OUT}/1-now.png', full_page=True)
    for t, name in [('fc', '2-forecast'), ('aq', '3-aqi')]:
        pg.click(f'nav button[data-t={t}]'); pg.wait_for_timeout(500); pg.screenshot(path=f'{OUT}/{name}.png', full_page=True)
    pg.click('nav button[data-t=map]'); pg.wait_for_timeout(1500); pg.screenshot(path=f'{OUT}/4-map-radar.png')
    pg.click('#mapLayers .chip[data-v=temp]'); pg.wait_for_timeout(1500); pg.screenshot(path=f'{OUT}/5-map-temp.png')
    pg.click('#mapLayers .chip[data-v=wind]'); pg.wait_for_timeout(1500); pg.screenshot(path=f'{OUT}/5b-map-wind.png')
    pg.click('nav button[data-t=md]'); pg.wait_for_timeout(300)
    pg.click('#t-md .mchip[data-m=kma_seamless]'); pg.wait_for_timeout(200); pg.click('#t-md .mchip[data-m=knmi_seamless]'); pg.wait_for_timeout(1500)
    pg.screenshot(path=f'{OUT}/6-models.png', full_page=True)
    pg.click('#cityBtn'); pg.wait_for_timeout(300); pg.fill('#q', 'شیراز'); pg.wait_for_timeout(900); pg.screenshot(path=f'{OUT}/7-cities.png')
    pg.click('#qRes .row'); pg.wait_for_timeout(800)
    pg.click('#setBtn'); pg.wait_for_timeout(300); pg.click('#setS .seg[data-k=t] button[data-v=f]'); pg.wait_for_timeout(200); pg.screenshot(path=f'{OUT}/8-settings.png')
    pg.click('#setS [data-close]'); pg.click('nav button[data-t=now]'); pg.wait_for_timeout(300); pg.screenshot(path=f'{OUT}/9-now-F.png')
    b.close()
srv.shutdown()
print('ERRORS:', errors or 'none')
