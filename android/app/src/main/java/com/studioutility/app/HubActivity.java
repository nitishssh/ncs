package com.studioutility.app;

import android.annotation.SuppressLint;
import android.app.DownloadManager;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/** The hub's controls full-screen. Connects to whichever of the hub's addresses answers, shows a clear screen
 *  while it can't, and reconnects by itself when the phone changes network. */
public class HubActivity extends AppCompatActivity {
    private static final int SUB = Color.parseColor("#9A9A9F"), PANEL = Color.parseColor("#1C1C1E"),
            ACCENT = Color.parseColor("#0A84FF");

    private Hub hub;
    private Hubs hubs;
    private WebView web;
    private PhoneMic mic;
    private View status;
    private TextView statusTitle, statusText;
    private ProgressBar spinner;
    private LinearLayout statusButtons;
    private String base;  // http://host:port/ currently in use
    private boolean connecting;
    private final List<String> discovered = Collections.synchronizedList(new ArrayList<>());
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Runnable retry = this::connect;

    @SuppressLint("SetJavaScriptEnabled")
    @Override protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);  // framing a shot: don't sleep
        hub = new Hub(this);
        if (!hub.paired()) {
            pairAgain();
            return;
        }
        hubs = new Hubs(this);

        web = new WebView(this);
        web.setBackgroundColor(Color.BLACK);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setUserAgentString(s.getUserAgentString() + " StudioUtilityAndroid/0.2");  // page skips its browser welcome
        CookieManager.getInstance().setAcceptCookie(true);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (base != null && base.startsWith(u.getScheme() + "://" + u.getAuthority())) return false;
                startActivity(new Intent(Intent.ACTION_VIEW, u));
                return true;
            }

            @Override public void onPageFinished(WebView view, String url) {
                if (base != null && url.startsWith(base)) status.setVisibility(View.GONE);
            }

            @Override public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) connect();  // the network changed under us: find the hub again
            }
        });
        web.setDownloadListener((url, ua, disposition, mime, length) -> download(url, disposition, mime));
        mic = new PhoneMic(this, web, hub, () -> base);
        web.addJavascriptInterface(mic, "StudioAndroid");  // the page's MIC button records natively

        FrameLayout root = new FrameLayout(this);
        root.addView(web);
        root.addView(status = buildStatus());
        setContentView(root);

        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override public void handleOnBackPressed() {
                if (web.canGoBack()) web.goBack();
                else menu();
            }
        });
        hubs.start(url -> {  // a hub announcing itself is one more address to try
            String h = Uri.parse(url).getAuthority();
            if (h != null && !discovered.contains(h)) discovered.add(h);
        });
        connect();
    }

    @Override protected void onResume() {
        super.onResume();
        if (web != null && base == null) connect();
    }

    @Override protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
    }

    @Override protected void onDestroy() {
        main.removeCallbacks(retry);
        if (hubs != null) hubs.stop();
        if (mic != null) mic.stop();
        super.onDestroy();
    }

    // ---- connecting
    private void connect() {
        if (connecting) return;
        connecting = true;
        main.removeCallbacks(retry);
        showStatus("Connecting to Studio Hub…", "", true);
        List<String> extra = new ArrayList<>(discovered);
        new Thread(() -> {
            String host = hub.reach(extra);
            runOnUiThread(() -> {
                connecting = false;
                if (isFinishing()) return;
                if (host == null) {
                    base = null;
                    showStatus("Can't reach Studio Hub",
                            "Tried " + String.join(", ", hub.hosts()) + ".\n\n"
                                    + "• Is the hub computer on?\n"
                                    + "• Is this phone on the same Wi-Fi, or is Tailscale on?\n\n"
                                    + "Trying again every few seconds.", false);
                    main.postDelayed(retry, 5000);
                    return;
                }
                base = "http://" + host + "/";
                // this phone's own key, as the cookie the page's requests carry (set per address)
                CookieManager.getInstance().setCookie(base, "studio_token=" + hub.token() + "; Path=/; Max-Age=31536000");
                CookieManager.getInstance().flush();
                web.loadUrl(base, Map.of("X-Studio-Device", Hub.DEVICE));
            });
        }).start();
    }

    private View buildStatus() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_VERTICAL);
        box.setPadding(dp(28), dp(28), dp(28), dp(28));
        box.setBackgroundColor(Color.BLACK);
        box.setClickable(true);
        spinner = new ProgressBar(this);
        box.addView(spinner, new LinearLayout.LayoutParams(dp(36), dp(36)));
        statusTitle = new TextView(this);
        statusTitle.setTextColor(Color.WHITE);
        statusTitle.setTextSize(24);
        statusTitle.setTypeface(Typeface.DEFAULT_BOLD);
        statusTitle.setPadding(0, dp(16), 0, dp(8));
        box.addView(statusTitle);
        statusText = new TextView(this);
        statusText.setTextColor(SUB);
        statusText.setTextSize(15);
        statusText.setLineSpacing(0, 1.2f);
        box.addView(statusText);
        statusButtons = new LinearLayout(this);
        statusButtons.setOrientation(LinearLayout.VERTICAL);
        statusButtons.setPadding(0, dp(24), 0, 0);
        statusButtons.addView(button("Try again", true, v -> connect()));
        View gap = new View(this);
        statusButtons.addView(gap, new LinearLayout.LayoutParams(1, dp(10)));
        statusButtons.addView(button("Pair with another hub", false, v -> pairAgain()));
        box.addView(statusButtons);
        return box;
    }

    private void showStatus(String title, String text, boolean busy) {
        status.setVisibility(View.VISIBLE);
        statusTitle.setText(title);
        statusText.setText(text);
        spinner.setVisibility(busy ? View.VISIBLE : View.GONE);
        statusButtons.setVisibility(busy ? View.GONE : View.VISIBLE);
    }

    // ---- downloads: originals from the card go to Downloads/Studio Utility
    private void download(String url, String disposition, String mime) {
        Uri u = Uri.parse(url);
        String path = u.getQueryParameter("path");
        String name = path != null ? path.substring(path.lastIndexOf('/') + 1) : URLUtil.guessFileName(url, disposition, mime);
        DownloadManager.Request r = new DownloadManager.Request(u)
                .setTitle(name)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Studio Utility/" + name)
                .addRequestHeader("X-Studio-Token", hub.token())
                .addRequestHeader("X-Studio-Device", Hub.DEVICE);
        ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
        Toast.makeText(this, "Downloading " + name, Toast.LENGTH_SHORT).show();
    }

    private void menu() {
        new AlertDialog.Builder(this)
                .setItems(new CharSequence[]{"Reload", "Pair with another hub", "Close"}, (d, which) -> {
                    if (which == 0) connect();
                    else if (which == 1) pairAgain();
                    else finish();
                }).show();
    }

    private void pairAgain() {
        startActivity(new Intent(this, SetupActivity.class).putExtra("change", true));
        finish();
    }

    private Button button(String label, boolean primary, View.OnClickListener click) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setTextSize(17);
        b.setTextColor(primary ? Color.WHITE : ACCENT);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(primary ? ACCENT : PANEL);
        bg.setCornerRadius(dp(14));
        b.setBackground(bg);
        b.setLayoutParams(new LinearLayout.LayoutParams(-1, dp(52)));
        b.setOnClickListener(click);
        return b;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
