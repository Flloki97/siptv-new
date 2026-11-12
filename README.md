# siptv-new

Kodi playlist for Big Brother VIP Kosova, Kanali 1 and Kanali 2.

## Kodi setup

Use IPTV Simple Client 21.11.0 or newer for Kodi 21. For Kodi 22, use IPTV Simple Client 22.5.0 or newer. Use this URL in **IPTV Simple Client → Configure → General → M3U play list URL**.

`https://flloki97.github.io/siptv-new/playlist.m3u`

Set **Location** to **Remote path**. If your Kodi version offers **Auto refresh mode**, choose **Repeated refresh** every five minutes.

The `playlist.m3u` in this repository is a snapshot for initial testing. Use the Pages URL for automatic updates.

## Stremio setup

In M3U Mux, add `https://flloki97.github.io/siptv-new/stremio.m3u` as an **M3U URL** and enable **Include live TV channels**. Save the setup, reindex, and open the Live TV catalog in Stremio.

## Updates

Kodi gets a fresh HLS URL when tuning to a web player. GitHub Actions is scheduled to check both channels every five minutes. Both playlists prefer BBVIPKS.COM, switch an affected channel to a verified BBVIP.LIVE feed during an outage, and restore the preferred feed once it plays again. Checks reject ended playlists and stale live windows when the stream includes timestamps. If both sources fail, the run reports the outage and retains that channel's previous entry while allowing the other channel to update.

The same workflow checks the BBVIPKS.COM backup players for Stremio. It selects a 480p HLS rendition when available and renews expiring stream URLs during scheduled checks.

On a new repository, select **GitHub Actions** under **Settings → Pages → Source**, then run **Check and publish Kodi playlist** from the **Actions** tab. GitHub may delay scheduled runs and disables them after 60 days of inactivity in public repositories.
