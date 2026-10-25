import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'discovery_server.dart';
import 'control_server.dart';
import 'screen_capture.dart';
import 'input_simulator.dart';

void main() {
  runApp(const RemoteControlServerApp());
}

class RemoteControlServerApp extends StatelessWidget {
  const RemoteControlServerApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'LAN Remote Control Server',
      theme: ThemeData(
        primarySwatch: Colors.blue,
        useMaterial3: true,
      ),
      home: const ServerHomePage(),
    );
  }
}

class ServerHomePage extends StatefulWidget {
  const ServerHomePage({super.key});

  @override
  State<ServerHomePage> createState() => _ServerHomePageState();
}

class _ServerHomePageState extends State<ServerHomePage> {
  final ScreenCapture _screenCapture = ScreenCapture();
  DiscoveryServer? _discovery;
  ControlServer? _controlServer;

  bool _isRunning = false;
  bool _screenReady = false;
  bool _accessibilityReady = false;
  String _status = '未启动';
  String _deviceName = 'Android Device';

  // Connection password (persisted to disk). Empty string = no password.
  String _password = '';
  static const String _prefsPath = 'server_config.json';

  @override
  void initState() {
    super.initState();
    _loadDeviceName();
    _loadPassword();
    _checkPermissions();
  }

  Future<void> _loadPassword() async {
    try {
      final dir = await _configDir();
      final f = File('${dir.path}/$_prefsPath');
      if (await f.exists()) {
        final json = jsonDecode(await f.readAsString());
        final pw = json['password'];
        if (mounted && pw is String) setState(() => _password = pw);
      }
    } catch (_) {}
  }

  Future<void> _savePassword(String pw) async {
    _password = pw;
    try {
      final dir = await _configDir();
      final f = File('${dir.path}/$_prefsPath');
      await f.writeAsString(jsonEncode({'password': pw}));
    } catch (_) {}
  }

  Future<Directory> _configDir() async {
    // Application support directory without pulling in another dependency:
    // use the app's documents dir via dart:io on Android.
    final dir = Directory('/data/user/0/com.example.remote_control_server/app_flutter');
    if (await dir.exists()) return dir;
    return Directory.systemTemp.createTempSync('rcs');
  }

  Future<void> _loadDeviceName() async {
    try {
      final name = await const MethodChannel('com.example.remote_control_server/device')
          .invokeMethod<String>('getDeviceName');
      if (name != null && mounted) {
        setState(() => _deviceName = name);
      }
    } catch (_) {}
  }

  Future<void> _checkPermissions() async {
    final input = InputSimulator(screenWidth: 1080, screenHeight: 1920);
    final enabled = await input.isAccessibilityEnabled();
    if (mounted) setState(() => _accessibilityReady = enabled);
  }

  Future<void> _startServer() async {
    // Start screen capture
    await _screenCapture.startProjection();
    await _screenCapture.initialize();

    if (!_screenCapture.isInitialized) {
      setState(() => _status = '屏幕捕获权限被拒绝');
      return;
    }

    setState(() {
      _screenReady = true;
      _status = '正在启动...';
    });

    // Start discovery
    _discovery = DiscoveryServer(
      controlPort: 9001,
      hostname: _deviceName,
    );
    await _discovery!.start();

    // Start control server. A configured password is enforced on every
    // connection (constant-time compare inside ClientSession); an empty
    // password means open access, which the UI warns about.
    _controlServer = ControlServer(
      port: 9001,
      screenCapture: _screenCapture,
      password: _password.isEmpty ? null : _password,
    );
    await _controlServer!.start();

    setState(() {
      _isRunning = true;
      _status = '运行中 - 等待连接';
    });
  }

  Future<void> _stopServer() async {
    await _controlServer?.stop();
    _discovery?.stop();
    await _screenCapture.stop();

    setState(() {
      _isRunning = false;
      _screenReady = false;
      _status = '已停止';
    });
  }

  Future<void> _editPassword() async {
    final controller = TextEditingController(text: _password);
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('设置连接密码'),
        content: TextField(
          controller: controller,
          obscureText: true,
          decoration: const InputDecoration(hintText: '留空表示不启用密码'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, controller.text),
            child: const Text('保存'),
          ),
        ],
      ),
    );
    if (result != null) {
      await _savePassword(result.trim());
      if (mounted) setState(() {});
    }
  }

  @override
  void dispose() {
    _stopServer();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('远程控制 - 被控端'),
        centerTitle: true,
      ),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Status card
            Card(
              elevation: 4,
              child: Padding(
                padding: const EdgeInsets.all(20.0),
                child: Column(
                  children: [
                    Icon(
                      _isRunning ? Icons.tv : Icons.tv_off,
                      size: 64,
                      color: _isRunning ? Colors.green : Colors.grey,
                    ),
                    const SizedBox(height: 12),
                    Text(
                      _status,
                      style: const TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text('设备名: $_deviceName'),
                    const Text('端口: 9001'),
                  ],
                ),
              ),
            ),

            const SizedBox(height: 24),

            // Connection password
            Card(
              child: ListTile(
                leading: Icon(
                  _password.isEmpty ? Icons.lock_open : Icons.lock,
                  color: _password.isEmpty ? Colors.orange : Colors.green,
                ),
                title: const Text('连接密码'),
                subtitle: Text(_password.isEmpty ? '未设置（任何局域网设备可连接）' : '已设置'),
                trailing: const Icon(Icons.edit),
                onTap: _isRunning ? null : _editPassword,
              ),
            ),

            // Permission status
            _PermissionTile(
              icon: Icons.screen_share,
              title: '屏幕捕获',
              subtitle: _screenReady ? '已授权' : '需要授权',
              ok: _screenReady,
            ),
            _PermissionTile(
              icon: Icons.accessibility,
              title: '无障碍服务',
              subtitle: _accessibilityReady ? '已启用' : '需要启用',
              ok: _accessibilityReady,
              onTap: () async {
                final input = InputSimulator(screenWidth: 1080, screenHeight: 1920);
                await input.openAccessibilitySettings();
              },
            ),

            const Spacer(),

            // Start/Stop button
            ElevatedButton(
              onPressed: _isRunning ? _stopServer : _startServer,
              style: ElevatedButton.styleFrom(
                padding: const EdgeInsets.symmetric(vertical: 16),
                backgroundColor: _isRunning ? Colors.red : Colors.green,
                foregroundColor: Colors.white,
              ),
              child: Text(
                _isRunning ? '停止服务' : '启动服务',
                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
            ),

            const SizedBox(height: 12),
            const Text(
              '确保控制端与本设备在同一局域网内',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey),
            ),
          ],
        ),
      ),
    );
  }
}

class _PermissionTile extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final bool ok;
  final VoidCallback? onTap;

  const _PermissionTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.ok,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ListTile(
        leading: Icon(icon, color: ok ? Colors.green : Colors.orange),
        title: Text(title),
        subtitle: Text(subtitle),
        trailing: Icon(
          ok ? Icons.check_circle : Icons.error,
          color: ok ? Colors.green : Colors.orange,
        ),
        onTap: onTap,
      ),
    );
  }
}
