
import asyncio
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Language codes supported by the extension (from src/common/config.js)
# ---------------------------------------------------------------------------
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({
    "af", "am", "ar", "be", "bg", "bn", "bs", "ca", "cs", "cy",
    "da", "de", "el", "en", "es", "et", "eu", "fa", "fi", "fr",
    "ga", "gl", "gu", "he", "hi", "hr", "hu", "hy", "id", "is",
    "it", "ja", "ka", "kn", "ko", "lt", "lv", "mk", "ml", "mr",
    "ms", "mt", "ne", "nl", "no", "pa", "pl", "pt", "ro", "ru",
    "sk", "sl", "sq", "sr", "sv", "sw", "ta", "te", "th", "tl",
    "tr", "uk", "ur", "vi", "zh", "zh-cn", "zh-tw",
})

# Regex to accept only YouTube URLs (mirrors extension host_permissions)
_YT_PATTERN = re.compile(r"https?://(www\.)?(youtube\.com|youtu\.be)/")

# How long (seconds) to wait for the content-script isolated world to appear
_CONTEXT_WAIT_TIMEOUT = 20.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


from playwright.async_api import async_playwright
def LangFilter():
    def __init__(self, concurrency=1,ext_dir=None, crx_path=None):
        self.async_playwright = async_playwright
        self.ext_dir =  ext_dir if ext_dir else self._extract_crx(crx_path or (Path(__file__).parent / "YuLaF.crx"))
        self._semaphore = asyncio.Semaphore(concurrency)
    # it is one time use
    def _extract_crx(self, crx_path: Path) -> Path:
        """
        A .crx file is a ZIP archive (with a small binary header that Python's
        zipfile handles fine).  Extract it into a fresh temp directory and return
        the path so Playwright can load it as an unpacked extension.
        """
        ext_dir = Path(tempfile.mkdtemp(prefix="yulaf_ext_"))
        with zipfile.ZipFile(crx_path) as zf:
            zf.extractall(ext_dir)
        return ext_dir

    async def _find_content_script_context(self,cdp_session, timeout: float) -> Optional[int]:
        """
        Content scripts run in Chrome's *isolated world* — a separate JS execution
        context from the page.  CDP reports every context via Runtime.executionContextCreated.

        We listen for isolated contexts and probe each one for `window.LanguageService`
        (set by language-service.js) to confirm it is the YuLaF content-script world.

        Returns the CDP contextId, or None if it never appeared within `timeout` seconds.
        """
        isolated_contexts: list[dict] = []

        def _on_context(params: dict) -> None:
            ctx = params.get("context", {})
            # Content-script worlds always have auxData.type == "isolated"
            if ctx.get("auxData", {}).get("type") == "isolated":
                isolated_contexts.append(ctx)

        cdp_session.on("Runtime.executionContextCreated", _on_context)

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            for ctx in list(isolated_contexts):
                try:
                    probe = await cdp_session.send("Runtime.evaluate", {
                        "expression": "typeof window.LanguageService",
                        "contextId": ctx["id"],
                    })
                    if probe.get("result", {}).get("value") == "object":
                        return ctx["id"]
                except Exception:
                    pass  # context may have been destroyed; ignore
            await asyncio.sleep(0.25)

        return None
    async def _is_url_match(self, page, url: str, languages: list[str], strict_mode: bool) -> bool:
        """
        Navigate to one YouTube URL, wait for YuLaF's content scripts to inject,
        then call window.LanguageService.detectLanguage() inside the extension's
        isolated world using a CDP session.

        Returns True if the video title matches one of the requested languages.
        """
        # Open a fresh CDP session for this page
        cdp = await page.context.new_cdp_session(page)

        # Enable Runtime domain BEFORE navigating so we catch every context event
        await cdp.send("Runtime.enable")

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # Wait for the content scripts (document_idle) to finish injecting
        ctx_id = await self._find_content_script_context(cdp, timeout=_CONTEXT_WAIT_TIMEOUT)

        if ctx_id is None:
            print(f"[YuLaF] Warning: content-script context not found for {url}")
            await cdp.detach()
            return False

        # --- Configure LanguageService with the caller's language list ----------
        langs_json = json.dumps(languages)
        strict_js = "true" if strict_mode else "false"
        await cdp.send("Runtime.evaluate", {
            "expression": (
                f"window.LanguageService.setLanguages({langs_json});"
                f"window.LanguageService.setStrictMode({strict_js});"
            ),
            "contextId": ctx_id,
            "awaitPromise": False,
        })

        # --- Extract the video title from the live DOM --------------------------
        # Tries the same selectors the extension's DOMService uses, falling back
        # to the <title> element.
        title_result = await cdp.send("Runtime.evaluate", {
            "expression": """(function() {
                const selectors = [
                    'h1.ytd-video-primary-info-renderer yt-formatted-string',
                    'h1 yt-formatted-string#title',
                    '#title h1',
                    'yt-formatted-string#video-title',
                    '#video-title',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.textContent.trim().length >= 3) {
                        return el.textContent.trim();
                    }
                }
                // Final fallback: strip " - YouTube" suffix from <title>
                return document.title.replace(/ - YouTube$/, '').trim();
            })()""",
            "contextId": ctx_id,
            "awaitPromise": False,
        })
        title: str = title_result.get("result", {}).get("value", "").strip()

        if not title or len(title) < 3:
            print(f"[YuLaF] Warning: could not extract title from {url}")
            await cdp.detach()
            return False

        # --- Call the extension's own detectLanguage() --------------------------
        # This runs inside the isolated world, so chrome.i18n.detectLanguage is
        # available — exactly as the extension uses it.
        detect_result = await cdp.send("Runtime.evaluate", {
            "expression": f"window.LanguageService.detectLanguage({json.dumps(title)})",
            "contextId": ctx_id,
            "awaitPromise": True,   # detectLanguage() returns a Promise
            "timeout": 8_000,
        })

        matched: bool = bool(detect_result.get("result", {}).get("value", False))

        print(
            f"[YuLaF] {'✓ KEEP' if matched else '✗ SKIP'}  "
            f"{url!r}  title={title!r}"
        )

        await cdp.detach()
        return matched

    async def _run_filter(
        urls: list[str],
        languages: list[str],
        strict_mode: bool,
    ) -> list[str]:
        """Async implementation: launch Chrome+extension, process all URLs."""
        async_playwright = self.async_playwright
        ext_dir = self.ext_dir
        profile_dir = tempfile.mkdtemp(prefix="yulaf_profile_")
        try:
            async with async_playwright() as pw:
                # launch_persistent_context is required to load extensions in Playwright
                context = await pw.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=False,                     # extensions need a real window
                    args=[
                        f"--load-extension={ext_dir}",
                        f"--disable-extensions-except={ext_dir}",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                    ignore_https_errors=True,
                )

                semaphore = self._semaphore
                results: dict[str, bool] = {}

                async def process(url: str) -> None:
                    async with semaphore:
                        page = await context.new_page()
                        try:
                            results[url] = await self._is_url_match(page, url, languages, strict_mode)
                        except Exception as exc:
                            print(f"[YuLaF] Error processing {url}: {exc}")
                            results[url] = False
                        finally:
                            await page.close()

                await asyncio.gather(*[process(url) for url in urls])
                await context.close()

            # Preserve original ordering
            return [url for url in urls if results.get(url, False)]

        finally:
            shutil.rmtree(ext_dir, ignore_errors=True)
            shutil.rmtree(profile_dir, ignore_errors=True)


    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def filter_youtube_urls(
        self,
        urls: list[str],
        languages: list[str],
        strict_mode: bool = False,
    ) -> list[str]:
        """
        Filter a list of YouTube URLs, keeping only those whose video title is
        detected as belonging to one of the requested languages.

        Detection is performed by the YuLaF extension's own JS pipeline running
        inside a real Chrome instance — no third-party language library is used.

        Parameters
        ----------
        urls : list[str]
            YouTube video URLs to evaluate.
        languages : list[str]
            BCP-47 language codes to keep, e.g. ``["en", "fr", "de"]``.
            Must be a subset of ``SUPPORTED_LANGUAGES``.
        crx_path : str | Path
            Path to ``YuLaF.crx``.  Defaults to the file beside this script.
        strict_mode : bool
            Mirrors the extension's *Strict Mode* toggle (higher confidence
            threshold).  Default is ``False``, matching the extension default.
        concurrency : int
            Number of browser tabs to run in parallel.  Keep at 1 to avoid
            YouTube rate-limiting; raise carefully for larger batches.

        Returns
        -------
        list[str]
            Filtered URLs in the same order as the input.

        Raises
        ------
        FileNotFoundError
            If ``crx_path`` does not exist.
        ValueError
            If any language code is not in ``SUPPORTED_LANGUAGES``.

        Example
        -------
        >>> from yulaf_wrapper import filter_youtube_urls
        >>> filter_youtube_urls(
        ...     urls=[
        ...         "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ...         "https://www.youtube.com/watch?v=9bZkp7q19f0",
        ...     ],
        ...     languages=["en"],
        ... )
        ['https://www.youtube.com/watch?v=dQw4w9WgXcQ']
        """
        crx_path = Path(crx_path)
        if not crx_path.exists():
            raise FileNotFoundError(f"CRX not found: {crx_path}")

        unknown = set(languages) - SUPPORTED_LANGUAGES
        if unknown:
            raise ValueError(
                f"Unsupported language code(s): {sorted(unknown)}. "
                f"Supported: {sorted(SUPPORTED_LANGUAGES)}"
            )

        if not languages:
            # Extension behaviour: no languages selected → show everything
            return list(urls)

        # Warn about and drop non-YouTube URLs
        yt_urls = [u for u in urls if _YT_PATTERN.match(u)]
        dropped = [u for u in urls if not _YT_PATTERN.match(u)]
        if dropped:
            print(f"[YuLaF] Skipping {len(dropped)} non-YouTube URL(s): {dropped}")

        if not yt_urls:
            return []

        return asyncio.run(
            _run_filter(yt_urls, languages, crx_path, strict_mode, concurrency)
        )


# ---------------------------------------------------------------------------
# Quick smoke-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    TEST_URLS = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",  # English — Rick Astley
        "https://www.youtube.com/watch?v=9bZkp7q19f0",  # Korean  — Gangnam Style
        "https://www.youtube.com/watch?v=JGwWNGJdvx8",  # English — Justin Bieber
    ]

    print("=== Keeping English only ===")
    en_only = filter_youtube_urls(TEST_URLS, languages=["en"])
    for url in en_only:
        print(" ", url)

    print("\n=== Keeping Korean only ===")
    ko_only = filter_youtube_urls(TEST_URLS, languages=["ko"])
    for url in ko_only:
        print(" ", url)