package com.studioutility.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.pm.PackageManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.URL;

/** The phone's mic for the hub page (window.StudioAndroid): records 48 kHz 16-bit mono WAV while the camera
 *  records, then streams it to the hub's /api/audio. Browsers block the mic on the hub's http page; the app doesn't. */
public class PhoneMic {
    private static final int RATE = 48000, PERMISSION = 7;

    private final AppCompatActivity activity;
    private final WebView web;
    private final Hub hub;
    private final java.util.function.Supplier<String> base;  // http://host:port/ currently in use
    private volatile boolean running;
    private Thread thread;
    private File file;

    PhoneMic(AppCompatActivity activity, WebView web, Hub hub, java.util.function.Supplier<String> base) {
        this.activity = activity;
        this.web = web;
        this.hub = hub;
        this.base = base;
    }

    private boolean allowed() {
        return ContextCompat.checkSelfPermission(activity, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
    }

    @JavascriptInterface public void prepareMic() {
        if (!allowed()) activity.runOnUiThread(() -> ActivityCompat.requestPermissions(
                activity, new String[]{Manifest.permission.RECORD_AUDIO}, PERMISSION));
    }

    @SuppressLint("MissingPermission")
    @JavascriptInterface public synchronized void startMic() {
        if (running) return;
        if (!allowed()) {
            prepareMic();
            done(false, "allow the microphone, then record again");
            return;
        }
        int min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        // UNPROCESSED: no phone noise suppression / AGC, so the voice chain on the computer gets the real signal
        AudioRecord rec = new AudioRecord(MediaRecorder.AudioSource.UNPROCESSED, RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, Math.max(min, RATE));
        if (rec.getState() != AudioRecord.STATE_INITIALIZED) {
            rec.release();
            rec = new AudioRecord(MediaRecorder.AudioSource.MIC, RATE, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, Math.max(min, RATE));
        }
        file = new File(activity.getCacheDir(), "phone-mic.wav");
        running = true;
        AudioRecord r = rec;
        thread = new Thread(() -> record(r), "phone-mic");
        thread.start();
    }

    @JavascriptInterface public synchronized void stopMic(String device, String startedMs) {
        if (!running) return;
        running = false;
        Thread t = thread;
        new Thread(() -> {
            try {
                t.join(3000);
                String name = upload(device, startedMs);
                done(true, name);
            } catch (Exception e) {
                done(false, "upload failed: " + e.getMessage() + " (kept on the phone until the next take)");
            }
        }, "phone-mic-upload").start();
    }

    private void record(AudioRecord rec) {
        byte[] buf = new byte[RATE / 10 * 2];  // 100 ms
        long bytes = 0;
        try (RandomAccessFile out = new RandomAccessFile(file, "rw")) {
            out.setLength(0);
            out.write(new byte[44]);  // header filled in at the end
            rec.startRecording();
            while (running) {
                int n = rec.read(buf, 0, buf.length);
                if (n > 0) {
                    out.write(buf, 0, n);
                    bytes += n;
                }
            }
            out.seek(0);
            out.write(header(bytes));
        } catch (IOException e) {
            done(false, e.getMessage());
        } finally {
            rec.stop();
            rec.release();
        }
    }

    private static byte[] header(long data) {
        java.nio.ByteBuffer b = java.nio.ByteBuffer.allocate(44).order(java.nio.ByteOrder.LITTLE_ENDIAN);
        b.put("RIFF".getBytes()).putInt((int) (36 + data)).put("WAVE".getBytes())
                .put("fmt ".getBytes()).putInt(16).putShort((short) 1).putShort((short) 1)
                .putInt(RATE).putInt(RATE * 2).putShort((short) 2).putShort((short) 16)
                .put("data".getBytes()).putInt((int) data);
        return b.array();
    }

    private String upload(String device, String startedMs) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(base.get() + "api/audio").openConnection();
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setFixedLengthStreamingMode(file.length());
        c.setRequestProperty("Content-Type", "audio/wav");
        c.setRequestProperty("X-Studio-Token", hub.token());
        c.setRequestProperty("X-Studio-Device", device);
        c.setRequestProperty("X-Audio-Started", startedMs);
        try (InputStream in = new FileInputStream(file); OutputStream out = c.getOutputStream()) {
            byte[] buf = new byte[1 << 16];
            for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
        }
        int code = c.getResponseCode();
        InputStream body = code < 400 ? c.getInputStream() : c.getErrorStream();
        String text = body == null ? "" : new String(readAll(body));
        if (code >= 400) throw new IOException(code + " " + text);
        file.delete();
        org.json.JSONObject j;
        try {
            j = new org.json.JSONObject(text);
            return j.optString("name", "saved");
        } catch (org.json.JSONException e) {
            return "saved";
        }
    }

    private static byte[] readAll(InputStream in) throws IOException {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
        return out.toByteArray();
    }

    private void done(boolean ok, String msg) {
        String js = "window.studioMicDone && studioMicDone(" + ok + "," + org.json.JSONObject.quote(msg) + ")";
        activity.runOnUiThread(() -> web.evaluateJavascript(js, null));
    }

    void stop() {
        running = false;
    }
}
