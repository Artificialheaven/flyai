package top.eastcloud.flyai.poc;

import android.content.Context;
import android.content.pm.PackageInfo;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;

/** rootfs 代码版本状态（.flyai-state.json），以及 pending/backup 的清理。 */
final class OtaState {
    private OtaState() {}

    private static final Object LOCK = new Object();

    static File stateFile(File filesDir) {
        return new File(new File(filesDir, "rootfs"), ".flyai-state.json");
    }

    static JSONObject read(File filesDir) {
        synchronized (LOCK) {
            return readLocked(filesDir);
        }
    }

    private static JSONObject readLocked(File filesDir) {
        File file = stateFile(filesDir);
        if (file.isFile() && file.length() < 256 * 1024) {
            try {
                byte[] data = new byte[(int) file.length()];
                try (RandomAccessFile raf = new RandomAccessFile(file, "r")) {
                    raf.readFully(data);
                }
                return new JSONObject(new String(data, StandardCharsets.UTF_8));
            } catch (Exception ignored) {
                // 损坏时重建
            }
        }
        return migrateLocked(filesDir);
    }

    private static JSONObject migrateLocked(File filesDir) {
        JSONObject state = defaults();
        File rootfs = new File(filesDir, "rootfs");
        File marker = new File(rootfs, ".installed");
        if (marker.isFile() && marker.length() < 4096) {
            try {
                byte[] data = new byte[(int) marker.length()];
                try (RandomAccessFile raf = new RandomAccessFile(marker, "r")) {
                    raf.readFully(data);
                }
                state.put("baseRev", new String(data, StandardCharsets.UTF_8).trim());
                state.put("requirementsSha256", requirementsSha256(rootfs));
            } catch (Exception ignored) {
            }
        }
        writeLocked(filesDir, state);
        return state;
    }

    static JSONObject defaults() {
        JSONObject state = new JSONObject();
        try {
            state.put("schema", 1);
            state.put("baseRev", "");
            state.put("codeRev", "");
            state.put("overlayVersion", 0L);
            state.put("requirementsSha256", "");
            state.put("appVersionCode", 0L);
            state.put("applying", JSONObject.NULL);
            state.put("backupDir", "");
            state.put("prevCodeRev", "");
            state.put("prevOverlayVersion", 0L);
            state.put("boot", new JSONObject().put("attempts", 0));
            state.put("lastRollback", JSONObject.NULL);
            state.put("lastCheckAt", 0L);
        } catch (Exception ignored) {
        }
        return state;
    }

    static void write(File filesDir, JSONObject state) {
        synchronized (LOCK) {
            writeLocked(filesDir, state);
        }
    }

    private static void writeLocked(File filesDir, JSONObject state) {
        File file = stateFile(filesDir);
        File parent = file.getParentFile();
        if (parent != null && !parent.isDirectory()) parent.mkdirs();
        File tmp = new File(file.getPath() + ".tmp");
        try (FileOutputStream out = new FileOutputStream(tmp)) {
            out.write(state.toString(2).getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            throw new RuntimeException("写入更新状态失败", e);
        }
        if (!tmp.renameTo(file)) {
            // Windows/部分文件系统 rename 覆盖已存在文件会失败
            //noinspection ResultOfMethodCallIgnored
            file.delete();
            if (!tmp.renameTo(file)) {
                throw new RuntimeException("替换更新状态失败");
            }
        }
    }

    static long versionCode(Context context) {
        try {
            PackageInfo info = context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0);
            return info.versionCode;
        } catch (Exception e) {
            return 0;
        }
    }

    static String requirementsSha256(File rootfs) {
        File req = new File(rootfs, "opt/flyai/requirements.txt");
        if (!req.isFile()) return "";
        try {
            byte[] data = new byte[(int) req.length()];
            try (RandomAccessFile raf = new RandomAccessFile(req, "r")) {
                raf.readFully(data);
            }
            String text = new String(data, StandardCharsets.UTF_8)
                    .replace("\r\n", "\n")
                    .trim();
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] out = digest.digest(text.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : out) {
                sb.append(String.format(Locale.US, "%02x", b & 0xff));
            }
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    /** 前端挂载成功后调用：清掉启动看门狗，并删除已生效的 backup/pending。 */
    static void markReady(File filesDir) {
        synchronized (LOCK) {
            JSONObject state = readLocked(filesDir);
            try {
                JSONObject boot = state.optJSONObject("boot");
                if (boot == null) boot = new JSONObject();
                boot.put("attempts", 0);
                state.put("boot", boot);
                state.put("applying", JSONObject.NULL);
                state.put("backupDir", "");
                state.put("prevCodeRev", "");
                state.put("prevOverlayVersion", 0);
                writeLocked(filesDir, state);
            } catch (Exception ignored) {
            }
            // 只删已生效版本的备份；pending 可能刚下载、尚未应用，不能在这里删
            deleteRecursive(new File(filesDir, "update/backup"));
        }
    }

    static void deleteRecursive(File file) {
        if (file == null || !file.exists()) return;
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) {
                for (File child : children) deleteRecursive(child);
            }
        }
        //noinspection ResultOfMethodCallIgnored
        file.delete();
    }
}
