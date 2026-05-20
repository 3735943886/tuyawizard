import sys
import tuyawizard.__main__

if '--postprocess' not in sys.argv:
    sys.argv.append('--postprocess')

tuyawizard.__main__.main()
