# Tuya Wizard

**tuyawizard** is a powerful and interactive CLI tool and Python library designed to help you discover and manage devices registered on the Tuya Cloud. It simplifies the authentication process using QR code login via the Smart Life or Tuya Smart app.

---

## 🔧 Features

- **Easy Authentication**: Support for QR code login using the Smart Life or Tuya Smart mobile apps.
- **Automatic Token Management**: Automatically handles and refreshes cloud tokens.
- **Device Discovery**: Fetches a comprehensive list of all registered devices and their details.
- **Flexible Usage**: Use it as a standalone CLI tool or integrate it into your own Python projects.
- **Refactored & Typed**: Modern Python implementation with type hints and improved error handling.

---

## 📦 Installation

Install the package via pip:

```bash
pip install tuyawizard
```

To enable the local network **scan** feature, you need to install the optional dependency:

```bash
pip install rustuya
```

---

## ▶️ Quick Start (CLI)

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

## � How to get User Code

1. Open the **Smart Life** or **Tuya Smart** app.
2. Go to **Me** (Profile) tab.
3. Tap on the **Settings** icon in the top right corner.
4. Go to **Account and Security**.
5. You will find your **User Code** (or Account ID) there.

---

## 💾 Output Files

- **`tuyacreds.json`**: Contains sensitive authentication tokens and endpoint information. Keep this file secure.
- **`tuyadevices.json`**: A structured JSON list of all your Tuya devices and their properties.

---

## ❗ Notes

- **Client Credentials**: This tool currently uses temporary Home Assistant client credentials. These are intended to be replaced when official 3rd-party developer credentials become more widely available.
- **Python Version**: Requires Python 3.8 or higher.

---

## 📄 License

This project is licensed under the MIT License.
