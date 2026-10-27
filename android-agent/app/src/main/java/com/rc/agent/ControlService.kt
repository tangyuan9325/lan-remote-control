package com.rc.agent

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import org.json.JSONObject

/**
 * 无障碍手势服务：监听 127.0.0.1:8586，接收电脑下发的 JSON 指令，
 * 通过 dispatchGesture / performGlobalAction 注入触摸与按键。
 *
 * 指令协议（换行分隔 JSON）：
 *   {"type":"tap","x":100,"y":200}
 *   {"type":"swipe","x1":..,"y1":..,"x2":..,"y2":..,"duration":300}
 *   {"type":"key","key":"HOME|BACK|RECENTS|NOTIFICATIONS|QUICK_SETTINGS|POWER_DIALOG"}
 * 应答：{"ok":true} 或 {"ok":false,"error":"..."}
 *
 * 安全：只绑定 127.0.0.1，局域网内其它设备无法直接向本机注入手势。
 */
class ControlService : AccessibilityService() {

    companion object {
        const val CONTROL_PORT = 8586
        private const val TAG = "RCControl"
        // 读空闲超时：半开连接不会永久占住线程
        private const val READ_TIMEOUT_MS = 120_000

        @Volatile
        private var instance: ControlService? = null

        fun isRunning(): Boolean = instance != null

        fun stop() {
            instance?.let {
                it.running = false
                try { it.server?.close() } catch (_: IOException) {}
            }
        }
    }

    @Volatile
    private var running = false
    private var server: ServerSocket? = null
    private var thread: Thread? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        running = true
        thread = Thread {
            try {
                val ss = ServerSocket()
                ss.reuseAddress = true
                ss.bind(InetSocketAddress(InetAddress.getLoopbackAddress(), CONTROL_PORT))
                server = ss
                Log.i(TAG, "控制端口就绪 127.0.0.1:$CONTROL_PORT")
                while (running) {
                    val client = try {
                        ss.accept()
                    } catch (e: IOException) {
                        if (running) Log.w(TAG, "accept 异常: ${e.message}")
                        break
                    }
                    // 每个客户端独立线程：原实现串行处理，旧连接不退出就永远 accept 不到新连接
                    Thread { handleClient(client) }.apply { isDaemon = true }.start()
                }
            } catch (t: Throwable) {
                if (running) Log.w(TAG, "控制服务异常: ${t.message}")
            }
        }.also { it.start() }
    }

    private fun handleClient(client: Socket) {
        try {
            client.soTimeout = READ_TIMEOUT_MS
            client.tcpNoDelay = true
            val reader = BufferedReader(InputStreamReader(client.getInputStream()))
            val out = client.getOutputStream()
            while (running) {
                val line = reader.readLine() ?: break
                val reply = process(line)
                out.write((reply + "\n").toByteArray(Charsets.UTF_8))
                out.flush()
            }
        } catch (e: Exception) {
            if (running) Log.w(TAG, "处理客户端异常: ${e.message}")
        } finally {
            try { client.close() } catch (_: Exception) {}
        }
    }

    private fun process(msg: String): String {
        return try {
            val o = JSONObject(msg)
            when (o.optString("type")) {
                "tap" -> {
                    val ok = gestureTap(o.getInt("x"), o.getInt("y"))
                    ack(ok, if (ok) "" else "手势被系统拒绝（可能有其它手势正在执行）")
                }
                "swipe" -> {
                    val ok = gestureSwipe(
                        o.getInt("x1"), o.getInt("y1"),
                        o.getInt("x2"), o.getInt("y2"),
                        o.optInt("duration", 300)
                    )
                    ack(ok, if (ok) "" else "手势被系统拒绝（可能有其它手势正在执行）")
                }
                "key" -> ack(performKey(o.optString("key")), "unknown key")
                else -> ack(false, "unknown type")
            }
        } catch (e: Exception) {
            // 用 JSONObject 生成错误应答，避免消息里的引号/反斜杠破坏 JSON 结构
            ack(false, e.message ?: "bad request")
        }
    }

    private fun ack(ok: Boolean, err: String): String {
        val o = JSONObject().put("ok", ok)
        if (!ok && err.isNotEmpty()) o.put("error", err)
        return o.toString()
    }

    private fun performKey(key: String): Boolean {
        val g = when (key.uppercase()) {
            "HOME" -> GLOBAL_ACTION_HOME
            "BACK" -> GLOBAL_ACTION_BACK
            "RECENTS" -> GLOBAL_ACTION_RECENTS
            "NOTIFICATIONS" -> GLOBAL_ACTION_NOTIFICATIONS
            "QUICK_SETTINGS" -> GLOBAL_ACTION_QUICK_SETTINGS
            "POWER_DIALOG" -> GLOBAL_ACTION_POWER_DIALOG
            else -> return false
        }
        return performGlobalAction(g)
    }

    /** 屏幕坐标钳制，避免越界坐标导致空手势。 */
    private fun clampX(v: Int): Float = v.coerceIn(0, resources.displayMetrics.widthPixels - 1).toFloat()
    private fun clampY(v: Int): Float = v.coerceIn(0, resources.displayMetrics.heightPixels - 1).toFloat()

    private fun gestureTap(x: Int, y: Int): Boolean {
        val path = Path().apply { moveTo(clampX(x), clampY(y)) }
        val stroke = GestureDescription.StrokeDescription(path, 0, 60)
        return dispatchGesture(GestureDescription.Builder().addStroke(stroke).build(), null, null)
    }

    private fun gestureSwipe(x1: Int, y1: Int, x2: Int, y2: Int, dur: Int): Boolean {
        val path = Path().apply {
            moveTo(clampX(x1), clampY(y1))
            lineTo(clampX(x2), clampY(y2))
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, dur.coerceIn(20, 3000).toLong())
        return dispatchGesture(GestureDescription.Builder().addStroke(stroke).build(), null, null)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // 不需要处理事件
    }

    override fun onInterrupt() {
        // ignore
    }

    override fun onDestroy() {
        running = false
        instance = null
        try { server?.close() } catch (_: IOException) {}
        server = null
        thread?.interrupt()
        thread = null
        super.onDestroy()
    }
}
