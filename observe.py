#!/usr/bin/env python3
"""Read-only timed observation; samples every 30s, reports every 5min."""
import argparse
import datetime
import json
import time
from pathlib import Path

from hostctl import action


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='SSH destination configured for your switch')
    parser.add_argument('--minimum', type=int, choices=(20, 30), default=20)
    parser.add_argument('--minutes', type=int, default=60)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.minutes < 1:
        parser.error('--minutes must be positive')
    start = time.monotonic()
    duration = args.minutes * 60
    next_sample = 0
    next_report = min(300, duration)
    window = []
    errors = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as target:
        while True:
            time.sleep(max(0, start + next_sample - time.monotonic()))
            elapsed = time.monotonic() - start
            utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
            row = {'host_utc': utc, 'elapsed_s': round(elapsed, 3)}
            try:
                row['sample'] = action(args.host, 'sample')
            except Exception as error:
                row['error'] = str(error)
                errors += 1
                print(json.dumps({'event': 'sample_error', **row}), flush=True)
            target.write(json.dumps(row) + '\n')
            target.flush()
            window.append(row)
            if elapsed >= next_report:
                samples = [r['sample'] for r in window if 'sample' in r]
                report = {'event': 'report', 'elapsed_s': round(elapsed, 3),
                          'host_utc': utc, 'samples': len(samples), 'total_errors': errors}
                if samples:
                    latest = samples[-1]
                    report.update(temperatures_C=latest['temperatures_C'],
                                  window_max_C=[max(s['temperatures_C'][i] for s in samples) for i in range(4)],
                                  applied_speed=latest['applied_speed'],
                                  levels=latest['levels'], pfm_pid=latest['pfm_pid'])
                    for rotor in ('fan1', 'fan2'):
                        values = [r[3] for r in latest['rotors'] if r[1] == rotor]
                        report[rotor + '_rpm'] = [min(values), round(sum(values) / len(values)), max(values)]
                    report['healthy'] = all(s['levels'] == [args.minimum, 70, 99] and s['fixed_speed'] == 0
                                            and s['applied_speed'] in (args.minimum, 70, 99)
                                            and all(r[2] == s['applied_speed'] and r[3] >= 300 for r in s['rotors'])
                                            for s in samples)
                print(json.dumps(report), flush=True)
                window = []
                next_report = min(next_report + 300, duration)
            if elapsed >= duration:
                print(json.dumps({'event': 'complete', 'elapsed_s': round(time.monotonic() - start, 3),
                                  'total_errors': errors}), flush=True)
                break
            next_sample = min(next_sample + 30, duration)


if __name__ == '__main__':
    main()
