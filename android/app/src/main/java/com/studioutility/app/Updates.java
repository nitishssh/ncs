package com.studioutility.app;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.widget.Toast;

import androidx.appcompat.app.AlertDialog;
import androidx.core.content.FileProvider;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;

/** Updates from the project's GitHub releases: the newest -android.apk, checked against SHA256SUMS.txt, then
 *  handed to Android's installer (the user confirms there). Same file naming as the desktop updater. */
final class Updates {
    static final String LATEST = "https://api.github.com/repos/nitishssh/ncs/releases/latest";
    private static final String SKIP = "update_skip";
    private static boolean checkedThisRun;

    private Updates() {}

    /** Once per app start: quiet unless there's a newer version the user hasn't skipped. */
    static void check(Activity a) {
        if (checkedThisRun) return;
        checkedThisRun = true;
        new Thread(() -> {
            try {
                JSONObject rel = new JSONObject(new String(get(LATEST), "UTF-8"));
                String version = rel.getString("tag_name").replaceFirst("^[vV]", "");
                String apk = null, apkName = null, sums = null;
                JSONArray assets = rel.getJSONArray("assets");
                for (int i = 0; i < assets.length(); i++) {
                    JSONObject x = assets.getJSONObject(i);
                    String name = x.getString("name");
                    if (name.endsWith("-android.apk")) { apk = x.getString("browser_download_url"); apkName = name; }
                    if (name.equals("SHA256SUMS.txt")) sums = x.getString("browser_download_url");
                }
                String current = a.getPackageManager().getPackageInfo(a.getPackageName(), 0).versionName;
                SharedPreferences prefs = a.getSharedPreferences(SetupActivity.PREFS, Activity.MODE_PRIVATE);
                if (apk == null || sums == null || !newer(version, current) || version.equals(prefs.getString(SKIP, null)))
                    return;
                final String url = apk, name = apkName, sumsUrl = sums;
                a.runOnUiThread(() -> {
                    if (a.isFinishing()) return;
                    new AlertDialog.Builder(a).setTitle("Update available")
                            .setMessage("Studio Utility " + version + " is available (you have " + current + ").")
                            .setPositiveButton("Update", (d, w) -> download(a, url, name, sumsUrl))
                            .setNegativeButton("Later", null)
                            .setNeutralButton("Skip this version", (d, w) -> prefs.edit().putString(SKIP, version).apply())
                            .show();
                });
            } catch (Exception ignored) {
                // offline, GitHub rate limit, no release yet: try again next start
            }
        }, "update-check").start();
    }

    static boolean newer(String latest, String current) {
        int[] l = parts(latest), c = parts(current);
        for (int i = 0; i < Math.max(l.length, c.length); i++) {
            int x = i < l.length ? l[i] : 0, y = i < c.length ? c[i] : 0;
            if (x != y) return x > y;
        }
        return false;
    }

    private static int[] parts(String v) {
        try {
            String[] p = v.split("-")[0].split("\\.");
            int[] out = new int[p.length];
            for (int i = 0; i < p.length; i++) out[i] = Integer.parseInt(p[i]);
            return out;
        } catch (NumberFormatException e) {
            return new int[0];
        }
    }

    private static void download(Activity a, String url, String name, String sumsUrl) {
        if (Build.VERSION.SDK_INT >= 26 && !a.getPackageManager().canRequestPackageInstalls()) {
            Toast.makeText(a, "Allow Studio Utility to install updates, then tap Update again", Toast.LENGTH_LONG).show();
            a.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + a.getPackageName())));
            checkedThisRun = false;  // offer it again when the user comes back
            return;
        }
        Toast.makeText(a, "Downloading the update…", Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            try {
                String want = null;
                for (String line : new String(get(sumsUrl), "UTF-8").split("\n")) {
                    String[] p = line.trim().split("\\s+");
                    if (p.length == 2 && p[1].replaceFirst("^\\*", "").equals(name)) want = p[0].toLowerCase();
                }
                if (want == null) throw new Exception(name + " isn't in SHA256SUMS.txt");
                byte[] data = get(url);
                if (!hex(MessageDigest.getInstance("SHA-256").digest(data)).equals(want))
                    throw new Exception("the download doesn't match its published checksum");
                File dir = new File(a.getCacheDir(), "updates");
                dir.mkdirs();
                File out = new File(dir, "update.apk");
                try (FileOutputStream f = new FileOutputStream(out)) { f.write(data); }
                Uri uri = FileProvider.getUriForFile(a, a.getPackageName() + ".updates", out);
                Intent install = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
                a.runOnUiThread(() -> a.startActivity(install));
            } catch (Exception e) {
                a.runOnUiThread(() -> Toast.makeText(a, "Update failed: " + e.getMessage(), Toast.LENGTH_LONG).show());
            }
        }, "update-download").start();
    }

    private static byte[] get(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();  // follows GitHub's redirect to its CDN
        c.setConnectTimeout(10000);
        c.setReadTimeout(60000);
        c.setRequestProperty("Accept", url.equals(LATEST) ? "application/vnd.github+json" : "application/octet-stream");
        try (InputStream in = c.getInputStream(); ByteArrayOutputStream buf = new ByteArrayOutputStream()) {
            byte[] b = new byte[1 << 16];
            for (int n; (n = in.read(b)) > 0; ) buf.write(b, 0, n);
            return buf.toByteArray();
        } finally {
            c.disconnect();
        }
    }

    private static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x));
        return s.toString();
    }
}
