import hashlib
import json
import os
import re
import sys
import time

import requests

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LETTER_RE = re.compile(r"[A-Ha-h]")


class AIAnswerer:
    """
    调用大模型「思考并判断」正确答案。
    支持任意 OpenAI 兼容的 Chat Completions 接口（OpenAI / 通义 / DeepSeek / 本地 Ollama 等）。
    答案按题目哈希缓存，避免重复消耗 token，并保证多次运行结果一致。
    """

    def __init__(self, cfg: dict):
        self.base = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.key = (cfg.get("api_key") or "").strip()
        self.model = cfg.get("model") or "gpt-4o-mini"
        self.temp = float(cfg.get("temperature", 0.0))
        self.timeout = int(cfg.get("timeout", 30))
        self.retries = max(1, int(cfg.get("max_retries", 3)))
        self.cache_file = cfg.get("cache_file") or os.path.join(BASE_DIR, "answers", "ai_cache.json")
        if not os.path.isabs(self.cache_file):
            self.cache_file = os.path.join(BASE_DIR, self.cache_file)
        self.cache = self._load_cache()

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        try:
            folder = os.path.dirname(os.path.abspath(self.cache_file))
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @staticmethod
    def _hash(q: str, options) -> str:
        raw = q + "||" + "||".join(options or [])
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def ask(self, question: str, options, qtype: str) -> dict:
        """
        返回 {"type": qtype, "indices": [...]/None, "text": str, "raw": str}
        - single/multiple: indices 为 0 基选项下标列表
        - judge: text 为 '正确' / '错误'
        - fill:  text 为填空答案
        """
        h = self._hash(question, options)
        if h in self.cache:
            return self.cache[h]

        options_text = (
            "\n".join("%d. %s" % (i + 1, o) for i, o in enumerate(options))
            if options
            else "(无选项，为填空/简答)"
        )
        system = (
            "你是一个严谨的答题助手。请仔细思考题目与选项，给出最合理的正确答案。"
            "只输出最终答案，不要任何解释。输出格式严格如下：\n"
            "- 单选题：只输出正确选项的序号数字（如 2）\n"
            "- 多选题：输出正确选项序号组合，升序无分隔（如 13 表示第1和第3项）\n"
            "- 判断题：只输出“正确”或“错误”\n"
            "- 填空/简答题：直接输出答案文本"
        )
        user = "题型：%s\n题目：%s\n选项：\n%s" % (qtype, question, options_text)

        raw = None
        last_err = None
        for attempt in range(self.retries):
            try:
                resp = requests.post(
                    self.base + "/chat/completions",
                    headers={
                        "Authorization": "Bearer %s" % self.key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "temperature": self.temp,
                        "max_tokens": 512,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                    },
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                raw = (resp.json()["choices"][0]["message"]["content"] or "").strip()
                if raw:
                    break
            except Exception as e:
                last_err = e
                if attempt < self.retries - 1:
                    time.sleep(2)

        result = self._parse(raw, qtype, options)
        result["raw"] = raw
        if last_err is not None and not raw:
            result["error"] = str(last_err)

        # 关键：只有拿到有效结果才写入缓存。
        # 否则接口临时失败会被永久缓存，导致这道题之后永远答不出来。
        if raw:
            self.cache[h] = result
            self._save_cache()
        return result

    def _parse(self, raw, qtype, options):
        if not raw:
            return {"type": qtype, "indices": None, "text": None, "raw": raw}

        if qtype == "judge":
            # 先判否定的说法："不正确/不对/错误"都算错
            if any(k in raw for k in ("不正确", "不对", "错误", "错", "否", "×")):
                return {"type": qtype, "indices": None, "text": "错误"}
            return {"type": qtype, "indices": None, "text": "正确"}

        if qtype == "fill":
            return {"type": qtype, "indices": None, "text": raw}

        # 单/多选：优先解析数字序号，其次解析字母（模型可能回 "B" / "AC"）
        # 只取第一行，避免解释文字里的年份/数字被误当成选项序号
        n = len(options or [])
        head = raw.strip().split("\n")[0].strip()
        head = re.sub(r"^(答案|正确答案|答)\s*[:：是为]?\s*", "", head)
        indices = []
        digits = [int(c) for c in head if c.isdigit()]
        for d in digits:
            if 1 <= d <= n:
                indices.append(d - 1)
        if not indices and n:
            for letter in LETTER_RE.findall(head):
                idx = ord(letter.upper()) - ord("A")
                if 0 <= idx < n:
                    indices.append(idx)
        if qtype == "single" and len(indices) > 1:
            indices = indices[:1]

        seen, uniq = set(), []
        for i in indices:
            if i not in seen:
                seen.add(i)
                uniq.append(i)
        return {"type": qtype, "indices": uniq, "text": None, "raw": raw}
