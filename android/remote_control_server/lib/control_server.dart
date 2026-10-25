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

  /// Maximum concurrent client sessions (DoS protection).
  static const int maxClients = 8;

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
      // Enforce a connection limit so a flood of connections cannot
      // exhaust memory / CPU with endless screen-capture streams.
      if (_clients.length >= maxClients) {
        try {
          socket.add(Protocol.packJson(
              {'type': 'hello_fail', 'reason': 'server_busy'}));
          socket.destroy();
        } catch (_) {}
        return;
      }
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

  /// Maximum accepted message payload (same limit as the Go server).
  static const int maxMessageSize = 64 * 1024 * 1024;

  bool _authenticated = false;
  bool _running = false;
  Timer? _streamTimer;
  Timer? _idleTimer;
  InputSimulator? _input;
  final Completer<void> _done = Completer<void>();
  StreamSubscription<List<int>>? _socketSub;

  /// Reassembly buffer kept across chunks (O(1) amortized, no re-scan of
  /// the whole buffer on every TCP segment).
  final BytesBuilder _buffer = BytesBuilder(copy: false);
  int _bufferedLength = 0;

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
      // Wait for hello with a read timeout so stalled/pre-auth
      // connections cannot linger forever.
      final header = await _recvExact(Protocol.headerSize)
          .timeout(const Duration(seconds: 10));
      final msgType = header[0];
      final length = ByteData.sublistView(header, 1, 5).getUint32(0, Endian.big);
      if (length > maxMessageSize) {
        close();
        return;
      }
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

      if (password != null && !_passwordMatches(msg['password'])) {
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

      // Idle watchdog: drop sessions that stop sending anything
      // (keeps dead NAT/firewall mappings from leaking sessions).
      _resetIdleTimer();

      // Handle incoming messages
      _socketSub = socket.listen(
        (data) => _onData(data),
        onError: (_) => close(),
        onDone: () => close(),
      );
    } catch (e) {
      close();
    }
  }

  /// Constant-time-ish password comparison to avoid trivial timing leaks.
  bool _passwordMatches(dynamic provided) {
    if (provided is! String) return false;
    final expected = password!;
    if (provided.length != expected.length) return false;
    var diff = 0;
    for (var i = 0; i < expected.length; i++) {
      diff |= expected.codeUnitAt(i) ^ provided.codeUnitAt(i);
    }
    return diff == 0;
  }

  void _resetIdleTimer() {
    _idleTimer?.cancel();
    _idleTimer = Timer(const Duration(seconds: 60), close);
  }

  void _onData(List<int> data) {
    if (!_authenticated) return;
    _resetIdleTimer();
    _buffer.add(data);
    _bufferedLength += data.length;
    _drainBuffer();
  }

  void _drainBuffer() {
    // Parse complete frames directly out of the accumulated buffer.
    var bytes = _buffer.toBytes();
    var offset = 0;
    while (bytes.length - offset >= Protocol.headerSize) {
      final length = ByteData.sublistView(bytes, offset + 1, offset + 5)
          .getUint32(0, Endian.big);
      if (length > maxMessageSize) {
        close(); // oversized frame — protocol violation / attack
        return;
      }
      if (bytes.length - offset < Protocol.headerSize + length) break;

      final msgType = bytes[offset];
      final payload = Uint8List.sublistView(
          bytes, offset + Protocol.headerSize, offset + Protocol.headerSize + length);
      offset += Protocol.headerSize + length;

      _dispatch(msgType, Uint8List.fromList(payload));
    }
    // Compact: keep only the unparsed remainder.
    if (offset > 0) {
      final remaining = bytes.sublist(offset);
      _buffer.clear();
      _buffer.add(remaining);
      _bufferedLength = remaining.length;
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
        // Clamp to a sane range and forward to the capture pipeline.
        final q = (msg['quality'] is num) ? (msg['quality'] as num).toInt() : null;
        if (q != null) {
          screenCapture.quality = q.clamp(10, 100);
        }
        break;
      case 'mouse_move':
      case 'touch_move':
        _input?.touchMove(_coord(msg['x']), _coord(msg['y']));
        break;
      case 'mouse_down':
      case 'touch_down':
        _input?.touchDown(_coord(msg['x']), _coord(msg['y']));
        break;
      case 'mouse_up':
      case 'touch_up':
        _input?.touchUp(_coord(msg['x']), _coord(msg['y']));
        break;
      case 'mouse_click':
      case 'tap':
        _input?.tap(_coord(msg['x']), _coord(msg['y']));
        break;
      case 'mouse_double':
        _input?.tap(_coord(msg['x']), _coord(msg['y']));
        break;
      case 'mouse_scroll':
        _input?.scroll(
          _coord(msg['x']),
          _coord(msg['y']),
          _coord(msg['dx']),
          _coord(msg['dy']),
        );
        break;
      case 'key_down':
        _input?.keyEvent(msg['key'] is String ? msg['key'] as String : '', true);
        break;
      case 'key_up':
        _input?.keyEvent(msg['key'] is String ? msg['key'] as String : '', false);
        break;
      case 'key_type':
      case 'text_input':
        _input?.typeText(msg['text'] is String ? msg['text'] as String : '');
        break;
    }
  }

  /// Safely coerce a JSON value to double; malformed/missing values become 0
  /// instead of throwing on `null + .0`.
  static double _coord(dynamic v) => v is num ? v.toDouble() : 0.0;

  Future<void> _streamFrame() async {
    if (!_running) return;
    try {
      final jpeg = await screenCapture.captureJpeg();
      if (jpeg != null && _running) {
        socket.add(Protocol.packJpeg(jpeg));
        // Backpressure: without flush the OS send buffer can absorb a
        // slow/blocked viewer and every session would keep capturing
        // frames that are silently dropped by TCP later.
        await socket.flush().timeout(const Duration(seconds: 2), onTimeout: () {});
      }
    } catch (_) {
      // Slow client or broken pipe — drop the session instead of hanging.
      close();
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
      onError: (e) {
        if (!completer.isCompleted) completer.completeError(e);
      },
      onDone: () {
        if (!completer.isCompleted) completer.completeError('closed');
      },
    );
    // If the caller times out, make sure the temporary listener is removed
    // so it cannot race with the main socket.listen below.
    return completer.future.whenComplete(() => sub.cancel());
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
    _idleTimer?.cancel();
    try {
      _socketSub?.cancel();
    } catch (_) {}
    try {
      socket.destroy();
    } catch (_) {}
    if (!_done.isCompleted) _done.complete();
  }
}
