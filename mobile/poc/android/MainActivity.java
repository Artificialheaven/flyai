package top.eastcloud.flyai.poc;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.net.InetSocketAddress;
import java.net.Socket;

/** 本地一体化 POC：前台服务拉起 rootfs 里的 FlyAI，WebView 连 127.0.0.1:8000。 */
public class MainActivity extends Activity {
    private static final int PORT = 8000;
    private static final int FILE_CHOOSER = 1001;
    private static final long CONSOLE_INTERVAL = 700;

    private WebView webView;
    private LinearLayout splash;
    private ProgressBar spinner;
    private TextView status;
    private TextView hint;
    private TextView console;
    private ScrollView consoleScroll;
    private Button retry;
    private ValueCallback<Uri[]> fileCallback;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean polling;

    private final Runnable consoleTick = new Runnable() {
        @Override
        public void run() {
            if (!polling) return;
            refreshConsole();
            handler.postDelayed(this, CONSOLE_INTERVAL);
        }
    };

    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String text = intent.getStringExtra("text");
            if (text != null && status != null && splash.getVisibility() == View.VISIBLE) {
                status.setText(text);
                refreshConsole();
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().setStatusBarColor(Color.parseColor("#0F1115"));
        getWindow().setNavigationBarColor(Color.parseColor("#0F1115"));

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#0F1115"));

        webView = new WebView(this);
        webView.setVisibility(View.INVISIBLE);
        webView.setBackgroundColor(Color.WHITE);
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        splash = buildSplash();
        root.addView(splash, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        configureWebView();
        startServerService();
        startConsole();
        waitForServer(0);
    }

    /** 首启页：上半品牌/进度，下半实时控制台；失败时同一页给出日志与重试。 */
    private LinearLayout buildSplash() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Color.parseColor("#0F1115"));
        page.setFitsSystemWindows(true);
        page.setPadding(dp(24), dp(24), dp(24), dp(16));

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setGravity(Gravity.CENTER);
        page.addView(header, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        TextView brand = new TextView(this);
        brand.setText("FlyAI");
        brand.setTextColor(Color.WHITE);
        brand.setTextSize(32);
        brand.setTypeface(Typeface.DEFAULT_BOLD);
        brand.setGravity(Gravity.CENTER);
        header.addView(brand);

        TextView sub = new TextView(this);
        sub.setText("本地服务 · 数据保存在本机");
        sub.setTextColor(Color.parseColor("#9AA4B2"));
        sub.setTextSize(12);
        sub.setGravity(Gravity.CENTER);
        header.addView(sub, marginTop(dp(6)));

        spinner = new ProgressBar(this);
        header.addView(spinner, marginTop(dp(28)));
        spinner.getLayoutParams().width = dp(40);
        spinner.getLayoutParams().height = dp(40);

        status = new TextView(this);
        status.setText("正在启动本地服务…");
        status.setTextColor(Color.parseColor("#E6EAF0"));
        status.setTextSize(14);
        status.setGravity(Gravity.CENTER);
        header.addView(status, marginTop(dp(14)));

        hint = new TextView(this);
        hint.setText("首次启动需解压运行环境（约 350MB），请保持前台等待");
        hint.setTextColor(Color.parseColor("#7C8794"));
        hint.setTextSize(11);
        hint.setGravity(Gravity.CENTER);
        header.addView(hint, marginTop(dp(8)));

        retry = new Button(this);
        retry.setText("重试启动");
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(v -> {
            retry.setVisibility(View.GONE);
            spinner.setVisibility(View.VISIBLE);
            status.setTextColor(Color.parseColor("#E6EAF0"));
            status.setText("正在重新启动本地服务…");
            startServerService();
            waitForServer(0);
        });
        header.addView(retry, marginTop(dp(14)));

        // ---- 实时控制台 ----
        LinearLayout consoleBox = new LinearLayout(this);
        consoleBox.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.parseColor("#151A21"));
        bg.setCornerRadius(dp(10));
        bg.setStroke(dp(1), Color.parseColor("#232A34"));
        consoleBox.setBackground(bg);
        consoleBox.setPadding(dp(12), dp(10), dp(12), dp(8));

        TextView title = new TextView(this);
        title.setText("启动控制台 · 实时");
        title.setTextColor(Color.parseColor("#8B96A5"));
        title.setTextSize(10);
        consoleBox.addView(title);

        consoleScroll = new ScrollView(this);
        consoleScroll.setVerticalScrollBarEnabled(true);
        console = new TextView(this);
        console.setTextColor(Color.parseColor("#9FE3B0"));
        console.setTextSize(9);
        console.setTypeface(Typeface.MONOSPACE);
        console.setText("等待服务输出…");
        consoleScroll.addView(console, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        LinearLayout.LayoutParams scrollLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(190));
        scrollLp.topMargin = dp(6);
        consoleBox.addView(consoleScroll, scrollLp);

        page.addView(consoleBox, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return page;
    }

    private LinearLayout.LayoutParams marginTop(int px) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = px;
        lp.gravity = Gravity.CENTER_HORIZONTAL;
        return lp;
    }

    private void startConsole() {
        polling = true;
        handler.post(consoleTick);
    }

    private void stopConsole() {
        polling = false;
        handler.removeCallbacks(consoleTick);
    }

    private void refreshConsole() {
        File files = getFilesDir();
        StringBuilder text = new StringBuilder();
        String boot = readText(new File(files, "boot.log"), 4000);
        if (!boot.isEmpty()) text.append(boot);
        String server = BootService.tail(new File(files, "server.log"), 4000);
        if (!server.isEmpty()) {
            if (text.length() > 0 && !text.toString().endsWith("\n")) text.append('\n');
            text.append("---- server.log ----\n").append(server);
        }
        if (text.length() == 0) return;
        String current = console.getText().toString();
        String next = text.toString();
        if (!next.equals(current)) {
            console.setText(next);
            consoleScroll.post(() -> consoleScroll.fullScroll(View.FOCUS_DOWN));
        }
    }

    private static String readText(File file, int maxBytes) {
        if (!file.isFile()) return "";
        try {
            if (file.length() > maxBytes) {
                return BootService.tail(file, maxBytes);
            }
            byte[] data = new byte[(int) file.length()];
            try (java.io.FileInputStream in = new java.io.FileInputStream(file)) {
                int off = 0;
                while (off < data.length) {
                    int n = in.read(data, off, data.length - off);
                    if (n < 0) break;
                    off += n;
                }
                return new String(data, 0, off, "UTF-8");
            }
        } catch (Exception e) {
            return "";
        }
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setMediaPlaybackRequiresUserGesture(false);
        if (Build.VERSION.SDK_INT >= 21) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
        if (Build.VERSION.SDK_INT >= 19) {
            WebView.setWebContentsDebuggingEnabled(true);
        }
        webView.setWebViewClient(new WebViewClient());
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("*/*");
                try {
                    startActivityForResult(Intent.createChooser(intent, "选择角色卡文件"), FILE_CHOOSER);
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });
    }

    private void startServerService() {
        Intent service = new Intent(this, BootService.class);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(service);
        } else {
            startService(service);
        }
    }

    private void waitForServer(int attempt) {
        new Thread(() -> {
            boolean ready = probe();
            handler.post(() -> {
                if (ready) {
                    showWebView();
                } else if (attempt < 600) {
                    int seconds = attempt / 2;
                    status.setText("正在启动本地服务… " + seconds + "s");
                    if (seconds > 20) {
                        hint.setText("首次启动解压较慢属正常现象，请继续等待");
                    }
                    handler.postDelayed(() -> waitForServer(attempt + 1), 500);
                } else {
                    showFailure();
                }
            });
        }, "flyai-probe").start();
    }

    private void showWebView() {
        stopConsole();
        splash.animate().alpha(0f).setDuration(220).withEndAction(() -> {
            splash.setVisibility(View.GONE);
        }).start();
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl("http://127.0.0.1:" + PORT + "/");
    }

    private void showFailure() {
        spinner.setVisibility(View.GONE);
        status.setTextColor(Color.parseColor("#FF8A80"));
        status.setText("本地服务启动失败");
        hint.setText("可先重试；仍失败请把控制台日志发给我");
        retry.setVisibility(View.VISIBLE);
        refreshConsole();
    }

    private boolean probe() {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("127.0.0.1", PORT), 400);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onStart() {
        super.onStart();
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(statusReceiver, new IntentFilter(BootService.ACTION_STATUS), Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(statusReceiver, new IntentFilter(BootService.ACTION_STATUS));
        }
    }

    @Override
    protected void onStop() {
        try {
            unregisterReceiver(statusReceiver);
        } catch (Exception ignored) {
        }
        super.onStop();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_CHOOSER) {
            Uri[] result = null;
            if (resultCode == RESULT_OK && data != null && data.getData() != null) {
                result = new Uri[]{data.getData()};
            }
            if (fileCallback != null) {
                fileCallback.onReceiveValue(result);
                fileCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onDestroy() {
        stopConsole();
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
