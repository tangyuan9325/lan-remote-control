/// TCP control server for Android.
/// Accepts viewer connections, streams screen, and handles input commands.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'protocol.dart';
import 'screen_capture.dart';
import 'input_simulator.dart';

class ControlServer {
  final int port;
  final ScreenCapture screenCapture;
  final String? password;

  ServerSocket? _server;
  final List<ClientSession> _clients = [];
  bool _running = false;

  ControlServer({
    this.port = 9001,
    required this.screenCapture,
    this.password,
  });

  Future<void> start() async {
    if (_running) return;
    _server = await ServerSocket.bind(InternetAddress.anyIPv4, port);
    _running = true;
    _server!.listen((socket) {
      final session = ClientSession(
        socket: socket,
        screenCapture: screenCapture,
        password: password,
      );
      _clients.add(session);
      session.start();
      session.done.then((_) => _clients.remove(session));
    });
  }

  Future<void> stop() async {
    _running = false;
    for (final c in _clients) {
      c.close();
    }
    _clients.clear();
    await _server?.close();
    _server = null;
  }
}

class ClientSession {
  final Socket socket;
  final ScreenCapture screenCapture;
  final String? password;

  bool _authenticated = false;
  bool _running = false;
  Timer? _streamTimer;
  InputSimulator? _input;
  final Completer<void> _done = Completer<void>();

  Future<void> get done => _done.future;

  ClientSession({
    required this.socket,
    required this.screenCapture,
    this.password,
  });

  void start() {
    _running = true;
    _handle();
  }

  Future<void> _handle() async {
    try {
      // Wait for hello
      final header = await _recvExact(Protocol.headerSize);
      final msgType = header[0];
      final length = ByteData.sublistView(header, 1, 5).getUint32(0, Endian.big);
      final payload = await _recvExact(length);

      if (msgType != Protocol.msgJson) {
        close();
        return;
      }

      final msg = jsonDecode(utf8.decode(payload)) as Map<String, dynamic>;
      if (msg['type'] != 'hello') {
        close();
        return;
      }

      if (password != null && msg['password'] != password) {
        _sendJson({'type': 'hello_fail', 'reason': 'wrong_password'});
        close();
        return;
      }

      _authenticated = true;
      _input = InputSimulator(
        screenWidth: screenCapture.width,
        screenHeight: screenCapture.height,
      );

      _sendJson({
        'type': 'hello_ok',
        'width': screenCapture.width,
        'height': screenCapture.height,
      });

      // Start screen streaming
      _streamTimer = Timer.periodic(const Duration(milliseconds: 33), (_) => _streamFrame());

      // Handle incoming messages
      socket.listen(
        (data) => _onData(data),
        onError: (_) => close(),
        onDone: () => close(),
      );
    } catch (e) {
      close();
    }
  }

  final BytesBuilder _buffer = BytesBuilder();

  void _onData(List<int> data) {
    if (!_authenticated) return;
    _buffer.add(data);
    while (_buffer.length >= Protocol.headerSize) {
      final bytes = _buffer.toBytes();
      final msgType = bytes[0];
      final length = ByteData.sublistView(bytes, 1, 5).getUint32(0, Endian.big);
      if (bytes.length < Protocol.headerSize + length) break;

      final payload = bytes.sublist(Protocol.headerSize, Protocol.headerSize + length);
      _buffer.clear();
      _buffer.add(bytes.sublist(Protocol.headerSize + length));

      _dispatch(msgType, payload);
    }
  }

  void _dispatch(int msgType, Uint8List payload) {
    if (msgType == Protocol.msgJson) {
      try {
        final msg = jsonDecode(utf8.decode(payload)) as Map<String, dynamic>;
        _handleCommand(msg);
      } catch (_) {}
    }
  }

  void _handleCommand(Map<String, dynamic> msg) {
    final type = msg['type'];
    switch (type) {
      case 'ping':
        _sendJson({'type': 'pong'});
        break;
      case 'set_quality':
        // Quality handled in capture
        break;
      case 'mouse_move':
      case 'touch_move':
        _input?.touchMove(msg['x'] + .0, msg['y'] + .0);
        break;
      case 'mouse_down':
      case 'touch_down':
        _input?.touchDown(msg['x'] + .0, msg['y'] + .0);
        break;
      case 'mouse_up':
      case 'touch_up':
        _input?.touchUp(msg['x'] + .0, msg['y'] + .0);
        break;
      case 'mouse_click':
      case 'tap':
        _input?.tap(msg['x'] + .0, msg['y'] + .0);
        break;
      case 'mouse_double':
        _input?.tap(msg['x'] + .0, msg['y'] + .0);
        break;
      case 'mouse_scroll':
        _input?.scroll(
          msg['x'] + .0,
          msg['y'] + .0,
          (msg['dx'] ?? 0) + .0,
          (msg['dy'] ?? 0) + .0,
        );
        break;
      case 'key_down':
        _input?.keyEvent(msg['key'] ?? '', true);
        break;
      case 'key_up':
        _input?.keyEvent(msg['key'] ?? '', false);
        break;
      case 'key_type':
        _input?.typeText(msg['text'] ?? '');
        break;
    }
  }

  Future<void> _streamFrame() async {
    if (!_running) return;
    try {
      final jpeg = await screenCapture.captureJpeg();
      if (jpeg != null && _running) {
        socket.add(Protocol.packJpeg(jpeg));
      }
    } catch (_) {
      // Frame dropped
    }
  }

  Future<Uint8List> _recvExact(int n) async {
    final completer = Completer<Uint8List>();
    final buffer = BytesBuilder();
    late StreamSubscription sub;
    sub = socket.listen(
      (data) {
        buffer.add(data);
        if (buffer.length >= n) {
          sub.cancel();
          completer.complete(buffer.toBytes().sublist(0, n));
        }
      },
      onError: (e) => completer.completeError(e),
      onDone: () => completer.completeError('closed'),
    );
    return completer.future;
  }

  void _sendJson(Map<String, dynamic> obj) {
    try {
      socket.add(Protocol.packJson(obj));
    } catch (_) {}
  }

  void close() {
    if (!_running) return;
    _running = false;
    _streamTimer?.cancel();
    try {
      socket.close();
    } catch (_) {}
    if (!_done.isCompleted) _done.complete();
  }
}
