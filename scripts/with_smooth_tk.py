#!/usr/bin/env python3
"""Run Python arguments using the same optional Tk runtime as the GUI."""
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from asus_power.runtime import ensure_smooth_tk

# Preserve the project on the child import path for -m unittest and scripts.
import os
os.environ['PYTHONPATH'] = os.pathsep.join([str(Path(__file__).resolve().parent.parent), os.environ.get('PYTHONPATH', '')])
ensure_smooth_tk(sys.argv[1:])
if len(sys.argv) > 2 and sys.argv[1] == '-m':
    module = sys.argv[2]
    sys.argv = sys.argv[2:]
    runpy.run_module(module, run_name='__main__', alter_sys=True)
else:
    script = sys.argv[1]
    sys.argv = sys.argv[1:]
    runpy.run_path(script, run_name='__main__')
