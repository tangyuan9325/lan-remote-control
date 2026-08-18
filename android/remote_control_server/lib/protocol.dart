/// Wire protocol constants and helpers for the Android server v1.2.
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

  static Uint8List packJpeg(Uint8List data) {
    final bytes = BytesBuilder()
      ..addByte(msgJpeg)
      ..add(_uint32Be(data.length))
      ..add(data);
    return bytes.toBytes();
  }

  static Uint8List _uint32Be(int value) {
    final data = ByteData(4)..setUint32(0, value, Endian.big);
    return data.buffer.asUint8List();
  }

  /// Read exactly n bytes from a socket stream.
  static Future<Uint8List> recvExact(Stream<List<int>> stream, int n) async {
    final buffer = BytesBuilder();
    await for (final chunk in stream) {
      buffer.add(chunk);
      if (buffer.length >= n) break;
    }
    return buffer.toBytes().sublist(0, n);
  }
}
