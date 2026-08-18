import 'package:flutter/material.dart';
import 'discovery.dart';
import 'protocol.dart';
import 'connection.dart';
import 'remote_view.dart';

void main() {
  runApp(const RemoteControlApp());
}

class RemoteControlApp extends StatelessWidget {
  const RemoteControlApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'LAN Remote Control',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.blueGrey),
        useMaterial3: true,
      ),
      home: const DeviceListPage(),
      debugShowCheckedModeBanner: false,
    );
  }
}

class DeviceListPage extends StatefulWidget {
  const DeviceListPage({super.key});

  @override
  State<DeviceListPage> createState() => _DeviceListPageState();
}

class _DeviceListPageState extends State<DeviceListPage> {
  List<DeviceInfo> _devices = [];
  bool _scanning = false;
  bool _autoRefresh = true;

  @override
  void initState() {
    super.initState();
    _startScan();
  }

  Future<void> _startScan() async {
    setState(() => _scanning = true);
    final devices = await DeviceDiscovery.discover();
    if (!mounted) return;
    setState(() {
      _devices = devices;
      _scanning = false;
    });
    // Auto refresh every 5s
    if (_autoRefresh) {
      Future.delayed(const Duration(seconds: 5), _startScan);
    }
  }

  Future<void> _connect(DeviceInfo device) async {
    String? password;
    if (device.passwordRequired) {
      password = await _askPassword(device.hostname);
      if (password == null) return;
    }

    final conn = RemoteConnection(
      host: device.ip,
      port: device.port,
      password: password,
    );
    final ok = await conn.connect();
    if (!ok && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Connection failed')),
      );
      conn.dispose();
      return;
    }

    if (!mounted) return;
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => RemoteView(connection: conn),
      ),
    ).then((_) => conn.dispose());
  }

  Future<String?> _askPassword(String hostname) {
    final controller = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Password for $hostname'),
        content: TextField(
          controller: controller,
          obscureText: true,
          autofocus: true,
          decoration: const InputDecoration(hintText: 'Enter password'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, null),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, controller.text),
            child: const Text('Connect'),
          ),
        ],
      ),
    );
  }

  Future<void> _connectByIp() async {
    final ipController = TextEditingController();
    final portController = TextEditingController(text: '9001');
    final pwdController = TextEditingController();

    final result = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Connect by IP'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: ipController,
              decoration: const InputDecoration(
                labelText: 'IP Address',
                hintText: '192.168.1.50',
              ),
              keyboardType: TextInputType.number,
            ),
            TextField(
              controller: portController,
              decoration: const InputDecoration(labelText: 'Port'),
              keyboardType: TextInputType.number,
            ),
            TextField(
              controller: pwdController,
              decoration: const InputDecoration(
                labelText: 'Password (optional)',
              ),
              obscureText: true,
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Connect'),
          ),
        ],
      ),
    );

    if (result != true || ipController.text.isEmpty) return;

    final conn = RemoteConnection(
      host: ipController.text.trim(),
      port: int.tryParse(portController.text) ?? 9001,
      password: pwdController.text.isEmpty ? null : pwdController.text,
    );
    final ok = await conn.connect();
    if (!ok && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Connection failed')),
      );
      conn.dispose();
      return;
    }
    if (!mounted) return;
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => RemoteView(connection: conn)),
    ).then((_) => conn.dispose());
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('LAN Remote Control'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _scanning ? null : _startScan,
          ),
          PopupMenuButton<String>(
            onSelected: (v) {
              if (v == 'ip') _connectByIp();
            },
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'ip', child: Text('Connect by IP')),
            ],
          ),
        ],
      ),
      body: Column(
        children: [
          SwitchListTile(
            title: const Text('Auto refresh'),
            value: _autoRefresh,
            onChanged: (v) {
              setState(() => _autoRefresh = v);
              if (v) _startScan();
            },
          ),
          const Divider(height: 1),
          Expanded(
            child: _scanning && _devices.isEmpty
                ? const Center(child: CircularProgressIndicator())
                : _devices.isEmpty
                    ? const Center(
                        child: Text(
                          'No devices found.\nMake sure the server is running on the same LAN.',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: Colors.grey),
                        ),
                      )
                    : ListView.builder(
                        itemCount: _devices.length,
                        itemBuilder: (ctx, i) {
                          final d = _devices[i];
                          return ListTile(
                            leading: const Icon(Icons.computer),
                            title: Text(d.hostname),
                            subtitle: Text('${d.ip}:${d.port}  \u2022  ${d.os}'),
                            trailing: d.passwordRequired
                                ? const Icon(Icons.lock, size: 18)
                                : const Icon(Icons.chevron_right),
                            onTap: () => _connect(d),
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }
}
