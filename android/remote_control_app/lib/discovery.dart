/// UDP LAN device discovery.
/// Broadcasts DISCOVER and collects discovery_response replies.
library;

import 'dart:convert';
import 'dart:io';
import 'protocol.dart';

class DeviceDiscovery {
  static const int discoveryPort = 9000;
  static const List<int> _magic = [68, 73, 83, 67, 79, 86, 69, 82]; // "DISCOVER"

  /// Broadcast discovery and return all found devices.
  static Future<List<DeviceInfo>> discover({Duration timeout = const Duration(seconds: 2)}) async {
    final devices = <String, DeviceInfo>{};
    RawDatagramSocket? socket;

    try {
      socket = await RawDatagramSocket.bind(InternetAddress.anyIPv4, 0);
      socket.broadcastEnabled = true;

      // Send 3 broadcasts for reliability
      for (var i = 0; i < 3; i++) {
        try {
          socket.send(_magic, InternetAddress('255.255.255.255'), discoveryPort);
        } catch (_) {}
        await Future.delayed(const Duration(milliseconds: 100));
      }

      final deadline = DateTime.now().add(timeout);
      while (DateTime.now().isBefore(deadline)) {
        final event = socket.receive();
        if (event == null) {
          // Wait briefly for next event
          await Future.delayed(const Duration(milliseconds: 50));
          continue;
        }
        try {
          final data = utf8.decode(event.data);
          final json = jsonDecode(data) as Map<String, dynamic>;
          if (json['type'] == 'discovery_response') {
            final device = DeviceInfo.fromJson(json);
            final key = '${device.ip}:${device.port}';
            devices[key] = device;
          }
        } catch (_) {
          // Ignore malformed packets
        }
      }
    } catch (e) {
      // Discovery failed; return empty
    } finally {
      socket?.close();
    }

    return devices.values.toList();
  }
}
