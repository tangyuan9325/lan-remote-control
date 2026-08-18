/// TCP connection manager for the remote control session.
/// Handles handshake, frame reception, and input sending.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'protocol.dart';

class RemoteConnection {
  final String host;
  final int port;
  final String? password;

  Socket? _socket;
  int _remoteWidth = 0;
  int _remoteHeight = 0;
  bool _connected = false;

  final StreamController<Uint8List> _frameController =
      StreamController<Uint8List>.broadcast();
  final StreamController<String> _statusController =
      StreamController<String>.broadcast();

  Stream<Uint8List> get frameStream => _frameController.stream;
  Stream<String> get statusStream => _statusController.stream;
  int get remoteWidth => _remoteWidth;
  int get remoteHeight => _remoteHeight;
  bool get isConnected => _connected;

  RemoteConnection({required this.host, required this.port, this.password});

  /// Connect and perform handshake. Returns true on success.
  Future<bool> connect() async {
    try {
      _statusController.add('Connecting to $host:$port...');
      _socket = await Socket.connect(host, port, timeout: const Duration(seconds: 5));

      // Send hello
      _socket!.add(Protocol.packJson({
        'type': 'hello',
        'password': password ?? '',
      }));
      await _socket!.flush();

      // Read handshake response
      final header = await _readExact(Protocol.headerSize);
      final msgType = header[0];
      final length = ByteData.sublistView(header, 1, 5).getUint32(0, Endian.big);
      final payload = await _readExact(length);

      if (msgType != Protocol.msgJson) {
        throw 'Bad handshake';
      }
      final msg = jsonDecode(utf8.decode(payload)) as Map<String, dynamic>;
      if (msg['type'] != 'hello_ok') {
        throw msg['reason'] ?? 'Connection rejected';
      }

      _remoteWidth = msg['width'] ?? 0;
      _remoteHeight = msg['height'] ?? 0;
      _connected = true;
      _statusController.add('Connected  ${_remoteWidth}x$_remoteHeight');

      // Start reading frames
      _readLoop();
      return true;
    } catch (e) {
      _statusController.add('Error: $e');
      disconnect();
      return false;
    }
  }

  Future<Uint8List> _readExact(int n) async {
    final buffer = BytesBuilder();
    while (buffer.length < n) {
      final data = await _socket!.first;
      buffer.add(data);
    }
    return buffer.toBytes();
  }

  void _readLoop() async {
    try {
      final buffer = BytesBuilder();
      await for (final data in _socket!) {
        buffer.add(data);
        // Process all complete messages in buffer
        while (true) {
          final bytes = buffer.toBytes();
          if (bytes.length < Protocol.headerSize) break;
          final length = ByteData.sublistView(bytes, 1, 5).getUint32(0, Endian.big);
          if (bytes.length < Protocol.headerSize + length) break;

          final msgType = bytes[0];
          final payload = bytes.sublist(Protocol.headerSize, Protocol.headerSize + length);

          if (msgType == Protocol.msgJpeg) {
            _frameController.add(Uint8List.fromList(payload));
          }

          // Remove processed bytes
          final remaining = bytes.sublist(Protocol.headerSize + length);
          buffer.clear();
          buffer.add(remaining);
        }
      }
    } catch (e) {
      // Connection closed
    } finally {
      _connected = false;
      _statusController.add('Disconnected');
    }
  }

  /// Send a JSON input event.
  void sendInput(Map<String, dynamic> event) {
    if (_socket != null && _connected) {
      try {
        _socket!.add(Protocol.packJson(event));
      } catch (_) {}
    }
  }

  void disconnect() {
    _connected = false;
    try {
      _socket?.destroy();
    } catch (_) {}
    _socket = null;
  }

  void dispose() {
    disconnect();
    _frameController.close();
    _statusController.close();
  }
}
