# Manual screenshot provenance

These PNG files are captures of the real Tk or Qt application widgets on macOS. They are not
drawn mockups. All PC names, IP addresses, DRAM parts, binaries, campaigns, jobs, and timestamps
shown in them are generated demo values and contain no FTP credentials.

Regenerate the Workbench, Scratch, Rig, and monitoring views:

```bash
.venv/bin/python scripts/capture_manual_screenshots.py
```

Regenerate the Sequence Generator views from the sibling repository:

```bash
cd ../test-sequence-generator
.venv/bin/python scripts/capture_manual_screenshots.py \
  --output-dir ../project/docs/assets/screenshots
```
