"""Validate raw traces, summarize runs, and produce vector SVG figures."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import html
import json
from pathlib import Path
import statistics
import sys
from benchmark import percentile, PHASES

def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def read_rows(path):
    with path.open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    integers = ('rate', 'repeat', 'id', 'status')
    floats = ('planned_s', 'sent_s', 'ended_s', 'launch_lag_ms', 'latency_ms', 'scheduled_latency_ms', 'queue_ms', 'dependency_ms')
    for row in rows:
        for k in integers:
            row[k] = int(row[k])
        for k in floats:
            row[k] = float(row[k]) if row.get(k) else 0
        row['value'] = int(row['value']) if row['value'] else None
    return rows

def figures(folder, summary, aggregate, all_rows, cfg):
    import reportlab
    from reportlab.graphics.shapes import Drawing, String, Rect, Line, Circle, PolyLine
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics import renderSVG, renderPDF
    from reportlab.lib import colors
    palette = [colors.HexColor('#1f598f'), colors.HexColor('#c15027')]
    outputs = []

    def label(d, x, y, text, size=11, color=colors.black, anchor='start'):
        d.add(String(x, y, text, fontName='Helvetica', fontSize=size, fillColor=color, textAnchor=anchor))

    d = Drawing(1000, 500)
    label(d, 32, 474, 'Latency by offered request cohort and I/O implementation', 20)
    label(d, 32, 453, 'Median of five run-level p95 values; whiskers: minimum to maximum. Includes all response statuses.', 11)
    for panel, rate in enumerate(cfg['rates']):
        x = 65 + panel * 490
        chart = VerticalBarChart()
        chart.x, chart.y, chart.width, chart.height = x, 95, 390, 300
        chart.data = [[next(a['median_p95_ms'] for a in aggregate if a['rate'] == rate and a['mode'] == mode and a['phase'] == phase) for phase in PHASES] for mode in ('sync', 'async')]
        chart.categoryAxis.categoryNames = ['normal', 'slow', 'fault', 'recovered cohort']
        chart.categoryAxis.labels.fontSize = 11
        maximum = max(a['max_p95_ms'] for a in aggregate if a['rate'] == rate)
        ceiling = (int(maximum / (100 if maximum < 500 else 500)) + 1) * (100 if maximum < 500 else 500)
        chart.valueAxis.valueMin = 0
        chart.valueAxis.valueMax = ceiling
        chart.valueAxis.valueStep = ceiling / 5
        chart.valueAxis.labels.fontSize = 10
        chart.bars[0].fillColor, chart.bars[1].fillColor = palette
        chart.groupSpacing = 18
        chart.barSpacing = 4
        chart.strokeColor = None
        d.add(chart)
        label(d, x, 414, f'{rate} requests/s', 15)
        label(d, x, 398, 'p95, ms', 10)
        # get generated bar coordinates rather than infer chart spacing.
        chart._computeBarPositions()
        for mode_index, mode in enumerate(('sync', 'async')):
            for phase_index, phase in enumerate(PHASES):
                a = next(a for a in aggregate if a['rate'] == rate and a['mode'] == mode and a['phase'] == phase)
                bx, by, bw, bh = chart._barPositions[mode_index][phase_index]
                center = bx + bw / 2
                low = chart.y + a['min_p95_ms'] / ceiling * chart.height
                high = chart.y + a['max_p95_ms'] / ceiling * chart.height
                d.add(Line(center, low, center, high, strokeColor=colors.black, strokeWidth=1))
                d.add(Line(center - 3, low, center + 3, low, strokeWidth=1))
                d.add(Line(center - 3, high, center + 3, high, strokeWidth=1))
    for i, text in enumerate(('Blocking sockets / 8 threads', 'asyncio / 8 active calls')):
        d.add(Rect(265 + i * 280, 38, 14, 14, fillColor=palette[i], strokeColor=None))
        label(d, 285 + i * 280, 40, text)
    renderSVG.drawToFile(d, str(folder / 'latency.svg'))
    renderPDF.drawToFile(d, str(folder / 'latency.pdf'))
    outputs.append('latency.svg')

    d = Drawing(1000, 380)
    label(d, 32, 352, 'Response latency across the injected request cohorts', 19)
    label(d, 32, 332, '60 requests/s. Curves: median of five run-level p95 values in each 0.5 s offered-time bin.', 11)
    x0, y0, w, h = 75, 65, 850, 230
    bin_values = defaultdict(list)
    for tag, rows in all_rows.items():
        if rows[0]['rate'] != max(cfg['rates']):
            continue
        for b in range(24):
            group = [r for r in rows if b * .5 <= r['planned_s'] < (b + 1) * .5]
            if group:
                bin_values[(rows[0]['mode'], b)].append(percentile([r['scheduled_latency_ms'] for r in group], .95))
    ceiling = (int(max(statistics.median(v) for v in bin_values.values()) / 500) + 1) * 500
    for i in range(6):
        y = y0 + h * i / 5
        d.add(Line(x0, y, x0 + w, y, strokeColor=colors.HexColor('#dddddd')))
        label(d, x0 - 10, y - 4, f'{ceiling * i / 5:.0f}', 10, anchor='end')
    for phase_index, phase in enumerate(PHASES):
        x = x0 + w * phase_index / 4
        d.add(Line(x, y0, x, y0 + h, strokeColor=colors.HexColor('#aaaaaa')))
        label(d, x + 6, y0 + h + 8, 'recovered cohort' if phase == 'recovered' else phase, 11)
    for i, mode in enumerate(('sync', 'async')):
        points = [(x0 + (b * .5 + .25) / 12 * w, y0 + statistics.median(bin_values[(mode, b)]) / ceiling * h) for b in range(24)]
        d.add(PolyLine([v for p in points for v in p], strokeColor=palette[i], strokeWidth=2))
        for x, y in points:
            d.add(Circle(x, y, 2.5, fillColor=palette[i], strokeColor=None))
        label(d, 760 + i * 100, 17, mode, 11, palette[i])
    for t in range(13):
        label(d, x0 + w * t / 12, y0 - 16, str(t), 10, anchor='middle')
    label(d, 500, 17, 'Planned arrival time, s (cohort is carried with each request)', 11, anchor='middle')
    label(d, 10, 309, 'p95, ms', 10)
    renderSVG.drawToFile(d, str(folder / 'timeline.svg'))
    renderPDF.drawToFile(d, str(folder / 'timeline.pdf'))
    outputs.append('timeline.svg')

    d = Drawing(1000, 370)
    label(d, 30, 337, 'Local TCP experiment: three processes on one Windows computer', 20)
    label(d, 30, 305, 'Per run: service and dependency restarted; generator process and event loop reused within the series.', 12)
    boxes = [(25, 145, 245, 130), (345, 145, 310, 130), (730, 145, 245, 130)]
    for x, y, width, height in boxes:
        d.add(Rect(x, y, width, height, fillColor=colors.HexColor('#f2f6fa'), strokeColor=palette[0], rx=8, ry=8))
    lines = [
        (45, 250, ['Generator', '20 or 60 requests/s; open loop', 'Same trace; maximum 128 in flight', 'Measure planned and actual send']),
        (365, 250, ['Service: common asyncio ingress', '8 active calls + 64 queue slots', 'A: blocking sockets, 8 threads', 'B: asyncio streams, semaphore 8']),
        (750, 250, ['Dependency simulator', '20 ms / 200 ms / injected faults', '503 or 600 ms response delay', 'Recovered cohort: 20 ms'])]
    for x, y, content in lines:
        for j, text in enumerate(content):
            label(d, x, y - j * 25, text, 12 if j else 14)
    for start, end in ((270, 345), (655, 730)):
        d.add(Line(start + 3, 210, end - 5, 210, strokeColor=palette[0], strokeWidth=2))
        d.add(Line(end - 12, 217, end - 5, 210, strokeColor=palette[0], strokeWidth=2))
        d.add(Line(end - 12, 203, end - 5, 210, strokeColor=palette[0], strokeWidth=2))
        label(d, (start + end) / 2, 229, 'TCP', 10, anchor='middle')
    label(d, 30, 105, 'New loopback connection per request; JSON/LF; no HTTP, TLS, retries, pooling, or circuit breaker.', 12)
    label(d, 30, 80, '300 ms deadline: sync starts inside blocking_rpc after thread dispatch; async starts on entry to the rpc timeout.', 11)
    label(d, 30, 60, 'Semaphore wait excluded. dependency_ms includes sync dispatch and socket closing; it is not the sleep duration.', 11)
    label(d, 30, 40, 'recovered is a new request cohort, not a global dependency recovery. asyncio is not Reactive Streams.', 11)
    label(d, 30, 20, 'generator_cpu_s also includes final control RPCs, sorting, validation and CSV writing.', 11)
    renderSVG.drawToFile(d, str(folder / 'architecture.svg'))
    renderPDF.drawToFile(d, str(folder / 'architecture.pdf'))
    outputs.append('architecture.svg')
    for name in outputs:
        text = (folder / name).read_text(encoding='utf-8')
        assert '<image' not in text and 'data:image' not in text
    (folder / 'analysis_environment.json').write_text(json.dumps({'python': sys.version, 'reportlab': reportlab.Version, 'figures': outputs, 'vector_validation': 'SVG has no image or data:image elements'}, indent=2), encoding='utf-8')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=Path(__file__).parent / 'results')
    args = parser.parse_args()
    folder = args.input.resolve()
    cfg = json.loads((folder / 'config.json').read_text())
    manifest = json.loads((folder / 'manifest.json').read_text())
    complete = json.loads((folder / 'COMPLETE.json').read_text())
    all_rows = {p.stem: read_rows(p) for p in sorted(folder.glob('*rps_rep*.csv'))}
    assert len(all_rows) == complete['runs'] == len(manifest['order'])
    summary, diagnostics = [], []
    for tag, rows in all_rows.items():
        rate, rep, mode = rows[0]['rate'], rows[0]['repeat'], rows[0]['mode']
        n = round(rate * cfg['phase_seconds'] * 4)
        assert [r['id'] for r in rows] == list(range(n))
        assert all(abs(r['planned_s'] - r['id'] / rate) < 1e-6 for r in rows)
        assert all(r['phase'] == PHASES[min(3, int(r['id'] / rate / cfg['phase_seconds']))] for r in rows)
        unexpected = []
        for r in rows:
            expected = (504 if r['id'] % 2 else 503) if r['phase'] == 'fault' else 200
            assert r['value'] == (r['id'] * 2 + 1 if r['status'] == 200 else None)
            if r['status'] not in (expected, 429):
                unexpected.append(r['id'])
        meta = json.loads((folder / f'{tag}.json').read_text())
        assert meta['service_stats']['peak_active'] <= cfg['concurrency']
        assert meta['service_stats']['peak_pending'] <= cfg['concurrency'] + cfg['queue_capacity']
        assert meta['service_stats']['active'] == meta['service_stats']['pending'] == 0
        diagnostics.append({'run': tag, 'rate': rate, 'repeat': rep, 'mode': mode, 'requests': n,
            'unexpected_statuses': len(unexpected), 'lag_p95_ms': percentile([r['launch_lag_ms'] for r in rows], .95),
            'lag_max_ms': max(r['launch_lag_ms'] for r in rows),
            'peak_active': meta['service_stats']['peak_active'], 'peak_pending': meta['service_stats']['peak_pending'],
            'service_cpu_s': meta['service_cpu_s'], 'generator_cpu_s': meta['generator_cpu_s'],
            'dependency_cpu_s': meta['dependency_cpu_s'], 'peak_generator': meta['peak_generator']})
        for phase in PHASES:
            group = [r for r in rows if r['phase'] == phase]
            summary.append({'run': tag, 'rate': rate, 'repeat': rep, 'mode': mode, 'phase': phase,
                'n': len(group), 'successes': sum(r['status'] == 200 for r in group),
                'rejections': sum(r['status'] == 429 for r in group),
                'dependency_errors': sum(r['status'] == 503 for r in group),
                'timeouts': sum(r['status'] == 504 for r in group),
                'success_pct': 100 * sum(r['status'] == 200 for r in group) / len(group),
                'p50_ms': percentile([r['latency_ms'] for r in group], .5),
                'p95_ms': percentile([r['latency_ms'] for r in group], .95),
                'scheduled_p95_ms': percentile([r['scheduled_latency_ms'] for r in group], .95),
                'queue_p95_ms': percentile([r['queue_ms'] for r in group], .95),
                'success_latency_p95_ms': percentile([r['latency_ms'] for r in group if r['status'] == 200], .95) if any(r['status'] == 200 for r in group) else ''})
    # Pairwise identity of planned workload; response statuses can differ because admission depends on timing.
    for rate in cfg['rates']:
        for rep in range(1, cfg['repeats'] + 1):
            pair = [rows for rows in all_rows.values() if rows[0]['rate'] == rate and rows[0]['repeat'] == rep]
            assert len(pair) == 2 and {r[0]['mode'] for r in pair} == {'sync', 'async'}
            assert [(r['id'], r['phase']) for r in pair[0]] == [(r['id'], r['phase']) for r in pair[1]]
    aggregate, paired = [], []
    for rate in cfg['rates']:
        for phase in PHASES:
            for mode in ('sync', 'async'):
                group = [r for r in summary if r['rate'] == rate and r['phase'] == phase and r['mode'] == mode]
                assert len(group) == cfg['repeats']
                aggregate.append({'rate': rate, 'phase': phase, 'mode': mode,
                    'median_p95_ms': statistics.median(r['p95_ms'] for r in group),
                    'min_p95_ms': min(r['p95_ms'] for r in group), 'max_p95_ms': max(r['p95_ms'] for r in group),
                    'median_success_pct': statistics.median(r['success_pct'] for r in group),
                    'total_rejections': sum(r['rejections'] for r in group),
                    'median_queue_p95_ms': statistics.median(r['queue_p95_ms'] for r in group)})
            for rep in range(1, cfg['repeats'] + 1):
                pair = {r['mode']: r for r in summary if r['rate'] == rate and r['phase'] == phase and r['repeat'] == rep}
                paired.append({'rate': rate, 'phase': phase, 'repeat': rep,
                    'delta_p95_async_minus_sync_ms': pair['async']['p95_ms'] - pair['sync']['p95_ms'],
                    'delta_success_async_minus_sync_pp': pair['async']['success_pct'] - pair['sync']['success_pct']})
    write_csv(folder / 'summary_by_run_phase.csv', summary)
    write_csv(folder / 'aggregate.csv', aggregate)
    write_csv(folder / 'paired_differences.csv', paired)
    write_csv(folder / 'diagnostics.csv', diagnostics)
    timing = []
    for rate in cfg['rates']:
        for mode in ('sync', 'async'):
            for phase, status in [('normal', 200), ('slow', 200), ('fault', 504), ('recovered', 200)]:
                group = [r for rows in all_rows.values() for r in rows if r['rate'] == rate and r['mode'] == mode and r['phase'] == phase and r['status'] == status]
                timing.append({'rate': rate, 'mode': mode, 'phase': phase, 'status': status,
                    'n': len(group), 'dependency_min_ms': min(r['dependency_ms'] for r in group),
                    'dependency_p50_ms': percentile([r['dependency_ms'] for r in group], .5),
                    'dependency_p95_ms': percentile([r['dependency_ms'] for r in group], .95),
                    'dependency_max_ms': max(r['dependency_ms'] for r in group)})
    write_csv(folder / 'timer_diagnostics.csv', timing)
    validation = {'runs': len(all_rows), 'measured_requests': sum(len(r) for r in all_rows.values()),
        'semantic_probe_count': len(all_rows) * 5, 'warmup_requests': len(all_rows) * cfg['warmup_requests'],
        'complete_trace': True, 'paired_workload_identity': True, 'business_values_correct': True,
        'concurrency_and_queue_limits': True, 'unexpected_status_count': sum(r['unexpected_statuses'] for r in diagnostics),
        'status_counts': dict(Counter(r['status'] for rows in all_rows.values() for r in rows)),
        'maximum_generator_lag_ms': max(r['lag_max_ms'] for r in diagnostics),
        'saved_benchmark_hash_matches': hashlib.sha256((folder / 'benchmark_source.py').read_bytes()).hexdigest() == manifest['source_sha256']}
    early = []
    for rows in all_rows.values():
        for r in rows:
            nominal = cfg['dependency_timeout_seconds'] if r['status'] == 504 else (cfg['slow_delay_seconds'] if r['phase'] == 'slow' else cfg['normal_delay_seconds'])
            if r['status'] in (200, 504) and r['dependency_ms'] < nominal * 1000 - 0.5:
                early.append((r['run'], r['id']))
    validation['dependency_total_below_nominal_lower_bound_count'] = len(early)
    validation['clock_mode'] = cfg.get('clock_mode', 'default')
    if cfg.get('clock_mode') == 'perf_counter':
        assert not early, 'Timing lower-bound check failed'
    (folder / 'validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')
    figures(folder, summary, aggregate, all_rows, cfg)
    table = ''.join('<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in (a['rate'], a['phase'], a['mode'], f"{a['median_p95_ms']:.1f}", f"{a['min_p95_ms']:.1f}–{a['max_p95_ms']:.1f}", f"{a['median_success_pct']:.1f}", a['total_rejections'])) + '</tr>' for a in aggregate)
    pairtable = ''
    for rate in cfg['rates']:
        for phase in PHASES:
            ds = [r['delta_p95_async_minus_sync_ms'] for r in paired if r['rate'] == rate and r['phase'] == phase]
            pairtable += f'<tr><td>{rate}</td><td>{phase}</td><td>{statistics.median(ds):.1f}</td><td>{min(ds):.1f}–{max(ds):.1f}</td></tr>'
    doc = '''<!doctype html><html lang="uk"><meta charset="utf-8"><title>Пілотне дослідження Python I/O</title><style>body{max-width:1100px;margin:40px auto;padding:0 24px;font:17px/1.55 Arial,sans-serif;color:#172536}h1{font-size:30px;line-height:1.2}h2{font-size:23px;margin-top:30px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid #ccd6df;padding:7px;text-align:left}th{background:#edf3f7}figure{margin:25px 0}figure img{width:100%}figcaption{font-size:14px;color:#435467}code{background:#edf3f7;padding:2px 5px}.note{border-left:4px solid #c15027;padding:12px 18px;background:#fff6ef}</style>
<h1>Поведінка синхронних та асинхронних Python-сервісів за деградації I/O-залежностей</h1>
<p>Відтворюваний локальний пілот для обґрунтування експериментальної частини розділу монографії. Власна реалізація; вихідний код магістерської роботи не використано.</p>
<p class="note"><b>Межа інтерпретації:</b> порівняно блокувальні та неблокувальні виклики залежності за однакового обмеження паралельності. Це не тест Reactive Streams і не перевірка виробничого мікросервісного комплексу. Наукову новизну ці дані самі по собі не встановлюють.</p>
<h2>Методика</h2><p>Генератор, сервіс і залежність працювали в окремих процесах на одному комп'ютері через TCP loopback. Приймач запитів сервісу спільний; оброблення використовує або пул із 8 потоків і блокувальні сокети, або asyncio із семафором на 8 активних операцій. Черга — до 64 очікувачів. Номінальний бюджет I/O — 300 мс: у sync дедлайн починається всередині blocking_rpc, коли завдання вже виконується в потоці після dispatch; у async — при вході в asyncio.timeout усередині rpc після допуску. Очікування семафора виключено в обох. Dispatch до blocking_rpc не входить до дедлайну sync, але входить до dependency_ms; цей показник також охоплює закриття сокета поза дедлайном. Отже, наскрізні бюджети після допуску не тотожні. Повторних спроб немає.</p>
<p>Відкрите навантаження 20 та 60 запитів/с; по 5 парних повторень із перемішаним порядком. Перед кожним запуском створено нові процеси сервісу та залежності; генератор і його event loop залишаються спільними для всієї серії. Повтори не є повністю незалежними процесними чи системними середовищами. Кожен запуск: чотири фази сліду по 3 с. Параметр затримки залежності — 20 мс, потім 200 мс; у fault половина запитів має результат 503, половина — перевищення таймауту. recovered — нова когорта із заданими 20 мс і успішним результатом залежності, а не глобальна зміна стану залежності. Когорта передається з кожним запитом; запити різних когорт можуть виконуватися одночасно. Глобальне відновлення залежності та час такого відновлення не вимірювалися.</p>
<p>generator_cpu_s включає генерацію навантаження, завершальні контрольні RPC, сортування результатів, перевірки, підготовку полів і запис сирого CSV до моменту кінцевого process_time. Це не чистий CPU генерації запитів; межі цього інтервалу не тотожні інтервалам CPU сервісу й залежності, обчисленим між їхніми контрольними RPC. Висновки про CPU-ефективність на основі таких різних меж не робляться.</p>
<figure><img src="architecture.svg" alt="Векторна схема експерименту"><figcaption>Рис. 1. Стенд: генератор → сервіс → модельована залежність. Усі компоненти на одному комп'ютері.</figcaption></figure>
<h2>Результати та перевірки</h2>'''
    doc += f'<p>Завершено {validation["runs"]} запусків, {validation["measured_requests"]} виміряних запитів, {validation["semantic_probe_count"]} семантичних проб та {validation["warmup_requests"]} запитів прогрівання. Повноту слідів, відповідність бізнес-значень та ліміти перевірено. Неочікуваних статусів: {validation["unexpected_status_count"]}. Максимальне запізнення генератора: {validation["maximum_generator_lag_ms"]:.1f} мс.</p>'
    doc += '<p>У таблиці наведено медіану п' + "'" + 'яти p95, обчислених окремо для запусків. Діапазон — мінімум–максимум цих п' + "'" + 'яти оцінок, не довірчий інтервал. Затримки охоплюють усі статуси; швидка відмова не є успішним виконанням. «429» — сумарні відхилення для п' + "'" + 'яти запусків відповідного режиму та фази.</p>'
    doc += '<table><tr><th>Запитів/с</th><th>Фаза</th><th>Режим</th><th>Медіана p95, мс</th><th>Діапазон p95, мс</th><th>Медіана успіхів, %</th><th>429, шт.</th></tr>' + table + '</table>'
    doc += '<figure><img src="latency.svg" alt="Векторний графік затримок"><figcaption>Рис. 2. Затримки запитів за фазами. Різні вертикальні масштаби для двох інтенсивностей.</figcaption></figure>'
    doc += '<h2>Парне зіставлення</h2><p>Різниці async − sync обчислено всередині кожної пари. Від' + "'" + 'ємна різниця означає менший p95 async у цій парі. Описові дані не є тестом статистичної значущості.</p><table><tr><th>Запитів/с</th><th>Фаза</th><th>Медіана різниць, мс</th><th>Діапазон різниць, мс</th></tr>' + pairtable + '</table>'
    doc += '<figure><img src="timeline.svg" alt="Векторний часовий графік"><figcaption>Рис. 3. Затримки від запланованого моменту відправлення, включно із запізненням генератора. Групування — за запланованим часом, а не часом завершення. Лінії з' + "'" + 'єднують медіани p95 для інтервалів 0,5 с.</figcaption></figure>'
    doc += '''<h2>Що можна стверджувати</h2><p>За 60 запитів/с фаза сліду із параметром затримки 200 мс перевищує ідеалізовану місткість 8/0,2 = 40 запитів/с. Тому черга та відхилення є передбачуваними наслідками обмежень, а не самостійним доказом недоліку конкретної моделі I/O. На нову когорту recovered впливає залишок черги попередніх когорт; це не вимірювання глобального відновлення залежності. Для висновку про якість потрібно спільно оцінювати затримку, успіхи та відхилення.</p><p>Пілот дає відтворювану методику й локальні вимірювання. Він не доводить універсальної переваги asyncio або потоків; в обох варіантах concurrency=8. Запити всередині запуску залежні, генератор спільний для серії, а кількість повторень мала.</p>
<h2>Обмеження і наступний етап</h2><p>Спільний комп'ютер і фонові програми, Windows-планування, генератор asyncio на тій самій машині, короткі фази, одна межа паралельності, відсутність TLS/HTTP/пулів з'єднань, штучна затримка залежності. Реальна БД, процесорне навантаження та розподілена мережа не досліджувалися. Потрібні окремий генератор, реальна залежність, довші фази, додаткові рівні паралельності та незалежне відтворення перед широкими висновками.</p><p><b>Часові механізми — суттєвий чинник:</b> у цьому Python 3.12 Windows time.monotonic має заявлену роздільність 15,625 мс (GetTickCount64), тоді як perf_counter — 0,0001 мс (QueryPerformanceCounter). asyncio використовує свій годинник для дедлайнів; блокувальний шлях обчислює залишок дедлайну через perf_counter і socket timeout. Отже, номінальні 300 мс задані однаково, але фактичні таймаути не тотожні. Малих різниць p95 не можна безпосередньо приписувати ефективності моделі I/O. Фактичні інтервали збережено в timer_diagnostics.csv, характеристики годинників — ../timing_environment.json. Потрібен окремий контроль цього чинника; налаштування таймерів ОС не змінювалися.</p><p>Окремо слід дослідити політики admission control, наскрізний дедлайн разом із чергою і обмежені повтори. Їхня користь або перевага тут не перевірялася.</p>
<h2>Артефакти і відтворення</h2><p>Поруч із цим звітом: сирі CSV кожного запуску; JSON з конфігурацією, середовищем, порядком запусків, семантичними пробами; summary_by_run_phase.csv, aggregate.csv, paired_differences.csv, diagnostics.csv, validation.json. Повний опис — ../PROTOCOL.txt. Код — ../benchmark.py та ../analyze.py. SVG містять векторні елементи без растрових вставок.</p><pre>python benchmark.py --config config.json --output results_new
python analyze.py --input results_new</pre><p>benchmark.py потребує Python 3.12+ і лише стандартної бібліотеки. analyze.py для SVG використовує reportlab. Версії зафіксовані. Новий каталог результатів має не існувати.</p>
<p>Документація: <a href="https://docs.python.org/3.12/library/asyncio-stream.html">asyncio streams</a>; <a href="https://docs.python.org/3.12/library/concurrent.futures.html">ThreadPoolExecutor</a>. Поведінку конкретного експерименту визначає збережений код і фактична версія Python.</p></html>'''
    if cfg.get('clock_mode') == 'perf_counter':
        doc = doc.replace('Відтворюваний локальний пілот для', 'Контрольна серія з високоточним годинником. Відтворюваний локальний пілот для')
        start = doc.index('<p><b>Часові механізми')
        end = doc.index('</p>', start) + 4
        doc = doc[:start] + '''<p><b>Контроль часових механізмів:</b> перед цією серією проведено діагностичний запуск зі стандартним годинником Windows/asyncio. Зафіксовано роздільність monotonic 15,625 мс і повні виклики залежності, коротші за номінальний параметр затримки; сам sleep не вимірювався. У контрольній серії всі три процеси використовують perf_counter для часу event loop та відповідну _clock_resolution; блокувальний дедлайн також використовує perf_counter, але починається після dispatch у потік. Це локальна зміна Python event loop, не налаштувань ОС. Використання приватного атрибута CPython 3.12 обмежує переносність; при відтворенні на іншій версії потрібна перевірка. timer_diagnostics.csv описує повний dependency_ms, а не окремий sleep. Відсутність повних викликів коротших за номінальний параметр перевіряє лише необхідну нижню межу: накладні витрати можуть маскувати коротший sleep, тому фактичну тривалість sleep це не доводить. Початкова серія збережена як діагностична, до статистики цієї таблиці не включена. Порядок «стандартний годинник → контроль» не рандомізований, тому прямі причинні висновки про різницю серій не обґрунтовані.</p>''' + doc[end:]
    (folder / 'report.html').write_text(doc, encoding='utf-8')
    print(json.dumps({'validation': validation, 'aggregate': aggregate}, indent=2))

if __name__ == '__main__':
    main()
