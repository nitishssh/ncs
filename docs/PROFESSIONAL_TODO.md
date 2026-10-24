# Professional readiness checklist

Items are completed in this order. A checkbox is marked only after implementation and automated verification.

## P0 — Shoot reliability

- [x] Confirm record/stop with the camera before changing UI or microphone state.
- [x] Run connection, live view, status, settings, focus, zoom, shoot and record I/O outside the UI thread.
- [x] Coalesce slow camera polling so requests cannot build an unbounded backlog.

## P1 — Daily production

- [x] Guided connection setup with prerequisite diagnostics.
- [x] Persistent rotating logs and privacy-safe support report export.
- [x] Durable import journal and restart recovery for interrupted transfers.
- [ ] Named production sessions with scene/take metadata.
- [x] Hub device identities, viewer/operator roles and revocation.

## P1 — Release engineering

- [x] Cross-platform locked dependencies.
- [x] Automated macOS and Windows tests across supported Python versions.
- [ ] Packaged-app smoke tests in CI.
- [ ] macOS signing/notarization and Windows signing hooks.
- [ ] Application update channel and release metadata.

## P2 — Operator experience

- [ ] Keyboard shortcuts for shoot, record, focus and monitoring overlays.
- [ ] Full-screen monitoring and saved workspace layouts.
- [ ] Persistent, accessible alerts with severity and operation history.
- [ ] Keyboard navigation, contrast and screen-reader checks.

## P2 — Hardware validation

- [ ] Network loss during recording and download.
- [ ] Disk-full and microphone-disconnect recovery.
- [ ] Long-recording thermal and reconnect endurance run.
- [ ] Canon EDSDK USB validation on macOS and Windows.
- [ ] Bluetooth UUID and remote-operation validation.
- [ ] UVC/capture-device latency and reconnect validation.

Hardware validation requires the physical Canon EOS R50 V and the relevant USB, Bluetooth and capture hardware.
