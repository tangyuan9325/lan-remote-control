/// Screen capture using Android MediaProjection via platform channel.
library;

import 'dart:typed_data';
import 'package:flutter/services.dart';

class ScreenCapture {
  static const MethodChannel _channel = MethodChannel('com.example.remote_control_server/screen');

  int _width = 0;
  int _height = 0;
  bool _initialized = false;
  int _quality = 70;
  bool _capturing = false;

  int get width => _width;
  int get height => _height;
  bool get isInitialized => _initialized;

  /// JPEG quality (10..100), settable via the `set_quality` command.
  set quality(int q) => _quality = q.clamp(10, 100);
  int get quality => _quality;

  Future<void> initialize() async {
    try {
      final result = await _channel.invokeMapMethod<String, dynamic>('initialize');
      if (result != null) {
        _width = result['width'] ?? 0;
        _height = result['height'] ?? 0;
        _initialized = true;
      }
    } catch (e) {
      _initialized = false;
    }
  }

  Future<Uint8List?> captureJpeg() async {
    if (!_initialized) return null;
    // Re-entrancy guard: MediaProjection captures can take longer than the
    // 33ms stream tick; overlapping calls would pile up channel requests.
    if (_capturing) return null;
    _capturing = true;
    try {
      final result = await _channel.invokeMethod('capture', {'quality': _quality});
      if (result is Uint8List) return result;
      if (result is List<int>) return Uint8List.fromList(result);
    } catch (e) {
      // Capture failed
    } finally {
      _capturing = false;
    }
    return null;
  }

  Future<void> startProjection() async {
    try {
      await _channel.invokeMethod('startProjection');
    } catch (e) {
      // User may need to grant permission
    }
  }

  Future<void> stop() async {
    try {
      await _channel.invokeMethod('stop');
    } catch (_) {}
    _initialized = false;
  }
}
