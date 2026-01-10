class StructuredLogger:
    def __init__(self, name, config):
        print(f"[Logger] Initialized logger {name}")
    def info(self, msg): print(f"[INFO] {msg}")
    def error(self, msg): print(f"[ERROR] {msg}")
