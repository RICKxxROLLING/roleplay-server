/*
 * Service worker: what makes the app installable, and how a push reaches you.
 *
 * Caching is for the app's own files only. Nothing under /api is ever cached:
 * chats stay on the server, so a lost or shared phone holds no conversation.
 *
 * Pushes arrive EMPTY (app/push.py). The relay -- Apple, Google, Mozilla --
 * carries only a wake-up; this worker then asks our own server what happened,
 * over the same signed-in connection the app uses.
 */
const SHELL = "rp-shell-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(["/", "/manifest.webmanifest"])));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return; // always the network, never stored
  // Network first so an update shows on the next open; the cache only covers
  // starting the app with no connection, where it then shows its own error.
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        if (res.ok && (url.pathname === "/" || url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/"))) {
          const copy = res.clone();
          caches.open(SHELL).then((c) => c.put(url.pathname === "/" ? "/" : event.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/") ? event.request : "/"))
  );
});

self.addEventListener("push", (event) => {
  event.waitUntil(
    (async () => {
      let items = [];
      try {
        const res = await fetch("/api/push/pending", { method: "POST", credentials: "include" });
        if (res.ok) items = (await res.json()).notifications || [];
      } catch {
        /* server unreachable: fall through to the generic notice */
      }
      // Browsers require a visible notification for every push (iOS revokes
      // the subscription otherwise), so a failed fetch still shows something --
      // with no content, since none could be fetched.
      if (!items.length) {
        return self.registration.showNotification("Roleplay", {
          body: "Something new is waiting.",
          icon: "/icons/icon-192.png",
          badge: "/icons/badge-96.png",
          tag: "rp-generic",
        });
      }
      for (const n of items) {
        await self.registration.showNotification(n.title, {
          body: n.body,
          icon: "/icons/icon-192.png",
          badge: "/icons/badge-96.png",
          tag: n.session_id ? `rp-session-${n.session_id}` : `rp-${n.id}`,
          data: { url: n.session_id ? `/?session=${n.session_id}` : "/" },
        });
      }
    })()
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = event.notification.data?.url || "/";
  event.waitUntil(
    (async () => {
      const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of open) {
        if (new URL(client.url).origin === self.location.origin) {
          await client.focus();
          client.postMessage({ type: "open", url: target });
          return;
        }
      }
      await self.clients.openWindow(target);
    })()
  );
});
