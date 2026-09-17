import sys
from pathlib import Path

# Make the slidehunt package importable when running ``pytest`` from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
