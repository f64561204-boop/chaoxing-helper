# -*- coding: utf-8 -*-
"""本地验证码流程自测：不连学习通，用假页面验证 检测→截图→OCR→填写→提交。"""
import io
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.captcha import configure, handle_captcha


class L:
    def info(self, m, *a):
        print("[INFO]", m % a if a else m)

    def warning(self, m, *a):
        print("[WARN]", m % a if a else m)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    data = os.path.join(os.path.dirname(here), "data")
    os.makedirs(data, exist_ok=True)
    img_path = os.path.join(data, "captcha_sample.png")

    img = Image.new("RGB", (120, 44), "white")
    d = ImageDraw.Draw(img)
    try:
        ft = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 28)
    except Exception:
        ft = ImageFont.load_default()
    d.text((8, 4), "7k2m", fill=(20, 20, 20), font=ft)
    img.save(img_path)

    html = """<!doctype html><meta charset=utf-8>
<style>#box{position:fixed;left:40px;top:40px;background:#fff;border:1px solid #999;padding:16px}</style>
<div id="box">
  <div>请输入验证码</div>
  <img id="imgVerCode" src="captcha_sample.png" width="120" height="44">
  <input id="code" type="text">
  <a id="sub" href="javascript:void(0)">提交</a>
</div>
<script>
document.getElementById('sub').onclick = function(){
  window.__submitted = document.getElementById('code').value;
  if (document.getElementById('code').value.length >= 3) {
    document.getElementById('box').style.display = 'none';
  }
};
</script>"""
    html_path = os.path.join(data, "_captcha_test.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.goto("file:///" + html_path.replace("\\", "/"))
        page.wait_for_timeout(500)

        configure({"enabled": True, "max_retries": 3, "min_interval": 0})
        got = handle_captcha(page, L())
        submitted = page.evaluate("() => window.__submitted || ''")
        hidden = page.evaluate("() => getComputedStyle(document.getElementById('box')).display === 'none'")
        print("RESULT: detected=%s submitted=%r hidden=%s" % (got, submitted, hidden))
        browser.close()


if __name__ == "__main__":
    main()
