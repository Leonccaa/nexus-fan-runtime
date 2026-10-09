"""Offline failure/transition checks; no switch, firmware image, or memory writes."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from assess_equilibrium import assess

SPEC = importlib.util.spec_from_file_location('nxos_patch', Path(__file__).with_name('nxos_patch.py'))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class MemoryFixture:
    low_addr = 4
    zone_addr = 8

    def __init__(self, low=50, state=50, temperatures=(42, 25, 25, 26)):
        self.words = {4: low, 8: state}
        self.temps = list(temperatures)
        self.writes = []

    def u32(self, address):
        return self.words[address]

    def write(self, address, value):
        self.words[address] = value
        self.writes.append((address, value))

    def temperatures(self):
        return self.temps


class PatchContracts(unittest.TestCase):
    def test_unknown_image_is_rejected_before_process_access(self):
        with patch('builtins.open', unittest.mock.mock_open(read_data=b'unknown image')):
            with patch.object(module.glob, 'glob') as processes:
                with self.assertRaisesRegex(RuntimeError, 'Unsupported PFM image'):
                    module.Memory(writable=True)
                processes.assert_not_called()

    def test_each_already_patched_normal_stage_is_untouched(self):
        for minimum in (20, 30):
            for state in (minimum, 70, 99):
                memory = MemoryFixture(low=minimum, state=state)
                with patch.object(module, 'refresh') as refresh:
                    self.assertEqual(module.ensure(memory, minimum), 'already_applied')
                    self.assertEqual(memory.writes, [])
                    refresh.assert_not_called()

    def test_warm_legacy_low_stage_defers_without_writes(self):
        memory = MemoryFixture(temperatures=(42, 25, 41, 26))
        self.assertEqual(module.ensure(memory), 'waiting_for_cold_transition')
        self.assertEqual(memory.writes, [])

    def test_restore_migrates_low_cache_but_preserves_hot_stages(self):
        for state in (20, 30, 70, 99):
            memory = MemoryFixture(low=30 if state == 30 else 20, state=state)
            with patch.object(module, 'refresh') as refresh:
                module.restore(memory)
            self.assertEqual(memory.words[4], 50)
            self.assertEqual(memory.words[8], 70 if state in (20, 30) else state)
            refresh.assert_called_once()

    def test_manual_increment_prevents_any_patch_write(self):
        memory = MemoryFixture()
        with patch.object(module, 'fans', return_value=[]):
            with patch.object(module, 'cli', return_value='hardware fan-zone 1 raise-speed 10'):
                with self.assertRaisesRegex(RuntimeError, 'Manual fan-zone'):
                    module.ensure(memory)
        self.assertEqual(memory.writes, [])

    def test_switch_to_30_notifies_hardware_and_uses_30_rpm_limits(self):
        memory = MemoryFixture(low=20, state=20)
        read_end, ack = os.pipe()

        def native_refresh():
            self.assertEqual(memory.words[8], 70)
            memory.words[8] = memory.words[4]

        memory.snapshot = lambda with_fans: {'zone_state': memory.words[8],
            'rotors': [['Fan%d' % i, rotor, memory.words[8], rpm]
                       for i in range(1, 5) for rotor, rpm in [('fan1', 3800), ('fan2', 2800)]]}
        try:
            before = [['Fan%d' % i, rotor, 20, rpm]
                      for i in range(1, 5) for rotor, rpm in [('fan1', 2300), ('fan2', 1700)]]
            with patch.object(module, 'fans', return_value=before), \
                 patch.object(module, 'cli', return_value=''), \
                 patch.object(module, 'arm_rollback', return_value=(ack, -123)), \
                 patch.object(module, 'refresh', side_effect=native_refresh), \
                 patch.object(module.os, 'waitpid', return_value=(-123, 0)):
                self.assertEqual(module.ensure(memory, 30), 'applied')
            self.assertEqual(memory.writes, [(4, 30), (8, 70)])
            self.assertEqual(os.read(read_end, 1), b'C')
        finally:
            os.close(read_end)
            try:
                os.close(ack)
            except OSError:
                pass

    def test_30_label_without_rpm_increase_rolls_back_instead_of_acknowledging(self):
        memory = MemoryFixture(low=20, state=20)
        read_end, ack = os.pipe()
        before = [['Fan%d' % i, rotor, 20, rpm]
                  for i in range(1, 5) for rotor, rpm in [('fan1', 2300), ('fan2', 1700)]]

        def native_refresh():
            memory.words[8] = memory.words[4]

        memory.snapshot = lambda with_fans: {'zone_state': memory.words[8],
            'rotors': [[name, rotor, memory.words[8], rpm] for name, rotor, _, rpm in before]}
        try:
            with patch.object(module, 'fans', return_value=before), \
                 patch.object(module, 'cli', return_value=''), \
                 patch.object(module, 'arm_rollback', return_value=(ack, -123)), \
                 patch.object(module, 'refresh', side_effect=native_refresh), \
                 patch.object(module.os, 'waitpid', return_value=(-123, 0)), \
                 patch.object(module, 'disable') as disabled, \
                 patch.object(module.time, 'time', side_effect=[0, 21]):
                with self.assertRaisesRegex(RuntimeError, 'Actual fan speed'):
                    module.ensure(memory, 30)
            self.assertEqual(memory.words[4], 50)
            disabled.assert_called_once()
            self.assertEqual(os.read(read_end, 1), b'')
        finally:
            os.close(read_end)
            try:
                os.close(ack)
            except OSError:
                pass


class EquilibriumContracts(unittest.TestCase):
    @staticmethod
    def rows():
        return [{'elapsed_s': index * 30,
                 'sample': {'temperatures_C': [59 + index % 2, 32, 26, 31]}}
                for index in range(41)]

    def test_integer_temperature_jitter_with_equal_peaks_is_stable(self):
        self.assertTrue(assess(self.rows())['stable'])

    def test_rising_internal_sensor_is_not_hidden_by_flat_asic(self):
        rows = self.rows()
        for row in rows:
            if row['elapsed_s'] > 900:
                row['sample']['temperatures_C'][3] = 32
        result = assess(rows)
        self.assertFalse(result['stable'])
        self.assertEqual(result['rising_sensor_indices'], [3])

    def test_error_or_missing_interval_cannot_count_as_equilibrium(self):
        rows = self.rows()
        rows[20] = {'elapsed_s': 600, 'error': 'lost connection'}
        self.assertFalse(assess(rows)['stable'])
        rows = self.rows()
        del rows[20]
        self.assertEqual(assess(rows)['reason'], 'sampling_gap')

    def test_short_observation_is_inconclusive(self):
        self.assertFalse(assess(self.rows()[:-1])['stable'])

    def test_isolated_one_degree_peak_is_jitter_not_sustained_warming(self):
        rows = self.rows()
        for row in rows:
            row['sample']['temperatures_C'][0] = 59
        rows[-1]['sample']['temperatures_C'][0] = 60
        self.assertFalse(assess(rows)['stable'])
        self.assertTrue(assess(rows, allow_integer_jitter=True)['stable'])

    def test_jitter_tolerance_still_rejects_sustained_one_degree_warming(self):
        rows = self.rows()
        for row in rows:
            if row['elapsed_s'] > 900:
                row['sample']['temperatures_C'][3] = 32
        self.assertFalse(assess(rows, allow_integer_jitter=True)['stable'])


if __name__ == '__main__':
    unittest.main()
