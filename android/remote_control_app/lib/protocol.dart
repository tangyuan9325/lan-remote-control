/// Wire protocol constants and helpers for the Flutter viewer.
///
/// Message framing (5-byte header + payload):
///   byte 0      : msgType  (0x01=JSON, 0x02=JPEG)
///   bytes 1..4  : length   (big-endian uint32)
library;

import 'dart:convert';
import 'dart:typed_data';

class Protocol {
  static const int msgJson = 0x01;
  static const int msgJpeg = 0x02;
  static const int headerSize = 5;

  /// Build a framed JSON message.
  static Uint8List packJson(Map<String, dynamic> obj) {
    final payload = utf8.encode(jsonEncode(obj));
    final bytes = BytesBuilder()
      ..addByte(msgJson)
      ..add(_uint32Be(payload.length))
      ..add(payload);
    return bytes.toBytes();
  }

  static Uint8List _uint32Be(int value) {
    final data = ByteData(4)..setUint32(0, value, Endian.big);
    return data.buffer.asUint8List();
  }
}

/// Parsed discovery response.
class DeviceInfo {
  final String hostname;
  final String ip;
  final int port;
  final String os;
  final String version;
  final bool passwordRequired;

  DeviceInfo({
    required this.hostname,
    required this.ip,
    required this.port,
    required this.os,
    required this.version,
    required this.passwordRequired,
  });

  factory DeviceInfo.fromJson(Map<String, dynamic> json) {
    return DeviceInfo(
      hostname: json['hostname'] ?? 'unknown',
      ip: json['ip'] ?? '',
      port: json['port'] ?? 9001,
      os: json['os'] ?? '',
      version: json['version'] ?? '',
      passwordRequired: json['password_required'] ?? false,
    );
  }
}
