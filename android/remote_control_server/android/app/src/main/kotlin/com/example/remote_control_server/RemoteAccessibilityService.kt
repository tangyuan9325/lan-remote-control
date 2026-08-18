package com.example.remote_control_server

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

class RemoteAccessibilityService : AccessibilityService() {
    private val handler = Handler(Looper.getMainLooper())

    companion object {
        private var instance: RemoteAccessibilityService? = null

        fun isEnabled(): Boolean = instance != null

        fun touchDown(x: Int, y: Int) {
            instance?.performTouchDown(x, y)
        }

        fun touchUp(x: Int, y: Int) {
            instance?.performTouchUp(x, y)
        }

        fun touchMove(x: Int, y: Int) {
            instance?.performTouchMove(x, y)
        }

        fun tap(x: Int, y: Int) {
            instance?.performTap(x, y)
        }

        fun longPress(x: Int, y: Int) {
            instance?.performLongPress(x, y)
        }

        fun scroll(x: Int, y: Int, dx: Double, dy: Double) {
            instance?.performScroll(x, y, dx, dy)
        }

        fun keyEvent(key: String, down: Boolean) {
            instance?.performKeyEvent(key, down)
        }

        fun typeText(text: String) {
            instance?.performTypeText(text)
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    override fun onDestroy() {
        super.onDestroy()
        instance = null
    }

    private fun performTap(x: Int, y: Int) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            val path = Path()
            path.moveTo(x.toFloat(), y.toFloat())
            val gesture = GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, 100))
                .build()
            dispatchGesture(gesture, null, null)
        }
    }

    private fun performLongPress(x: Int, y: Int) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            val path = Path()
            path.moveTo(x.toFloat(), y.toFloat())
            val gesture = GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, 1000))
                .build()
            dispatchGesture(gesture, null, null)
        }
    }

    private var downX = 0f
    private var downY = 0f

    private fun performTouchDown(x: Int, y: Int) {
        downX = x.toFloat()
        downY = y.toFloat()
    }

    private fun performTouchUp(x: Int, y: Int) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            val path = Path()
            path.moveTo(downX, downY)
            path.lineTo(x.toFloat(), y.toFloat())
            val gesture = GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, 50))
                .build()
            dispatchGesture(gesture, null, null)
        }
    }

    private fun performTouchMove(x: Int, y: Int) {
        downX = x.toFloat()
        downY = y.toFloat()
    }

    private fun performScroll(x: Int, y: Int, dx: Double, dy: Double) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            val path = Path()
            path.moveTo(x.toFloat(), y.toFloat())
            path.lineTo((x - dx * 50).toFloat(), (y - dy * 50).toFloat())
            val gesture = GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, 200))
                .build()
            dispatchGesture(gesture, null, null)
        }
    }

    private fun performKeyEvent(key: String, down: Boolean) {
        when (key.lowercase()) {
            "home" -> performGlobalAction(GLOBAL_ACTION_HOME)
            "back" -> performGlobalAction(GLOBAL_ACTION_BACK)
            "recents" -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            "notifications" -> performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
            "power" -> performGlobalAction(GLOBAL_ACTION_LOCK_SCREEN)
        }
    }

    private fun performTypeText(text: String) {
        try {
            val node = rootInActiveWindow ?: return
            val focused = findFocusedNode(node) ?: return
            val arguments = Bundle()
            arguments.putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                text
            )
            focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
        } catch (e: Exception) {
            // Ignore
        }
    }

    private fun findFocusedNode(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isFocused) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findFocusedNode(child)
            if (found != null) return found
        }
        return null
    }
}
