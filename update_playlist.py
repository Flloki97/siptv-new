"""Find and verify the public streams for the Kodi and Stremio playlists."""

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import cache
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urljoin, urlsplit


CHANNEL_PAGES = {
    1: "https://bbvipks.com/kanali-1-live-big-brother-kosova-vip-gzpim/",
    2: "https://bbvipks.com/kanali-2-live-big-brother-kosova-live-u4o1x/",
}
PLAYER_HOST = "s1.bbvipks.com"
EMBED_HOST = "ok.ru"
BACKUP_CDN = "okcdn.ru"
FALLBACK_CONFIG = "https://bbvip.live/api.php?action=config"
FALLBACK_CDN = "moldova-dns.xyz"
STREMIO_RESOLUTION = "852x480"
STREMIO_RENEWAL_MARGIN = 18 * 60 * 60
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


def is_backup_embed(url):
    return secure_url(url, EMBED_HOST) and re.fullmatch(
        r"/videoembed/\d+/?", urlsplit(url).path
    ) is not None


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
                if is_backup_embed(embed):
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


def check_live_window(lines):
    if "#EXT-X-ENDLIST" in lines:
        raise ValueError("The live playlist has ended")

    latest_end = None
    for line in lines:
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            stamp = datetime.fromisoformat(line.partition(":")[2].replace("Z", "+00:00"))
            latest_end = stamp.timestamp() if stamp.utcoffset() is not None else None
        elif latest_end is not None and line.startswith("#EXTINF:"):
            latest_end += float(line.partition(":")[2].partition(",")[0])

    if latest_end is not None and time.time() - latest_end > 90:
        raise ValueError("The live playlist has not published recent video")


def verify_video(master, domain, preferred_resolution=None):
    master_lines = hls_lines(master, domain)
    variants = [
        (tag, line) for tag, line in zip(master_lines, master_lines[1:])
        if tag.startswith("#EXT-X-STREAM-INF:") and not line.startswith("#")
    ]
    if variants:
        default_variant = variants[1] if len(variants) > 1 else variants[0]
        variant = next(
            (
                line for tag, line in variants
                if f"RESOLUTION={preferred_resolution}" in tag.partition(":")[2].split(",")
            ),
            default_variant[1],
        )
        media = urljoin(master, variant)
        media_lines = hls_lines(media, domain)
    elif any(line.startswith("#EXTINF:") for line in master_lines):
        media, media_lines = master, master_lines
    else:
        raise ValueError("The HLS master playlist has no video variants")
    check_live_window(media_lines)
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
    return media


def backup_master(embed):
    match = re.search(HLS_PATTERN, page(embed))
    if not match:
        raise ValueError("The backup player did not supply an HLS URL")
    return match.group(1)


def fallback_source(channel, stremio_stream=None):
    response = json.loads(page(FALLBACK_CONFIG))
    try:
        settings = next(
            item for item in response["config"]["channel_configs"]
            if re.fullmatch(rf"kanali?\s+{channel}", item["label"], flags=re.IGNORECASE)
        )
        urls = [source["url"] for source in settings["servers"]]
    except (KeyError, TypeError, StopIteration) as error:
        raise ValueError("BBVIP.LIVE did not list this channel") from error

    for url in urls:
        if not isinstance(url, str) or any(char.isspace() for char in url):
            continue
        try:
            if is_backup_embed(url):
                master = backup_master(url)
                if stremio_stream is not None:
                    return verified_stremio_stream(master, stremio_stream), False
                verify_video(master, BACKUP_CDN)
                return url, True
            if is_hls_host(url, FALLBACK_CDN) and urlsplit(url).path.endswith(".m3u8"):
                verify_video(url, FALLBACK_CDN)
                return url, False
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue

    raise ValueError("BBVIP.LIVE has no playable fallback for this channel")


def channel_entry(channel):
    try:
        url = find_embed(channel)
        verify_video(backup_master(url), BACKUP_CDN)
        is_embed = True
        print(f"Kanali {channel}: verified the preferred BBVIPKS.COM feed.")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"::warning::Kanali {channel}: BBVIPKS.COM unavailable ({error}).", file=sys.stderr)
        url, is_embed = fallback_source(channel)
        print(f"Kanali {channel}: verified the BBVIP.LIVE fallback.")

    stream = f"#WEBPROP:web-regex={HLS_PATTERN}\n@{url}" if is_embed else url
    return f"#EXTINF:-1,{CHANNEL_NAME}{channel}\n{stream}\n"


def verified_stremio_stream(master, stream):
    path = urlsplit(stream).path
    expiration = re.search(r"/expires/(\d{13})/", path)
    same_stream = path.split("/sig/", 1)[0] == urlsplit(master).path.split("/sig/", 1)[0]
    if (
        is_hls_host(stream, BACKUP_CDN)
        and same_stream
        and expiration
        and int(expiration[1]) / 1000 - time.time() > STREMIO_RENEWAL_MARGIN
    ):
        try:
            verify_video(stream, BACKUP_CDN)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        else:
            return stream

    return verify_video(master, BACKUP_CDN, preferred_resolution=STREMIO_RESOLUTION)


def stremio_entry(channel, previous):
    stream = previous.strip().split("\n")[-1]
    try:
        master = backup_master(find_embed(channel))
        stream = verified_stremio_stream(master, stream)
        print(f"Kanali {channel}: verified the preferred BBVIPKS.COM feed for Stremio.")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"::warning::Kanali {channel}: BBVIPKS.COM unavailable for Stremio ({error}).", file=sys.stderr)
        stream, _ = fallback_source(channel, stremio_stream=stream)
        print(f"Kanali {channel}: verified the BBVIP.LIVE fallback for Stremio.")

    return f'#EXTINF:-1 group-title="Live TV",{CHANNEL_NAME}{channel}\n{stream}\n'


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
    output = args.output
    prior = previous_entries(args.previous)
    entries = {}
    with ThreadPoolExecutor(max_workers=len(CHANNEL_PAGES)) as pool:
        if args.stremio:
            jobs = {
                channel: pool.submit(stremio_entry, channel, prior.get(channel, ""))
                for channel in CHANNEL_PAGES
            }
        else:
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
    if len(entries) != len(CHANNEL_PAGES):
        client = "Stremio" if args.stremio else "Kodi"
        sys.exit(f"A {client} channel is unavailable from both sources. Its previous entry was retained.")


if __name__ == "__main__":
    main()
