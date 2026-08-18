/// Remote desktop view widget.
/// Displays JPEG frames and forwards touch gestures as mouse events.
library;

import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'connection.dart';

class RemoteView extends StatefulWidget {
  final RemoteConnection connection;

  const RemoteView({super.key, required this.connection});

  @override
  State<RemoteView> createState() => _RemoteViewState();
}

class _RemoteViewState extends State<RemoteView> {
  Uint8List? _currentFrame;
  String _status = '';
  Offset? _lastTouch;

  @override
  void initState() {
    super.initState();
    widget.connection.frameStream.listen((frame) {
      if (mounted) {
        setState(() => _currentFrame = frame);
      }
    });
    widget.connection.statusStream.listen((s) {
      if (mounted) setState(() => _status = s);
    });
  }

  /// Convert a local touch position to normalized remote coords (0..1).
  Offset _toNormalized(Offset local, Size widgetSize) {
    final rw = widget.connection.remoteWidth;
    final rh = widget.connection.remoteHeight;
    if (rw == 0 || rh == 0) return Offset.zero;

    final scale = (widgetSize.width / rw < widgetSize.height / rh)
        ? widgetSize.width / rw
        : widgetSize.height / rh;
    final imgW = rw * scale;
    final imgH = rh * scale;
    final offsetX = (widgetSize.width - imgW) / 2;
    final offsetY = (widgetSize.height - imgH) / 2;

    final nx = ((local.dx - offsetX) / imgW).clamp(0.0, 1.0);
    final ny = ((local.dy - offsetY) / imgH).clamp(0.0, 1.0);
    return Offset(nx, ny);
  }

  void _send(Map<String, dynamic> event) {
    widget.connection.sendInput(event);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        title: Text(_status.isEmpty ? 'Remote' : _status),
        backgroundColor: Colors.grey[900],
        actions: [
          IconButton(
            icon: const Icon(Icons.keyboard),
            tooltip: 'Keyboard',
            onPressed: _showKeyboardDialog,
          ),
        ],
      ),
      body: GestureDetector(
        onPanStart: (details) {
          final size = context.size ?? Size.zero;
          final pos = _toNormalized(details.localPosition, size);
          _lastTouch = pos;
          _send({'type': 'mouse_down', 'x': pos.dx, 'y': pos.dy, 'button': 'left'});
        },
        onPanUpdate: (details) {
          final size = context.size ?? Size.zero;
          final pos = _toNormalized(details.localPosition, size);
          _lastTouch = pos;
          _send({'type': 'mouse_move', 'x': pos.dx, 'y': pos.dy});
        },
        onPanEnd: (details) {
          if (_lastTouch != null) {
            _send({
              'type': 'mouse_up',
              'x': _lastTouch!.dx,
              'y': _lastTouch!.dy,
              'button': 'left'
            });
          }
        },
        onTapUp: (details) {
          final size = context.size ?? Size.zero;
          final pos = _toNormalized(details.localPosition, size);
          _send({'type': 'mouse_click', 'x': pos.dx, 'y': pos.dy, 'button': 'left'});
        },
        onDoubleTapUp: (details) {
          final size = context.size ?? Size.zero;
          final pos = _toNormalized(details.localPosition, size);
          _send({'type': 'mouse_double', 'x': pos.dx, 'y': pos.dy, 'button': 'left'});
        },
        onLongPressStart: (details) {
          final size = context.size ?? Size.zero;
          final pos = _toNormalized(details.localPosition, size);
          _send({'type': 'mouse_down', 'x': pos.dx, 'y': pos.dy, 'button': 'right'});
        },
        onLongPressEnd: (details) {
          final size = context.size ?? Size.zero;
          final pos = _lastTouch ?? Offset.zero;
          _send({'type': 'mouse_up', 'x': pos.dx, 'y': pos.dy, 'button': 'right'});
        },
        child: Center(
          child: _currentFrame != null
              ? Image.memory(
                  _currentFrame!,
                  fit: BoxFit.contain,
                  gaplessPlayback: true,
                )
              : const Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    CircularProgressIndicator(color: Colors.white),
                    SizedBox(height: 16),
                    Text('Waiting for screen...',
                        style: TextStyle(color: Colors.white70)),
                  ],
                ),
        ),
      ),
    );
  }

  void _showKeyboardDialog() {
    final controller = TextEditingController();
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Send Text'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(hintText: 'Type text to send'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () {
              if (controller.text.isNotEmpty) {
                _send({'type': 'key_type', 'text': controller.text});
              }
              Navigator.pop(ctx);
            },
            child: const Text('Send'),
          ),
        ],
      ),
    );
  }
}
