# Validation results

These results describe one hardware deployment of the pinned firmware and a two-hour observation. They do not imply compatibility with other images, chassis, workloads, or ambient conditions.

## Offline checks

- Thirteen patch and thermal-assessment contract tests passed.
- Three additional public-release management tests passed, covering explicit host selection and reporting against the selected 30-stage target.
- Python 3 syntax checks passed. The device patch also ran under NX-OS Python 2.7.
- Original x86 curve emulation passed 1,452 cases for a minimum of 20 and another 1,452 for 30, with no differences except replacement of the original low stage of 50.
- The D2 heating/cooling sequence at 25, 47, 55, 25, and 25 degrees produced 20, 70, 99, 70, and 20 for the 20-stage patch.
- Tests cover unknown firmware rejection, preservation of hot states, deferred warm transitions, restoration, manual configuration rejection, actual change notification for 30, and rollback when a reported 30 state has no corresponding RPM increase.

The emulation uses the original instruction stream from a privately supplied PFM image. Only logging imports are stubbed. No hardware driver or full system is emulated, and firmware is not included in this repository.

## Hardware checks

- Read-only scheduled checks succeeded before enabling application.
- Native Scheduler independently applied 50 to 20 and reported `Success (0)`.
- Manual restoration from 20 to 50 succeeded. Rotor speeds returned to 6,482-6,633 RPM for fan1 and 4,770-4,804 RPM for fan2.
- The persistent disable marker prevented scheduled reapplication. After intentional re-enabling, Scheduler applied 20 again.
- The original PFM executable hash stayed unchanged. The fixed-speed flag stayed at zero, with automatic hot stages of 70 and 99 retained.
- Configuration comparison showed only the project's Scheduler additions. The job and schedule were saved to startup configuration.

## Two-hour observation

Two consecutive sessions each covered a full 60 minutes measured with a monotonic clock. Sampling occurred every 30 seconds, with reports every five minutes. The published dataset contains **242 valid samples and zero errors**. Four physical fan modules, with two rotors each, stayed healthy and at control level 20 throughout.

A real gap of approximately 34 seconds between sessions remains in the relative timeline. No interpolation or fabricated sample was added. Absolute timestamps, process IDs, device identity, location, and private host information have been removed.

| Measurement | Result |
| --- | ---: |
| Peak ASIC temperature | 60 degrees C |
| Peak internal D1 temperature | 32 degrees C |
| Peak internal D2 temperature | 26 degrees C |
| Peak internal D3 temperature | 31 degrees C |
| fan1 RPM range across four modules | 2,268-2,374 |
| fan2 RPM range across four modules | 1,642-1,740 |
| Applied control level | 20 |

At the end of the first hour, the latest four ASIC five-minute means were 59.0, 59.1, 59.2, and 59.64 degrees. The last value exceeded the permitted 0.5-degree increase from the first window, so observation continued for another hour.

At the end of the second hour, all four window peaks were identical at ASIC/D1/D2/D3 = 60/32/26/31 degrees. ASIC means were 59.6, 60.0, 60.0, and 60.0 degrees. Both the strict nonincreasing-peak rule and the optional integer-jitter rule passed. The selected minimum remained 20; the conditional increase to 30 was not needed.

[Anonymized CSV](examples/two-hour-observation.csv) contains every valid observation, including the eight separate rotor readings. The chart can be reproduced with `plot_hour.py`.

![Temperatures and all eight rotor speeds](examples/two-hour-observation.png)

## Five-minute reports

The minute labels below sum the scheduled observation intervals. The second session adds the approximately 34-second gap preserved in the CSV. RPM ranges compare the four modules at each report.

| Observation minute | ASIC/D1/D2/D3 degrees C | fan1 RPM | fan2 RPM | Control level |
| ---: | --- | --- | --- | ---: |
| 5 | 57/30/25/28 | 2291–2357 | 1670–1708 | 20 |
| 10 | 59/31/26/29 | 2287–2354 | 1689–1706 | 20 |
| 15 | 59/32/26/30 | 2285–2360 | 1698–1706 | 20 |
| 20 | 59/32/26/30 | 2283–2364 | 1669–1714 | 20 |
| 25 | 60/32/26/31 | 2297–2359 | 1661–1719 | 20 |
| 30 | 59/32/26/31 | 2292–2352 | 1664–1718 | 20 |
| 35 | 59/32/26/31 | 2298–2353 | 1657–1715 | 20 |
| 40 | 59/31/26/30 | 2301–2360 | 1664–1708 | 20 |
| 45 | 59/32/26/31 | 2292–2359 | 1662–1721 | 20 |
| 50 | 59/32/26/31 | 2303–2361 | 1659–1721 | 20 |
| 55 | 59/32/26/31 | 2301–2367 | 1663–1714 | 20 |
| 60 | 60/32/26/31 | 2298–2360 | 1664–1707 | 20 |
| 65 | 59/32/26/31 | 2298–2366 | 1660–1720 | 20 |
| 70 | 60/32/26/31 | 2303–2365 | 1665–1728 | 20 |
| 75 | 59/32/26/31 | 2290–2360 | 1663–1731 | 20 |
| 80 | 60/32/26/31 | 2302–2366 | 1659–1723 | 20 |
| 85 | 59/32/26/31 | 2289–2362 | 1663–1732 | 20 |
| 90 | 60/32/26/31 | 2296–2364 | 1662–1731 | 20 |
| 95 | 59/32/26/31 | 2293–2367 | 1661–1719 | 20 |
| 100 | 59/32/26/31 | 2299–2366 | 1656–1721 | 20 |
| 105 | 60/32/26/31 | 2291–2368 | 1656–1729 | 20 |
| 110 | 60/32/26/31 | 2279–2348 | 1656–1723 | 20 |
| 115 | 60/32/26/31 | 2300–2346 | 1658–1727 | 20 |
| 120 | 60/32/26/31 | 2292–2339 | 1655–1733 | 20 |

## Limits of validation

- No full switch reboot, deliberate PFM restart, or induced high-temperature hardware test was performed.
- The 30-stage option passed offline checks but was not applied to hardware.
- The rollback child was armed and disarmed during successful application. Deliberate parent termination was not tested on hardware.
- No calibrated acoustic measurement, controlled ambient-temperature series, or load sweep was performed.
- Configuration persistence does not establish that a cold boot has been tested.

Runtime patch code is preserved from the hardware-tested implementation. Public-release changes affect destination selection, observation reporting, chart text, and anonymized export; the public management tools have been checked offline rather than redeployed to the observed device.
