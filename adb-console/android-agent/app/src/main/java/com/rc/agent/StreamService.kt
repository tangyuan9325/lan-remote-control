package com.rc.agent

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.DisplayMetrics
import android.util.Log
import android.view.WindowManager
import java.io.BufferedOutputStream
import java.io.DataOutputStream
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.ByteBuffer
import java.util.concurrent.ArrayBlockingQueue

/**
 * 屏幕串流服务：MediaProjection + MediaCodec(H.264)，
 * 通过 ServerSocket 输出「4字节大端长度 + NAL 数据」。
 * 首个 '{' 开头的载荷为 JSON 配置消息。
 *
 * 安全：只绑定 127.0.0.1，局域网内其它设备无法直连；
 * 电脑端必须经 `adb forward` 才能访问（adb 通道本身已有 USB/授权保护）。
 *
 * 健壮性：
 *  - accept 循环：客户端断开后继续等待下一个连接，不需要重新授权录屏；
 *  - 注册 MediaProjection.Callback：用户点“停止共享”时能正确收尾；
 *  - 编码线程与发送线程解耦，队列只保留最新帧，慢客户端不会把延迟越拖越高。
 */
class StreamService : Service() {

    companion object {
        const val EXTRA_RESULT_CODE = "resultCode"
        const val EXTRA_RESULT_DATA = "data"
        const val VIDEO_PORT = 8585
        private const val CHANNEL_ID = "stream_channel"
        private const val NOTIF_ID = 1001
        private const val TAG = "RCStream"
        private const val BIT_RATE = 8_000_000
        private const val FPS = 30
        private const val SEND_QUEUE = 2

        @Volatile
        private var runningFlag = false
        fun isRunning(): Boolean = runningFlag
    }

    private var projection: MediaProjection? = null
    private var codec: MediaCodec? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var serverSocket: ServerSocket? = null
    private var thread: Thread? = null
    private val frameQueue = ArrayBlockingQueue<ByteArray>(SEND_QUEUE)

    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() {
            Log.i(TAG, "MediaProjection 被系统/用户停止")
            runningFlag = false
            stopSelf()
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, Activity.RESULT_CANCELED)
        val data: Intent? = if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(EXTRA_RESULT_DATA)
        }
        if (resultCode == Activity.RESULT_CANCELED || data == null) {
            stopSelf()
            return START_NOT_STICKY
        }

        // 重复点击“开始串流”时不重复起服务
        if (runningFlag) {
            Log.i(TAG, "串流服务已在运行")
            return START_NOT_STICKY
        }

        val notif = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("屏幕串流中")
            .setContentText("仅监听 127.0.0.1:$VIDEO_PORT（需 adb forward）")
            .setSmallIcon(android.R.drawable.ic_media_play)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(NOTIF_ID, notif)
        }

        val mpm = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val mp = mpm.getMediaProjection(resultCode, data)
        if (mp == null) {
            Log.w(TAG, "getMediaProjection 返回 null")
            stopSelf()
            return START_NOT_STICKY
        }
        // Android 14 起要求在 createVirtualDisplay 之前注册回调
        mp.registerCallback(projectionCallback, Handler(Looper.getMainLooper()))
        projection = mp
        runningFlag = true
        startServer()
        return START_NOT_STICKY
    }

    private fun startServer() {
        thread = Thread {
            try {
                val ss = ServerSocket()
                ss.reuseAddress = true
                // 只绑回环：Android 7+ 也要求应用不要监听 0.0.0.0
                ss.bind(InetSocketAddress(InetAddress.getLoopbackAddress(), VIDEO_PORT))
                serverSocket = ss
                Log.i(TAG, "等待客户端连接 127.0.0.1:$VIDEO_PORT")
                while (runningFlag) {
                    val client = try {
                        ss.accept()
                    } catch (e: IOException) {
                        if (runningFlag) Log.w(TAG, "accept 异常: ${e.message}")
                        break
                    }
                    Log.i(TAG, "客户端已连接: ${client.remoteSocketAddress}")
                    try {
                        stream(client)
                    } catch (t: Throwable) {
                        // 原实现只 catch IOException，编码器异常会让线程直接崩溃
                        Log.w(TAG, "串流异常: ${t.message}")
                    } finally {
                        try { client.close() } catch (_: Exception) {}
                        frameQueue.clear()
                        Log.i(TAG, "客户端断开，等待下一个连接")
                    }
                }
            } catch (t: Throwable) {
                Log.w(TAG, "串流服务异常: ${t.message}")
            } finally {
                runningFlag = false
                try { serverSocket?.close() } catch (_: Exception) {}
                serverSocket = null
                stopSelf()
            }
        }.also { it.start() }
    }

    private fun stream(client: Socket) {
        client.tcpNoDelay = true
        val out = DataOutputStream(BufferedOutputStream(client.getOutputStream(), 256 * 1024))

        val enc = createEncoder()
        codec = enc
        val inputSurface = enc.createInputSurface()
        val fmt = enc.inputFormat
        val w = fmt.getInteger(MediaFormat.KEY_WIDTH)
        val h = fmt.getInteger(MediaFormat.KEY_HEIGHT)

        // 先发配置消息
        val cfg = "{\"type\":\"config\",\"w\":$w,\"h\":$h}".toByteArray(Charsets.UTF_8)
        out.write(intLen(cfg.size))
        out.write(cfg)
        out.flush()

        enc.start()
        // 立刻请求一个关键帧：否则新客户端要等最多 1 秒（I 帧间隔）才有画面
        requestKeyFrame(enc)

        virtualDisplay = projection?.createVirtualDisplay(
            "RCStream",
            w, h,
            resources.displayMetrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            inputSurface,
            null,
            null
        )

        // 发送线程：编码线程只负责往队列里塞最新帧，网络慢时丢帧而不是堆积
        val writer = Thread {
            try {
                while (runningFlag) {
                    val bytes = frameQueue.take()
                    out.write(intLen(bytes.size))
                    out.write(bytes)
                    out.flush()
                }
            } catch (e: InterruptedException) {
                // 正常退出
            } catch (e: IOException) {
                Log.i(TAG, "客户端断开: ${e.message}")
            }
        }
        writer.isDaemon = true
        writer.start()

        val info = MediaCodec.BufferInfo()
        try {
            while (runningFlag && !Thread.currentThread().isInterrupted) {
                val idx = enc.dequeueOutputBuffer(info, 10000)
                when {
                    idx == MediaCodec.INFO_TRY_AGAIN_LATER -> Unit
                    idx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                        val f = enc.outputFormat
                        Log.i(TAG, "编码格式: ${f.getInteger(MediaFormat.KEY_WIDTH)}x${f.getInteger(MediaFormat.KEY_HEIGHT)}")
                    }
                    idx >= 0 -> {
                        val buf = enc.getOutputBuffer(idx)
                        if (buf == null) {
                            enc.releaseOutputBuffer(idx, false)
                        } else {
                            buf.position(info.offset)
                            buf.limit(info.offset + info.size)
                            val bytes = ByteArray(info.size)
                            buf.get(bytes)
                            enc.releaseOutputBuffer(idx, false)
                            offer(bytes)
                            if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) break
                        }
                    }
                }
            }
        } finally {
            writer.interrupt()
            try { enc.stop() } catch (_: Exception) {}
            try { enc.release() } catch (_: Exception) {}
            codec = null
            try { virtualDisplay?.release() } catch (_: Exception) {}
            virtualDisplay = null
        }
    }

    /** 入队最新帧，队列满时丢弃最旧的帧。 */
    private fun offer(bytes: ByteArray) {
        if (!frameQueue.offer(bytes)) {
            frameQueue.poll()
            frameQueue.offer(bytes)
        }
    }

    private fun requestKeyFrame(enc: MediaCodec) {
        try {
            val params = Bundle()
            params.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0)
            enc.setParameters(params)
        } catch (e: Exception) {
            Log.w(TAG, "请求关键帧失败: ${e.message}")
        }
    }

    private fun intLen(n: Int): ByteArray = ByteBuffer.allocate(4).putInt(n).array()

    private fun createEncoder(): MediaCodec {
        val wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        wm.defaultDisplay.getRealMetrics(metrics)
        // 有些机型会返回 0，避免用 0 尺寸配置编码器
        val w = if (metrics.widthPixels > 0) metrics.widthPixels else 1080
        val h = if (metrics.heightPixels > 0) metrics.heightPixels else 1920
        Log.i(TAG, "采集分辨率: ${w}x$h")

        val fmt = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, w, h)
        fmt.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
        fmt.setInteger(MediaFormat.KEY_BIT_RATE, BIT_RATE)
        fmt.setInteger(MediaFormat.KEY_FRAME_RATE, FPS)
        fmt.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
        fmt.setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR)
        val enc = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        enc.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        return enc
    }

    private fun createChannel() {
        val ch = NotificationChannel(CHANNEL_ID, "屏幕串流", NotificationManager.IMPORTANCE_LOW)
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(ch)
    }

    override fun onDestroy() {
        runningFlag = false
        try { serverSocket?.close() } catch (_: Exception) {}
        serverSocket = null
        thread?.interrupt()
        thread = null
        try { virtualDisplay?.release() } catch (_: Exception) {}
        virtualDisplay = null
        try { codec?.stop() } catch (_: Exception) {}
        try { codec?.release() } catch (_: Exception) {}
        codec = null
        try { projection?.unregisterCallback(projectionCallback) } catch (_: Exception) {}
        try { projection?.stop() } catch (_: Exception) {}
        projection = null
        super.onDestroy()
    }
}
