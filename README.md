# Tuya Wizard

**tuyawizard** is a powerful and interactive CLI tool and Python library designed to help you discover and manage devices registered on the Tuya Cloud. It simplifies the authentication process using QR code login via the Smart Life or Tuya Smart app.

---

## Features

- **Easy Authentication**: Support for QR code login using the Smart Life or Tuya Smart mobile apps.
- **Automatic Token Management**: Automatically handles and refreshes cloud tokens.
- **Device Discovery**: Fetches a comprehensive list of all registered devices and their details.
- **Flexible Usage**: Use it as a standalone CLI tool or integrate it into your own Python projects.
- **Refactored & Typed**: Modern Python implementation with type hints and improved error handling.

---

## Installation

Install the package via pip:

```bash
pip install tuyawizard
```

To enable the local network **scan** feature, install the optional dependency:

```bash
pip install rustuya
```

When `tuyawizard` was installed with `pipx`, inject it into the same venv instead:

```bash
pipx inject tuyawizard rustuya
```

### `cryptography` (upstream missing dependency)

`tuyawizard` itself does not depend on `cryptography`, but the underlying [`tuya-device-sharing-sdk`](https://github.com/tuya/tuya-device-sharing-sdk) imports it without declaring it in its own requirements. If `ModuleNotFoundError: No module named 'cryptography'` appears at runtime, install it manually:

```bash
pip install cryptography
```

When `tuyawizard` was installed with `pipx`, inject it into the same venv instead:

```bash
pipx inject tuyawizard cryptography
```

(The bundled Windows `.exe` already includes it, so this only affects `pip`/`pipx`-installed setups.)

### Windows Executable

A pre-built single-file `tuyawizard.exe` is available on the [Releases page](https://github.com/3735943886/tuyawizard/releases/latest). Download `tuyawizard-windows.zip`, extract it, and run `tuyawizard.exe`. No Python install required; the `scan` feature is bundled.

> The executable is **not code-signed**, so Windows SmartScreen or antivirus software may warn on first run. Click **More info → Run anyway** to proceed.

---

## Quick Start (CLI)

You can run the wizard directly from your terminal:

```bash
python -m tuyawizard
```

### Command-line Options

| Option | Default | Description |
| :--- | :--- | :--- |
| `-device-file FILE` | `tuyadevices.json` | Path to save the discovered devices list (JSON). |
| `-credentials-file FILE` | `tuyacreds.json` | Path to load/save cloud authentication credentials (JSON). |
| `--postprocess` | `false` | Run postprocess after fetching devices. |
| `--postprocess-only` | `false` | Run postprocess only (skip wizard login/fetch). |
| `--postprocess-mode` | `all` | Select postprocess mode: `parent`, `scan`, `all`. |

### Post process
- **parent**: In the post‑process stage, the output JSON is adjusted so that each `subdevice` is properly linked to its parent device.
- **scan**: The output JSON is enriched with `IP` and `version` details obtained through the realtime scanner (requires `rustuya`).

### Postprocess Examples

Run wizard and then postprocess:

```bash
python -m tuyawizard --postprocess
```

Run postprocess only:

```bash
python -m tuyawizard --postprocess-only
```

Run parent-only postprocess:

```bash
python -m tuyawizard --postprocess-only --postprocess-mode parent
```

Run scan-only postprocess:

```bash
python -m tuyawizard --postprocess-only --postprocess-mode scan
```

---

## Using as a Python Module

`tuyawizard` can also be imported and used as a module inside your own Python
project — fetch the device list from the Tuya cloud and, optionally, enrich
it with LAN scan data.

### With `rustuya` installed

If `rustuya` is installed, just call `postprocess_devices` after fetching —
it runs the LAN scan and merges the results in one step:

```python
from tuyawizard import TuyaWizard, postprocess_devices

tuya = TuyaWizard()
tuya.login_auto(user_code="YOUR_USER_CODE")
devices = tuya.fetch_devices()

postprocess_devices(devices, mode="scan")  # scans via rustuya and merges
```

### Without `rustuya` (inject your own scan data)

If `rustuya` is not available but you already have `id`/`ip`(/`version`)
data collected from elsewhere (for example via `rustuya-bridge`), inject
those results directly. `version` is optional per item:

```python
from tuyawizard import TuyaWizard, apply_scan_results

tuya = TuyaWizard()
tuya.login_auto(user_code="YOUR_USER_CODE")
devices = tuya.fetch_devices()

scan_results = [
    {"id": "bf...01", "ip": "192.168.1.10"},
    {"id": "bf...02", "ip": "192.168.1.11", "version": "3.4"},
]
apply_scan_results(devices, scan_results)
```

### Lifecycle and cleanup (`close()` / context manager, v0.1.8+)

The underlying [`tuya_sharing` SDK](https://github.com/tuya/tuya-device-sharing-sdk) does not fully tear down on `Manager.unload()` — its `requests.Session` connection pool is left open, and the optional `SharingMQ` background thread (if started) is not stopped. Long-running consumers that build a fresh `TuyaWizard` per operation will accumulate state (rustuya-manager observed **~750-950 KB RSS per cycle, never reclaimed**).

Use the context-manager form to make teardown deterministic:

```python
from tuyawizard import TuyaWizard

with TuyaWizard() as tuya:
    tuya.login_auto(user_code="YOUR_USER_CODE")
    devices = tuya.fetch_devices()
# close() is called on exit — sessions closed, MQ thread joined,
# cloud terminal invalidated.
```

Or call `close()` explicitly from a `finally` block. It is idempotent and safe before `login_auto()` (partial-construction tolerant).

---

## How to get User Code

1. Open the **Smart Life** or **Tuya Smart** app.
2. Go to **Me** (Profile) tab.
3. Tap on the **Settings** icon in the top right corner.
4. Go to **Account and Security**.
5. You will find your **User Code** (or Account ID) there.

---

## Output Files

- **`tuyacreds.json`**: Contains sensitive authentication tokens and endpoint information. Keep this file secure.
- **`tuyadevices.json`**: A structured JSON list of all your Tuya devices and their properties.

---

## Notes

- **Client Credentials**: This tool currently uses temporary Home Assistant client credentials. These are intended to be replaced when official 3rd-party developer credentials become more widely available.
- **Python Version**: Requires Python 3.8 or higher.

---

## License

This project is licensed under the MIT License.
