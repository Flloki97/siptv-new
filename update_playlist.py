"""Find the public backup players and publish a Kodi playlist that resolves them on tune."""

from concurrent.futures import ThreadPoolExecutor
from functools import cache
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urljoin, urlsplit


CHANNEL_PAGES = {
    1: "https://bbvipks.com/kanali-1-live-big-brother-kosova-vip-gzpim/",
    2: "https://bbvipks.com/kanali-2-live-big-brother-kosova-live-u4o1x/",
}
PLAYER_HOST = "s1.bbvipks.com"
EMBED_HOST = "ok.ru"
HLS_PATTERN = r"hlsMasterPlaylistUrl&quot;\s*:\s*&quot;(https://[^&<>\s]+)"
CHANNEL_NAME = "Big Brother VIP Kosova - Kanali "


class Iframes(HTMLParser):
    def __init__(self, html, page_url):
        super().__init__()
        self.urls = []
        self.page_url = page_url
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "iframe":
            values = dict(attrs)
            src = values.get("data-litespeed-src") or values.get("src")
            if src:
                self.urls.append(urljoin(self.page_url, src))


def fetch(url):
    result = subprocess.run(
        [
            "curl", "--fail", "--silent", "--show-error", "--location",
            "--proto", "=https", "--proto-redir", "=https",
            "--max-time", "15", "--max-filesize", "12000000", url,
        ],
        capture_output=True,
    )
    if result.returncode:
        raise ValueError(f"{urlsplit(url).hostname}: curl exit {result.returncode}")
    return result.stdout


@cache
def page(url):
    return fetch(url).decode("utf-8", errors="replace")


def secure_url(url, host):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.netloc.lower() == host


def find_embed(channel):
    channel_url = CHANNEL_PAGES[channel]
    for hub in Iframes(page(channel_url), channel_url).urls:
        if not secure_url(hub, PLAYER_HOST):
            continue

        players = re.findall(
            rf"""\bkanali{channel}(?:hd)?\s*:\s*['"]([^'"]+)['"]""", page(hub)
        )
        for link in players:
            player_url = urljoin(hub, link)
            if not secure_url(player_url, PLAYER_HOST):
                continue
            for embed in Iframes(page(player_url), player_url).urls:
                if secure_url(embed, EMBED_HOST) and re.fullmatch(
                    r"/videoembed/\d+/?", urlsplit(embed).path
                ):
                    return embed

    raise ValueError("The channel page does not list a supported backup player")


def hls_lines(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not (
        parsed.hostname and parsed.hostname.endswith(".okcdn.ru")
    ) or parsed.username is not None or parsed.password is not None:
        raise ValueError("The backup player returned an unexpected media host")
    lines = [line.strip() for line in fetch(url).decode().splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise ValueError("The backup player did not return an HLS playlist")
    return lines


def verify_video(embed):
    match = re.search(HLS_PATTERN, page(embed))
    if not match:
        raise ValueError("The backup player did not supply an HLS URL")
    master = match.group(1)
    master_lines = hls_lines(master)
    variants = [
        line for tag, line in zip(master_lines, master_lines[1:])
        if tag.startswith("#EXT-X-STREAM-INF:") and not line.startswith("#")
    ]
    if not variants:
        raise ValueError("The HLS master playlist has no video variants")
    media = urljoin(master, variants[1] if len(variants) > 1 else variants[0])
    segments = [line for line in hls_lines(media) if not line.startswith("#")]
    if not segments:
        raise ValueError("The live playlist has no video segments")

    segment = urljoin(media, segments[-2] if len(segments) > 1 else segments[-1])
    if not secure_url(segment, urlsplit(media).netloc.lower()):
        raise ValueError("The live segment is on an unexpected host")
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0",
            "-map", "0:v:0", "-frames:v", "1", "-f", "framehash", "-",
        ],
        input=fetch(segment),
        capture_output=True,
        timeout=15,
    )
    if result.returncode or not any(line.startswith(b"0,") for line in result.stdout.splitlines()):
        raise ValueError("The live segment did not deliver a decodable video frame")


def channel_entry(channel):
    embed = find_embed(channel)
    verify_video(embed)
    print(f"Kanali {channel}: verified a frame from the backup player.")
    return f'#EXTINF:-1,{CHANNEL_NAME}{channel}\n#WEBPROP:web-regex={HLS_PATTERN}\n@{embed}\n'


def previous_entries(path):
    if not path or not path.is_file():
        return {}
    entries = re.split(r"(?=^#EXTINF:)", path.read_text(), flags=re.MULTILINE)
    return {
        channel: entry.rstrip() + "\n"
        for channel in CHANNEL_PAGES
        for entry in entries
        if entry.startswith(f"#EXTINF:-1,{CHANNEL_NAME}{channel}\n")
    }


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("playlist.m3u")
    prior = previous_entries(Path(sys.argv[2]) if len(sys.argv) > 2 else None)
    entries = {}
    with ThreadPoolExecutor(max_workers=len(CHANNEL_PAGES)) as pool:
        jobs = {channel: pool.submit(channel_entry, channel) for channel in CHANNEL_PAGES}
        for channel, job in jobs.items():
            try:
                entries[channel] = job.result()
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                print(f"::warning::Kanali {channel}: {error}", file=sys.stderr)
    if not entries:
        sys.exit("No playable channels found. The published playlist was left in place.")
    if any(channel not in entries and channel not in prior for channel in CHANNEL_PAGES):
        sys.exit("A channel failed and no previous entry was available.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "#EXTM3U\n" + "".join(entries.get(channel) or prior[channel] for channel in CHANNEL_PAGES),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
