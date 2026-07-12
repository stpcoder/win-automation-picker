# Manual screenshot provenance

These PNG files are captures of the real Tk or Qt application widgets on macOS. They are not
drawn mockups. The Tk capture script uses macOS ScreenCaptureKit to resolve the exact independent
window by process and title, so unrelated desktop windows cannot enter the image. All PC names, IP addresses,
DRAM parts, binaries, campaigns, jobs, and timestamps are generated demo values. No password is
stored; the example uses the placeholder environment variable `RIG_FTP_PASSWORD`.

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
