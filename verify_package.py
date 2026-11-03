"""Read-only verification of the published experiment; no network or load generation."""
from pathlib import Path
import csv,hashlib,json,xml.etree.ElementTree as ET

root=Path(__file__).resolve().parent
checked=0
for line in (root/'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
    digest,name=line.split('  ',1)
    p=root/name
    assert p.resolve().is_relative_to(root.resolve()),name
    assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
    checked+=1
for series in ('results','results_controlled_clock'):
    folder=root/series
    cfg=json.loads((folder/'config.json').read_text())
    manifest=json.loads((folder/'manifest.json').read_text())
    assert hashlib.sha256((folder/'benchmark_source.py').read_bytes()).hexdigest()==manifest['source_sha256']
    files=sorted(folder.glob('*rps_rep*.csv'))
    assert len(files)==20
    pairs={}
    total=0
    statuses={}
    for f in files:
        rows=list(csv.DictReader(f.open(encoding='utf-8',newline='')))
        meta=json.loads(f.with_suffix('.json').read_text())
        assert len(rows)==meta['requests']==round(meta['rate']*cfg['phase_seconds']*4)
        assert meta['service_stats']['peak_active']<=cfg['concurrency']
        assert meta['service_stats']['peak_pending']<=cfg['concurrency']+cfg['queue_capacity']
        assert meta['peak_generator']<=cfg['generator_cap']
        for row in rows:
            status=int(row['status']); request_id=int(row['id'])
            assert status in (200,429,503,504)
            assert row['semantic_ok']=='True'
            assert row['value']==(str(request_id*2+1) if status==200 else '')
            statuses[str(status)]=statuses.get(str(status),0)+1
        pairs.setdefault((meta['rate'],meta['repeat']),{})[meta['mode']]=[(r['id'],r['phase']) for r in rows]
        total+=len(rows)
    assert total==9600 and len(pairs)==10
    assert all(p['sync']==p['async'] for p in pairs.values())
    validation=json.loads((folder/'validation.json').read_text())
    assert statuses==validation['status_counts']
    for svg in folder.glob('*.svg'):
        assert not any(e.tag.rsplit('}',1)[-1]=='image' for e in ET.parse(svg).iter())
    print(f'{series}: 20 runs, {total} requests, business values and bounds verified')
print(f'{checked} file checksums verified; no load generated')
