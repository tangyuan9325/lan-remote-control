package com.example.remote_control_server

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.WindowManager
import androidx.core.content.ContextCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.net.NetworkInterface

class MainActivity : FlutterActivity() {
    private val SCREEN_CHANNEL = "com.example.remote_control_server/screen"
    private val INPUT_CHANNEL = "com.example.remote_control_server/input"
    private val DEVICE_CHANNEL = "com.example.remote_control_server/device"

    private var mediaProjectionManager: MediaProjectionManager? = null
    private var projectionIntent: Intent? = null
    private var resultCode = Activity.RESULT_CANCELED

    companion object {
        private const val REQUEST_CODE_SCREEN_CAPTURE = 1001
        var instance: MainActivity? = null
            private set
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        instance = this
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        mediaProjectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        // Screen capture channel
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, SCREEN_CHANNEL).setMethodCallHandler { call, result ->
            when (call.method) {
                "startProjection" -> {
                    val intent = mediaProjectionManager?.createScreenCaptureIntent()
                    if (intent != null) {
                        startActivityForResult(intent, REQUEST_CODE_SCREEN_CAPTURE)
                        result.success(null)
                    } else {
                        result.error("UNAVAILABLE", "MediaProjection not available", null)
                    }
                }
                "initialize" -> {
                    if (projectionIntent != null && resultCode == Activity.RESULT_OK) {
                        val metrics = resources.displayMetrics
                        ScreenCaptureService.start(this, resultCode, projectionIntent!!)
                        result.success(mapOf(
                            "width" to metrics.widthPixels,
                            "height" to metrics.heightPixels
                        ))
                    } else {
                        result.error("NOT_GRANTED", "Screen capture permission not granted", null)
                    }
                }
                "capture" -> {
                    val quality = call.argument<Int>("quality") ?: 70
                    val jpeg = ScreenCaptureService.captureLatest(quality)
                    if (jpeg != null) {
                        result.success(jpeg)
                    } else {
                        result.success(null)
                    }
                }
                "stop" -> {
                    ScreenCaptureService.stop()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }

        // Input simulation channel
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, INPUT_CHANNEL).setMethodCallHandler { call, result ->
            when (call.method) {
                "touchDown" -> {
                    val x = call.argument<Int>("x") ?: 0
                    val y = call.argument<Int>("y") ?: 0
                    RemoteAccessibilityService.touchDown(x, y)
                    result.success(null)
                }
                "touchUp" -> {
                    val x = call.argument<Int>("x") ?: 0
                    val y = call.argument<Int>("y") ?: 0
                    RemoteAccessibilityService.touchUp(x, y)
                    result.success(null)
                }
                "touchMove" -> {
                    val x = call.argument<Int>("x") ?: 0
                    val y = call.argument<Int>("y") ?: 0
                    RemoteAccessibilityService.touchMove(x, y)
                    result.success(null)
                }
                "tap" -> {
                    val x = call.argument<Int>("x") ?: 0
                    val y = call.argument<Int>("y") ?: 0
                    RemoteAccessibilityService.tap(x, y)
                    result.success(null)
                }
                "longPress" -> {
                    val x = call.argument<Int>("x") ?: 0
                    val y = call.argument<Int>("y") ?: 0
                    RemoteAccessibilityService.longPress(x, y)
                    result.success(null)
                }
                "scroll" -> {
                    val x = call.argument<Int>("x") ?: 0
                    val y = call.argument<Int>("y") ?: 0
                    val dx = call.argument<Double>("dx") ?: 0.0
                    val dy = call.argument<Double>("dy") ?: 0.0
                    RemoteAccessibilityService.scroll(x, y, dx, dy)
                    result.success(null)
                }
                "keyEvent" -> {
                    val key = call.argument<String>("key") ?: ""
                    val down = call.argument<Boolean>("down") ?: true
                    RemoteAccessibilityService.keyEvent(key, down)
                    result.success(null)
                }
                "typeText" -> {
                    val text = call.argument<String>("text") ?: ""
                    RemoteAccessibilityService.typeText(text)
                    result.success(null)
                }
                "isAccessibilityEnabled" -> {
                    result.success(RemoteAccessibilityService.isEnabled())
                }
                "openAccessibilitySettings" -> {
                    val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    startActivity(intent)
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }

        // Device info channel
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, DEVICE_CHANNEL).setMethodCallHandler { call, result ->
            when (call.method) {
                "getDeviceName" -> {
                    result.success(getDeviceName())
                }
                else -> result.notImplemented()
            }
        }
    }

    private fun getDeviceName(): String {
        return try {
            val model = Build.MODEL
            val manufacturer = Build.MANUFACTURER
            "$manufacturer $model"
        } catch (e: Exception) {
            "Android Device"
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_CODE_SCREEN_CAPTURE) {
            this.resultCode = resultCode
            this.projectionIntent = data
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        ScreenCaptureService.stop()
        instance = null
    }
}
