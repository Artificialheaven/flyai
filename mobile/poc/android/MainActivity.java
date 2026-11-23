package top.eastcloud.flyai.poc;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.net.InetSocketAddress;
import java.net.Socket;

/** 本地一体化 POC：前台服务拉起 rootfs 里的 FlyAI，WebView 连 127.0.0.1:8000。 */
public class MainActivity extends Activity {
    private static final int PORT = 8000;
    private static final int FILE_CHOOSER = 1001;

    private WebView webView;
    private TextView status;
    private ValueCallback<Uri[]> fileCallback;
    private final Handler handler = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        FrameLayout root = new FrameLayout(this);
        webView = new WebView(this);
        status = new TextView(this);
        status.setText("正在启动本地服务…");
        status.setTextColor(Color.DKGRAY);
        status.setTextSize(14);
        status.setPadding(32, 32, 32, 32);
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        root.addView(status, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(root);

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

        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS}, 2001);
        }
        startServerService();
        waitForServer(0);
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
                    status.setVisibility(View.GONE);
                    webView.setVisibility(View.VISIBLE);
                    webView.loadUrl("http://127.0.0.1:" + PORT + "/");
                } else if (attempt < 600) {
                    status.setText("正在启动本地服务…（约 " + (attempt / 2 + 1) + " 秒）");
                    handler.postDelayed(() -> waitForServer(attempt + 1), 500);
                } else {
                    String log = BootService.tail(new File(getFilesDir(), "server.log"), 4000);
                    status.setText("本地服务启动失败。日志：\n" + log);
                }
            });
        }, "flyai-probe").start();
    }

    private boolean probe() {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("127.0.0.1", PORT), 400);
            return true;
        } catch (Exception e) {
            return false;
        }
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
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
