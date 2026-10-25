package com.studioutility.app;

import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;

import com.journeyapps.barcodescanner.ScanContract;
import com.journeyapps.barcodescanner.ScanOptions;

import java.util.LinkedHashSet;
import java.util.Set;

/** Onboarding: welcome, then pair with a Studio Hub (scan its QR, pick a found hub, or paste the link). */
public class SetupActivity extends AppCompatActivity {
    static final String PREFS = "studio", LINK = "hub_link";
    private static final int FG = Color.parseColor("#F5F5F7"), SUB = Color.parseColor("#9A9A9F"),
            PANEL = Color.parseColor("#1C1C1E"), ACCENT = Color.parseColor("#0A84FF");

    private Hub hub;
    private Hubs hubs;
    private LinearLayout foundList;
    private TextView foundStatus;
    private final Set<String> found = new LinkedHashSet<>();
    private final ActivityResultLauncher<ScanOptions> scanner = registerForActivityResult(new ScanContract(), r -> {
        if (r.getContents() != null) pairWith(r.getContents());
    });

    @Override protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        hub = new Hub(this);
        hubs = new Hubs(this);
        boolean change = getIntent().getBooleanExtra("change", false);
        if (change) hub.forget();
        if (hub.paired()) {
            openHub();
            return;
        }
        String old = getSharedPreferences(PREFS, MODE_PRIVATE).getString(LINK, null);  // first app version
        if (old != null) {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove(LINK).apply();
            pairWith(old);
            return;
        }
        showWelcome();
    }

    @Override protected void onDestroy() {
        if (hubs != null) hubs.stop();
        super.onDestroy();
    }

    // ---- pages
    private void showWelcome() {
        LinearLayout page = page();
        page.addView(text("Studio Utility", 32, FG, true));
        page.addView(text("Your Canon, on this phone.", 17, SUB, false));
        space(page, 28);
        for (String line : new String[]{"Live view with a Canon Log 3 preview", "Every setting the camera allows remotely",
                "Photos and clips straight off the card"}) {
            TextView t = text("—  " + line, 16, FG, false);
            t.setPadding(0, dp(10), 0, dp(10));
            page.addView(t);
        }
        space(page, 36);
        page.addView(button("Get started", true, v -> showPair()));
        setContentView(scroll(page));
    }

    private void showPair() {
        LinearLayout page = page();
        page.addView(text("Pair with your Studio Hub", 26, FG, true));
        page.addView(text("On the computer the camera is connected to, open Studio Hub's pairing page "
                + "(http://<that computer>:8765/pair) and scan the code.", 15, SUB, false));
        space(page, 24);
        page.addView(button("Scan pairing code", true, v -> scan()));
        space(page, 10);
        page.addView(button("Paste pairing link", false, v -> paste()));
        space(page, 28);
        foundStatus = text("Looking for hubs on this network…", 13, SUB, false);
        page.addView(foundStatus);
        foundList = new LinearLayout(this);
        foundList.setOrientation(LinearLayout.VERTICAL);
        page.addView(foundList);
        setContentView(scroll(page));
        found.clear();
        hubs.start(this::addFound);
    }

    private void addFound(String url) {
        if (!found.add(url)) return;
        foundStatus.setText("Studio Hub is on this network. Scan its code to pair:");
        TextView row = text(url.replace("http://", "").replaceAll("/$", ""), 16, FG, false);
        row.setPadding(dp(14), dp(14), dp(14), dp(14));
        row.setBackground(rounded(PANEL));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(8);
        row.setLayoutParams(lp);
        row.setOnClickListener(v -> Toast.makeText(this, "Scan the code on that computer (http://localhost:8765/pair) to pair",
                Toast.LENGTH_LONG).show());
        foundList.addView(row);
    }

    // ---- pairing
    private void scan() {
        ScanOptions o = new ScanOptions();
        o.setDesiredBarcodeFormats(ScanOptions.QR_CODE);
        o.setPrompt("Scan the pairing code on Studio Hub");
        o.setBeepEnabled(false);
        o.setOrientationLocked(false);
        scanner.launch(o);
    }

    private void paste() {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("http://192.168.0.100:8765/?token=…");
        new AlertDialog.Builder(this).setTitle("Pairing link").setView(input)
                .setPositiveButton("Pair", (d, w) -> pairWith(input.getText().toString().trim()))
                .setNegativeButton("Cancel", null).show();
    }

    private void pairWith(String link) {
        if (hubs != null) hubs.stop();
        LinearLayout page = page();
        page.addView(text("Pairing…", 26, FG, true));
        page.addView(text("Finding the hub on this network.", 15, SUB, false));
        setContentView(scroll(page));
        new Thread(() -> {
            try {
                hub.pair(link);
                runOnUiThread(this::openHub);
            } catch (Hub.Problem p) {
                runOnUiThread(() -> new AlertDialog.Builder(this).setTitle("Couldn't pair").setMessage(p.getMessage())
                        .setPositiveButton("Scan again", (d, w) -> { showPair(); scan(); })
                        .setNegativeButton("Back", (d, w) -> showPair()).setCancelable(false).show());
            }
        }).start();
    }

    private void openHub() {
        if (hubs != null) hubs.stop();
        startActivity(new Intent(this, HubActivity.class));
        finish();
    }

    // ---- tiny view helpers (keeps the app free of layout XML)
    private LinearLayout page() {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setPadding(dp(26), dp(64), dp(26), dp(32));
        return l;
    }

    private View scroll(View v) {
        ScrollView s = new ScrollView(this);
        s.setFillViewport(true);
        s.addView(v);
        return s;
    }

    private TextView text(String s, int sp, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setLineSpacing(0, 1.15f);
        return t;
    }

    private Button button(String label, boolean primary, View.OnClickListener click) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setTextSize(17);
        b.setTextColor(primary ? Color.WHITE : ACCENT);
        b.setBackground(rounded(primary ? ACCENT : PANEL));
        b.setGravity(Gravity.CENTER);
        b.setLayoutParams(new LinearLayout.LayoutParams(-1, dp(54)));
        b.setOnClickListener(click);
        return b;
    }

    private GradientDrawable rounded(int color) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(14));
        return d;
    }

    private void space(LinearLayout l, int dp) {
        View v = new View(this);
        v.setLayoutParams(new LinearLayout.LayoutParams(1, dp(dp)));
        l.addView(v);
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
