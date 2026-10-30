import json
import os


class Progress:
    """断点续跑：记录已完成章节/任务点，下次运行跳过。"""

    def __init__(self, file_path: str):
        self.file = file_path
        self.done = self._load()

    def _load(self):
        if not self.file or not os.path.exists(self.file):
            return set()
        try:
            with open(self.file, encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("done", [])) if isinstance(data, dict) else set()
        except Exception:
            return set()

    def save(self):
        try:
            folder = os.path.dirname(os.path.abspath(self.file))
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.file, "w", encoding="utf-8") as f:
                json.dump({"done": sorted(self.done)}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def is_done(self, key: str) -> bool:
        return key in self.done

    def mark_done(self, key: str):
        self.done.add(key)
        self.save()
