package com.studioutility.app;

import android.content.Context;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Handler;
import android.os.Looper;

import java.net.Inet4Address;
import java.net.InetAddress;

/** Finds Studio Hubs announcing "_studio-utility._tcp" on the local network (same name the hub uses). */
final class Hubs {
    interface Listener { void found(String url); }

    private static final String TYPE = "_studio-utility._tcp.";
    private final NsdManager nsd;
    private final WifiManager.MulticastLock lock;
    private final Handler main = new Handler(Looper.getMainLooper());
    private NsdManager.DiscoveryListener discovery;

    Hubs(Context context) {
        nsd = (NsdManager) context.getSystemService(Context.NSD_SERVICE);
        WifiManager wifi = (WifiManager) context.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        lock = wifi.createMulticastLock("studio-utility-discovery");
    }

    void start(Listener listener) {
        stop();
        lock.acquire();
        discovery = new NsdManager.DiscoveryListener() {
            @Override public void onServiceFound(NsdServiceInfo service) {
                nsd.resolveService(service, new NsdManager.ResolveListener() {
                    @Override public void onServiceResolved(NsdServiceInfo info) {
                        InetAddress host = info.getHost();
                        if (host == null) return;
                        String addr = host instanceof Inet4Address ? host.getHostAddress() : "[" + host.getHostAddress() + "]";
                        String url = "http://" + addr + ":" + info.getPort() + "/";
                        main.post(() -> listener.found(url));
                    }
                    @Override public void onResolveFailed(NsdServiceInfo info, int error) { }
                });
            }
            @Override public void onDiscoveryStarted(String type) { }
            @Override public void onServiceLost(NsdServiceInfo service) { }
            @Override public void onDiscoveryStopped(String type) { }
            @Override public void onStartDiscoveryFailed(String type, int error) { }
            @Override public void onStopDiscoveryFailed(String type, int error) { }
        };
        nsd.discoverServices(TYPE, NsdManager.PROTOCOL_DNS_SD, discovery);
    }

    void stop() {
        if (discovery != null) {
            try { nsd.stopServiceDiscovery(discovery); } catch (IllegalArgumentException ignored) { }
            discovery = null;
        }
        if (lock.isHeld()) lock.release();
    }
}
