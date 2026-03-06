import argparse
import logging
from .wizard import wizard, postprocess_file

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-device-file', default='tuyadevices.json', metavar='FILE', help="JSON file to load/save devices")
    parser.add_argument('-credentials-file', default='tuyacreds.json', metavar='FILE', help="JSON file to load/save cloud credentials")
    parser.add_argument('--postprocess', action='store_true', help="Run postprocess after fetching devices")
    parser.add_argument('--postprocess-only', action='store_true', help="Run postprocess only (skip wizard login/fetch)")
    parser.add_argument('--postprocess-mode', choices=['parent', 'scan', 'all'], default='all', help="Select postprocess mode")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if not args.postprocess_only:
        wizard(
            user_code=None,
            device_file=args.device_file,
            creds_file=args.credentials_file
        )
    if args.postprocess or args.postprocess_only:
        postprocess_file(args.device_file, args.postprocess_mode, logging.getLogger(__name__))

if __name__ == "__main__":
    main()
