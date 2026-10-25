package com.studioutility.app;

import android.annotation.SuppressLint;
import android.app.DownloadManager;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.view.View;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;

/** The hub's controls full-screen. Same page as in a browser, plus downloads to Downloads and a way back to setup. */
public class HubActivity extends AppCompatActivity {
    private WebView web;
    private Uri hub;

    @SuppressLint("SetJavaScriptEnabled")
    @Override protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);  // framing a shot: don't sleep
        hub = getIntent().getData();
        web = new WebView(this);
        web.setBackgroundColor(0xFF000000);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        // the page skips its browser welcome inside the app (this app did onboarding already)
        s.setUserAgentString(s.getUserAgentString() + " StudioUtilityAndroid/0.1");
        CookieManager.getInstance().setAcceptCookie(true);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (u.getHost() != null && u.getHost().equals(hub.getHost())) return false;  // stay in the app
                startActivity(new Intent(Intent.ACTION_VIEW, u));
                return true;
            }

            @Override public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) unreachable();
            }
        });
        web.setDownloadListener((url, userAgent, disposition, mime, length) -> download(url, disposition, mime));
        web.setOnLongClickListener(v -> false);
        setContentView(web);
        hideSystemBars();
        if (saved != null) web.restoreState(saved);
        else web.loadUrl(startUrl());

        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override public void handleOnBackPressed() {
                if (web.canGoBack()) web.goBack();
                else menu();
            }
        });
    }

    /** First visit trades the QR's pairing token for this device's own cookie; after that, leave the token out
     *  so each launch doesn't register the phone again. */
    private String startUrl() {
        String base = hub.buildUpon().clearQuery().build().toString();
        String cookie = CookieManager.getInstance().getCookie(base);
        return cookie != null && cookie.contains("studio_token=") ? base : hub.toString();
    }

    @Override protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();  // keep the device cookie across launches
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    private void hideSystemBars() {
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
    }

    /** Originals from the card go to Downloads; the hub session cookie comes along. */
    private void download(String url, String disposition, String mime) {
        String name = URLUtil.guessFileName(url, disposition, mime);
        Uri u = Uri.parse(url);
        String path = u.getQueryParameter("path");
        if (path != null) name = path.substring(path.lastIndexOf('/') + 1);
        DownloadManager.Request r = new DownloadManager.Request(u)
                .setTitle(name)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Studio Utility/" + name);
        String cookie = CookieManager.getInstance().getCookie(url);
        if (cookie != null) r.addRequestHeader("Cookie", cookie);
        r.addRequestHeader("X-Studio-Device", "Android phone");
        ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
        Toast.makeText(this, "Downloading " + name, Toast.LENGTH_SHORT).show();
    }

    private void unreachable() {
        new AlertDialog.Builder(this)
                .setTitle("Can't reach Studio Hub")
                .setMessage("Is the computer on, and this phone on the same network (or Tailscale)?\n\n" + hub.getHost())
                .setPositiveButton("Try again", (d, w) -> web.loadUrl(startUrl()))
                .setNegativeButton("Pair another hub", (d, w) -> changeHub())
                .setCancelable(false).show();
    }

    private void menu() {
        new AlertDialog.Builder(this)
                .setItems(new CharSequence[]{"Reload", "Pair another hub", "Close"}, (d, which) -> {
                    if (which == 0) web.reload();
                    else if (which == 1) changeHub();
                    else finish();
                }).show();
    }

    private void changeHub() {
        startActivity(new Intent(this, SetupActivity.class).putExtra("change", true));
        finish();
    }
}
