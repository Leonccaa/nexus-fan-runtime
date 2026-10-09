"""Management CLI contracts that require no SSH connection or switch access."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import hostctl
import observe


class ManagementContracts(unittest.TestCase):
    def test_host_commands_require_an_explicit_destination(self):
        with patch('sys.argv', ['hostctl.py', 'sample']), \
             patch.object(hostctl, 'ssh') as ssh, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                hostctl.main()
        self.assertEqual(error.exception.code, 2)
        ssh.assert_not_called()

    def observe_report(self, actual_minimum):
        sample = {'temperatures_C': [60, 32, 26, 31],
                  'levels': [actual_minimum, 70, 99], 'fixed_speed': 0,
                  'applied_speed': actual_minimum, 'pfm_pid': 123,
                  'rotors': [['Fan%d' % module, rotor, actual_minimum, rpm]
                             for module in range(1, 5)
                             for rotor, rpm in [('fan1', 3800), ('fan2', 2800)]]}
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            destination = str(Path(directory) / 'samples.jsonl')
            with patch('sys.argv', ['observe.py', '--host', 'switch-admin',
                                   '--minimum', '30', '--minutes', '1',
                                   '--output', destination]), \
                 patch.object(observe, 'action', return_value=sample), \
                 patch.object(observe.time, 'sleep'), \
                 patch.object(observe.time, 'monotonic', side_effect=[0, 0, 60, 60]), \
                 contextlib.redirect_stdout(output):
                observe.main()
        return next(row for row in map(json.loads, output.getvalue().splitlines())
                    if row['event'] == 'report')

    def test_selected_30_stage_reports_healthy(self):
        self.assertTrue(self.observe_report(30)['healthy'])

    def test_20_stage_does_not_pass_observation_targeting_30(self):
        self.assertFalse(self.observe_report(20)['healthy'])


if __name__ == '__main__':
    unittest.main()
