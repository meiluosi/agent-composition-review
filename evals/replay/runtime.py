"""Load the candidate runtime directly; no private alternate checker."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'candidates/v4/agent-composition-review'
spec = importlib.util.spec_from_file_location('composition', SOURCE / 'scripts/composition.py')
composition = importlib.util.module_from_spec(spec)
spec.loader.exec_module(composition)
