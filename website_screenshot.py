# -*- coding: utf-8 -*-
"""
按 urls.txt 中的顺序打开官网，截取 1600x900 首屏，并输出为：
img1.jpg、img2.jpg、img3.jpg……

安装：
    python -m pip install selenium webdriver-manager Pillow

运行：
    python website_screenshot.py

可选：每个网站截图前暂停，方便手动登录、关闭弹窗或完成验证：
    python website_screenshot.py --manual-each
"""

from __future__ import annotations

import argparse
import random
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageFilter, ImageOps
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


WIDTH = 1600
HEIGHT = 900
TARGET_BYTES = 100 * 1024
PAGE_LOAD_TIMEOUT = 45
EXTRA_WAIT_MIN = 2.0
EXTRA_WAIT_MAX = 4.0
OUTPUT_PREFIX = "img"

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "urls.txt"
DEFAULT_OUTPUT = BASE_DIR / "output"


def create_driver(headless: bool = False) -> webdriver.Chrome:
    """创建 Chrome；默认显示浏览器，以便需要时人工处理验证或登录。"""
    options = Options()
    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-notifications")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--no-first-run")
    options.add_argument("--lang=zh-CN")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)

    # 固定 CSS 视口和截图像素均为 1600x900，避免浏览器边框影响尺寸。
    driver.execute_cdp_cmd(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": WIDTH,
            "height": HEIGHT,
            "deviceScaleFactor": 1,
            "mobile": False,
        },
    )
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """
        },
    )
    return driver


def read_urls(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"找不到网址文件：{path}")

    urls: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not urlparse(line).scheme:
            line = "https://" + line
        urls.append(line)

    if not urls:
        raise SystemExit(f"网址文件为空：{path}")
    return urls


def page_needs_manual_action(driver: webdriver.Chrome) -> bool:
    """识别常见验证码、访问拦截或需要人工确认的页面。"""
    title = (driver.title or "").lower()
    current_url = (driver.current_url or "").lower()
    indicators = (
        "captcha",
        "unusual traffic",
        "access denied",
        "verify you are human",
        "安全验证",
        "人机验证",
        "访问受限",
    )
    if any(text in title or text in current_url for text in indicators):
        return True

    try:
        return bool(
            driver.find_elements(
                "css selector",
                "iframe[src*='captcha'], iframe[src*='recaptcha'], "
                ".g-recaptcha, #captcha, [class*='captcha'], [id*='captcha']",
            )
        )
    except WebDriverException:
        return False


def wait_until_visually_ready(driver: webdriver.Chrome) -> None:
    """等待 DOM、字体及大部分首屏图片完成加载。"""
    try:
        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
        )
    except TimeoutException:
        pass

    try:
        driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const timeout = new Promise(resolve => setTimeout(resolve, 10000));
            const fonts = document.fonts ? document.fonts.ready : Promise.resolve();
            const images = Promise.all(
                Array.from(document.images)
                    .filter(img => {
                        const r = img.getBoundingClientRect();
                        return r.top < window.innerHeight && r.bottom > 0;
                    })
                    .map(img => img.complete
                        ? Promise.resolve()
                        : new Promise(resolve => {
                            img.addEventListener('load', resolve, {once: true});
                            img.addEventListener('error', resolve, {once: true});
                        })
                    )
            );
            Promise.race([Promise.all([fonts, images]), timeout]).then(() => done());
            """
        )
    except (TimeoutException, WebDriverException):
        pass

    time.sleep(random.uniform(EXTRA_WAIT_MIN, EXTRA_WAIT_MAX))

    # 暂停动画，减少轮播图或过渡效果导致的半帧截图。
    try:
        driver.execute_script(
            """
            const style = document.createElement('style');
            style.textContent = `
                *, *::before, *::after {
                    animation-play-state: paused !important;
                    transition: none !important;
                    caret-color: transparent !important;
                }
            `;
            document.documentElement.appendChild(style);
            window.scrollTo(0, 0);
            """
        )
        time.sleep(0.3)
    except WebDriverException:
        pass


def normalize_screenshot(png_data: bytes) -> Image.Image:
    with Image.open(BytesIO(png_data)) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
        if image.size != (WIDTH, HEIGHT):
            image = ImageOps.fit(
                image,
                (WIDTH, HEIGHT),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.0),
            )
        return image.copy()


def encode_jpeg_under_target(image: Image.Image, target_bytes: int) -> bytes:
    """保持 1600x900 输出尺寸，尽可能以最高 JPEG 质量压到目标大小内。"""

    def encode(candidate: Image.Image, quality: int) -> bytes:
        buffer = BytesIO()
        candidate.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling="4:2:0",
        )
        return buffer.getvalue()

    # 二分寻找满足大小限制的最高质量。
    low, high = 1, 95
    best: bytes | None = None
    while low <= high:
        quality = (low + high) // 2
        data = encode(image, quality)
        if len(data) <= target_bytes:
            best = data
            low = quality + 1
        else:
            high = quality - 1
    if best is not None:
        return best

    # 极复杂或高噪点画面即使 quality=1 仍可能超限。
    # 逐步减少高频细节，再放回 1600x900；最终文件尺寸仍严格保持不变。
    scale = 0.90
    while scale >= 0.20:
        reduced = image.resize(
            (max(1, int(WIDTH * scale)), max(1, int(HEIGHT * scale))),
            Image.Resampling.LANCZOS,
        )
        restored = reduced.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        if scale <= 0.55:
            restored = restored.filter(ImageFilter.GaussianBlur(radius=0.35))
        data = encode(restored, 1)
        if len(data) <= target_bytes:
            return data
        scale -= 0.05

    raise RuntimeError("无法在保持 1600x900 的同时将图片压到 100KB 内")


def capture_one(
    driver: webdriver.Chrome,
    url: str,
    output_path: Path,
    manual_each: bool,
) -> tuple[int, int]:
    try:
        driver.get(url)
    except TimeoutException:
        # 超时不一定表示页面不可用；通常仍可截取已加载出的首屏。
        try:
            driver.execute_script("window.stop();")
        except WebDriverException:
            pass

    wait_until_visually_ready(driver)

    if page_needs_manual_action(driver):
        print("  检测到验证或访问拦截，请在浏览器中人工处理。")
        input("  处理完成后按回车继续截图……")
        wait_until_visually_ready(driver)
    elif manual_each:
        input("  如需登录或关闭弹窗，请处理后按回车截图……")

    png_data = driver.get_screenshot_as_png()
    image = normalize_screenshot(png_data)
    jpg_data = encode_jpeg_under_target(image, TARGET_BYTES)
    output_path.write_bytes(jpg_data)
    return image.size


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量截取官网 1600x900 首屏并压缩到 100KB 内")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="网址列表文件")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="图片输出目录")
    parser.add_argument("--manual-each", action="store_true", help="每个网站截图前等待人工确认")
    parser.add_argument("--headless", action="store_true", help="无界面运行（无法人工处理登录/验证）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    urls = read_urls(args.input.resolve())
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"共读取 {len(urls)} 个网址，准备启动浏览器。")
    driver: webdriver.Chrome | None = None
    succeeded = 0
    failures: list[tuple[int, str, str]] = []

    try:
        driver = create_driver(headless=args.headless)
        for index, url in enumerate(urls, start=1):
            output_path = output_dir / f"{OUTPUT_PREFIX}{index}.jpg"
            print(f"[{index}/{len(urls)}] {url}")
            try:
                dimensions = capture_one(driver, url, output_path, args.manual_each)
                size_kb = output_path.stat().st_size / 1024
                print(f"  已保存：{output_path.name}，{dimensions[0]}x{dimensions[1]}，{size_kb:.1f}KB")
                succeeded += 1
            except Exception as exc:
                failures.append((index, url, str(exc)))
                print(f"  失败：{exc}")

            if index < len(urls):
                time.sleep(random.uniform(1.0, 2.5))
    finally:
        if driver is not None:
            driver.quit()

    print(f"\n完成：成功 {succeeded} 个，失败 {len(failures)} 个。")
    print(f"输出目录：{output_dir}")
    if failures:
        print("失败项目（编号保持与 urls.txt 一致）：")
        for index, url, reason in failures:
            print(f"  img{index}.jpg <- {url}：{reason}")


if __name__ == "__main__":
    from screenshot_batch import main as batch_main
    batch_main()
