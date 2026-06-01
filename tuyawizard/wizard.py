import json
import os
import time
import logging
from typing import Optional, Dict, List, Any, Callable, Tuple
from tuya_sharing import LoginControl, Manager, SharingTokenListener

class _TokenListener(SharingTokenListener):
    def __init__(self, parent: "TuyaWizard"):
        self.parent = parent

    def update_token(self, token_info: Dict[str, Any]):
        self.parent.info.update(token_info)
        self.parent._save_info()
        self.parent.logger.info("Token updated from Tuya cloud")

class TuyaWizard:
    def __init__(
        self, 
        info_file: str = "./tuyacreds.json", 
        logger: Optional[logging.Logger] = None,
        client_id: str = "HA_3y9q4ak7g4ephrvke",
        schema: str = "haauthorize"
    ):
        # << BEGIN CLIENT CREDENTIALS SETUP >>
        # FIXME: Currently using temporary Home Assistant client credentials.
        # Intended to be replaced when 3rd Party credentials become available.
        self.client_id = client_id
        self.schema = schema
        # << END OF CLIENT CREDENTIALS SETUP >>

        self.info_file = info_file
        self.login = LoginControl()
        self.info: Dict[str, Any] = {}
        self.manager: Optional[Manager] = None
        self.qr_callback: Optional[Callable[[Optional[str]], None]] = None
        self.logger = logger or logging.getLogger(__name__)

    def _load_saved_info(self, info: Optional[Dict[str, Any]] = None) -> bool:
        try:
            if not info:
                if not os.path.exists(self.info_file):
                    return False
                with open(self.info_file, "r", encoding="utf-8") as f:
                    info = json.load(f)
            
            if info:
                # Don't let a stored user_code override one provided this
                # session, but keep it as a fallback so QR re-login still
                # works when tokens expire.
                stored_user_code = info.pop("user_code", None)
                self.info.update(info)
                if stored_user_code and not self.info.get("user_code"):
                    self.info["user_code"] = stored_user_code
                self.logger.info("Loaded stored login info")
                return True
            return False
        except Exception as e:
            self.logger.warning(f"Failed to load stored info: {e}")
            return False

    def _save_info(self):
        if not self.info:
            return
        try:
            with open(self.info_file, "w", encoding="utf-8") as f:
                json.dump(self.info, f, ensure_ascii=False, indent=2)
            self.logger.info(f"Login info saved to {self.info_file}")
        except Exception as e:
            self.logger.error(f"Failed to save login info: {e}")

    def get_qr_url(self) -> Tuple[str, str]:
        response = self.login.qr_code(self.client_id, self.schema, self.info.get("user_code"))
        if not response.get("success"):
            raise Exception("QR request failed: " + response.get("msg", ""))

        result = response.get("result", {})
        qr_token = result.get("qrcode")
        if not qr_token:
            raise Exception("QR token missing in response")
            
        qr_url = f"tuyaSmart--qrLogin?token={qr_token}"
        return qr_token, qr_url

    def wait_for_login_result(self, qr_token: str, retry_sec: int = 5, timeout: int = 120) -> bool:
        start = time.time()
        self.logger.info("Waiting for Tuya login confirmation...")
        while time.time() - start <= timeout:
            ret, info = self.login.login_result(qr_token, self.client_id, self.info.get("user_code"))
            if ret:
                if "user_code" in self.info:
                    info.pop("user_code", None)
                self.info.update(info)
                self.logger.info(f"Login success: {info.get('username')}")
                return True
            time.sleep(retry_sec)
        raise TimeoutError("Login timeout: User did not scan the QR code in time.")

    def init_manager(self):
        if not self.info:
            raise RuntimeError("Login info missing")

        token_listener = _TokenListener(self)
        self.manager = Manager(
            self.client_id,
            self.info.get("user_code"),
            self.info.get("terminal_id"),
            self.info.get("endpoint"),
            self.info,
            token_listener
        )
        self.logger.info("Manager initialized")

    def convert_to_dict_recursive(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: self.convert_to_dict_recursive(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self.convert_to_dict_recursive(item) for item in obj]
        elif hasattr(obj, "__dict__"):
            return self.convert_to_dict_recursive(obj.__dict__)
        elif isinstance(obj, str) and obj.startswith("{") and obj.endswith("}"):
            try:
                return json.loads(obj)
            except json.JSONDecodeError:
                pass
        return obj

    def fetch_devices(self) -> List[Dict[str, Any]]:
        if not self.manager:
            raise RuntimeError("Manager not initialized")
        self.logger.info("Fetching device cache from Tuya cloud...")
        try:
            self.manager.update_device_cache()
        except Exception as e:
            self.logger.warning(f"Update device cache failed: {e}. Attempting QR login.")
            if not self.qr_login():
                return []
            self.manager.update_device_cache()
        
        tuyadevices = [self.convert_to_dict_recursive(dev) for dev in self.manager.device_map.values()]
        return tuyadevices

    def qr_login(self) -> bool:
        if not self.info.get("user_code"):
            self.logger.error("No User Code provided.")
            return False
            
        self.logger.info("Starting QR login")
        qr_token, qr_url = self.get_qr_url()
        
        if self.qr_callback:
            self.qr_callback(qr_url)
        else:
            self.logger.warning(f"No QR callback provided. The URL is: {qr_url}")
            
        try:
            self.wait_for_login_result(qr_token)
            self.init_manager()
            self._save_info()
            if self.qr_callback:
                self.qr_callback(None)
            return True
        except Exception as e:
            self.logger.error(f"QR Login failed: {e}")
            return False

    def login_auto(
        self,
        user_code: Optional[str] = None,
        creds: Optional[Dict[str, Any]] = None,
        qr_callback: Optional[Callable[[Optional[str]], None]] = None
    ) -> bool:
        """Try stored info first, fallback to QR login if fails"""
        if user_code:
            self.info["user_code"] = user_code
        if qr_callback:
            self.qr_callback = qr_callback

        if self._load_saved_info(creds):
            try:
                self.logger.info("Trying login from stored info...")
                self.init_manager()
                self.logger.info("Login via saved info succeeded")
                return True
            except Exception as e:
                self.logger.warning(f"Stored login info failed → fallback to QR: {e}")

        return self.qr_login()

    def close(self) -> None:
        """Release SDK-side resources held by this wizard.

        Long-running consumers that build a fresh ``TuyaWizard`` per
        operation leak per-instance state — the `tuya_sharing` SDK does
        not close its `requests.Session` pools or stop its optional
        ``SharingMQ`` thread on `Manager.unload()`. Calling ``close()``
        (or using ``with TuyaWizard(...) as w:``) makes teardown
        deterministic.

        Idempotent and tolerant of partial-construction states (no
        ``login_auto`` called yet, ``Manager`` constructed but never
        used, etc.).
        """
        manager = self.manager
        self.manager = None

        if manager is not None:
            # Stop the SharingMQ background thread if one was started.
            # `Manager.unload()` (tuya_sharing 0.2.9) does NOT do this —
            # see https://github.com/tuya/tuya-device-sharing-sdk/blob/main/tuya_sharing/manager.py
            mq = getattr(manager, "mq", None)
            if mq is not None:
                try:
                    mq.stop()
                except Exception as exc:
                    self.logger.warning(f"SharingMQ.stop() failed: {exc}")
                try:
                    if mq.is_alive():
                        mq.join(timeout=5)
                except Exception:
                    pass
                manager.mq = None

            # Invalidate the cloud terminal (the server-side bit
            # Manager.unload does cover).
            try:
                manager.unload()
            except Exception as exc:
                self.logger.warning(f"Manager.unload() failed: {exc}")

            # Close the CustomerApi connection pool — the actual
            # RSS-accumulating culprit observed by downstream consumers
            # (~750-950 KB per cycle).
            customer_api = getattr(manager, "customer_api", None)
            if customer_api is not None:
                # Break the wizard ↔ manager ↔ customer_api ↔ token_listener
                # cycle eagerly; gc can collect it, but breaking it here
                # lets the refcount path do the work immediately.
                customer_api.token_listener = None
                session = getattr(customer_api, "session", None)
                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass

        # LoginControl also holds a requests.Session. Closing it doesn't
        # prevent a follow-up login (requests reopens transparently),
        # but if this wizard is being discarded the pool should go too.
        login_session = getattr(self.login, "session", None) if self.login else None
        if login_session is not None:
            try:
                login_session.close()
            except Exception:
                pass

        self.qr_callback = None

    def __enter__(self) -> "TuyaWizard":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def load_devices_file(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict):
        payload = payload.get("devices", [])
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def save_devices_file(path: str, devices: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(devices, f, ensure_ascii=False, indent=2)


def choose_parent(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None
    preferred = [c for c in candidates if c.get("category") in {"wg2", "jzq", "zjq"}]
    return preferred[0] if preferred else candidates[0]


def index_parents(devices: List[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, str]]]:
    parents_by_key: Dict[str, List[Dict[str, Any]]] = {}
    missing_local_key_parents: List[Dict[str, str]] = []
    for device in devices:
        local_key = device.get("local_key")
        if not local_key:
            if device.get("sub") is False:
                missing_local_key_parents.append(
                    {"id": str(device.get("id", "")), "name": str(device.get("name", ""))}
                )
            continue
        if device.get("sub") is False:
            parents_by_key.setdefault(str(local_key), []).append(device)
    return parents_by_key, missing_local_key_parents


def match_parents(
    devices: List[Dict[str, Any]], parents_by_key: Dict[str, List[Dict[str, Any]]]
) -> Tuple[int, List[Dict[str, str]], Dict[str, List[Dict[str, str]]]]:
    assigned = 0
    missing_local_key: List[Dict[str, str]] = []
    missing_parent_match: Dict[str, List[Dict[str, str]]] = {}
    for device in devices:
        if device.get("sub") is not True:
            continue
        local_key = device.get("local_key")
        if not local_key:
            missing_local_key.append(
                {"id": str(device.get("id", "")), "name": str(device.get("name", ""))}
            )
            continue
        parent = choose_parent(parents_by_key.get(str(local_key), []))
        if parent and parent.get("id"):
            device["parent"] = parent["id"]
            assigned += 1
        else:
            missing_parent_match.setdefault(str(local_key), []).append(
                {"id": str(device.get("id", "")), "name": str(device.get("name", ""))}
            )
    return assigned, missing_local_key, missing_parent_match


def apply_fallback_assumption(
    devices_by_id: Dict[str, Dict[str, Any]],
    parents_by_key: Dict[str, List[Dict[str, Any]]],
    missing_local_key: List[Dict[str, str]],
    missing_parent_match: Dict[str, List[Dict[str, str]]],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    if len(missing_local_key) != 1 or len(missing_parent_match) != 1:
        return [], {}
    only_key = next(iter(missing_parent_match))
    if parents_by_key.get(only_key):
        return [], {}
    assumed_parent = missing_local_key[0]
    parent_device = devices_by_id.get(assumed_parent["id"])
    if parent_device is None:
        return [], {}
    parent_device["local_key"] = only_key
    subdevices = []
    for item in missing_parent_match[only_key]:
        device = devices_by_id.get(item["id"])
        if not device:
            continue
        device["parent"] = assumed_parent["id"]
        subdevices.append(item)
    summary = {
        "assumed_parent": {
            "id": assumed_parent["id"],
            "name": assumed_parent["name"],
            "local_key": only_key,
        },
        "subdevices": subdevices,
        "missing_local_key_count": len(missing_local_key),
        "missing_parent_match_count": len(subdevices),
    }
    return subdevices, summary


def scan_devices() -> List[Dict[str, Any]]:
    try:
        from rustuya import Scanner
    except Exception:
        return []
    return Scanner.scan() or []


def normalize_version(raw_version: Optional[str]) -> Optional[str]:
    if not raw_version:
        return None
    if raw_version.startswith("V") and "_" in raw_version:
        version_body = raw_version[1:].replace("_", ".")
        return version_body
    return raw_version


def parent_link_transform(devices: List[Dict[str, Any]], context: Dict[str, Any]) -> None:
    parents_by_key, missing_local_key_parents = index_parents(devices)
    assigned, missing_local_key, missing_parent_match = match_parents(devices, parents_by_key)
    devices_by_id = {str(device.get("id", "")): device for device in devices}
    fallback_matches, fallback_summary = apply_fallback_assumption(
        devices_by_id, parents_by_key, missing_local_key, missing_parent_match
    )
    if fallback_matches:
        assigned += len(fallback_matches)
        missing_parent_match = {}
    context.update(
        {
            "assigned": assigned,
            "missing_local_key": missing_local_key,
            "missing_local_key_parents": missing_local_key_parents,
            "missing_parent_match": missing_parent_match,
            "fallback_summary": fallback_summary,
        }
    )


def apply_scan_results(
    devices: List[Dict[str, Any]],
    scan_results: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge externally-supplied scan results into devices.

    Each scan_results item must have ``id`` and ``ip``; ``version`` is optional.
    """
    if context is None:
        context = {}
    by_id = {str(item.get("id")): item for item in scan_results if item.get("id")}
    updated_ip_list: List[Dict[str, str]] = []
    updated_version_list: List[Dict[str, str]] = []
    for device in devices:
        device_id = str(device.get("id", ""))
        scan_item = by_id.get(device_id)
        if not scan_item:
            continue
        scanned_ip = scan_item.get("ip")
        scanned_version = normalize_version(scan_item.get("version"))
        if scanned_ip and device.get("ip") != scanned_ip:
            updated_ip_list.append(
                {
                    "id": device_id,
                    "name": str(device.get("name", "")),
                    "ip": str(scanned_ip),
                }
            )
            device["ip"] = scanned_ip
        if scanned_version and device.get("version") != scanned_version:
            updated_version_list.append(
                {
                    "id": device_id,
                    "name": str(device.get("name", "")),
                    "version": str(scanned_version),
                }
            )
            device["version"] = scanned_version
    context.update(
        {
            "scan_updated_ip_list": updated_ip_list,
            "scan_updated_version_list": updated_version_list,
        }
    )
    return context


def scan_update_transform(devices: List[Dict[str, Any]], context: Dict[str, Any]) -> None:
    scanned = scan_devices()
    if not scanned:
        return
    apply_scan_results(devices, scanned, context)


VALID_POSTPROCESS_MODES = ("parent", "scan", "all")


def postprocess_devices(
    devices: List[Dict[str, Any]],
    mode: str,
    scan_results: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if mode not in VALID_POSTPROCESS_MODES:
        raise ValueError(
            f"Invalid postprocess mode: {mode!r}. "
            f"Expected one of {VALID_POSTPROCESS_MODES}."
        )
    if scan_results is not None and mode == "parent":
        logging.getLogger(__name__).warning(
            "scan_results were provided but mode is 'parent'; scan_results will be ignored."
        )
    context: Dict[str, Any] = {}
    if mode in ("parent", "all"):
        parent_link_transform(devices, context)
    if mode in ("scan", "all"):
        if scan_results is not None:
            apply_scan_results(devices, scan_results, context)
        else:
            scan_update_transform(devices, context)
    return context


def log_summary(context: Dict[str, Any], logger: logging.Logger) -> None:
    summary = context.get("fallback_summary", {})
    if summary:
        logger.info(
            f"{summary['missing_local_key_count']} device has no local_key and "
            f"{summary['missing_parent_match_count']} subdevices share the same local_key "
            "but no parent exists, so it was assumed and matched to the device without local_key."
        )
        assumed_parent = summary["assumed_parent"]
        logger.info(
            f"Assuming parent: {assumed_parent['id']} {assumed_parent['name']} "
            f"({assumed_parent['local_key']})"
        )
        logger.info("Subdevices:")
        for item in summary["subdevices"]:
            logger.info(f"{item['id']} {item['name']}")
    missing_local_key = context.get("missing_local_key", [])
    missing_local_key_parents = context.get("missing_local_key_parents", [])
    missing_parent_match = context.get("missing_parent_match", {})
    if missing_local_key:
        logger.info("Missing local_key:")
        for item in missing_local_key:
            logger.info(f"{item['id']} {item['name']}")
    if missing_local_key_parents:
        logger.info("Missing local_key parents:")
        for item in missing_local_key_parents:
            logger.info(f"{item['id']} {item['name']}")
    if missing_parent_match:
        logger.info("Missing parent match:")
        for key, items in missing_parent_match.items():
            for item in items:
                logger.info(f"{item['id']} {item['name']} ({key})")
    scan_updated_ip_list = context.get("scan_updated_ip_list", [])
    scan_updated_version_list = context.get("scan_updated_version_list", [])
    if scan_updated_ip_list:
        logger.info(f"Scan updated ip count: {len(scan_updated_ip_list)}")
        logger.info("Scan updated ip list:")
        for item in scan_updated_ip_list:
            logger.info(f"{item['id']} {item['name']} -> {item['ip']}")
    if scan_updated_version_list:
        logger.info(f"Scan updated version count: {len(scan_updated_version_list)}")
        logger.info("Scan updated version list:")
        for item in scan_updated_version_list:
            logger.info(f"{item['id']} {item['name']} -> {item['version']}")


def postprocess_file(
    device_file: str,
    mode: str,
    logger: logging.Logger,
    scan_results: Optional[List[Dict[str, Any]]] = None,
) -> None:
    if not os.path.exists(device_file):
        logger.warning(f"Device file not found: {device_file}")
        return
    try:
        devices = load_devices_file(device_file)
        context = postprocess_devices(devices, mode, scan_results=scan_results)
        save_devices_file(device_file, devices)
        log_summary(context, logger)
    except Exception as exc:
        logger.warning(f"Postprocess skipped: {exc}")

def terminal_qr_handler(url: Optional[str]):
    import qrcode
    if url:
        print("\n=== QR Code Generated ===")
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
        print("Scan this code with the SmartLife or TuyaSmart App. Waiting for scan...")
    else:
        print("Scan done.")

def wizard(
    user_code: Optional[str] = None, 
    device_file: str = "tuyadevices.json", 
    creds_file: str = "tuyacreds.json", 
    creds: Optional[Dict[str, Any]] = None, 
    qr_callback: Optional[Callable[[Optional[str]], None]] = None
):
    logger = logging.getLogger(__name__)
    
    if not user_code and not creds:
        user_code = input("Enter User Code from SmartLife or Tuya App (Leave blank to use Stored Code): ").strip()
        if not user_code:
            user_code = None

    tuya = TuyaWizard(logger=logger, info_file=creds_file)

    if not tuya.login_auto(
        user_code=user_code, 
        creds=creds, 
        qr_callback=qr_callback or terminal_qr_handler
    ):
        logger.error("Authentication failed.")
        return

    tuyadevices = tuya.fetch_devices()

    if tuyadevices:
        print(f"\n>> Saving {len(tuyadevices)} tuya devices to {device_file}")
        try:
            with open(device_file, "w", encoding="utf-8") as outfile:
                json.dump(tuyadevices, outfile, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save devices to {device_file}: {e}")
    else:
        logger.warning("No devices found or failed to fetch devices.")
