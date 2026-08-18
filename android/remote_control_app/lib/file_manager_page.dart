/// File manager page for browsing, downloading, and uploading remote files.
library;

import 'dart:io';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'connection.dart';
import 'protocol.dart';

class FileManagerPage extends StatefulWidget {
  final RemoteConnection connection;
  const FileManagerPage({super.key, required this.connection});

  @override
  State<FileManagerPage> createState() => _FileManagerPageState();
}

class _FileManagerPageState extends State<FileManagerPage> {
  String _currentPath = '';
  List<FileEntry> _files = [];
  bool _loading = false;
  bool _downloading = false;

  @override
  void initState() {
    super.initState();
    widget.connection.fileListStream.listen(_onFileList);
    widget.connection.downloadStartStream.listen((_) {
      if (mounted) setState(() => _downloading = true);
    });
    widget.connection.downloadCompleteStream.listen(_onDownloadComplete);
    widget.connection.uploadDoneStream.listen((_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('上传完成')));
        _loadFiles(_currentPath);
      }
    });
    widget.connection.fileErrorStream.listen((err) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('错误: $err')));
        setState(() { _loading = false; _downloading = false; });
      }
    });
    _loadFiles('');
  }

  void _loadFiles(String path) {
    setState(() { _currentPath = path; _loading = true; });
    widget.connection.listFiles(path);
  }

  void _onFileList(Map<String, dynamic> data) {
    if (!mounted) return;
    setState(() {
      _currentPath = data['path'] ?? _currentPath;
      _files = (data['files'] as List?)?.map((f) => FileEntry.fromJson(f)).toList() ?? [];
      _loading = false;
    });
  }

  void _onDownloadComplete(Map<String, dynamic> data) async {
    if (!mounted) return;
    final name = data['name'] ?? 'download';
    final bytes = data['data'] as Uint8List;
    try {
      final dir = await getExternalStorageDirectory() ?? await getApplicationDocumentsDirectory();
      final file = File('${dir.path}/$name');
      await file.writeAsBytes(bytes);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('下载完成: ${file.path}')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('保存失败: $e')));
      }
    }
    if (mounted) setState(() => _downloading = false);
  }

  Future<void> _pickAndUpload() async {
    final controller = TextEditingController();
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('上传文件'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(hintText: '输入本地文件路径'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          TextButton(onPressed: () => Navigator.pop(ctx, controller.text), child: const Text('上传')),
        ],
      ),
    );
    if (result != null && result.isNotEmpty) {
      await widget.connection.uploadFile(result, _currentPath);
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('正在上传...')));
    }
  }

  String _formatSize(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1048576) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    if (bytes < 1073741824) return '${(bytes / 1048576).toStringAsFixed(1)} MB';
    return '${(bytes / 1073741824).toStringAsFixed(1)} GB';
  }

  void _goUp() {
    if (_currentPath.isEmpty) return;
    final parts = _currentPath.replaceAll('\\', '/').split('/').where((p) => p.isNotEmpty).toList();
    parts.removeLast();
    _loadFiles(parts.join('/'));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('文件管理'),
        actions: [IconButton(icon: const Icon(Icons.upload_file), onPressed: _pickAndUpload)],
      ),
      body: Column(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            color: Colors.grey[100],
            child: Row(
              children: [
                IconButton(icon: const Icon(Icons.arrow_upward), onPressed: _currentPath.isEmpty ? null : _goUp),
                Expanded(child: Text(_currentPath.isEmpty ? '/ (根目录)' : _currentPath, style: const TextStyle(fontSize: 13), overflow: TextOverflow.ellipsis)),
              ],
            ),
          ),
          if (_downloading) const LinearProgressIndicator(),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _files.isEmpty
                    ? const Center(child: Text('空目录'))
                    : ListView.builder(
                        itemCount: _files.length,
                        itemBuilder: (ctx, i) {
                          final f = _files[i];
                          return ListTile(
                            leading: Icon(f.isDir ? Icons.folder : Icons.insert_drive_file, color: f.isDir ? Colors.amber[700] : Colors.grey),
                            title: Text(f.name),
                            subtitle: Text(f.isDir ? '文件夹' : '${_formatSize(f.size)}  ${f.modified}'),
                            trailing: f.isDir ? const Icon(Icons.chevron_right) : IconButton(
                              icon: const Icon(Icons.download),
                              onPressed: () => widget.connection.downloadFile(_currentPath.isEmpty ? f.name : '$_currentPath/${f.name}'),
                            ),
                            onTap: () {
                              if (f.isDir) _loadFiles(_currentPath.isEmpty ? f.name : '$_currentPath/${f.name}');
                            },
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }
}
