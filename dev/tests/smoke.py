"""Smoke test for Arvan Weather with mocked APIs.
Usage: python3 dev/tests/smoke.py [outdir]   (run from repo root)
Serves the app locally, fakes Open-Meteo / RainViewer / map tiles, opens every tab and saves screenshots.
"""
import json, math, sys, threading, http.server, functools, os, io, random, urllib.parse
from datetime import datetime, timedelta, timezone
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

FAIL_OM = os.environ.get('FAIL_OM') == '1'
def metmock():
    st = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0); T = []
    for k in range(72):
        t = st + timedelta(hours=k); T.append({'time': t.strftime('%Y-%m-%dT%H:%M:%SZ'), 'data': {'instant': {'details': {'air_temperature': round(15 + 6 * math.sin(k / 24 * 6.28), 1), 'relative_humidity': 55, 'dew_point_temperature': 4, 'wind_speed': 3.2, 'wind_from_direction': 240, 'wind_speed_of_gust': 6, 'cloud_area_fraction': 40, 'air_pressure_at_sea_level': 1015, 'ultraviolet_index_clear_sky': 3}}, 'next_1_hours': {'summary': {'symbol_code': 'partlycloudy_day' if k % 24 < 12 else 'lightrain'}, 'details': {'precipitation_amount': 0 if k % 24 < 12 else .4, 'probability_of_precipitation': 10 if k % 24 < 12 else 60}}}})
    for k in range(72, 240, 6):
        t = st + timedelta(hours=k); T.append({'time': t.strftime('%Y-%m-%dT%H:%M:%SZ'), 'data': {'instant': {'details': {'air_temperature': 12, 'relative_humidity': 60, 'wind_speed': 4, 'wind_from_direction': 250, 'air_pressure_at_sea_level': 1012, 'cloud_area_fraction': 70}}, 'next_6_hours': {'summary': {'symbol_code': 'rain'}, 'details': {'precipitation_amount': 3, 'air_temperature_max': 16, 'air_temperature_min': 8, 'probability_of_precipitation': 70}}}})
    return {'properties': {'timeseries': T}}
def handle(route):
    u = urllib.parse.urlparse(route.request.url); q = urllib.parse.parse_qs(u.query)
    J = lambda o: route.fulfill(status=200, content_type='application/json', body=json.dumps(o))
    if u.hostname == 'ensemble-api.open-meteo.com':
        st = NOW.replace(hour=0); T = [(st + timedelta(hours=k)).strftime('%Y-%m-%dT%H:%M') for k in range(240)]; h = {'time': T}
        for m in range(31):
            sfx = '' if m == 0 else '_member%02d' % m
            h['temperature_2m' + sfx] = [round(18 - k / 40 + 6 * math.sin((k % 24 - 9) / 24 * 6.28) + (m - 15) * k / 900, 1) for k in range(240)]
            h['precipitation' + sfx] = [round(max(0, math.sin(k / 30 + m / 5)) * (1.5 if 60 < k < 110 else .2), 2) for k in range(240)]
        return J({'hourly': h})
    if u.hostname == 'api.met.no': return route.fulfill(status=200, content_type='application/json', body=json.dumps(metmock()))
    if FAIL_OM and 'open-meteo' in u.hostname: return route.fulfill(status=429, content_type='application/json', body=json.dumps({'error': True, 'reason': 'Hourly API request limit exceeded. Please try again in the next hour.'}))
    if u.hostname == 'api.open-meteo.com':
        lats = q.get('latitude', ['0'])[0].split(',')
        hv = q.get('hourly', [''])[0].split(',')
        if 'minutely_15' in q and len(lats) > 1:
            st = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0); st -= timedelta(minutes=st.minute % 15 + 120)
            T = [(st + timedelta(minutes=15 * k)).strftime('%Y-%m-%dT%H:%M') for k in range(33)]
            def m15(i):
                r, c = divmod(i, 28)
                return {'minutely_15': {'time': T, 'precipitation': [round(max(0, math.sin((c - k * .15) / 3) * math.cos(r / 4)) * 1.6, 2) for k in range(33)], 'snowfall': [0.3 if r < 4 and c < 8 else 0] * 33}}
            return J([m15(i) for i in range(len(lats))])
        BASEH = set(['temperature_2m','apparent_temperature','relative_humidity_2m','dew_point_2m','precipitation_probability','precipitation','weather_code','cloud_cover','visibility','wind_speed_10m','wind_direction_10m','wind_gusts_10m','uv_index','is_day','pressure_msl',''])
        if len(lats) > 1 or any(v not in BASEH for v in hv):
            N = 49 if 'start_hour' in q else 72
            st = datetime.now(timezone.utc).replace(tzinfo=None).replace(minute=0, second=0, microsecond=0) if 'start_hour' in q else NOW.replace(hour=0)
            tz = q.get('timezone', ['GMT'])[0]
            if tz == 'auto': st = NOW.replace(hour=0)
            LA = [float(x) for x in lats]; LO = [float(x) for x in q.get('longitude', ['0'])[0].split(',')]
            STEP = 6 if 'temporal_resolution' in q else 1
            if STEP == 6: N = int(q.get('forecast_days', ['7'])[0]) * 4
            def real(v, la, lo, k):
                kk = k * STEP / 6.0; e = math.exp
                if v == 'pressure_msl': return round(1016 + 9 * e(-((la - 39) ** 2 + (lo - 60) ** 2) / 40) - 14 * e(-((la - 33.5) ** 2 + (lo - 44 - kk * .45) ** 2) / 22), 1)
                if v == 'geopotential_height_500hPa': return round(5900 - 9.5 * max(0, la - 14) ** 1.25 - 220 * e(-((lo - 44 - kk * 1.2) ** 2) / 60 - ((la - 45) ** 2) / 150) + 160 * e(-((lo - 10 - kk * .8) ** 2) / 80 - ((la - 50) ** 2) / 120) - 200 * e(-((lo + 15) ** 2) / 70 - ((la - 58) ** 2) / 60), 0)
                if v == 'geopotential_height_1000hPa': return round((real('pressure_msl', la, lo, k) - 1013) * 8 + 110, 0)
                if v == 'geopotential_height_850hPa': return round(1500 + (real('pressure_msl', la, lo, k) - 1013) * 9, 0)
                if v == 'temperature_2m': return round(31 - .95 * (la - 20) - 7 * e(-((lo - 45 - kk * .45) ** 2) / 30) * (la - 20) / 24 + 3 * math.sin(k * STEP / 24 * 6.28), 1)
                if v == 'temperature_850hPa': return round(19 - (la - 20) - 9 * e(-((lo - 45 - kk * .45) ** 2) / 30), 1)
                if v == 'precipitation': return round(max(0, 4.5 * e(-((la - 34) ** 2 + (lo - 47 - kk * .45) ** 2) / 7) - .2), 2)
                if v == 'snowfall': return round(real('precipitation', la, lo, k) * .9 if la > 35.5 else 0, 2)
                if v == 'wind_speed_500hPa': return round(70 + 80 * e(-((la - 31) ** 2) / 12), 0)
                if v == 'wind_direction_500hPa': return round(255 + 25 * math.sin((lo - 44 - kk * .45) / 5), 0)
                if v == 'wind_gusts_10m': return round(18 + 55 * e(-((la - 33.5) ** 2 + (lo - 45 - kk * .45) ** 2) / 10), 0)
                if v == 'wind_speed_10m': return round(real('wind_gusts_10m', la, lo, k) * .6, 0)
                if v == 'wind_direction_10m': return round(230 + 40 * math.sin(lo / 4), 0)
                if v == 'cape': return round(1800 * e(-((la - 29) ** 2 + (lo - 50) ** 2) / 18), 0)
                if v == 'geopotential_height_250hPa': return round(10900 - 8 * max(0, la - 14) ** 1.3, 0)
                if 'direction' in v: return round(260 + 30 * math.sin((lo - kk) / 9), 0)
                if v == 'wind_speed_250hPa': return round(80 + 200 * e(-((la - 32 - 4 * math.sin(lo / 12)) ** 2) / 14), 0)
                return round(40 + 30 * math.sin(la / 5 + lo / 7 + kk / 3), 1)
            def one(i):
                h = {'time': [(st + timedelta(hours=k * STEP)).strftime('%Y-%m-%dT%H:%M') for k in range(N)]}
                if 'models' in q:
                    for v in hv: h[v] = [real(v, LA[i], LO[i], k) for k in range(N)]
                    return {'elevation': 1190, 'hourly': h}
                for v in hv:
                    lvl = 1 + (2 if 'hPa' in v and int(v.split('_')[-1][:-3]) < 400 else 0)
                    if 'direction' in v: h[v] = [(230 + i * 9 + k * 2) % 360 for k in range(N)]
                    elif 'speed' in v or 'gust' in v: h[v] = [round((8 + (i * 7) % 40 + 6 * math.sin(k / 5)) * lvl, 1) for k in range(N)]
                    elif 'geopotential' in v: h[v] = [{'200hPa':11800,'250hPa':10400,'300hPa':9200,'500hPa':5600,'700hPa':3050,'850hPa':1500,'950hPa':520}.get(v.split('_')[-1], 0)] * N
                    elif 'temperature' in v: h[v] = [round(18 - (i % 10) - (k % 24) / 4, 1) for k in range(N)]
                    elif 'cloud' in v: h[v] = [(i * 13 + k * 3) % 100 for k in range(N)]
                    else: h[v] = [round(max(0, math.sin(i / 3 + k / 6)) * 4, 1) for k in range(N)]
                dv = q.get('daily', [''])[0].split(',')
                nd = max(1, N // 24)
                dd = {'time': [(st + timedelta(days=k)).strftime('%Y-%m-%d') for k in range(nd)]}
                for v in dv:
                    if v: dd[v] = [round(2 + i % 7 + k * .7, 1) if 'max' in v else round(-3 + i % 5 - k, 1) if 'min' in v else round((i + k) % 6 * 1.3, 1) for k in range(nd)]
                return {'elevation': 1190, 'hourly': h, 'daily': dd}
            return J([one(i) for i in range(len(lats))] if len(lats) > 1 else one(0))
        if False:
            return J([{'current': {'temperature_2m': 10 + 8 * math.sin(i / 7) + i % 5, 'cloud_cover': (i * 13) % 100, 'wind_speed_10m': (i * 7) % 70, 'wind_direction_10m': (i * 30) % 360, 'precipitation': (i % 9) * .6}} for i in range(len(lats))])
        if 'models' in q:
            m = q['models'][0]
            if m == 'knmi_seamless':  # outside domain -> nulls
                f = forecast(7, with_current=False); f['daily'] = {k: ([None] * 7 if k != 'time' else v) for k, v in f['daily'].items()}; return J(f)
            if m == 'kma_seamless':
                return route.fulfill(status=400, content_type='application/json', body=json.dumps({'error': True, 'reason': 'Invalid model'}))
            return J(forecast(7, BIAS.get(m, 0), with_current=False))
        f = forecast(); f['minutely_15'] = {'time': [(NOW + timedelta(minutes=15 * k)).strftime('%Y-%m-%dT%H:%M') for k in range(9)], 'precipitation': [0, 0, .1, .3, .5, .4, .2, 0, 0]}
        return J(f)
    if u.hostname == 'air-quality-api.open-meteo.com':
        n = len(q.get('latitude', ['0'])[0].split(','))
        if n > 1: return J([{'current': {'us_aqi': (i * 37) % 190 + 10, 'pm2_5': 20 + i % 30, 'dust': (i * 53) % 300}, 'hourly': {'time': [], 'dust': []}} for i in range(n)])
        return J(aqi())
    if u.hostname == 'geocoding-api.open-meteo.com': return J({'results': [{'name': 'شیراز', 'admin1': 'استان فارس', 'country': 'ایران', 'latitude': 29.61, 'longitude': 52.53}]})
    if u.hostname == 'api.rainviewer.com': return J({'host': 'https://tilecache.rainviewer.com', 'radar': {'past': [{'time': 1793446800 + i * 600, 'path': f'/v2/radar/{i}'} for i in range(12)]}})
    if u.hostname == 'archive-api.open-meteo.com' and len(q['latitude'][0].split(',')) > 1:
        st = datetime.strptime(q['start_date'][0], '%Y-%m-%d'); en = datetime.strptime(q['end_date'][0], '%Y-%m-%d'); n = (en - st).days + 1
        T = [(st + timedelta(days=k)).strftime('%Y-%m-%d') for k in range(n)]
        return J([{'daily': {'time': T, 'precipitation_sum': [round(max(0, math.cos((st + timedelta(days=k)).timetuple().tm_yday / 365 * 6.28)) * ((k * 7 + i * 3) % 9) * (.6 if k < 365 else 1.0) * (1 + i % 4), 1) for k in range(n)]}} for i in range(len(q['latitude'][0].split(',')))])
    if u.hostname == 'archive-api.open-meteo.com':
        d0 = datetime.strptime(q['start_date'][0], '%Y-%m-%d'); n = (datetime.strptime(q['end_date'][0], '%Y-%m-%d') - d0).days + 1
        t = [(d0 + timedelta(days=k)) for k in range(n)]
        return J({'daily': {'time': [x.strftime('%Y-%m-%d') for x in t], 'temperature_2m_max': [round(22 + 13 * math.sin((x.timetuple().tm_yday - 105) / 365 * 2 * math.pi) + (k % 7) - 3 + (x.year - 1995) * .05, 1) for k, x in enumerate(t)], 'temperature_2m_min': [round(10 + 11 * math.sin((x.timetuple().tm_yday - 105) / 365 * 2 * math.pi) + (k % 5) - 2, 1) for k, x in enumerate(t)], 'precipitation_sum': [round(max(0, math.cos(x.timetuple().tm_yday / 365 * 2 * math.pi)) * (k % 4) * (0.5 + (x.year * 7 % 10) / 8), 1) for k, x in enumerate(t)]}})
    if u.hostname == 'marine-api.open-meteo.com':
        lats = q['latitude'][0].split(','); st = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
        T = [(st + timedelta(hours=k)).strftime('%Y-%m-%dT%H:%M') for k in range(48)]
        return J([{'hourly': {'time': T, 'wave_height': [None if i < 4 else round(.3 + i * .15, 2)] * 48, 'wave_period': [5] * 48, 'sea_surface_temperature': [None if i < 4 else 26.5] * 48}} for i in range(len(lats))])
    if u.hostname == 'earthquake.usgs.gov':
        import time as _t
        now = int(_t.time() * 1000)
        return J({'features': [{'id': 'q%d' % i, 'properties': {'mag': m, 'time': now - h * 3600000, 'place': 'x'}, 'geometry': {'coordinates': [lo, la, dp]}} for i, (m, h, la, lo, dp) in enumerate([(4.6, 3, 34.4, 47.2, 10), (3.1, 30, 27.3, 56.4, 12), (5.2, 100, 30.1, 57.0, 8), (2.8, 5, 35.9, 51.0, 9)])]})
    if 'overpass' in u.hostname or 'interpreter' in u.path:
        import random as _r
        els = [{'type': 'node', 'lat': 35.69 + (i % 7) * .01, 'lon': 51.39 + (i % 5) * .012, 'tags': {'name': ['عمارت شمس‌العماره', 'هتل اسپیناس', 'جایگاه سوخت ۱۲۴', 'موزه ملی', 'کاخ گلستان', 'بوستان آب و آتش'][i % 6], 'tourism': ['museum', 'hotel', None, 'museum', 'attraction', None][i % 6], 'amenity': [None, None, 'fuel', None, None, None][i % 6], 'stars': '5' if i % 6 == 1 else None, 'fuel:cng': 'yes', 'phone': '+98 21 1234', 'opening_hours': 'Mo-Su 08:00-20:00'}} for i in range(18)]
        for e in els: e['tags'] = {k: v for k, v in e['tags'].items() if v}
        return J({'elements': els})
    if u.hostname == 'www.kermanshahmet.ir':
        b = io.BytesIO(); im = Image.new('RGB', (600, 600), (20, 20, 20))
        for x in range(200, 330):
            for y in range(220, 300): im.putpixel((x, y), (0, 160, 230))
        im.save(b, 'PNG'); return route.fulfill(status=200, content_type='image/png', body=b.getvalue())
    if u.hostname == 'view.eumetsat.int':
        b = io.BytesIO(); im = Image.new('RGBA', (1040, 800), (0, 0, 0, 0))
        if 'li_afa' in route.request.url:
            for (cx, cy) in [(300, 420), (330, 440), (520, 610), (180, 380)]:
                for x in range(cx - 8, cx + 8):
                    for y in range(cy - 8, cy + 8): im.putpixel((x, y), (250, 200, 20, 230))
        else:
            for x in range(0, 1040, 2):
                for y in range(250, 520, 2): im.putpixel((x, y), (220, 220, 220, 150))
        im.save(b, 'PNG'); return route.fulfill(status=200, content_type='image/png', body=b.getvalue())
    if u.hostname == 'gibs.earthdata.nasa.gov':
        b = io.BytesIO(); im = Image.new('RGBA', (1040, 800), (0, 0, 0, 0)); [im.putpixel((x, y), (40, 120, 255, 200)) for x in range(400, 700) for y in range(300, 450)]; im.save(b, 'PNG'); return route.fulfill(status=200, content_type='image/png', body=b.getvalue())
    if 'tilecache.rainviewer.com' in u.hostname: return route.fulfill(status=200, content_type='image/png', body=RADAR)
    if 'Reference' in route.request.url: return route.fulfill(status=200, content_type='image/png', body=png((0, 0, 0, 0)))
    if 'World_Street_Map' in route.request.url: return route.fulfill(status=200, content_type='image/png', body=png((236, 232, 220, 255)))
    if 'cartocdn' in u.hostname or 'arcgisonline' in u.hostname: return route.fulfill(status=200, content_type='image/png', body=TILE)
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
    pg.screenshot(path=f'{OUT}/0-onboard.png'); pg.click('#obSkip'); pg.wait_for_timeout(300)
    pg.screenshot(path=f'{OUT}/1-now.png', full_page=True)
    for t, name in [('fc', '2-forecast'), ('aq', '3-aqi')]:
        pg.click(f'nav button[data-t={t}]'); pg.wait_for_timeout(500); pg.screenshot(path=f'{OUT}/{name}.png', full_page=True)
    pg.click('nav button[data-t=map]'); pg.wait_for_timeout(2500)
    pg.screenshot(path=f'{OUT}/4b-map-wind.png'); pg.click('#mapLevels .chip[data-v="250hPa"]'); pg.wait_for_timeout(2500); pg.mouse.click(150, 450); pg.wait_for_timeout(300); pg.screenshot(path=f'{OUT}/4c-map-jet.png')
    pg.click('#mapLayers .chip[data-v=temp]'); pg.wait_for_timeout(1500); pg.screenshot(path=f'{OUT}/5-map-temp.png')
    pg.click('nav button[data-t=rd]'); pg.wait_for_timeout(2500); pg.screenshot(path=f'{OUT}/r1-iran.png', full_page=True)
    pg.click('#rdSrc .chip[data-v=nasa]'); pg.wait_for_timeout(1500); pg.screenshot(path=f'{OUT}/r2-nasa.png')
    pg.click('#rdSrc .chip[data-v=ksh]'); pg.wait_for_timeout(1800); pg.screenshot(path=f'{OUT}/r6-ksh.png', full_page=True)
    pg.click('#rdSrc .chip[data-v=lt]'); pg.wait_for_timeout(1500); pg.screenshot(path=f'{OUT}/r5-lt.png', full_page=True)
    pg.click('#rdSrc .chip[data-v=rv]'); pg.wait_for_timeout(1200); pg.click('#rdInfo [data-st=IRKR]'); pg.wait_for_timeout(800); pg.screenshot(path=f'{OUT}/r3-rv.png', full_page=True)
    pg.click('nav button[data-t=map]'); pg.wait_for_timeout(1500); pg.screenshot(path=f'{OUT}/r4-backmap.png')
    pg.click('#mapLayers .chip[data-v=wind]'); pg.wait_for_timeout(1500); pg.screenshot(path=f'{OUT}/5b-map-wind.png')
    pg.click('#mapLayers .chip[data-v=eq]'); pg.wait_for_timeout(1200); pg.screenshot(path=f'{OUT}/5c-map-eq.png', full_page=True)
    pg.click('#mapLayers .chip[data-v=pres]'); pg.wait_for_timeout(1500); pg.screenshot(path=f'{OUT}/5d-map-pres.png')
    pg.click('nav button[data-t=now]'); pg.wait_for_timeout(500); pg.click('#ircV button[data-v=rain]'); pg.wait_for_timeout(1500); pg.locator('#ircCard').screenshot(path=f'{OUT}/irc-rain.png')
    pg.evaluate('openStats(IRC.find(c=>c[0]==="سنندج"))'); pg.wait_for_timeout(2500); pg.locator('.sheet.on .panel').evaluate('e=>e.style.maxHeight="none"'); pg.locator('.sheet.on .panel').screenshot(path=f'{OUT}/stats.png'); pg.evaluate("document.querySelectorAll('.sheet.on').forEach(e=>e.remove())"); pg.wait_for_timeout(300)
    pg.click('nav button[data-t=tr]'); pg.wait_for_timeout(2500); pg.locator('#poiCard').screenshot(path=f'{OUT}/poi.png'); pg.click('#poiCard details.poi >> nth=0'); pg.wait_for_timeout(300); pg.locator('#poiCard').screenshot(path=f'{OUT}/poi2.png'); pg.click('#poiBody [data-pm="0"] >> nth=0'); pg.wait_for_timeout(1000); pg.screenshot(path=f'{OUT}/poimap.png'); pg.click('#pmX'); pg.wait_for_timeout(200); pg.click('#tpGo'); pg.wait_for_timeout(1500); pg.screenshot(path=f'{OUT}/tr1.png', full_page=True)
    pg.click('nav button[data-t=fc]'); pg.wait_for_timeout(2500); pg.screenshot(path=f'{OUT}/fc2.png', full_page=True)
    pg.click('nav button[data-t=now]'); pg.wait_for_timeout(500); pg.evaluate('openGlance()'); pg.wait_for_timeout(1200); pg.screenshot(path=f'{OUT}/glance.png'); pg.click('#glance'); pg.wait_for_timeout(300)
    pg.click('nav button[data-t=md]'); pg.wait_for_timeout(300)
    pg.wait_for_timeout(2500); pg.screenshot(path=f'{OUT}/wb1.png', full_page=True)
    pg.click('#wbL .chip[data-l="72"]'); pg.wait_for_timeout(800); pg.screenshot(path=f'{OUT}/wb1b.png', full_page=True); pg.click('#t-md [data-wp=jet]'); pg.wait_for_timeout(2500); pg.screenshot(path=f'{OUT}/wb-jet.png', full_page=True); pg.click('#t-md [data-wp=z500]'); pg.wait_for_timeout(2500); pg.click('#wbCmp'); pg.wait_for_timeout(3000); pg.screenshot(path=f'{OUT}/wb2.png', full_page=True)
    pg.click('#mdV button[data-v=city]'); pg.wait_for_timeout(2500); pg.screenshot(path=f'{OUT}/ens.png', full_page=True)
    pg.click('#t-md .mchip[data-m=kma_seamless]'); pg.wait_for_timeout(200); pg.click('#t-md .mchip[data-m=knmi_seamless]'); pg.wait_for_timeout(1500)
    pg.screenshot(path=f'{OUT}/6-models.png', full_page=True)
    pg.click('#cityBtn'); pg.wait_for_timeout(300); pg.fill('#q', 'شیراز'); pg.wait_for_timeout(900); pg.screenshot(path=f'{OUT}/7-cities.png', full_page=True)
    pg.click('#qRes .row'); pg.wait_for_timeout(800)
    pg.click('#setBtn'); pg.wait_for_timeout(300); pg.click('#setS .seg[data-k=t] button[data-v=f]'); pg.wait_for_timeout(200); pg.screenshot(path=f'{OUT}/8-settings.png')
    pg.click('#setS [data-close]'); pg.click('nav button[data-t=now]'); pg.wait_for_timeout(300); pg.screenshot(path=f'{OUT}/9-now-F.png', full_page=True)
    for th in ['light', 'dark', 'oled']:
        pg.click('#setBtn'); pg.wait_for_timeout(200); pg.click(f'#setS .seg[data-k=theme] button[data-v={th}]'); pg.wait_for_timeout(200); pg.click('#setS [data-close]'); pg.wait_for_timeout(300); pg.screenshot(path=f'{OUT}/th-{th}.png', full_page=True)
    pg.click('nav button[data-t=fc]'); pg.wait_for_timeout(500); pg.screenshot(path=f'{OUT}/th-light-fc.png', full_page=True)
    pg.click('#setBtn'); pg.wait_for_timeout(200); pg.screenshot(path=f'{OUT}/8b-settings.png', full_page=True)
    b.close()
srv.shutdown()
print('ERRORS:', errors or 'none')
