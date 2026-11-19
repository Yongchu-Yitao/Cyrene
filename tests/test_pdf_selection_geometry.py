"""Hit-test visible PDF coordinates independently of PDF.js's DOM text spans."""

from io import BytesIO
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import reportlab
from reportlab.pdfbase.pdfmetrics import registerFont, stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from cyrene.plugins.builtin.cyrene_content.pdf_routes import _PDF_VIEWER_HTML


STATIC = Path(__file__).resolve().parents[1] / "src/cyrene/workbench/webui/static/app"


def test_pdf_word_selection_tracks_page_and_font_metrics():
    playwright = pytest.importorskip("playwright.sync_api")
    pdf = BytesIO()
    canvas = Canvas(pdf, pagesize=(450, 420))
    canvas.setFont("Helvetica", 18)
    canvas.drawString(22, 370, "WIDE middle initial WIDTH")
    # Embedded bold face and non-default inter-word advances reproduce the
    # TeX title case where the line width matched but its first T was missed.
    bold = "PdfSelectionVeraBold"
    registerFont(TTFont(bold, str(Path(reportlab.__file__).parent / "fonts/VeraBd.ttf")))
    title = canvas.beginText(22, 310)
    title.setFont(bold, 14)
    title.setWordSpace(3)
    title.textLine("MT3: Meta Test-Time Training for Self-Supervised")
    canvas.drawText(title)
    title_x = 22 + stringWidth("MT3: Meta ", bold, 14) + 2 * 3
    canvas.save()

    def serve(route):
        path = urlsplit(route.request.url).path
        if path == "/pdf/viewer":
            route.fulfill(body=_PDF_VIEWER_HTML("/sample.pdf", "Sample", "zh"), content_type="text/html")
        elif path == "/sample.pdf":
            route.fulfill(body=pdf.getvalue(), content_type="application/pdf")
        elif path.startswith("/static/app/"):
            asset = STATIC / path.removeprefix("/static/app/")
            route.fulfill(path=asset, content_type=mimetypes.guess_type(asset.name)[0])
        else:
            route.abort()

    with playwright.sync_playwright() as driver:
        # The browser extra is optional. Support both Playwright's Chromium and
        # developer machines which only have a system Chrome installation.
        try:
            browser = driver.chromium.launch()
        except playwright.Error as error:
            if "doesn't exist" not in str(error):
                raise
            try:
                browser = driver.chromium.launch(channel="chrome")
            except playwright.Error as chrome_error:
                if "not found" not in str(chrome_error) and "doesn't exist" not in str(chrome_error):
                    raise
                pytest.skip("Install Chromium with playwright install chromium")
        page = browser.new_page(viewport={"width": 870, "height": 700})
        page.route("**/*", serve)
        page.goto("http://localhost/pdf/viewer")
        page.locator(".textLayer span").first.wait_for()
        # Exercise the inherited typography used by both embedded previews too.
        page.add_style_tag(url="/static/app/shared/theme/base.css")
        for width in (870, 680):
            page.set_viewport_size({"width": width, "height": 700})
            page.wait_for_function(
                """width => {
                  const drawn = document.querySelector('.page canvas').getBoundingClientRect().width;
                  return drawn > width * 0.9 && drawn <= width;
                }""",
                arg=width,
            )
            page.locator(".textLayer:not([hidden]) span").first.wait_for()
            box = page.locator(".page canvas").first.bounding_box()
            overlay = page.locator(".textLayer").first.bounding_box()
            for key in ("x", "y", "width", "height"):
                assert abs(box[key] - overlay[key]) <= 1

            scale = box["width"] / 450
            left = box["x"] + (22 + stringWidth("WIDE middle initial ", "Helvetica", 18)) * scale
            right = left + stringWidth("WIDTH", "Helvetica", 18) * scale
            y = box["y"] + 44 * scale
            page.mouse.move(left + 1, y)
            page.mouse.down()
            page.mouse.move(right - 1, y, steps=8)
            page.mouse.up()
            assert page.evaluate("getSelection().toString()") == "WIDTH"
            for target in ("T", "Test-Time", "Test-Time Training"):
                page.evaluate("getSelection().removeAllRanges()")
                start = box["x"] + title_x * scale
                end = start + (stringWidth(target, bold, 14) + 3 * target.count(" ")) * scale
                y = box["y"] + 105 * scale
                left, right = start + 1, end - 1
                if " " in target:
                    left, right = right, left
                page.mouse.move(left, y)
                page.mouse.down()
                page.mouse.move(right, y, steps=8)
                page.mouse.up()
                assert page.evaluate("getSelection().toString()") == target
                hit = page.evaluate("getSelection().getRangeAt(0).getBoundingClientRect().left")
                assert abs(hit - start) < 1
        browser.close()
