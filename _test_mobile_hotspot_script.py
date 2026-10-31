import importlib.util
import logging
import subprocess
from pathlib import Path


script_path = Path(__file__).with_name("start_hotspot.py")
spec = importlib.util.spec_from_file_location("hotspot", script_path)
if spec is None or spec.loader is None:
    raise RuntimeError("Could not load start_hotspot.py")

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
log_path = module.configure_logging()
assert log_path.read_bytes().startswith(b"\xef\xbb\xbf")
logging.info("移动热点集成测试")

result = subprocess.run(
    [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        module.POWERSHELL_SCRIPT,
    ],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    check=False,
)

print(result.stdout, end="")
if result.stderr:
    print(result.stderr, end="")
if result.returncode != 0:
    raise SystemExit(result.returncode)
assert "SUNNBT111" in result.stdout
assert "On" in result.stdout
assert "SUNNBT111" not in result.stderr
for handler in logging.getLogger().handlers[:]:
    logging.getLogger().removeHandler(handler)
    handler.close()
print("MOBILE_HOTSPOT_SCRIPT_TEST_OK")
