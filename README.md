# Tuya Wizard

**tuyawizard** is an interactive wizard for discovering registered devices on Tuya Cloud.

---

## 🔧 Features
- QR code login via SmartLife / Tuya app
- Automatic cloud credential handling
- Fetch Tuya device information from the cloud

---

## 📦 Installation
```bash
pip install tuyawizard
```

---

## ▶️ Usage

### Standard execution

```bash
python3 -m tuyawizard
```

---

## ⚙️ Command-line Options

| Option                   | Description                                                                       |
| ------------------------ | --------------------------------------------------------------------------------- |
| `-device-file FILE`      | Path for storing the device list JSON                                             |
| `-credentials-file FILE` | Path for storing cloud credentials JSON                                           |

---

## 💾 Output

Upon completion, the wizard writes the following:

* Cloud credentials JSON
* Device informations JSON

---

## ❗ Notes

* Currently using temporary `Home Assistant` client credentials. Intended to be replaced when 3rd Party credentials become available.

---
