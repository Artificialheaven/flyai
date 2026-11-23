package top.eastcloud.flyai.poc;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.CharsetDecoder;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.HashMap;
import java.util.Map;
import java.util.zip.GZIPInputStream;

/**
 * 最小 tar.gz 解包器：Android 上的 toybox tar 版本不一致，直接依赖不可靠。
 * 支持 ustar 常规文件/目录/软链/硬链，以及 pax('x') 与 GNU longname('L')。
 */
final class TarGzExtractor {
    private TarGzExtractor() {}

    static void extract(File archive, File destRoot) throws IOException {
        try (InputStream raw = new BufferedInputStream(new FileInputStream(archive), 1 << 16)) {
            extract(raw, destRoot);
        }
    }

    /** 直接从 AssetManager 的流解包，避免把 100MB 资源再复制一份到私有目录。 */
    static void extract(InputStream input, File destRoot) throws IOException {
        Path root = destRoot.toPath().toAbsolutePath().normalize();
        Files.createDirectories(root);
        try (InputStream raw = new BufferedInputStream(input, 1 << 16);
             GZIPInputStream gz = new GZIPInputStream(raw, 1 << 16)) {
            byte[] header = new byte[512];
            String paxPath = null;
            String paxLink = null;
            String longName = null;
            String longLink = null;
            while (true) {
                int read = readFully(gz, header, 512);
                if (read < 512) break;
                boolean zero = true;
                for (byte b : header) {
                    if (b != 0) { zero = false; break; }
                }
                if (zero) break;

                String name = cstr(header, 0, 100);
                String prefix = cstr(header, 345, 155);
                if (!prefix.isEmpty()) name = prefix + "/" + name;
                long size = octal(header, 124, 12);
                char type = (char) (header[156] & 0xff);
                if (type == 0) type = '0';
                String link = cstr(header, 157, 100);
                int mode = (int) octal(header, 100, 8);

                if (type == 'x' || type == 'g') {
                    byte[] data = readData(gz, size);
                    Map<String, String> attrs = parsePax(data);
                    if ("x".equals(String.valueOf(type))) {
                        if (attrs.containsKey("path")) paxPath = attrs.get("path");
                        if (attrs.containsKey("linkpath")) paxLink = attrs.get("linkpath");
                    }
                    skipPadding(gz, size);
                    continue;
                }
                if (type == 'L') {
                    longName = new String(readData(gz, size), StandardCharsets.UTF_8).replace("\0", "");
                    skipPadding(gz, size);
                    continue;
                }
                if (type == 'K') {
                    longLink = new String(readData(gz, size), StandardCharsets.UTF_8).replace("\0", "");
                    skipPadding(gz, size);
                    continue;
                }

                String entryName = paxPath != null ? paxPath : (longName != null ? longName : name);
                String entryLink = paxLink != null ? paxLink : (longLink != null ? longLink : link);
                paxPath = null;
                paxLink = null;
                longName = null;
                longLink = null;

                Path target = safeResolve(root, entryName);
                if (target == null) {
                    skipData(gz, size);
                    continue;
                }
                if (type == '5') {
                    Files.createDirectories(target);
                } else if (type == '2') {
                    Files.createDirectories(target.getParent());
                    if (Files.exists(target, LinkOption.NOFOLLOW_LINKS)) Files.delete(target);
                    try {
                        Files.createSymbolicLink(target, Paths.get(entryLink));
                    } catch (Exception ignored) {
                        // 某些文件系统不允许软链：退化为普通文件（极少见）
                        Files.createFile(target);
                    }
                } else if (type == '1') {
                    // 硬链：tar 里的 linkname 是归档内相对路径
                    Path source = safeResolve(root, entryLink);
                    Files.createDirectories(target.getParent());
                    if (source != null && Files.exists(source)) {
                        try {
                            Files.createLink(target, source);
                        } catch (Exception e) {
                            Files.copy(source, target, StandardCopyOption.REPLACE_EXISTING);
                        }
                    }
                    skipData(gz, size);
                } else if (type == '0') {
                    if (target.equals(root) || Files.isDirectory(target, LinkOption.NOFOLLOW_LINKS)) {
                        skipData(gz, size);
                        skipPadding(gz, size);
                        continue;
                    }
                    Files.createDirectories(target.getParent());
                    try (FileOutputStream out = new FileOutputStream(target.toFile())) {
                        copyN(gz, out, size);
                    }
                    applyMode(target.toFile(), mode);
                } else {
                    skipData(gz, size);
                }
                skipPadding(gz, size);
            }
        }
    }

    private static Path safeResolve(Path root, String name) {
        String cleaned = name.replace('\\', '/');
        while (cleaned.startsWith("./")) cleaned = cleaned.substring(2);
        if (cleaned.startsWith("/")) cleaned = cleaned.substring(1);
        Path resolved = root.resolve(cleaned).normalize();
        if (!resolved.startsWith(root)) return null;
        return resolved;
    }

    private static void applyMode(File file, int mode) {
        boolean exec = (mode & 0111) != 0;
        file.setExecutable(exec, false);
        file.setReadable(true, false);
        file.setWritable((mode & 0200) != 0, true);
    }

    private static int readFully(InputStream in, byte[] buf, int len) throws IOException {
        int off = 0;
        while (off < len) {
            int n = in.read(buf, off, len - off);
            if (n < 0) return off;
            off += n;
        }
        return off;
    }

    private static byte[] readData(InputStream in, long size) throws IOException {
        if (size <= 0) return new byte[0];
        byte[] data = new byte[(int) size];
        int off = 0;
        while (off < size) {
            int n = in.read(data, off, (int) size - off);
            if (n < 0) break;
            off += n;
        }
        return data;
    }

    private static void copyN(InputStream in, FileOutputStream out, long size) throws IOException {
        byte[] buf = new byte[1 << 16];
        long left = size;
        while (left > 0) {
            int n = in.read(buf, 0, (int) Math.min(buf.length, left));
            if (n < 0) break;
            out.write(buf, 0, n);
            left -= n;
        }
    }

    private static void skipData(InputStream in, long size) throws IOException {
        long left = size;
        byte[] buf = new byte[8192];
        while (left > 0) {
            int n = in.read(buf, 0, (int) Math.min(buf.length, left));
            if (n < 0) break;
            left -= n;
        }
    }

    private static void skipPadding(InputStream in, long size) throws IOException {
        long pad = (512 - (size % 512)) % 512;
        skipData(in, pad);
    }

    private static String cstr(byte[] buf, int off, int len) {
        int end = off;
        while (end < off + len && buf[end] != 0) end++;
        return decodeName(buf, off, end - off).trim();
    }

    /** tar 文件名按字节存储；非 UTF-8 时退回 ISO-8859-1，保证路径唯一且合法。 */
    private static String decodeName(byte[] buf, int off, int len) {
        try {
            CharsetDecoder decoder = StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT);
            return decoder.decode(ByteBuffer.wrap(buf, off, len)).toString();
        } catch (Exception e) {
            return new String(buf, off, len, StandardCharsets.ISO_8859_1);
        }
    }

    private static long octal(byte[] buf, int off, int len) {
        String value = cstr(buf, off, len).trim();
        if (value.isEmpty()) return 0;
        try {
            return Long.parseLong(value, 8);
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private static Map<String, String> parsePax(byte[] data) {
        Map<String, String> out = new HashMap<>();
        String text = new String(data, StandardCharsets.UTF_8);
        int index = 0;
        while (index < text.length()) {
            int space = text.indexOf(' ', index);
            if (space <= index) break;
            int len;
            try {
                len = Integer.parseInt(text.substring(index, space));
            } catch (NumberFormatException e) {
                break;
            }
            if (len <= 0 || index + len > text.length()) break;
            String record = text.substring(space + 1, index + len).trim();
            int eq = record.indexOf('=');
            if (eq > 0) out.put(record.substring(0, eq), record.substring(eq + 1));
            index += len;
        }
        return out;
    }
}
