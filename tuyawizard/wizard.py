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
                # Remove user_code if it exists as it should be provided per session or from current info
                info.pop("user_code", None)
                self.info.update(info)
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
