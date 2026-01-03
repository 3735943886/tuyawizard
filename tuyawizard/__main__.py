import argparse
import logging
from .wizard import wizard

def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('-device-file', default='tuyadevices.json', metavar='FILE', help=f"JSON file to load/save devices")
  parser.add_argument('-credentials-file', default='tuyacreds.json', metavar='FILE', help=f"JSON file to load/save cloud credentials")
  args = parser.parse_args()

  logging.basicConfig(level=logging.INFO)

  wizard(
    user_code=None,
    DEVICEFILE=args.device_file,
    CREDSFILE=args.credentials_file
  )

if __name__ == "__main__":
  main()
