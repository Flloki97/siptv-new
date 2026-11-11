"""Find and verify the public streams for the Kodi and Stremio playlists."""

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from html.parser import HTMLParser
import json
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
STREMIO_CONFIG = "https://bbvip.live/api.php?action=config"
STREMIO_CDN = "moldova-dns.xyz"
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


def is_hls_host(url, domain):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return (
        parsed.scheme == "https"
        and (host == domain or host.endswith(f".{domain}"))
        and parsed.username is None
        and parsed.password is None
    )


def hls_lines(url, domain):
    if not is_hls_host(url, domain):
        raise ValueError("The backup player returned an unexpected media host")
    lines = [line.strip() for line in fetch(url).decode().splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise ValueError("The backup player did not return an HLS playlist")
    return lines


def verify_video(master, domain):
    master_lines = hls_lines(master, domain)
    variants = [
        line for tag, line in zip(master_lines, master_lines[1:])
        if tag.startswith("#EXT-X-STREAM-INF:") and not line.startswith("#")
    ]
    if variants:
        media = urljoin(master, variants[1] if len(variants) > 1 else variants[0])
        media_lines = hls_lines(media, domain)
    elif any(line.startswith("#EXTINF:") for line in master_lines):
        media, media_lines = master, master_lines
    else:
        raise ValueError("The HLS master playlist has no video variants")
    segments = [line for line in media_lines if not line.startswith("#")]
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
    match = re.search(HLS_PATTERN, page(embed))
    if not match:
        raise ValueError("The backup player did not supply an HLS URL")
    verify_video(match.group(1), "okcdn.ru")
    print(f"Kanali {channel}: verified a frame from the backup player.")
    return f'#EXTINF:-1,{CHANNEL_NAME}{channel}\n#WEBPROP:web-regex={HLS_PATTERN}\n@{embed}\n'


def stremio_entry(channel):
    response = json.loads(page(STREMIO_CONFIG))
    try:
        sources = response["config"]["channel_configs"]
        settings = next(
            item for item in sources
            if re.fullmatch(rf"kanali?\s+{channel}", item["label"], flags=re.IGNORECASE)
        )
        streams = [source["url"] for source in settings["servers"]]
    except (KeyError, TypeError, StopIteration) as error:
        raise ValueError("The Stremio source did not list this channel") from error

    for stream in streams:
        if not isinstance(stream, str) or any(c.isspace() for c in stream):
            continue
        parsed = urlsplit(stream)
        if parsed.query or parsed.fragment or not parsed.path.endswith(".m3u8"):
            continue
        if is_hls_host(stream, STREMIO_CDN):
            verify_video(stream, STREMIO_CDN)
            print(f"Kanali {channel}: verified a frame from the direct Stremio stream.")
            return f'#EXTINF:-1 group-title="Live TV",{CHANNEL_NAME}{channel}\n{stream}\n'

    raise ValueError("The Stremio source did not supply a stable direct HLS URL")


def previous_entries(path):
    if not path or not path.is_file():
        return {}
    entries = re.split(r"(?=^#EXTINF:)", path.read_text(), flags=re.MULTILINE)
    return {
        channel: entry.rstrip() + "\n"
        for channel in CHANNEL_PAGES
        for entry in entries
        if entry.startswith("#EXTINF:")
        and entry.partition("\n")[0].rsplit(",", 1)[-1] == f"{CHANNEL_NAME}{channel}"
    }


def main():
    parser = ArgumentParser()
    parser.add_argument("output", nargs="?", type=Path, default=Path("playlist.m3u"))
    parser.add_argument("previous", nargs="?", type=Path)
    parser.add_argument("--stremio", action="store_true")
    args = parser.parse_args()
    entry_builder = stremio_entry if args.stremio else channel_entry
    output = args.output
    prior = previous_entries(args.previous)
    entries = {}
    with ThreadPoolExecutor(max_workers=len(CHANNEL_PAGES)) as pool:
        jobs = {channel: pool.submit(entry_builder, channel) for channel in CHANNEL_PAGES}
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
