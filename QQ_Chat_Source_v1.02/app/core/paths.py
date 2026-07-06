from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
APP_DIR = ROOT_DIR / "app"
CONFIG_DIR = ROOT_DIR / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
DATA_ROOT = ROOT_DIR / "ALL_Fold"
NAPCAT_DIR = ROOT_DIR / "NapCat.Shell.Windows.Node"
PYTHON_EXE = ROOT_DIR / "python" / "python.exe"
