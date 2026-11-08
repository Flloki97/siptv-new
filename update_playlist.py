"""Resolve both public streams, verify video, and generate Kodi's playlist."""

import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

PLAYER_API = "https://s1.bbvipks.com/fetch_m3u8_{channel}.php"
CHANNELS = (1, 2)


def resolve_stream(channel):
    player_api = PLAYER_API.format(channel=channel)
    response = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--max-time", "15", player_api],
        capture_output=True,
        check=True,
    )
    data = json.loads(response.stdout)
    stream = data.get("url") if isinstance(data, dict) else None
    if not isinstance(stream, str) or any(char.isspace() for char in stream):
        raise ValueError("The player API returned an invalid stream URL.")

    parsed = urlsplit(stream)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.lower().endswith(".m3u8")
    ):
        raise ValueError("The player API did not return a public HTTPS HLS URL.")
    return stream


def verify_video(stream):
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-rw_timeout", "12000000",
            "-protocol_whitelist", "https,httpproxy,tls,tcp,crypto",
            "-i", stream, "-map", "0:v:0", "-frames:v", "1",
            "-f", "framehash", "-",
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    has_frame = any(line.startswith("0,") for line in result.stdout.splitlines())
    if result.returncode != 0 or not has_frame:
        raise ValueError("The HLS stream did not deliver a decodable video frame.")


def main():
    playlist = ["#EXTM3U"]
    for channel in CHANNELS:
        name = f"Big Brother VIP Kosova - Kanali {channel}"
        try:
            stream = resolve_stream(channel)
            verify_video(stream)
        except subprocess.CalledProcessError as error:
            detail = error.stderr.decode(errors="replace").strip()
            sys.exit(f"{name}: Player API request failed: {detail}")
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            sys.exit(f"{name}: Playlist check failed: {error}")

        playlist.extend((f"#EXTINF:-1,{name}", stream))
        print(f"{name}: Decoded a video frame.")

    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("playlist.m3u")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(playlist) + "\n", encoding="utf-8")
    print("Generated the two-channel Kodi playlist.")


if __name__ == "__main__":
    main()
