#!/usr/bin/env python3
"""
LAN Remote Control - Viewer (controller side)
Runs on a Windows PC to discover and control remote machines.

Usage:
  python main.py
"""

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

from discovery import discover, DiscoveryThread
from viewer_window import RemoteWindow


class ViewerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("LAN Remote Control - Viewer")
        self.root.geometry("640x480")

        # Toolbar
        toolbar = ttk.Frame(root, padding=8)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="Refresh", command=self.refresh_now).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Connect by IP", command=self.connect_by_ip).pack(
            side=tk.LEFT, padx=8
        )
        self.auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="Auto refresh", variable=self.auto_var,
                        command=self._toggle_auto).pack(side=tk.LEFT)

        # Device list
        columns = ("hostname", "ip", "port", "os", "password")
        self.tree = ttk.Treeview(root, columns=columns, show="headings")
        for col, text, w in [
            ("hostname", "Hostname", 160),
            ("ip", "IP Address", 130),
            ("port", "Port", 60),
            ("os", "OS", 120),
            ("password", "Password", 80),
        ]:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor=tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.tree.bind("<Double-1>", lambda e: self.connect_selected())

        # Status
        self.status = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self.status, anchor=tk.W,
                  padding=(8, 4)).pack(fill=tk.X)

        self._devices = []
        self._discovery_thread = None
        self._toggle_auto()
        self.refresh_now()

    def refresh_now(self):
        self.status.set("Searching LAN...")
        self.root.update_idletasks()
        devices = discover(timeout=2.0)
        self._devices = devices
        self._populate(devices)
        self.status.set(f"Found {len(devices)} device(s)")

    def _populate(self, devices):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for d in devices:
            self.tree.insert("", tk.END, values=(
                d.get("hostname", ""),
                d.get("ip", ""),
                d.get("port", 9001),
                d.get("os", ""),
                "Yes" if d.get("password_required") else "No",
            ))

    def _toggle_auto(self):
        if self.auto_var.get():
            if not self._discovery_thread:
                self._discovery_thread = DiscoveryThread(self._on_devices)
                self._discovery_thread.start()
        else:
            if self._discovery_thread:
                self._discovery_thread.stop()
                self._discovery_thread = None

    def _on_devices(self, devices):
        # Called from background thread; marshal to UI thread
        self.root.after(0, self._populate, devices)
        self.root.after(0, lambda: self.status.set(
            f"Found {len(devices)} device(s) (auto)"))

    def connect_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select", "Please select a device first.")
            return
        values = self.tree.item(sel[0], "values")
        hostname, ip, port, os_, pwd_required = values
        password = None
        if pwd_required == "Yes":
            password = simpledialog.askstring(
                "Password", f"Enter password for {hostname}:", show="*")
            if password is None:
                return
        RemoteWindow(self.root, ip, int(port), password)

    def connect_by_ip(self):
        ip = simpledialog.askstring("Connect", "Enter IP address:")
        if not ip:
            return
        port = simpledialog.askinteger("Port", "Port:", initialvalue=9001)
        if not port:
            return
        password = simpledialog.askstring("Password", "Password (optional):",
                                          show="*")
        RemoteWindow(self.root, ip, port, password)


def main():
    root = tk.Tk()
    app = ViewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
