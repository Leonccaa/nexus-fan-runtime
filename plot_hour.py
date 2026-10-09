#!/usr/bin/env python3
"""Export observations to CSV and render temperature / individual rotor charts."""
import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

TEMPERATURES = ['ASIC_C', 'D1_C', 'D2_C', 'D3_C']
ROTORS = ['module%d_fan%d_rpm' % (module, rotor)
          for module in range(1, 5) for rotor in (1, 2)]
FIELDS = ['host_utc', 'elapsed_s'] + TEMPERATURES + ['applied_speed', 'pfm_pid'] + ROTORS + ['error']


def load(path):
    if path.suffix == '.csv':
        with path.open(newline='') as source:
            return list(csv.DictReader(source))
    rows = []
    for line in path.read_text().splitlines():
        observation = json.loads(line)
        sample = observation.get('sample', {})
        row = dict.fromkeys(FIELDS, '')
        row.update(host_utc=observation['host_utc'], elapsed_s=observation['elapsed_s'],
                   error=observation.get('error', ''))
        if sample:
            row.update(zip(TEMPERATURES, sample['temperatures_C']))
            row.update(applied_speed=sample['applied_speed'], pfm_pid=sample['pfm_pid'])
            for name, rotor, speed, rpm in sample['rotors']:
                row['module%s_%s_rpm' % (name[3], rotor)] = rpm
        rows.append(row)
    return rows


def number(row, key):
    return float(row[key]) if row.get(key, '') != '' else math.nan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='observe.py JSONL or exported CSV')
    parser.add_argument('--output', type=Path, required=True, help='PNG, SVG, or PDF destination')
    parser.add_argument('--csv', type=Path, help='Optional CSV export destination')
    parser.add_argument('--anonymize', action='store_true',
                        help='Exclude absolute timestamps, process IDs, and raw error details from CSV')
    args = parser.parse_args()
    rows = load(args.input)
    if not rows:
        parser.error('No observation rows')
    if args.csv:
        with args.csv.open('w', newline='') as target:
            fields = ([key for key in FIELDS if key not in ('host_utc', 'pfm_pid')]
                      if args.anonymize else FIELDS)
            writer = csv.DictWriter(target, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            for row in rows:
                exported = {key: row.get(key, '') for key in fields}
                if args.anonymize and exported['error']:
                    exported['error'] = 'sample_error'
                writer.writerow(exported)
    plt.rcParams.update({'font.size': 11, 'axes.unicode_minus': False,
                         'axes.spines.top': False, 'axes.spines.right': False})
    x = [number(row, 'elapsed_s') / 60 for row in rows]
    fig, axes = plt.subplots(3, 1, figsize=(13, 10), sharex=True,
                             gridspec_kw={'height_ratios': [1.4, 1, 1]})
    fig.subplots_adjust(top=.88, bottom=.1, hspace=.28, left=.09, right=.97)
    duration = x[-1]
    fig.suptitle('Nexus automatic fan curve: temperatures and rotor speeds', fontsize=19, y=.97)
    fig.text(.5, .925, '%.1f minutes | %d samples | Relative observation time' %
             (duration, len(rows)), ha='center', color='#52606d')
    sensor_colors = ['#d1495b', '#00798c', '#edae49', '#6457a6']
    for key, label, color in zip(TEMPERATURES, ['ASIC', 'Internal D1', 'Internal D2', 'Internal D3'], sensor_colors):
        y = [number(row, key) for row in rows]
        axes[0].plot(x, y, label=label, color=color, linewidth=2)
    peak = max(number(row, 'ASIC_C') for row in rows if row['ASIC_C'] != '')
    axes[0].set_title('Temperatures | ASIC peak %.0f°C' % peak, loc='left', fontweight='bold')
    axes[0].set_ylabel('Temperature (°C)')
    axes[0].legend(loc='lower right', bbox_to_anchor=(1, 1.02), ncol=4, frameon=False)
    colors = ['#0072b2', '#e69f00', '#009e73', '#cc79a7']
    for rotor, axis in zip((1, 2), axes[1:]):
        for module, color in zip(range(1, 5), colors):
            y = [number(row, 'module%d_fan%d_rpm' % (module, rotor)) for row in rows]
            axis.plot(x, y, label='Module %d' % module, color=color, linewidth=1.5, alpha=.9)
        axis.set_title('Rotor fan%d in each module' % rotor, loc='left', fontweight='bold')
        axis.set_ylabel('Speed (RPM)')
        axis.legend(loc='upper right', ncol=4, frameon=False)
        axis.margins(y=.3)
    axis_end = max(5, math.ceil((duration - .001) / 5) * 5)
    for axis in axes:
        axis.grid(True, color='#dfe5eb', linewidth=.7, alpha=.8)
        axis.set_xlim(0, axis_end)
    axes[-1].set_xticks(range(0, axis_end + 1, 5))
    axes[-1].set_xlabel('Elapsed observation time (minutes)')
    valid = sum(not row.get('error') for row in rows)
    speeds = sorted({str(row['applied_speed']) for row in rows if row['applied_speed'] != ''})
    fig.text(.09, .035, 'Valid samples %d/%d | Applied control level %s | 4 modules, 2 rotors each | RPM axes use different ranges' %
             (valid, len(rows), '/'.join(speeds)), fontsize=10, color='#52606d')
    fig.savefig(args.output, dpi=160, facecolor='white')
    plt.close(fig)
    print(json.dumps({'chart': str(args.output), 'rows': len(rows), 'duration_min': duration}))


if __name__ == '__main__':
    main()
