package com.rc.agent

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/**
 * Agent 主界面：
 *   - 启动屏幕串流（MediaProjection，端口 8585）
 *   - 引导启用无障碍手势服务（端口 8586）
 *   - 显示 adb forward 用法提示
 */
class MainActivity : Activity() {

    companion object {
        private const val REQ_STREAM = 1001
    }

    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(56, 56, 56, 56)
        }

        root.addView(TextView(this).apply {
            text = "ADB 远程控制 Agent"
            textSize = 22f
        })

        status = TextView(this).apply {
            setPadding(0, 24, 0, 24)
            textSize = 15f
        }

        root.addView(status)

        root.addView(Button(this).apply {
            text = "启动屏幕串流（MediaProjection）"
            setOnClickListener {
                requestNotificationIfNeeded()
                val mpm = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
                startActivityForResult(mpm.createScreenCaptureIntent(), REQ_STREAM)
            }
        })

        root.addView(Button(this).apply {
            text = "启用无障碍手势服务"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
        })

        root.addView(Button(this).apply {
            text = "停止所有服务"
            setOnClickListener {
                stopService(Intent(this@MainActivity, StreamService::class.java))
                ControlService.stop()
                Toast.makeText(this@MainActivity, "已停止", Toast.LENGTH_SHORT).show()
            }
        })

        root.addView(TextView(this).apply {
            text = "使用步骤（手机端）：\n" +
                "1. 点击“启用无障碍手势服务”，在系统设置中开启“ADB远程控制 Agent”\n" +
                "2. 如需串流，点击“启动屏幕串流”并授予录屏权限\n\n" +
                "电脑端：\n" +
                "adb forward tcp:8585 tcp:8585   # 视频流\n" +
                "adb forward tcp:8586 tcp:8586   # 手势指令\n\n" +
                "安全说明：两个端口都只监听 127.0.0.1，\n" +
                "局域网内其它设备无法直连，必须经 adb 转发。\n\n" +
                "Windows 客户端会自动执行转发；\n" +
                "在设备标签页把视频模式切到“Agent 串流”、输入模式切到“Agent 手势”即可。"
            setPadding(0, 32, 0, 0)
            textSize = 14f
        })

        setContentView(ScrollView(this).apply { addView(root) })
    }

    private fun requestNotificationIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    override fun onResume() {
        super.onResume()
        updateStatus()
    }

    private fun updateStatus() {
        val ctrlRunning = ControlService.isRunning()
        val streamRunning = StreamService.isRunning()
        // 原实现两个分支内容完全一样（只差一个标题），这里合并
        val head = if (ctrlRunning || streamRunning) "状态：正在运行" else "状态：未运行"
        status.text = buildString {
            append(head).append('\n')
            append("  手势服务：").append(if (ctrlRunning) "已启用(127.0.0.1:8586)" else "未启用").append('\n')
            append("  屏幕串流：").append(if (streamRunning) "运行中(127.0.0.1:8585)" else "未运行")
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_STREAM) {
            if (resultCode == RESULT_OK && data != null) {
                val intent = Intent(this, StreamService::class.java)
                intent.putExtra(StreamService.EXTRA_RESULT_CODE, resultCode)
                intent.putExtra(StreamService.EXTRA_RESULT_DATA, data)
                startForegroundService(intent)
                Toast.makeText(this, "屏幕串流已启动（端口 8585）", Toast.LENGTH_LONG).show()
            } else {
                Toast.makeText(this, "已取消录屏授权", Toast.LENGTH_SHORT).show()
            }
        }
    }
}
