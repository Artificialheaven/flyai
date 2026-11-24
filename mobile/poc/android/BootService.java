package top.eastcloud.flyai.poc;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.io.RandomAccessFile;
import java.net.InetAddress;
import java.util.ArrayList;
import java.util.List;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 前台服务：首次启动解包 Debian 用户态 + 用 PRoot 拉起 FlyAI（FastAPI）。
 * 原生侧只负责进程与文件，业务全部在 rootfs 里的 Python 中。
 */
public class BootService extends Service {
    private static final String TAG = "FlyAI-Boot";
    private static final String CHANNEL = "flyai_server";
    private static final int NOTIFICATION_ID = 1001;

    private volatile Process server;
    private PowerManager.WakeLock wakeLock;
    private volatile boolean starting;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        try {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "FlyAI:server");
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire();
        } catch (Exception ignored) {
        }
        startForegroundCompat("正在准备运行环境…");
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (!starting && (server == null || !server.isAlive())) {
            starting = true;
            new Thread(this::boot, "flyai-boot").start();
        }
        return START_STICKY;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager manager = getSystemService(NotificationManager.class);
        NotificationChannel channel = new NotificationChannel(
                CHANNEL, "FlyAI 本地服务", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("在设备上运行 FlyAI 服务端");
        manager.createNotificationChannel(channel);
    }

    private void startForegroundCompat(String text) {
        Intent open = new Intent(this, MainActivity.class);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pending = PendingIntent.getActivity(this, 0, open, flags);
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL)
                : new Notification.Builder(this);
        Notification notification = builder
                .setContentTitle("FlyAI 本地服务")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setOngoing(true)
                .setContentIntent(pending)
                .build();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void stage(String text) {
        try {
            Intent intent = new Intent(ACTION_STATUS);
            intent.setPackage(getPackageName());
            intent.putExtra("text", text);
            sendBroadcast(intent);
        } catch (Exception ignored) {
        }
        try (java.io.FileWriter writer = new java.io.FileWriter(new File(getFilesDir(), "boot.log"), true)) {
            String time = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date());
            writer.write("[" + time + "] " + text + "\n");
        } catch (Exception ignored) {
        }
    }

    static final String ACTION_STATUS = "top.eastcloud.flyai.poc.BOOT_STATUS";

    private void notify(String text) {
        try {
            Intent intent = new Intent(ACTION_STATUS);
            intent.setPackage(getPackageName());
            intent.putExtra("text", text);
            sendBroadcast(intent);
        } catch (Exception ignored) {
        }
        try {
            startForegroundCompat(text);
        } catch (Exception ignored) {
        }
    }

    private void boot() {
        File files = getFilesDir();
        File rootfs = new File(files, "rootfs");
        File prootDir = new File(files, "proot");
        try {
            stage("准备 PRoot 运行环境");
            installProot(prootDir);
            File marker = new File(rootfs, ".installed");
            if (!marker.isFile()) {
                notify("首次启动：正在解压运行环境（约 350MB）…");
                stage("解压 rootfs（约 350MB，首次启动）…");
                deleteRecursive(rootfs);
                try (InputStream payload = getAssets().open("rootfs_payload.bin")) {
                    TarGzExtractor.extract(payload, rootfs);
                }
                if (!marker.createNewFile()) {
                    throw new RuntimeException("无法写入安装标记");
                }
                stage("rootfs 解压完成");
            } else {
                stage("rootfs 已安装，跳过解压");
            }
            prepareDns(rootfs);
            notify("正在启动 FlyAI…");
            stage("启动 Python 服务（PRoot）…");
            startServer(files, rootfs, prootDir);
            readLogTail(files);
            notify("FlyAI 正在运行");
            stage("服务进程已拉起，等待端口 8000 就绪");
        } catch (Throwable error) {
            Log.e(TAG, "boot failed", error);
            notify("启动失败：" + error.getMessage());
            stage("启动失败：" + error);
        } finally {
            starting = false;
        }
    }

    /**
     * 把设备当前 DNS 写进 rootfs 的 /etc/resolv.conf。
     * Android 没有 glibc 可用的 resolv.conf，PRoot 里的 Ubuntu 必须自带一份；
     * 构建机上的集群 DNS（如 10.x）在手机上不可达，会导致 httpx 名称解析失败。
     */
    private void prepareDns(File rootfs) {
        List<String> servers = new ArrayList<>();
        try {
            ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
            Network network = cm == null ? null : cm.getActiveNetwork();
            LinkProperties props = network == null ? null : cm.getLinkProperties(network);
            if (props != null) {
                for (InetAddress address : props.getDnsServers()) {
                    String ip = address.getHostAddress();
                    if (ip != null && !ip.isEmpty() && !servers.contains(ip)) servers.add(ip);
                }
            }
        } catch (Exception ignored) {
        }
        if (servers.isEmpty()) {
            servers.add("223.5.5.5");
            servers.add("119.29.29.29");
        }
        try {
            File etc = new File(rootfs, "etc");
            if (!etc.isDirectory()) etc.mkdirs();
            try (PrintWriter out = new PrintWriter(new File(new File(rootfs, "etc"), "resolv.conf"), "UTF-8")) {
                for (String server : servers) out.println("nameserver " + server);
                out.println("options timeout:2 attempts:2");
            }
            try (PrintWriter out = new PrintWriter(new File(etc, "hosts"), "UTF-8")) {
                out.println("127.0.0.1 localhost");
                out.println("::1 localhost");
            }
            stage("DNS 已写入：" + String.join(", ", servers));
        } catch (Exception e) {
            stage("写入 DNS 失败：" + e);
        }
    }

    private void installProot(File dir) throws Exception {
        if (!dir.isDirectory() && !dir.mkdirs()) {
            throw new RuntimeException("无法创建 proot 目录");
        }
        String[] names = {"proot", "loader", "libtalloc.so.2", "libandroid-shmem.so"};
        for (String name : names) {
            File out = new File(dir, name);
            try (InputStream in = getAssets().open("proot/" + name);
                 FileOutputStream os = new FileOutputStream(out)) {
                copy(in, os);
            }
            out.setReadable(true, true);
            out.setExecutable(true, true);
        }
    }

    private void startServer(File files, File rootfs, File prootDir) throws Exception {
        File loader = new File(prootDir, "loader");
        File proot = new File(prootDir, "proot");
        File tmp = new File(files, "proot-tmp");
        if (!tmp.isDirectory()) tmp.mkdirs();
        File log = new File(files, "server.log");
        if (log.exists()) log.delete();

        ProcessBuilder pb = new ProcessBuilder(
                proot.getAbsolutePath(),
                "--link2symlink",
                "-0",
                "-r", rootfs.getAbsolutePath(),
                "-b", "/dev",
                "-b", "/proc",
                "-b", "/sys",
                "-b", "/proc/self/fd",
                "-w", "/opt/flyai",
                "/usr/bin/env", "-i",
                "HOME=/root",
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "TERM=xterm",
                "PYTHONUNBUFFERED=1",
                "LANG=C.UTF-8",
                "/bin/sh", "/opt/flyai/start.sh"
        );
        pb.directory(files);
        pb.redirectErrorStream(true);
        pb.redirectOutput(ProcessBuilder.Redirect.appendTo(log));
        pb.environment().put("PROOT_LOADER", loader.getAbsolutePath());
        pb.environment().put("PROOT_TMP_DIR", tmp.getAbsolutePath());
        pb.environment().put("PROOT_NO_SECCOMP", "1");
        pb.environment().put("LD_LIBRARY_PATH", prootDir.getAbsolutePath());
        server = pb.start();

        new Thread(() -> {
            try {
                int code = server.waitFor();
                Log.w(TAG, "server exited: " + code);
                notify("FlyAI 已停止（退出码 " + code + "）");
            } catch (InterruptedException ignored) {
            }
        }, "flyai-watch").start();
    }

    private void readLogTail(File files) {
        File log = new File(files, "server.log");
        // 只用于调试：日志留在 filesDir，MainActivity 失败时会展示末尾内容
    }

    private static void copy(InputStream in, OutputStream out) throws Exception {
        byte[] buffer = new byte[1 << 16];
        int n;
        while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
    }

    private static void deleteRecursive(File file) {
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) for (File child : children) deleteRecursive(child);
        }
        //noinspection ResultOfMethodCallIgnored
        file.delete();
    }

    static String tail(File log, int maxBytes) {
        if (!log.isFile()) return "";
        try (RandomAccessFile raf = new RandomAccessFile(log, "r")) {
            long length = raf.length();
            long start = Math.max(0, length - maxBytes);
            raf.seek(start);
            byte[] data = new byte[(int) (length - start)];
            raf.readFully(data);
            return new String(data, "UTF-8");
        } catch (Exception e) {
            return "";
        }
    }

    @Override
    public void onDestroy() {
        if (server != null) server.destroy();
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        super.onDestroy();
    }
}
