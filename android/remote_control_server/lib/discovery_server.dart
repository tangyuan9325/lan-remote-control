/// UDP device discovery server for Android.
/// Listens on port 9000 for DISCOVER broadcasts and replies with device info.
library;

import 'dart:convert';
import 'dart:io';

class DiscoveryServer {
  static const int discoveryPort = 9000;
  static const List<int> _magic = [68, 73, 83, 67, 79, 86, 69, 82]; // "DISCOVER"

  final int controlPort;
  final String hostname;
  final bool passwordRequired;
  RawDatagramSocket? _socket;
  bool _running = false;

  DiscoveryServer({
    this.controlPort = 9001,
    required this.hostname,
    this.passwordRequired = false,
  });

  Future<void> start() async {
    if (_running) return;
    try {
      _socket = await RawDatagramSocket.bind(InternetAddress.anyIPv4, discoveryPort);
      _running = true;
      _socket!.listen((event) {
        if (event == RawSocketEvent.read) {
          final datagram = _socket!.receive();
          if (datagram != null && _listEquals(datagram.data, _magic)) {
            _reply(datagram.address, datagram.port);
          }
        }
      });
    } catch (e) {
      // Port may be in use; ignore
    }
  }

  void _reply(InternetAddress address, int port) async {
    final localIp = await _getLocalIp();
    final reply = {
      'type': 'discovery_response',
      'hostname': hostname,
      'ip': localIp,
      'port': controlPort,
      'os': 'Android ${Platform.operatingSystemVersion}',
      'version': '1.2.0',
      'password_required': passwordRequired,
    };
    try {
      _socket!.send(utf8.encode(jsonEncode(reply)), address, port);
    } catch (_) {}
  }

  Future<String> _getLocalIp() async {
    try {
      final interfaces = await NetworkInterface.list();
      for (final interface in interfaces) {
        for (final addr in interface.addresses) {
          if (addr.type == InternetAddressType.IPv4 && !addr.isLoopback) {
            return addr.address;
          }
        }
      }
    } catch (_) {}
    return '0.0.0.0';
  }

  bool _listEquals(List<int> a, List<int> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }

  void stop() {
    _running = false;
    _socket?.close();
    _socket = null;
  }
}
