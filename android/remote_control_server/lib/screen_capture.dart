/// Screen capture using Android MediaProjection via platform channel.
library;

import 'dart:typed_data';
import 'package:flutter/services.dart';

class ScreenCapture {
  static const MethodChannel _channel = MethodChannel('com.example.remote_control_server/screen');

  int _width = 0;
  int _height = 0;
  bool _initialized = false;

  int get width => _width;
  int get height => _height;
  bool get isInitialized => _initialized;

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

  Future<Uint8List?> captureJpeg({int quality = 70}) async {
    try {
      final result = await _channel.invokeMethod('capture', {'quality': quality});
      if (result is Uint8List) return result;
      if (result is List<int>) return Uint8List.fromList(result);
    } catch (e) {
      // Capture failed
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
