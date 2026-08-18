/// TCP connection manager for the remote control session v1.2.
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
  String? _downloadName;
  int _downloadSize = 0;
  BytesBuilder? _downloadData;
  String? _uploadPath;

  final StreamController<Uint8List> _frameController = StreamController.broadcast();
  final StreamController<String> _statusController = StreamController.broadcast();
  final StreamController<Map<String, dynamic>> _fileListController = StreamController.broadcast();
  final StreamController<Map<String, dynamic>> _downloadStartController = StreamController.broadcast();
  final StreamController<int> _downloadProgressController = StreamController.broadcast();
  final StreamController<Map<String, dynamic>> _downloadCompleteController = StreamController.broadcast();
  final StreamController<Map<String, dynamic>> _uploadDoneController = StreamController.broadcast();
  final StreamController<String> _fileErrorController = StreamController.broadcast();
  final StreamController<Uint8List> _audioController = StreamController.broadcast();
  final StreamController<void> _voiceReadyController = StreamController.broadcast();
  final StreamController<String> _voiceErrorController = StreamController.broadcast();

  Stream<Uint8List> get frameStream => _frameController.stream;
  Stream<String> get statusStream => _statusController.stream;
  Stream<Map<String, dynamic>> get fileListStream => _fileListController.stream;
  Stream<Map<String, dynamic>> get downloadStartStream => _downloadStartController.stream;
  Stream<int> get downloadProgressStream => _downloadProgressController.stream;
  Stream<Map<String, dynamic>> get downloadCompleteStream => _downloadCompleteController.stream;
  Stream<Map<String, dynamic>> get uploadDoneStream => _uploadDoneController.stream;
  Stream<String> get fileErrorStream => _fileErrorController.stream;
  Stream<Uint8List> get audioStream => _audioController.stream;
  Stream<void> get voiceReadyStream => _voiceReadyController.stream;
  Stream<String> get voiceErrorStream => _voiceErrorController.stream;

  int get remoteWidth => _remoteWidth;
  int get remoteHeight => _remoteHeight;
  bool get isConnected => _connected;

  RemoteConnection({required this.host, required this.port, this.password});

  Future<bool> connect() async {
    try {
      _statusController.add('Connecting to $host:$port...');
      _socket = await Socket.connect(host, port, timeout: const Duration(seconds: 5));
      _socket!.add(Protocol.packJson({'type': 'hello', 'password': password ?? ''}));
      await _socket!.flush();
      final header = await _readExact(Protocol.headerSize);
      final msgType = header[0];
      final length = ByteData.sublistView(header, 1, 5).getUint32(0, Endian.big);
      final payload = await _readExact(length);
      if (msgType != Protocol.msgJson) throw 'Bad handshake';
      final msg = jsonDecode(utf8.decode(payload)) as Map<String, dynamic>;
      if (msg['type'] != 'hello_ok') throw msg['reason'] ?? 'Connection rejected';
      _remoteWidth = msg['width'] ?? 0;
      _remoteHeight = msg['height'] ?? 0;
      _connected = true;
      _statusController.add('Connected  ${_remoteWidth}x$_remoteHeight');
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
        while (true) {
          final bytes = buffer.toBytes();
          if (bytes.length < Protocol.headerSize) break;
          final length = ByteData.sublistView(bytes, 1, 5).getUint32(0, Endian.big);
          if (bytes.length < Protocol.headerSize + length) break;
          final msgType = bytes[0];
          final payload = bytes.sublist(Protocol.headerSize, Protocol.headerSize + length);
          if (msgType == Protocol.msgJpeg) {
            _frameController.add(Uint8List.fromList(payload));
          } else if (msgType == Protocol.msgFile) {
            if (_downloadData != null) {
              _downloadData!.add(payload);
              _downloadProgressController.add(_downloadData!.length);
            }
          } else if (msgType == Protocol.msgAudio) {
            _audioController.add(Uint8List.fromList(payload));
          } else if (msgType == Protocol.msgJson) {
            _handleJson(payload);
          }
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

  void _handleJson(Uint8List payload) {
    try {
      final msg = jsonDecode(utf8.decode(payload)) as Map<String, dynamic>;
      final t = msg['type'];
      switch (t) {
        case 'file_list_response':
          _fileListController.add(msg);
          break;
        case 'file_list_error':
        case 'file_download_error':
        case 'file_upload_error':
          _fileErrorController.add(msg['error'] ?? 'File error');
          _downloadName = null;
          _downloadData = null;
          break;
        case 'file_download_start':
          _downloadName = msg['name'];
          _downloadSize = msg['size'] ?? 0;
          _downloadData = BytesBuilder();
          _downloadStartController.add(msg);
          break;
        case 'file_download_complete':
          if (_downloadData != null) {
            _downloadCompleteController.add({
              'name': _downloadName,
              'size': _downloadData!.length,
              'data': _downloadData!.toBytes(),
            });
          }
          _downloadName = null;
          _downloadData = null;
          break;
        case 'file_upload_ready':
          _uploadPath = msg['path'];
          break;
        case 'file_upload_done':
          _uploadDoneController.add(msg);
          break;
        case 'voice_ready':
          _voiceReadyController.add(null);
          break;
        case 'voice_error':
          _voiceErrorController.add(msg['error'] ?? 'Voice error');
          break;
      }
    } catch (_) {}
  }

  void sendInput(Map<String, dynamic> event) {
    if (_socket != null && _connected) {
      try { _socket!.add(Protocol.packJson(event)); } catch (_) {}
    }
  }

  void listFiles(String path) {
    sendInput({'type': 'file_list', 'path': path});
  }

  void downloadFile(String path) {
    _downloadName = null;
    _downloadSize = 0;
    _downloadData = null;
    sendInput({'type': 'file_download', 'path': path});
  }

  Future<void> uploadFile(String localPath, String remoteDir) async {
    final file = File(localPath);
    if (!await file.exists()) return;
    final name = file.path.split('/').last;
    final size = await file.length();
    sendInput({'type': 'file_upload_start', 'path': remoteDir, 'name': name, 'size': size});
    await Future.delayed(const Duration(milliseconds: 500));
    final stream = file.openRead();
    await for (final chunk in stream) {
      if (_socket != null && _connected) {
        _socket!.add(Protocol.packFile(Uint8List.fromList(chunk)));
      }
    }
    sendInput({'type': 'file_upload_complete', 'name': name});
  }

  void startVoice() { sendInput({'type': 'voice_start'}); }
  void stopVoice() { sendInput({'type': 'voice_stop'}); }

  void sendAudio(Uint8List data) {
    if (_socket != null && _connected) {
      try {
        final header = BytesBuilder()
          ..addByte(Protocol.msgAudio)
          ..add((ByteData(4)..setUint32(0, data.length, Endian.big)).buffer.asUint8List())
          ..add(data);
        _socket!.add(header.toBytes());
      } catch (_) {}
    }
  }

  void disconnect() {
    _connected = false;
    _downloadData = null;
    try { _socket?.destroy(); } catch (_) {}
    _socket = null;
  }

  void dispose() {
    disconnect();
    _frameController.close();
    _statusController.close();
    _fileListController.close();
    _downloadStartController.close();
    _downloadProgressController.close();
    _downloadCompleteController.close();
    _uploadDoneController.close();
    _fileErrorController.close();
    _audioController.close();
    _voiceReadyController.close();
    _voiceErrorController.close();
  }
}
