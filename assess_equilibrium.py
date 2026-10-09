#!/usr/bin/env python3
"""Read-only assessment of four sensor peaks over the latest 20 minutes."""
import argparse
import json
from pathlib import Path


def assess(rows, allow_integer_jitter=False):
    if not rows:
        return {'stable': False, 'reason': 'no_samples'}
    end = rows[-1]['elapsed_s']
    if end < 1200:
        return {'stable': False, 'reason': 'less_than_20_minutes'}
    recent = [row for row in rows if row['elapsed_s'] >= end - 1230]
    if any(b['elapsed_s'] - a['elapsed_s'] > 45 for a, b in zip(recent, recent[1:])):
        return {'stable': False, 'reason': 'sampling_gap'}
    blocks = []
    means = []
    for index in range(4):
        upper = end - (3 - index) * 300
        lower = upper - 300
        window = [row for row in rows if lower < row['elapsed_s'] <= upper]
        valid = [row['sample'] for row in window if 'sample' in row]
        if len(valid) < 9 or len(valid) != len(window):
            return {'stable': False, 'reason': 'missing_samples', 'block': index + 1}
        blocks.append([max(sample['temperatures_C'][sensor] for sample in valid)
                       for sensor in range(4)])
        means.append([sum(sample['temperatures_C'][sensor] for sample in valid) / len(valid)
                      for sensor in range(4)])
    if allow_integer_jitter:
        # Permit a one-degree quantized peak, but reject a sustained mean rise.
        rising = [sensor for sensor in range(4)
                  if any(blocks[index][sensor] > blocks[0][sensor] + 1
                         or means[index][sensor] > means[0][sensor] + .5
                         for index in range(1, 4))]
    else:
        rising = [sensor for sensor in range(4)
                  if any(blocks[index + 1][sensor] > blocks[index][sensor] for index in range(3))]
    reason = ('integer_temperature_jitter_with_stable_means' if allow_integer_jitter
              else 'five_minute_peaks_nonincreasing') if not rising else 'temperature_rising'
    return {'stable': not rising, 'elapsed_s': end, 'reason': reason,
            'sensors': ['ASIC', 'D1', 'D2', 'D3'], 'five_minute_peaks_C': blocks,
            'five_minute_means_C': [[round(value, 2) for value in row] for row in means],
            'allow_integer_jitter': allow_integer_jitter,
            'rising_sensor_indices': rising}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='observe.py JSONL')
    parser.add_argument('--allow-integer-jitter', action='store_true',
                        help='Allow 1C peak jitter; reject any later block mean increase above 0.5C')
    args = parser.parse_args()
    print(json.dumps(assess([json.loads(line) for line in args.input.read_text().splitlines()],
                           allow_integer_jitter=args.allow_integer_jitter),
                     indent=2))


if __name__ == '__main__':
    main()
