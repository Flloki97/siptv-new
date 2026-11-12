const channelPages = {
  1: "https://bbvipks.com/kanali-1-live-big-brother-kosova-vip-gzpim/",
  2: "https://bbvipks.com/kanali-2-live-big-brother-kosova-live-u4o1x/",
};
const playerHost = "s1.bbvipks.com";
const fallbackConfig = "https://bbvip.live/api.php?action=config";
const timeoutMs = 8000;
const probeHeaders = { Range: "bytes=0-187" };

function inDomain(url, domain) {
  return url.protocol === "https:" && !url.username && !url.password && !url.port &&
    (url.hostname === domain || url.hostname.endsWith("." + domain));
}

async function upstream(url, domain, headers) {
  let target = new URL(url);
  const signal = AbortSignal.timeout(timeoutMs);
  for (let i = 0; i < 3; i++) {
    if (!inDomain(target, domain)) throw new Error("Unexpected upstream host");
    const result = await fetch(target.href, { redirect: "manual", signal, headers });
    if (result.ok) return { result, target };
    await result.body?.cancel();
    const location = result.headers.get("Location");
    if (result.status < 300 || result.status >= 400 || !location) {
      throw new Error("Upstream returned HTTP " + result.status);
    }
    target = new URL(location, target);
  }
  throw new Error("Too many redirects");
}

async function textFrom(url, domain) {
  const { result, target } = await upstream(url, domain);
  return { text: await result.text(), target };
}

function iframeLinks(html, base) {
  return Array.from(html.matchAll(/<iframe\b[^>]*>/gi)).flatMap(([tag]) => {
    const src = /\bdata-litespeed-src\s*=\s*["']([^"']+)["']/i.exec(tag) ||
      /\ssrc\s*=\s*["']([^"']+)["']/i.exec(tag);
    if (!src) return [];
    try {
      return [new URL(src[1].replaceAll("&amp;", "&"), base)];
    } catch {
      return [];
    }
  });
}

function isEmbed(url) {
  return url.hostname === "ok.ru" && inDomain(url, "ok.ru") &&
    /^\/videoembed\/\d+\/?$/.test(url.pathname);
}

async function preferredEmbeds(channel) {
  const { text, target } = await textFrom(channelPages[channel], "bbvipks.com");
  const hub = iframeLinks(text, target).find((url) => url.hostname === playerHost);
  if (!hub) throw new Error("Channel player unavailable");
  const { text: config } = await textFrom(hub, playerHost);
  const pattern = new RegExp("\\bkanali" + channel + "(?:hd)?\\s*:\\s*['\"]([^'\"]+)['\"]", "g");
  const playerLinks = Array.from(config.matchAll(pattern), ([, link]) => new URL(link, hub))
    .filter((url) => url.hostname === playerHost);
  const pages = await Promise.allSettled(
    playerLinks.slice(0, 3).map(async (url) => {
      const { text } = await textFrom(url, playerHost);
      return iframeLinks(text, url).filter(isEmbed);
    }),
  );
  return pages.flatMap((page) => page.status === "fulfilled" ? page.value : []);
}

async function embedMaster(embed) {
  const { text } = await textFrom(embed, "ok.ru");
  const match = /hlsMasterPlaylistUrl&quot;\s*:\s*&quot;(https:\/\/[^&<>\s]+)/.exec(text);
  if (!match) throw new Error("Player returned no HLS URL");
  return new URL(match[1]);
}

async function hls(url, domain) {
  const { text, target } = await textFrom(url, domain);
  const lines = text.trim().split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (lines[0] !== "#EXTM3U") throw new Error("Invalid HLS playlist");
  return { lines, target };
}

function checkRecentMedia(lines) {
  if (lines.includes("#EXT-X-ENDLIST")) throw new Error("Stream ended");
  let endTime;
  for (const line of lines) {
    if (line.startsWith("#EXT-X-PROGRAM-DATE-TIME:")) {
      endTime = Date.parse(line.slice("#EXT-X-PROGRAM-DATE-TIME:".length));
    } else if (Number.isFinite(endTime) && line.startsWith("#EXTINF:")) {
      endTime += Number.parseFloat(line.slice("#EXTINF:".length)) * 1000;
    }
  }
  if (Number.isFinite(endTime) && Date.now() - endTime > 90000) {
    throw new Error("Stream has gone stale");
  }
}

async function verifyMedia({ lines, target }, domain) {
  checkRecentMedia(lines);
  const segments = lines.filter((line, i) => lines[i - 1]?.startsWith("#EXTINF:") &&
    !line.startsWith("#"));
  if (!segments.length) throw new Error("No video segments");
  const segment = new URL(segments.at(-2) || segments.at(-1), target);
  const { result } = await upstream(segment, domain, probeHeaders);
  try {
    const kind = result.headers.get("Content-Type") || "";
    if (/^(?:text\/|application\/(?:json|xml))/i.test(kind) ||
        result.headers.get("Content-Length") === "0") {
      throw new Error("Segment did not return video");
    }
  } finally {
    await result.body?.cancel();
  }
  return target.href;
}

async function streamFrom(url) {
  const domain = isEmbed(url) ? "okcdn.ru" : "moldova-dns.xyz";
  const master = isEmbed(url) ? await embedMaster(url) : url;
  const { lines, target } = await hls(master, domain);
  const variants = lines.flatMap((line, i) => {
    const next = lines[i + 1];
    return line.startsWith("#EXT-X-STREAM-INF:") && next && !next.startsWith("#") ?
      [{ tag: line, url: new URL(next, target) }] : [];
  });
  if (variants.length) {
    const medium = variants.find(({ tag }) => /(?:[:,])RESOLUTION=852x480(?:,|$)/.test(tag));
    const low = variants.find(({ tag }) => /(?:[:,])RESOLUTION=640x360(?:,|$)/.test(tag));
    for (const variant of new Set([medium, low, variants[0]].filter(Boolean))) {
      try {
        return await verifyMedia(await hls(variant.url, domain), domain);
      } catch {
        // An advertised rendition can fail independently of the others.
      }
    }
    throw new Error("No playable rendition");
  }
  return verifyMedia({ lines, target }, domain);
}

async function firstPlayable(urls) {
  for (const url of urls) {
    try {
      return await streamFrom(url);
    } catch {
      // Try the next listed player.
    }
  }
  return null;
}

async function resolveChannel(channel) {
  try {
    const url = await firstPlayable((await preferredEmbeds(channel)).slice(0, 2));
    if (url) return { url, source: "BBVIPKS.COM" };
  } catch {
    // Discovery may fail independently of video delivery.
  }
  const { text } = await textFrom(fallbackConfig, "bbvip.live");
  const settings = JSON.parse(text).config?.channel_configs?.find(({ label }) =>
    new RegExp("^kanali?\\s+" + channel + "$", "i").test(label),
  );
  const urls = (settings?.servers || []).flatMap(({ url }) => {
    try {
      const link = new URL(url);
      return isEmbed(link) ||
        (inDomain(link, "moldova-dns.xyz") && link.pathname.endsWith(".m3u8")) ? [link] : [];
    } catch {
      return [];
    }
  });
  const url = await firstPlayable(urls.slice(0, 2));
  if (!url) throw new Error("Both sources are unavailable");
  return { url, source: "BBVIP.LIVE" };
}

function playlist(origin) {
  return "#EXTM3U\n" + [1, 2].map((channel) =>
    '#EXTINF:-1 group-title="Live TV",Big Brother VIP Kosova - Kanali ' +
    channel + "\n" + origin + "/kanali/" + channel + ".m3u8\n"
  ).join("");
}

export default {
  async fetch(request, _env, ctx) {
    const endpoint = new URL(request.url);
    const headers = {
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-store",
    };
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response(null, { status: 405, headers: { ...headers, Allow: "GET, HEAD" } });
    }
    if (endpoint.pathname === "/playlist.m3u") {
      return new Response(request.method === "HEAD" ? null : playlist(endpoint.origin), {
        headers: { ...headers, "Content-Type": "audio/x-mpegurl; charset=utf-8" },
      });
    }

    const match = /^\/kanali\/([12])\.m3u8$/.exec(endpoint.pathname);
    if (!match) return new Response("Use /playlist.m3u", { status: 404, headers });

    try {
      const cacheKey = new Request(endpoint.origin + endpoint.pathname);
      const cache = globalThis.caches?.default;
      const cached = await cache?.match(cacheKey).catch(() => undefined);
      const stream = cached ? await cached.json() : await resolveChannel(Number(match[1]));
      if (!cached && cache) {
        const value = Response.json(stream, { headers: { "Cache-Control": "public, max-age=20" } });
        ctx.waitUntil(cache.put(cacheKey, value).catch(() => {}));
      }
      return new Response(null, {
        status: 302,
        headers: { ...headers, Location: stream.url, "X-Stream-Source": stream.source },
      });
    } catch {
      return new Response(request.method === "HEAD" ? null : "No live source available", {
        status: 503,
        headers: { ...headers, "Retry-After": "15" },
      });
    }
  },
};
