"""Bounded blocking-vs-async TCP I/O pilot. Python 3.12+, standard library only."""
import argparse
import asyncio
import concurrent.futures
import csv
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
PHASES = ('normal', 'slow', 'fault', 'recovered')

def encode(obj):
    return (json.dumps(obj, separators=(',', ':')) + '\n').encode()

def behavior(req, cfg):
    if req['phase'] == 'slow':
        return cfg['slow_delay_seconds'], 200
    if req['phase'] == 'fault':
        if req['id'] % 2:
            return cfg['hang_delay_seconds'], 200
        return cfg['normal_delay_seconds'], 503
    return cfg['normal_delay_seconds'], 200

def result(req, status):
    return {'id': req['id'], 'status': status,
            'value': req['id'] * 2 + 1 if status == 200 else None}

async def close(writer):
    if writer is not None:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass

async def rpc(port, req, timeout):
    writer = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(encode(req))
            await writer.drain()
            data = await reader.readline()
            return json.loads(data)
    finally:
        await close(writer)

def blocking_rpc(port, req, timeout):
    deadline = time.perf_counter() + timeout
    def remaining():
        left = deadline - time.perf_counter()
        if left <= 0:
            raise TimeoutError()
        return left
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(remaining())
        sock.connect(('127.0.0.1', port))
        sock.settimeout(remaining())
        sock.sendall(encode(req))
        data = bytearray()
        while not data.endswith(b'\n'):
            sock.settimeout(remaining())
            block = sock.recv(4096)
            if not block:
                raise ConnectionError('EOF')
            data.extend(block)
        return json.loads(data)

async def server(args, cfg):
    sem = asyncio.Semaphore(cfg['concurrency'])
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=cfg['concurrency']) if args.role == 'sync' else None
    active = pending = peak_active = peak_pending = 0

    async def handle(reader, writer):
        nonlocal active, pending, peak_active, peak_pending
        try:
            req = json.loads(await asyncio.wait_for(reader.readline(), 5))
            if req.get('control') == 'stats':
                out = {'peak_active': peak_active, 'peak_pending': peak_pending,
                       'active': active, 'pending': pending, 'cpu_seconds': time.process_time()}
                if req.get('reset'):
                    peak_active = peak_pending = 0
            elif args.role == 'dependency':
                active += 1
                peak_active = max(peak_active, active)
                try:
                    delay, status = behavior(req, cfg)
                    await asyncio.sleep(delay)
                    out = result(req, status)
                finally:
                    active -= 1
            elif pending >= cfg['concurrency'] + cfg['queue_capacity']:
                out = dict(result(req, 429), queue_ms=0, dependency_ms=0)
            else:
                arrival = time.perf_counter()
                pending += 1
                peak_pending = max(peak_pending, pending)
                try:
                    async with sem:
                        start = time.perf_counter()
                        active += 1
                        peak_active = max(peak_active, active)
                        try:
                            if pool:
                                out = await asyncio.get_running_loop().run_in_executor(
                                    pool, blocking_rpc, args.dep_port, req, cfg['dependency_timeout_seconds'])
                            else:
                                out = await rpc(args.dep_port, req, cfg['dependency_timeout_seconds'])
                        except TimeoutError:
                            out = result(req, 504)
                        except (OSError, ValueError):
                            out = result(req, 502)
                        finally:
                            active -= 1
                        out['queue_ms'] = (start - arrival) * 1000
                        out['dependency_ms'] = (time.perf_counter() - start) * 1000
                finally:
                    pending -= 1
            writer.write(encode(out))
            await writer.drain()
        except (ConnectionError, OSError, ValueError, TimeoutError):
            pass
        finally:
            await close(writer)

    srv = await asyncio.start_server(handle, '127.0.0.1', 0, backlog=256)
    print(json.dumps({'port': srv.sockets[0].getsockname()[1]}), flush=True)
    async with srv:
        await srv.serve_forever()

def launch(role, cfg_path, dep_port, log):
    cmd = [sys.executable, str(Path(__file__).resolve()), '--role', role,
           '--config', str(cfg_path), '--dep-port', str(dep_port)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, text=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError(f'{role} exited during startup: {proc.poll()}')
    return proc, json.loads(line)['port']

def percentile(values, p):
    values = sorted(values)
    k = (len(values) - 1) * p
    lo = int(k)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (k - lo)

def hardware():
    out = {'platform': platform.platform(), 'python': sys.version,
           'executable': sys.executable, 'logical_cpus': os.cpu_count(),
           'processor': platform.processor(), 'event_loop_policy': type(asyncio.get_event_loop_policy()).__name__}
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                out['cpu_model'] = winreg.QueryValueEx(key, 'ProcessorNameString')[0]
            class Memory(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(x, ctypes.c_ulonglong) for x in ('total_phys', 'avail_phys', 'total_page', 'avail_page', 'total_virtual', 'avail_virtual', 'avail_extended')]
            memory = Memory()
            memory.length = ctypes.sizeof(memory)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
            out['memory_total_bytes'] = memory.total_phys
            out['memory_available_bytes'] = memory.avail_phys
        except Exception as exc:
            out['hardware_error'] = str(exc)
    return out

async def one_run(cfg, cfg_path, folder, rate, rep, mode, index, calibration=False):
    tag = f'{index:02d}_{rate}rps_rep{rep}_{mode}'
    processes = []
    with (folder / f'{tag}.stderr.log').open('w', encoding='utf-8') as log:
        try:
            dep, dep_port = launch('dependency', cfg_path, 0, log)
            processes.append(dep)
            svc, svc_port = launch(mode, cfg_path, dep_port, log)
            processes.append(svc)
            # Semantic probes do not enter the timed workload.
            probes = []
            for req in ({'id': 4, 'phase': 'normal'}, {'id': 7, 'phase': 'slow'},
                        {'id': 8, 'phase': 'fault'}, {'id': 9, 'phase': 'fault'},
                        {'id': 10, 'phase': 'recovered'}):
                out = await rpc(svc_port, req, 5)
                expected = 504 if req['phase'] == 'fault' and req['id'] % 2 else behavior(req, cfg)[1]
                assert {k: out[k] for k in ('id', 'status', 'value')} == result(req, expected), (req, out)
                probes.append({'request': req, 'response': out})
            # Concurrent warm-up also creates all pool threads.
            for base in range(0, cfg['warmup_requests'], cfg['concurrency']):
                await asyncio.gather(*(rpc(svc_port, {'id': i, 'phase': 'normal'}, 5)
                                       for i in range(base, min(base + cfg['concurrency'], cfg['warmup_requests']))))
            await asyncio.sleep(cfg['hang_delay_seconds'])
            before = await rpc(svc_port, {'control': 'stats', 'reset': True}, 5)
            dep_before = await rpc(dep_port, {'control': 'stats', 'reset': True}, 5)
            rows, tasks = [], set()
            duration = cfg['phase_seconds'] * 4
            count = round(rate * duration)
            origin = time.perf_counter() + 0.1
            gen_cpu_start = time.process_time()
            peak_generator = 0

            async def request(i, planned, phase):
                sent = time.perf_counter()
                req = {'id': i, 'phase': phase}
                try:
                    out = await rpc(svc_port, req, cfg['client_timeout_seconds'])
                except TimeoutError:
                    out = result(req, 598)
                except (OSError, ValueError):
                    out = result(req, 599)
                ended = time.perf_counter()
                row = {'run': tag, 'rate': rate, 'repeat': rep, 'mode': mode, 'id': i,
                       'phase': phase, 'planned_s': planned - origin, 'sent_s': sent - origin,
                       'ended_s': ended - origin, 'launch_lag_ms': (sent - planned) * 1000,
                       'latency_ms': (ended - sent) * 1000, 'scheduled_latency_ms': (ended - planned) * 1000,
                       **out}
                row['semantic_ok'] = out['id'] == i and out['value'] == (i * 2 + 1 if out['status'] == 200 else None)
                rows.append(row)

            for i in range(count):
                planned = origin + i / rate
                await asyncio.sleep(max(0, planned - time.perf_counter()))
                phase = PHASES[min(3, int((i / rate) / cfg['phase_seconds']))]
                if len(tasks) >= cfg['generator_cap']:
                    raise RuntimeError('Generator cap exceeded; run invalid')
                task = asyncio.create_task(request(i, planned, phase))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                peak_generator = max(peak_generator, len(tasks))
            await asyncio.gather(*tasks)
            await asyncio.sleep(max(0, origin + duration - time.perf_counter()))
            end = time.perf_counter()
            after = await rpc(svc_port, {'control': 'stats'}, 5)
            dep_after = await rpc(dep_port, {'control': 'stats'}, 5)
            rows.sort(key=lambda r: r['id'])
            assert len(rows) == count and all(r['semantic_ok'] for r in rows)
            assert after['peak_active'] <= cfg['concurrency']
            assert after['peak_pending'] <= cfg['concurrency'] + cfg['queue_capacity']
            fields = sorted(set().union(*(r.keys() for r in rows)))
            with (folder / f'{tag}.csv').open('w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fields)
                writer.writeheader()
                writer.writerows(rows)
            meta = {'tag': tag, 'utc': datetime.now(timezone.utc).isoformat(), 'rate': rate,
                    'repeat': rep, 'mode': mode, 'duration_s': end - origin,
                    'service_cpu_s': after['cpu_seconds'] - before['cpu_seconds'],
                    'dependency_cpu_s': dep_after['cpu_seconds'] - dep_before['cpu_seconds'],
                    'generator_cpu_s': time.process_time() - gen_cpu_start,
                    'service_stats': after, 'dependency_stats': dep_after,
                    'peak_generator': peak_generator, 'semantic_probes': probes,
                    'requests': len(rows), 'hardware_at_end': hardware()}
            (folder / f'{tag}.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
            print(json.dumps({'completed': tag, 'requests': count,
                              'p95_ms': round(percentile([r['latency_ms'] for r in rows], .95), 2),
                              'statuses': {s: sum(r['status'] == s for r in rows) for s in sorted(set(r['status'] for r in rows))},
                              'peak_active': after['peak_active']}), flush=True)
        finally:
            for proc in reversed(processes):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

async def run(args, cfg):
    folder = Path(args.output).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    cfg_path = folder / 'config.json'
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    (folder / 'environment.json').write_text(json.dumps(hardware(), indent=2), encoding='utf-8')
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (folder / 'benchmark_source.py').write_bytes(Path(__file__).read_bytes())
    (folder / 'timing_environment.json').write_text(json.dumps({k: vars(time.get_clock_info(k)) for k in ('perf_counter', 'monotonic', 'process_time')}, indent=2), encoding='utf-8')
    rng = random.Random(cfg['seed'])
    blocks = [(rate, rep) for rate in cfg['rates'] for rep in range(1, cfg['repeats'] + 1)]
    rng.shuffle(blocks)
    order = []
    # Counterbalance within rate; randomize which mode starts the first pair.
    starts = {rate: rng.choice([0, 1]) for rate in cfg['rates']}
    for rate, rep in blocks:
        modes = ['sync', 'async']
        if (rep + starts[rate]) % 2:
            modes.reverse()
        for mode in modes:
            order.append({'rate': rate, 'repeat': rep, 'mode': mode})
    (folder / 'manifest.json').write_text(json.dumps({'started_utc': datetime.now(timezone.utc).isoformat(),
        'source_sha256': source_hash, 'command': sys.argv, 'order': order}, indent=2), encoding='utf-8')
    for index, item in enumerate(order, 1):
        await one_run(cfg, cfg_path, folder, index=index, **{'rate': item['rate'], 'rep': item['repeat'], 'mode': item['mode']})
        await asyncio.sleep(0.3)
    (folder / 'COMPLETE.json').write_text(json.dumps({'finished_utc': datetime.now(timezone.utc).isoformat(), 'runs': len(order)}), encoding='utf-8')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=['run', 'dependency', 'sync', 'async'], default='run')
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('--output', default=str(ROOT / 'results'))
    parser.add_argument('--dep-port', type=int, default=0)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    def loop_factory():
        loop = asyncio.ProactorEventLoop() if sys.platform == 'win32' else asyncio.SelectorEventLoop()
        if config.get('clock_mode') == 'perf_counter':
            # Experimental timing control shared by generator, ingress and dependency.
            # CPython 3.12 private look-ahead resolution is set to the same clock.
            loop.time = time.perf_counter
            loop._clock_resolution = time.get_clock_info('perf_counter').resolution
        return loop
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(run(args, config) if args.role == 'run' else server(args, config))
