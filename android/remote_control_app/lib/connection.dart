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
  bool _handshakeDone = false;
  Completer<bool>? _handshakeCompleter;

  String? _downloadName;
  int _downloadSize = 0;
  BytesBuilder? _downloadData;
  String? _uploadPath;

  /// 单条消息最大长度（与 Go 端 protocol.MaxMessageSize 对齐，64MB），
  /// 防止恶意/异常长度头导致 OOM。
  static const int maxMessageSize = 64 * 1024 * 1024;

  /// 下载文件累计字节上限（512MB），超限即中止并报错，防止内存耗尽。
  static const int maxDownloadSize = 512 * 1024 * 1024;

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

      // Start read loop FIRST, then send hello — avoids data loss from _socket.first
      _handshakeCompleter = Completer<bool>();
      _handshakeDone = false;
      _readLoop();

      _socket!.add(Protocol.packJson({'type': 'hello', 'password': password ?? ''}));
      await _socket!.flush();

      final ok = await _handshakeCompleter!.future.timeout(
        const Duration(seconds: 5),
        onTimeout: () => false,
      );

      if (!ok) {
        disconnect();
        return false;
      }

      _connected = true;
      _statusController.add('Connected  ${_remoteWidth}x$_remoteHeight');
      return true;
    } catch (e) {
      _statusController.add('Error: $e');
      disconnect();
      return false;
    }
  }

  void _readLoop() async {
    // 游标式缓冲解析：避免每帧 buffer.toBytes()/clear() 造成的 O(n^2) 整体拷贝。
    var buf = Uint8List(0);
    var start = 0; // 已消费偏移
    var end = 0;   // 有效数据末尾

    try {
      await for (final data in _socket!) {
        final avail = end - start + data.length;
        if (buf.length < avail) {
          // 扩容并压缩已消费空间（摊还 O(1)）。
          var newCap = buf.isEmpty ? 4096 : buf.length * 2;
          if (newCap < avail) newCap = avail;
          final grown = Uint8List(newCap);
          grown.setRange(0, end - start, buf, start);
          buf = grown;
          end -= start;
          start = 0;
        }
        buf.setRange(end, end + data.length, data);
        end += data.length;

        while (true) {
          final pending = end - start;
          if (pending < Protocol.headerSize) break;
          final length = ByteData.sublistView(buf, start + 1, start + 5).getUint32(0, Endian.big);
          // 长度守卫：与 Go 端 MaxMessageSize 对齐，防恶意/异常长度头导致 OOM。
          if (length > maxMessageSize) {
            _statusController.add('Error: oversized message ($length bytes), disconnecting');
            disconnect();
            return;
          }
          if (pending < Protocol.headerSize + length) break;

          final msgType = buf[start];
          final payload = buf.sublist(start + Protocol.headerSize, start + Protocol.headerSize + length);
          start += Protocol.headerSize + length;

          if (!_handshakeDone) {
            if (msgType == Protocol.msgJson) {
              try {
                final msg = jsonDecode(utf8.decode(payload)) as Map<String, dynamic>;
                if (msg['type'] == 'hello_ok') {
                  _remoteWidth = msg['width'] ?? 0;
                  _remoteHeight = msg['height'] ?? 0;
                  _handshakeDone = true;
                  _handshakeCompleter?.complete(true);
                } else if (msg['type'] == 'hello_fail') {
                  _statusController.add('Rejected: ${msg['reason'] ?? 'unknown'}');
                  _handshakeCompleter?.complete(false);
                }
              } catch (_) {
                _handshakeCompleter?.complete(false);
              }
            } else {
              _handshakeCompleter?.complete(false);
            }
          } else {
            if (msgType == Protocol.msgJpeg) {
              _frameController.add(Uint8List.fromList(payload));
            } else if (msgType == Protocol.msgFile) {
              if (_downloadData != null) {
                // 下载累计上限：防止超大文件把整包读进内存导致 OOM。
                if (_downloadData!.length + payload.length > maxDownloadSize) {
                  _fileErrorController.add('Download exceeds ${maxDownloadSize ~/ (1024 * 1024)}MB limit');
                  _downloadName = null;
                  _downloadData = null;
                } else {
                  _downloadData!.add(payload);
                  _downloadProgressController.add(_downloadData!.length);
                }
              }
            } else if (msgType == Protocol.msgAudio) {
              _audioController.add(Uint8List.fromList(payload));
            } else if (msgType == Protocol.msgJson) {
              _handleJson(payload);
            }
          }
        }
      }
    } catch (e) {
      // Connection closed
    } finally {
      if (!_handshakeDone && _handshakeCompleter != null && !_handshakeCompleter!.isCompleted) {
        _handshakeCompleter!.complete(false);
      }
      _connected = false;
      _statusController.add('Disconnected');
    }
  }

  /// 等待 `file_upload_ready` 确认信号（事件驱动，替代固定 200ms 延时）。
  Completer<bool>? _uploadReadyCompleter;

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
          // 上传出错时唤醒等待 ready 的协程，避免卡到超时。
          if (_uploadReadyCompleter != null && !_uploadReadyCompleter!.isCompleted) {
            _uploadReadyCompleter!.complete(false);
          }
          break;
        case 'file_download_start':
          _downloadName = msg['name'];
          _downloadSize = msg['size'] ?? 0;
          // 服务端声明的大小超过内存上限时直接拒绝，防止 OOM。
          if (_downloadSize > maxDownloadSize) {
            _fileErrorController.add('Remote file too large ($_downloadSize bytes)');
            _downloadName = null;
            break;
          }
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
          if (_uploadReadyCompleter != null && !_uploadReadyCompleter!.isCompleted) {
            _uploadReadyCompleter!.complete(true);
          }
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
    // 兼容 Windows/POSIX 路径分隔符（原实现 split('/') 在反斜杠路径下会取错文件名）。
    final name = file.uri.pathSegments.last;
    final size = await file.length();

    // 事件驱动握手：发送 upload_start 后等待服务端 `file_upload_ready` 确认，
    // 替代原先固定 200ms 延时（网络抖动时会在 ready 前发出数据块导致上传失败）。
    _uploadReadyCompleter = Completer<bool>();
    sendInput({'type': 'file_upload_start', 'path': remoteDir, 'name': name, 'size': size});
    bool ready;
    try {
      ready = await _uploadReadyCompleter!.future.timeout(
        const Duration(seconds: 10),
        onTimeout: () => false,
      );
    } finally {
      _uploadReadyCompleter = null;
    }
    if (!ready || !_connected || _socket == null) {
      _fileErrorController.add('Upload not accepted by remote (no file_upload_ready)');
      return;
    }

    final stream = file.openRead();
    await for (final chunk in stream) {
      if (_socket == null || !_connected) {
        _fileErrorController.add('Upload interrupted: connection lost');
        return;
      }
      _socket!.add(Protocol.packFile(Uint8List.fromList(chunk)));
      // 周期性 flush，利用 TCP 背压避免一次性灌满写缓冲。
      await _socket!.flush();
    }
    sendInput({'type': 'file_upload_complete', 'name': name});
    await _socket!.flush();
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
    _handshakeDone = false;
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
