package com.studioutility.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/** Pairing and reaching the Studio Hub. The hub has several addresses (home Wi-Fi, hotspot, Tailscale); the app
 *  keeps them all and uses whichever answers right now. Network calls block: call from a background thread. */
final class Hub {
    private static final String PREFS = "studio", TOKEN = "device_token", HOSTS = "hosts";
    static final String DEVICE = "Android app";

    /** What the user sees when pairing fails; message is written for them, not for a log. */
    static final class Problem extends Exception {
        Problem(String message) { super(message); }
    }

    private final SharedPreferences prefs;

    Hub(Context context) {
        prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    boolean paired() { return prefs.getString(TOKEN, null) != null; }
    String token() { return prefs.getString(TOKEN, ""); }

    List<String> hosts() {
        List<String> out = new ArrayList<>();
        for (String h : prefs.getString(HOSTS, "").split(",")) if (!h.isEmpty()) out.add(h);
        return out;
    }

    void forget() { prefs.edit().clear().apply(); }

    /** Pairing link from the hub's QR: http://best:8765/?token=ADMIN&hosts=a,b,c  ->  this phone's own key. */
    void pair(String link) throws Problem {
        Uri u = Uri.parse(link.trim());
        String admin = u.getQueryParameter("token");
        if (u.getHost() == null || admin == null || !("http".equals(u.getScheme()) || "https".equals(u.getScheme())))
            throw new Problem("That isn't a Studio Hub pairing code. Open http://localhost:8765/pair on the hub computer and scan the code there.");
        int port = u.getPort() > 0 ? u.getPort() : 8765;
        Set<String> candidates = new LinkedHashSet<>();
        candidates.add(u.getHost() + ":" + port);
        String extra = u.getQueryParameter("hosts");
        if (extra != null) for (String h : extra.split(",")) if (!h.trim().isEmpty()) candidates.add(h.trim() + ":" + port);

        String host = firstReachable(new ArrayList<>(candidates), admin);
        if (host == null)
            throw new Problem("This phone can't reach the hub at " + String.join(", ", candidates)
                    + ".\n\nPut the phone on the same Wi-Fi as the hub computer, or turn on Tailscale, then scan again.");
        try {
            JSONObject r = new JSONObject(request("POST", "http://" + host + "/api/pair", admin, "{\"name\":\"" + DEVICE + "\"}", 5000));
            List<String> hosts = new ArrayList<>();
            hosts.add(host);  // the one that worked first
            JSONArray all = r.optJSONArray("hosts");
            if (all != null) for (int i = 0; i < all.length(); i++) if (!hosts.contains(all.getString(i))) hosts.add(all.getString(i));
            prefs.edit().putString(TOKEN, r.getString("device_token")).putString(HOSTS, String.join(",", hosts)).apply();
        } catch (Exception e) {
            throw new Problem("The hub answered but wouldn't pair (" + e.getMessage() + "). Update Studio Hub on the computer and try again.");
        }
    }

    /** host:port of the hub that answers fastest with this phone's key, or null. Extra hosts come from discovery. */
    String reach(List<String> discovered) {
        Set<String> all = new LinkedHashSet<>(hosts());
        all.addAll(discovered);
        return firstReachable(new ArrayList<>(all), token());
    }

    /** Tries every address at once; the first to answer /api/hello wins (it's also the fastest route). */
    static String firstReachable(List<String> hosts, String token) {
        if (hosts.isEmpty()) return null;
        ExecutorService pool = Executors.newFixedThreadPool(Math.min(hosts.size(), 6));
        ExecutorCompletionService<String> done = new ExecutorCompletionService<>(pool);
        List<Future<String>> jobs = new ArrayList<>();
        for (String h : hosts) jobs.add(done.submit(() -> {
            String body = request("GET", "http://" + h + "/api/hello", token, null, 2500);
            return body.contains("studio-utility-hub") ? h : null;
        }));
        try {
            for (int i = 0; i < jobs.size(); i++) {
                Future<String> f = done.poll(4, TimeUnit.SECONDS);
                if (f == null) break;
                try {
                    String h = f.get();
                    if (h != null) return h;
                } catch (Exception ignored) { }  // that address is down; keep waiting for the others
            }
            return null;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return null;
        } finally {
            pool.shutdownNow();
        }
    }

    static String request(String method, String url, String token, String json, int timeoutMs) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(timeoutMs);
        c.setReadTimeout(timeoutMs);
        c.setRequestProperty("X-Studio-Token", token);
        c.setRequestProperty("X-Studio-Device", DEVICE);
        if (json != null) {
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            try (OutputStream o = c.getOutputStream()) { o.write(json.getBytes(StandardCharsets.UTF_8)); }
        }
        int code = c.getResponseCode();
        try (InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream()) {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            if (in != null) {
                byte[] buf = new byte[4096];
                for (int n; (n = in.read(buf)) > 0; ) b.write(buf, 0, n);
            }
            String body = b.toString("UTF-8");
            if (code >= 400) throw new Exception("HTTP " + code + (body.isEmpty() ? "" : ": " + body));
            return body;
        } finally {
            c.disconnect();
        }
    }
}
