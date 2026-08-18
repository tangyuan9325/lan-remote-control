package com.example.remote_control_server

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.WindowManager
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.util.concurrent.CompletableFuture

class MainActivity : FlutterActivity() {
    private val SCREEN_CHANNEL = "com.example.remote_control_server/screen"
    private val INPUT_CHANNEL = "com.example.remote_control_server/input"
    private val DEVICE_CHANNEL = "com.example.remote_control_server/device"

    private var mediaProjectionManager: MediaProjectionManager? = null
    private var projectionFuture: CompletableFuture<Boolean>? = null

    companion object {
        private const val REQUEST_CODE_PERMISSIONS = 1000
        var instance: MainActivity? = null
            private set
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        instance = this
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        mediaProjectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        requestRuntimePermissions()
    }

    private fun requestRuntimePermissions() {
        val perms = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            perms.add(Manifest.permission.FOREGROUND_SERVICE_MEDIA_PROJECTION)
        }
        if (perms.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, perms.toTypedArray(), REQUEST_CODE_PERMISSIONS)
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, SCREEN_CHANNEL).setMethodCallHandler { call, result ->
            when (call.method) {
                "startProjection" -> {
                    try {
                        val intent = mediaProjectionManager?.createScreenCaptureIntent()
                        if (intent != null) {
                            projectionFuture = CompletableFuture()
                            startActivityForResult(intent, 1001)
                            try {
                                val granted = projectionFuture?.get() ?: false
                                if (granted) {
                                    result.success(null)
                                } else {
                                    result.error("DENIED", "User denied screen capture", null)
                                }
                            } catch (e: Exception) {
                                result.error("TIMEOUT", "Screen capture authorization timed out", null)
                            }
                        } else {
                            result.error("UNAVAILABLE", "MediaProjection not available", null)
                        }
                    } catch (e: Exception) {
                        result.error("ERROR", "startProjection failed: ${e.message}", null)
                    }
                }
                "initialize" -> {
                    try {
                        val metrics = resources.displayMetrics
                        val width = metrics.widthPixels
                        val height = metrics.heightPixels
                        val rc = projectionFuture?.let { if (it.isDone) it.get() else false } ?: false
                        if (rc) {
                            ScreenCaptureService.start(this)
                            result.success(mapOf("width" to width, "height" to height))
                        } else {
                            result.error("NOT_GRANTED", "Screen capture not granted", null)
                        }
                    } catch (e: Exception) {
                        result.error("ERROR", "initialize failed: ${e.message}", null)
                    }
                }
                "capture" -> {
                    try {
                        val quality = call.argument<Int>("quality") ?: 70
                        val jpeg = ScreenCaptureService.captureLatest(quality)
                        result.success(jpeg)
                    } catch (e: Exception) {
                        result.success(null)
                    }
                }
                "stop" -> {
                    try { ScreenCaptureService.stop() } catch (_: Exception) {}
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, INPUT_CHANNEL).setMethodCallHandler { call, result ->
            try {
                when (call.method) {
                    "touchDown" -> { val x = call.argument<Int>("x") ?: 0; val y = call.argument<Int>("y") ?: 0; RemoteAccessibilityService.touchDown(x, y); result.success(null) }
                    "touchUp" -> { val x = call.argument<Int>("x") ?: 0; val y = call.argument<Int>("y") ?: 0; RemoteAccessibilityService.touchUp(x, y); result.success(null) }
                    "touchMove" -> { val x = call.argument<Int>("x") ?: 0; val y = call.argument<Int>("y") ?: 0; RemoteAccessibilityService.touchMove(x, y); result.success(null) }
                    "tap" -> { val x = call.argument<Int>("x") ?: 0; val y = call.argument<Int>("y") ?: 0; RemoteAccessibilityService.tap(x, y); result.success(null) }
                    "longPress" -> { val x = call.argument<Int>("x") ?: 0; val y = call.argument<Int>("y") ?: 0; RemoteAccessibilityService.longPress(x, y); result.success(null) }
                    "scroll" -> { val x = call.argument<Int>("x") ?: 0; val y = call.argument<Int>("y") ?: 0; val dx = call.argument<Double>("dx") ?: 0.0; val dy = call.argument<Double>("dy") ?: 0.0; RemoteAccessibilityService.scroll(x, y, dx, dy); result.success(null) }
                    "keyEvent" -> { val key = call.argument<String>("key") ?: ""; val down = call.argument<Boolean>("down") ?: true; RemoteAccessibilityService.keyEvent(key, down); result.success(null) }
                    "typeText" -> { val text = call.argument<String>("text") ?: ""; RemoteAccessibilityService.typeText(text); result.success(null) }
                    "isAccessibilityEnabled" -> { result.success(RemoteAccessibilityService.isEnabled()) }
                    "openAccessibilitySettings" -> { val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS); intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK); startActivity(intent); result.success(null) }
                    else -> result.notImplemented()
                }
            } catch (e: Exception) {
                result.error("ERROR", e.message, null)
            }
        }

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, DEVICE_CHANNEL).setMethodCallHandler { call, result ->
            when (call.method) {
                "getDeviceName" -> result.success(getDeviceName())
                else -> result.notImplemented()
            }
        }
    }

    private fun getDeviceName(): String {
        return try { "${Build.MANUFACTURER} ${Build.MODEL}" } catch (e: Exception) { "Android Device" }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 1001) {
            val granted = resultCode == Activity.RESULT_OK && data != null
            if (granted && data != null) {
                ScreenCaptureService.setProjectionData(resultCode, data)
            }
            projectionFuture?.complete(granted)
            projectionFuture = null
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        try { ScreenCaptureService.stop() } catch (_: Exception) {}
        instance = null
    }
}
