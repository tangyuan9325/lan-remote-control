/// Wire protocol constants and helpers for the Flutter viewer v1.2.
library;

import 'dart:convert';
import 'dart:typed_data';

class Protocol {
  static const int msgJson = 0x01;
  static const int msgJpeg = 0x02;
  static const int msgFile = 0x03;
  static const int msgAudio = 0x04;
  static const int headerSize = 5;

  static Uint8List packJson(Map<String, dynamic> obj) {
    final payload = utf8.encode(jsonEncode(obj));
    final bytes = BytesBuilder()
      ..addByte(msgJson)
      ..add(_uint32Be(payload.length))
      ..add(payload);
    return bytes.toBytes();
  }

  static Uint8List packFile(Uint8List data) {
    final bytes = BytesBuilder()
      ..addByte(msgFile)
      ..add(_uint32Be(data.length))
      ..add(data);
    return bytes.toBytes();
  }

  static Uint8List _uint32Be(int value) {
    final data = ByteData(4)..setUint32(0, value, Endian.big);
    return data.buffer.asUint8List();
  }
}

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

class FileEntry {
  final String name;
  final bool isDir;
  final int size;
  final String modified;

  FileEntry({
    required this.name,
    required this.isDir,
    required this.size,
    required this.modified,
  });

  factory FileEntry.fromJson(Map<String, dynamic> json) {
    return FileEntry(
      name: json['name'] ?? '',
      isDir: json['is_dir'] ?? false,
      size: json['size'] ?? 0,
      modified: json['modified'] ?? '',
    );
  }
}
