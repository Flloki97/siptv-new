# siptv-new

Kodi playlist for Big Brother VIP Kosova, Kanali 1 and Kanali 2.

## Kodi setup

Once GitHub Pages has deployed, use this URL in **IPTV Simple Client → Configure → General → M3U play list URL**.

`https://flloki97.github.io/siptv-new/playlist.m3u`

Set **Location** to **Remote path**. If your Kodi version offers **Auto refresh mode**, choose **Repeated refresh** every five minutes.

The `playlist.m3u` in this repository is a snapshot for initial testing. Use the Pages URL for automatic updates.

## Updates

The GitHub Actions workflow is scheduled to check both HLS feeds every five minutes. It decodes a video frame from each channel and publishes a fresh playlist when either stream URL changes. If a check fails, Actions identifies the affected channel and keeps the last published playlist available.

On a new repository, select **GitHub Actions** under **Settings → Pages → Source**, then run **Check and publish Kodi playlist** from the **Actions** tab. GitHub may delay scheduled runs and disables them after 60 days of inactivity in public repositories.
