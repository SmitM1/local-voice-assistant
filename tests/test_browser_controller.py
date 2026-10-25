import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser_controller import BrowserController


class FakeResponse:
    def __init__(
        self,
        body: bytes = b"",
        *,
        status_code: int = 200,
        content_type: str = "text/html; charset=utf-8",
        location: str | None = None,
    ) -> None:
        self.body = body
        self.status_code = status_code
        self.encoding = "utf-8"
        self.headers = {"Content-Type": content_type}
        if location is not None:
            self.headers["Location"] = location
        self.closed = False

    def iter_content(self, size: int):
        for index in range(0, len(self.body), size):
            yield self.body[index : index + size]

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = list(responses)
        self.requested: list[str] = []

    def get(self, url: str, **_kwargs) -> FakeResponse:
        self.requested.append(url)
        if not self.responses:
            raise AssertionError("No fake response remains")
        return self.responses.pop(0)


PUBLIC_DNS = [(None, None, None, None, ("93.184.216.34", 443))]


class BrowserControllerTests(unittest.TestCase):
    @patch("browser_controller.socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_page_exposes_numbered_links_and_ignores_script_text(self, _dns) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    b"<html><head><title>Example</title><script>ignore me</script></head>"
                    b"<body>Hello <a href='/next'>Next page</a></body></html>"
                ),
                FakeResponse(b"<html><title>Next</title><body>Done</body></html>"),
            ]
        )
        browser = BrowserController(session)
        page = browser.navigate("https://example.com/start")
        self.assertEqual("Example", page.title)
        self.assertNotIn("ignore me", page.excerpt)
        self.assertEqual("https://example.com/next", page.links[0].url)
        followed = browser.follow_link(1)
        self.assertEqual("Next", followed.title)

    @patch("browser_controller.socket.getaddrinfo")
    def test_redirect_to_private_network_is_blocked(self, dns) -> None:
        dns.side_effect = [PUBLIC_DNS, [(None, None, None, None, ("127.0.0.1", 80))]]
        session = FakeSession(
            [FakeResponse(status_code=302, location="http://127.0.0.1/private")]
        )
        browser = BrowserController(session)
        with self.assertRaisesRegex(ValueError, "private"):
            browser.navigate("https://example.com/start")

    @patch("browser_controller.socket.getaddrinfo", return_value=PUBLIC_DNS)
    def test_download_writes_exact_bytes_without_execution(self, _dns) -> None:
        payload = b"download contents"
        session = FakeSession(
            [FakeResponse(payload, content_type="application/octet-stream")]
        )
        browser = BrowserController(session)
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "download.bin"
            result = browser.download("https://example.com/file", destination)
            self.assertEqual(payload, destination.read_bytes())
            self.assertEqual(len(payload), result.size)


if __name__ == "__main__":
    unittest.main()
