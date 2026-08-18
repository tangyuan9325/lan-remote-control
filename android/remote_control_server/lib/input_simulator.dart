/// Input simulation using Android AccessibilityService via platform channel.
library;

import 'package:flutter/services.dart';

class InputSimulator {
  static const MethodChannel _channel = MethodChannel('com.example.remote_control_server/input');

  int screenWidth;
  int screenHeight;

  InputSimulator({required this.screenWidth, required this.screenHeight});

  int _toAbsX(double nx) => (nx.clamp(0.0, 1.0) * (screenWidth - 1)).round();
  int _toAbsY(double ny) => (ny.clamp(0.0, 1.0) * (screenHeight - 1)).round();

  Future<void> touchDown(double nx, double ny) async {
    try {
      await _channel.invokeMethod('touchDown', {'x': _toAbsX(nx), 'y': _toAbsY(ny)});
    } catch (_) {}
  }

  Future<void> touchUp(double nx, double ny) async {
    try {
      await _channel.invokeMethod('touchUp', {'x': _toAbsX(nx), 'y': _toAbsY(ny)});
    } catch (_) {}
  }

  Future<void> touchMove(double nx, double ny) async {
    try {
      await _channel.invokeMethod('touchMove', {'x': _toAbsX(nx), 'y': _toAbsY(ny)});
    } catch (_) {}
  }

  Future<void> tap(double nx, double ny) async {
    try {
      await _channel.invokeMethod('tap', {'x': _toAbsX(nx), 'y': _toAbsY(ny)});
    } catch (_) {}
  }

  Future<void> longPress(double nx, double ny) async {
    try {
      await _channel.invokeMethod('longPress', {'x': _toAbsX(nx), 'y': _toAbsY(ny)});
    } catch (_) {}
  }

  Future<void> scroll(double nx, double ny, double dx, double dy) async {
    try {
      await _channel.invokeMethod('scroll', {
        'x': _toAbsX(nx),
        'y': _toAbsY(ny),
        'dx': dx,
        'dy': dy,
      });
    } catch (_) {}
  }

  Future<void> keyEvent(String key, bool down) async {
    try {
      await _channel.invokeMethod('keyEvent', {'key': key, 'down': down});
    } catch (_) {}
  }

  Future<void> typeText(String text) async {
    try {
      await _channel.invokeMethod('typeText', {'text': text});
    } catch (_) {}
  }

  Future<bool> isAccessibilityEnabled() async {
    try {
      return await _channel.invokeMethod('isAccessibilityEnabled') ?? false;
    } catch (_) {
      return false;
    }
  }

  Future<void> openAccessibilitySettings() async {
    try {
      await _channel.invokeMethod('openAccessibilitySettings');
    } catch (_) {}
  }
}
