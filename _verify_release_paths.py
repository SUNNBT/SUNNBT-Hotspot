import importlib.util
import logging
import sys
import tempfile
from pathlib import Path


source = Path(__file__).with_name("start_hotspot.py")
spec = importlib.util.spec_from_file_location("hotspot", source)
if spec is None or spec.loader is None:
    raise RuntimeError("Could not load start_hotspot.py")

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
had_frozen = hasattr(sys, "frozen")
old_frozen = getattr(sys, "frozen", None)
old_executable = sys.executable

try:
    with tempfile.TemporaryDirectory(prefix="SUNNBT release path with spaces ") as folder:
        fake_executable = Path(folder) / "SUNNBT_Hotspot.exe"
        fake_executable.touch()
        sys.frozen = True
        sys.executable = str(fake_executable)

        assert module.application_path() == fake_executable.resolve()
        log_path = module.configure_logging()
        logging.info("Portable path verification")
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert log_path == Path(folder) / "Logs" / "start_hotspot.log"
        contents = log_path.read_text(encoding="utf-8-sig")
        assert "Portable path verification" in contents
        print(f"PORTABLE_PATH_TEST_OK: {log_path}")
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)
            handler.close()
finally:
    for handler in logging.getLogger().handlers[:]:
        logging.getLogger().removeHandler(handler)
        handler.close()
    sys.executable = old_executable
    if had_frozen:
        sys.frozen = old_frozen
    else:
        del sys.frozen
