from __future__ import annotations

import ipaddress
import os
import socket
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests


MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
MAX_LINKS = 200
MAX_REDIRECTS = 5


@dataclass(frozen=True)
class BrowserLink:
    index: int
    text: str
    url: str


@dataclass(frozen=True)
class BrowserPage:
    url: str
    title: str
    excerpt: str
    links: tuple[BrowserLink, ...]


@dataclass(frozen=True)
class BrowserDownload:
    url: str
    destination: Path
    size: int
    content_type: str


class _StructuredHTMLParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._hidden_depth = 0
        self._in_title = False
        self._active_href: str | None = None
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden_depth += 1
            return
        if self._hidden_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "a" and self._active_href is None and len(self.links) < MAX_LINKS:
            href = next((value for name, value in attrs if name.casefold() == "href"), None)
            if href:
                absolute = urljoin(self.base_url, href)
                if urlparse(absolute).scheme in {"http", "https"}:
                    self._active_href = absolute
                    self._active_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden_depth = max(self._hidden_depth - 1, 0)
            return
        if self._hidden_depth:
            return
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._active_href is not None:
            text = " ".join(" ".join(self._active_text).split())
            if not text:
                parsed = urlparse(self._active_href)
                text = f"{parsed.netloc}{parsed.path}"[:200]
            self.links.append((text[:300], self._active_href))
            self._active_href = None
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        self.text_parts.append(cleaned)
        if self._in_title:
            self.title_parts.append(cleaned)
        if self._active_href is not None:
            self._active_text.append(cleaned)


class BrowserController:
    """Stateful, read-mostly HTTP browser with validated public-network navigation."""

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        allowed_domains: tuple[str, ...] = (),
        blocked_domains: tuple[str, ...] = (),
    ) -> None:
        self._verify_peer = session is None
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "LocalVoiceAssistant/1.0"})
        self.allowed_domains = tuple(domain.casefold().strip(". ") for domain in allowed_domains)
        self.blocked_domains = tuple(domain.casefold().strip(". ") for domain in blocked_domains)
        self.current_page: BrowserPage | None = None
        self._history: list[str] = []

    @property
    def history_depth(self) -> int:
        return len(self._history)

    @staticmethod
    def _domain_matches(hostname: str, rule: str) -> bool:
        return hostname == rule or hostname.endswith(f".{rule}")

    def _resolve_public_ips(self, hostname: str, port: int) -> set[str]:
        try:
            addresses = socket.getaddrinfo(hostname, port)
        except socket.gaierror as exc:
            raise ValueError("The website hostname could not be resolved") from exc
        resolved = {item[4][0] for item in addresses}
        if not resolved or any(not ipaddress.ip_address(item).is_global for item in resolved):
            raise ValueError("Local and private network addresses are blocked")
        return resolved

    def public_url(self, address: str) -> str:
        address = address.strip()
        if not address or len(address) > 2_048 or any(c in address for c in "\r\n\x00"):
            raise ValueError("That website address is invalid")
        parsed = urlparse(address)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Only public HTTP or HTTPS URLs are accepted")
        if parsed.hostname.casefold() in {"localhost", "localhost.localdomain"}:
            raise ValueError("Local and private network addresses are blocked")
        hostname = parsed.hostname.casefold().strip(".")
        if any(self._domain_matches(hostname, rule) for rule in self.blocked_domains):
            raise ValueError("That website domain is blocked by local policy")
        if self.allowed_domains and not any(
            self._domain_matches(hostname, rule) for rule in self.allowed_domains
        ):
            raise ValueError("That website domain is outside the local allowlist")
        self._resolve_public_ips(
            hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
        return address

    @staticmethod
    def audit_url(address: str) -> str:
        parsed = urlparse(address)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    def _open_validated_response(self, address: str) -> tuple[requests.Response, str]:
        current = address
        for _redirect in range(MAX_REDIRECTS + 1):
            current = self.public_url(current)
            parsed = urlparse(current)
            expected_ips = self._resolve_public_ips(
                str(parsed.hostname),
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
            response = self.session.get(
                current,
                timeout=(10, 30),
                stream=True,
                allow_redirects=False,
            )
            if self._verify_peer:
                connection = getattr(response.raw, "_connection", None)
                sock = getattr(connection, "sock", None)
                if sock is None:
                    response.close()
                    raise ValueError("The browser could not verify the connected peer")
                peer_ip = str(sock.getpeername()[0])
                if peer_ip not in expected_ips or not ipaddress.ip_address(peer_ip).is_global:
                    response.close()
                    raise ValueError("The connected peer did not match validated public DNS")
            if 300 <= response.status_code < 400 and response.headers.get("Location"):
                next_url = urljoin(current, response.headers["Location"])
                response.close()
                current = next_url
                continue
            response.raise_for_status()
            return response, current
        raise ValueError("The website redirected too many times")

    def _request(self, address: str, *, max_bytes: int) -> tuple[requests.Response, bytes, str]:
        response, current = self._open_validated_response(address)
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(
                        f"The response exceeded the {max_bytes // (1024 * 1024)} MB limit"
                    )
                chunks.append(chunk)
        except Exception:
            response.close()
            raise
        return response, b"".join(chunks), current

    def navigate(self, address: str, *, add_history: bool = True) -> BrowserPage:
        response, body, final_url = self._request(address, max_bytes=MAX_PAGE_BYTES)
        try:
            content_type = response.headers.get("Content-Type", "").casefold()
            if "text/html" not in content_type and "text/plain" not in content_type:
                raise ValueError("The URL did not return an HTML or plain-text page")
            text = body.decode(response.encoding or "utf-8", errors="replace")
        finally:
            response.close()
        parser = _StructuredHTMLParser(final_url)
        parser.feed(text)
        links = tuple(
            BrowserLink(index, label, url)
            for index, (label, url) in enumerate(parser.links, start=1)
        )
        page = BrowserPage(
            url=final_url,
            title=" ".join(parser.title_parts)[:500],
            excerpt=" ".join(parser.text_parts)[:8_000],
            links=links,
        )
        if add_history and self.current_page is not None:
            self._history.append(self.current_page.url)
        self.current_page = page
        return page

    def follow_link(self, link_number: int) -> BrowserPage:
        if self.current_page is None:
            raise ValueError("No browser page is active; read a page first")
        if link_number < 1 or link_number > len(self.current_page.links):
            raise ValueError("That link number is not present on the current page")
        return self.navigate(self.current_page.links[link_number - 1].url)

    def back(self) -> BrowserPage:
        if not self._history:
            raise ValueError("There is no previous browser page")
        address = self._history.pop()
        return self.navigate(address, add_history=False)

    def resolve_link(self, link_number: int) -> str:
        if self.current_page is None:
            raise ValueError("No browser page is active; read a page first")
        if link_number < 1 or link_number > len(self.current_page.links):
            raise ValueError("That link number is not present on the current page")
        return self.current_page.links[link_number - 1].url

    def download(self, address: str, destination: Path) -> BrowserDownload:
        response, final_url = self._open_validated_response(address)
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
        size = 0
        try:
            with temporary.open("xb") as handle:
                for chunk in response.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise ValueError("The download exceeded the 250 MB limit")
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(destination)
        finally:
            response.close()
            temporary.unlink(missing_ok=True)
        return BrowserDownload(final_url, destination, size, content_type[:200])
