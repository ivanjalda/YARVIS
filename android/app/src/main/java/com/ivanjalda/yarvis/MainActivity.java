package com.ivanjalda.yarvis;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** Yarvis: abre el panel privado a pantalla completa, como una app. */
public class MainActivity extends Activity {

    private static final String PANEL = "https://ivanjalda.github.io/mi-panel/";
    private WebView web;

    @Override
    protected void onCreate(Bundle estado) {
        super.onCreate(estado);
        getWindow().setStatusBarColor(Color.parseColor("#0d0f14"));
        getWindow().setNavigationBarColor(Color.parseColor("#0d0f14"));

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0d0f14"));
        WebSettings ajustes = web.getSettings();
        ajustes.setJavaScriptEnabled(true);
        ajustes.setDomStorageEnabled(true);
        ajustes.setDatabaseEnabled(true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView vista, WebResourceRequest peticion) {
                Uri uri = peticion.getUrl();
                if (uri.toString().startsWith(PANEL)) {
                    return false; // el panel se queda dentro de la app
                }
                // Telegram, webs y demás enlaces se abren en su app correspondiente
                startActivity(new Intent(Intent.ACTION_VIEW, uri));
                return true;
            }
        });

        setContentView(web);
        if (estado != null) {
            web.restoreState(estado);
        } else {
            web.loadUrl(PANEL);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle estado) {
        super.onSaveInstanceState(estado);
        web.saveState(estado);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
