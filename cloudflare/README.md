# Cloudflare Worker

Deploy `worker.mjs` as a Cloudflare Worker on the Free plan. In Workers & Pages, create a Worker, replace the starter code with this file, and deploy. Add `/playlist.m3u` to the Worker URL to get the two-channel playlist for Kodi or a Stremio IPTV add-on.

When a player opens a channel, the Worker tries the current BBVIPKS.COM feed first. If it cannot verify playable live HLS, it tries BBVIP.LIVE. It redirects the player to the upstream stream and briefly caches the selected link. If both sources fail, it returns HTTP 503. When a stream stops during playback, reopen the channel to resolve its link again.

To test the deployment, open the playlist URL and confirm it lists both channels. Check `/kanali/1.m3u8` and `/kanali/2.m3u8` for HTTP 302 and the `X-Stream-Source` header. Verify playback in each app before changing its playlist URL.
