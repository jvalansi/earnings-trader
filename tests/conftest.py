import sys
from pathlib import Path

# Allow imports from src/ without PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
