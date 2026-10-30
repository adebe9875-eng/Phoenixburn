#!/usr/bin/env python3
"""
PhoenixBurn - a real ISO -> USB burner
--------------------------------------
Python handles the experience (window, buttons, downloads).
C handles the bytes (finding drives, writing, verifying).

- Only REMOVABLE / USB drives are listed. Your internal disk is never shown.
- You must type the drive path (like /dev/sdb) to confirm before anything is erased.
- Built-in ISO downloader with checksum check (SHA-256).
- After writing, every byte is read back and compared with the ISO.
"""
import os
import re
import sys
import pwd
import hashlib
import subprocess
import tempfile
import threading
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

# =====================================================================
# ROOT: writing to a drive needs administrator rights
# =====================================================================
if os.geteuid() != 0:
    print("[!] PhoenixBurn needs administrator rights to write to a drive. Asking sudo...")
    os.execvp("sudo", ["sudo", "-E", sys.executable] + sys.argv)

# =====================================================================
# EMBEDDED C ENGINE: SCAN + BURN + VERIFY
# =====================================================================
C_ENGINE = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <limits.h>
#include <sys/stat.h>
#include <sys/types.h>

#define CHUNK (1024 * 1024)

static void read_sysfs(const char *path, char *buf, size_t len) {
    FILE *f = fopen(path, "r");
    buf[0] = 0;
    if (f) {
        if (fgets(buf, len, f)) buf[strcspn(buf, "\n")] = 0;
        fclose(f);
    }
    if (!buf[0]) snprintf(buf, len, "Unknown");
}

/* A drive plugged in through USB lives under a ".../usb..." path in sysfs */
static int is_usb(const char *name) {
    char link[PATH_MAX], real[PATH_MAX];
    snprintf(link, sizeof(link), "/sys/block/%s", name);
    if (!realpath(link, real)) return 0;
    return strstr(real, "/usb") != NULL;
}

static int skip_name(const char *n) {
    return n[0] == '.' || !strncmp(n, "loop", 4) || !strncmp(n, "ram", 3) ||
           !strncmp(n, "dm-", 3) || !strncmp(n, "zram", 4) || !strncmp(n, "sr", 2) ||
           !strncmp(n, "md", 2) || !strncmp(n, "nbd", 3) || !strncmp(n, "fd", 2);
}

/* Lists ONLY removable / USB drives. Internal disks are never printed. */
static int cmd_scan(void) {
    DIR *dir = opendir("/sys/block");
    if (!dir) { fprintf(stderr, "Cannot read /sys/block\n"); return 1; }
    printf("SCAN_START\n");
    struct dirent *e;
    while ((e = readdir(dir)) != NULL) {
        const char *n = e->d_name;
        if (skip_name(n)) continue;
        char path[PATH_MAX], removable[16], model[256];
        snprintf(path, sizeof(path), "/sys/block/%s/removable", n);
        read_sysfs(path, removable, sizeof(removable));
        if (strcmp(removable, "1") != 0 && !is_usb(n)) continue;
        long long sectors = 0;
        snprintf(path, sizeof(path), "/sys/block/%s/size", n);
        FILE *f = fopen(path, "r");
        if (f) { if (fscanf(f, "%lld", &sectors) != 1) sectors = 0; fclose(f); }
        if (sectors <= 0) continue;               /* empty card-reader slot */
        snprintf(path, sizeof(path), "/sys/block/%s/device/model", n);
        read_sysfs(path, model, sizeof(model));
        for (char *p = model; *p; p++) if (*p == '|') *p = ' ';
        printf("DISK|/dev/%s|%s|%.1f GB|USB\n", n, model,
               (sectors * 512.0) / (1024.0 * 1024.0 * 1024.0));
    }
    printf("SCAN_END\n");
    closedir(dir);
    return 0;
}

static ssize_t read_full(int fd, char *buf, size_t n) {
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, buf + got, n - got);
        if (r < 0) { if (errno == EINTR) continue; return -1; }
        if (r == 0) break;
        got += (size_t)r;
    }
    return (ssize_t)got;
}

static int write_full(int fd, const char *buf, size_t n) {
    size_t put = 0;
    while (put < n) {
        ssize_t w = write(fd, buf + put, n - put);
        if (w < 0) { if (errno == EINTR) continue; return -1; }
        put += (size_t)w;
    }
    return 0;
}

static int cmd_burn(const char *src, const char *dst) {
    int sfd = open(src, O_RDONLY);
    if (sfd < 0) { perror("Cannot open the ISO"); return 1; }
    struct stat st;
    if (fstat(sfd, &st) < 0 || st.st_size <= 0) {
        fprintf(stderr, "The ISO file is empty or unreadable\n"); return 1;
    }
    int dfd = open(dst, O_RDWR | O_SYNC);
    if (dfd < 0) { perror("Cannot open the drive"); return 1; }

#ifndef ALLOW_FILE
    struct stat dst_st;
    if (fstat(dfd, &dst_st) < 0 || !S_ISBLK(dst_st.st_mode)) {
        fprintf(stderr, "%s is not a drive. Refusing to write.\n", dst); return 1;
    }
#endif

    off_t dsize = lseek(dfd, 0, SEEK_END);
    lseek(dfd, 0, SEEK_SET);
    if (dsize < st.st_size) {
        fprintf(stderr, "The ISO (%lld bytes) is bigger than the drive (%lld bytes)\n",
                (long long)st.st_size, (long long)dsize);
        return 1;
    }

    char *a = malloc(CHUNK), *b = malloc(CHUNK);
    if (!a || !b) { fprintf(stderr, "Out of memory\n"); return 1; }

    long long total = st.st_size, done = 0;
    int last = -1;
    printf("START\n"); fflush(stdout);
    ssize_t r;
    while ((r = read_full(sfd, a, CHUNK)) > 0) {
        if (write_full(dfd, a, (size_t)r) < 0) { perror("Write failed"); return 1; }
        done += r;
        int pct = (int)(done * 100 / total);
        if (pct != last) { printf("P:%d\n", pct); fflush(stdout); last = pct; }
    }
    if (r < 0) { perror("Read failed"); return 1; }

    fsync(dfd);
    posix_fadvise(dfd, 0, 0, POSIX_FADV_DONTNEED);   /* make verify read the real drive */

    printf("VERIFY\n"); fflush(stdout);
    lseek(sfd, 0, SEEK_SET);
    lseek(dfd, 0, SEEK_SET);
    done = 0; last = -1;
    while ((r = read_full(sfd, a, CHUNK)) > 0) {
        if (read_full(dfd, b, (size_t)r) != r || memcmp(a, b, (size_t)r) != 0) {
            printf("FAIL\n"); fflush(stdout); return 2;
        }
        done += r;
        int pct = (int)(done * 100 / total);
        if (pct != last) { printf("V:%d\n", pct); fflush(stdout); last = pct; }
    }
    printf("OK\n"); fflush(stdout);
    close(sfd); close(dfd);
    free(a); free(b);
    return 0;
}

int main(int argc, char *argv[]) {
    if (argc < 2) { fprintf(stderr, "Usage: engine [scan | burn <iso> <drive>]\n"); return 1; }
    if (!strcmp(argv[1], "scan")) return cmd_scan();
    if (!strcmp(argv[1], "burn") && argc == 4) return cmd_burn(argv[2], argv[3]);
    fprintf(stderr, "Invalid command\n");
    return 1;
}
"""

_engine_path = None

def get_engine():
    """Compile the C engine once per run, in a private folder (only root can touch it)."""
    global _engine_path
    if _engine_path and os.path.exists(_engine_path):
        return _engine_path
    folder = tempfile.mkdtemp(prefix="phoenixburn_")
    src = os.path.join(folder, "engine.c")
    out = os.path.join(folder, "engine")
    with open(src, "w") as f:
        f.write(C_ENGINE)
    try:
        r = subprocess.run(["gcc", "-O2", "-o", out, src], capture_output=True, text=True)
    except FileNotFoundError:
        raise Exception("gcc was not found. Install it first:  sudo apt install gcc")
    if r.returncode:
        raise Exception(r.stderr)
    _engine_path = out
    return out

def run_engine(args):
    try:
        out = subprocess.check_output([get_engine()] + args, text=True, stderr=subprocess.PIPE)
        return out.strip().split("\n"), None
    except subprocess.CalledProcessError as e:
        err = e.stderr if isinstance(e.stderr, str) else e.stderr.decode("utf-8", errors="replace")
        return None, err
    except Exception as e:
        return None, str(e)

# =====================================================================
# SAFETY HELPERS
# =====================================================================
def root_disk_name():
    """Name of the disk that holds the running system (never offered as a target)."""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                p = line.split()
                if len(p) >= 2 and p[1] == "/" and p[0].startswith("/dev/"):
                    return re.sub(r"p?\d+$", "", os.path.basename(p[0]))
    except OSError:
        pass
    return None

def unmount_all(dev_path):
    """Unmount every partition of the drive before writing. Returns a list of errors."""
    errors = []
    try:
        with open("/proc/mounts") as f:
            mounts = [l.split() for l in f]
    except OSError:
        return errors
    pattern = re.compile(re.escape(dev_path) + r"p?\d*$")
    for parts in mounts:
        if len(parts) >= 2 and pattern.fullmatch(parts[0]):
            mp = parts[1].replace("\\040", " ")
            r = subprocess.run(["umount", mp], capture_output=True, text=True)
            if r.returncode:
                errors.append(f"{mp}: {r.stderr.strip()}")
    return errors

# =====================================================================
# ISO DOWNLOADER (finds the newest file name by itself, so links never go stale)
# =====================================================================
UA = {"User-Agent": "PhoenixBurn/1.1 (ISO downloader)"}

def http_get_text(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def resolve_ubuntu():
    base = "https://releases.ubuntu.com/24.04/"
    m = re.search(r"^([0-9a-f]{64})\s+\*?(ubuntu-24\.04[\d.]*-desktop-amd64\.iso)$",
                  http_get_text(base + "SHA256SUMS"), re.M)
    if not m:
        raise Exception("Could not find the Ubuntu ISO name")
    return base + m.group(2), m.group(2), m.group(1)

def resolve_debian():
    base = "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/"
    m = re.search(r"^([0-9a-f]{64})\s+\*?(debian-[\d.]+-amd64-netinst\.iso)$",
                  http_get_text(base + "SHA256SUMS"), re.M)
    if not m:
        raise Exception("Could not find the Debian ISO name")
    return base + m.group(2), m.group(2), m.group(1)

def resolve_alpine():
    base = "https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64/"
    m = re.search(r"file:\s*(alpine-standard-[\d.]+-x86_64\.iso)",
                  http_get_text(base + "latest-releases.yaml"))
    if not m:
        raise Exception("Could not find the Alpine ISO name")
    name = m.group(1)
    sha = None
    try:
        s = re.match(r"([0-9a-f]{64})", http_get_text(base + name + ".sha256"))
        sha = s.group(1) if s else None
    except Exception:
        pass
    return base + name, name, sha

def real_user_home():
    """Under sudo, '~' would be /root. Use the real user's folder instead."""
    user = os.environ.get("SUDO_USER")
    if user:
        try:
            return pwd.getpwnam(user).pw_dir
        except KeyError:
            pass
    return os.path.expanduser("~")

def give_back_to_user(path):
    user = os.environ.get("SUDO_USER")
    if user:
        try:
            pw = pwd.getpwnam(user)
            os.chown(path, pw.pw_uid, pw.pw_gid)
        except (KeyError, OSError):
            pass

# =====================================================================
# GUI - built around the flame, not around a disk-utility template.
# Three words replace the usual wizard labels: FUEL (the ISO),
# VESSEL (the USB drive), IGNITE (burn + verify). Progress is drawn
# as a rising flame on a canvas, not a grey bar.
# =====================================================================
INK     = "#0d0609"
PANEL   = "#170c10"
EMBER   = "#2a141a"
GOLD    = "#ffb43c"
ORANGE  = "#f7810e"
CRIMSON = "#d00a4f"
TEXT    = "#f5e9ec"
MUTED   = "#8a6b74"
OK_GREEN  = "#3ecb82"
FAIL_RED  = "#ff5577"


class FlameGauge(tk.Canvas):
    """A flame that rises and brightens with progress, instead of a progress bar."""
    def __init__(self, master, width=420, height=140):
        super().__init__(master, width=width, height=height, bg=INK, highlightthickness=0)
        self.w, self.h = width, height
        self.pct = 0
        self.label = ""
        self._draw()

    def set(self, pct, label=""):
        self.pct = max(0, min(100, pct))
        self.label = label
        self._draw()

    def _draw(self):
        self.delete("all")
        base_y = self.h - 18
        flame_h = 14 + (self.h - 40) * (self.pct / 100.0)
        cx = self.w / 2
        half = 60 + 40 * (self.pct / 100.0)
        top_color = GOLD if self.pct >= 100 else ORANGE
        # layered flame silhouette, hand-built, not an icon font
        self.create_polygon(
            cx, base_y - flame_h,
            cx - half * 0.55, base_y - flame_h * 0.55,
            cx - half * 0.3, base_y - flame_h * 0.15,
            cx - half, base_y,
            cx, base_y - flame_h * 0.05,
            cx + half, base_y,
            cx + half * 0.3, base_y - flame_h * 0.15,
            cx + half * 0.55, base_y - flame_h * 0.55,
            fill=CRIMSON, outline="", smooth=True,
        )
        inner_h = flame_h * 0.6
        self.create_polygon(
            cx, base_y - inner_h,
            cx - half * 0.3, base_y - inner_h * 0.4,
            cx - half * 0.45, base_y,
            cx, base_y - inner_h * 0.1,
            cx + half * 0.45, base_y,
            cx + half * 0.3, base_y - inner_h * 0.4,
            fill=top_color, outline="", smooth=True,
        )
        self.create_rectangle(cx - 70, base_y, cx + 70, base_y + 10, fill=PANEL, outline="")
        self.create_text(cx, self.h - 4, text=self.label, fill=MUTED, font=("Consolas", 9))
        self.create_text(cx, 14, text=f"{int(self.pct)}%", fill=TEXT, font=("Segoe UI", 11, "bold"))


class Panel(tk.Frame):
    """One stage of the flow: a word, a sentence, and whatever body it needs."""
    def __init__(self, master, word, sentence):
        super().__init__(master, bg=INK)
        tk.Label(self, text=word, bg=INK, fg=ORANGE,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=30, pady=(24, 0))
        tk.Label(self, text=sentence, bg=INK, fg=MUTED, font=("Segoe UI", 10),
                 wraplength=460, justify="left").pack(anchor="w", padx=30, pady=(2, 14))
        self.body = tk.Frame(self, bg=INK)
        self.body.pack(fill="both", expand=True, padx=30)


class PhoenixBurnGUI:
    ISO_SOURCES = {
        "Ubuntu 24.04 LTS": resolve_ubuntu,
        "Debian Netinst (latest)": resolve_debian,
        "Alpine Linux (latest)": resolve_alpine,
    }
    WORDS = ["FUEL", "VESSEL", "IGNITE"]

    def __init__(self, root):
        self.root = root
        root.title("PhoenixBurn")
        root.geometry("560x620")
        root.configure(bg=INK)
        self.drives_data = []
        self.iso_path = None
        self.stage = 0

        tk.Frame(root, bg=ORANGE, height=3).pack(fill="x")

        # the word-rail: tap a stage to jump to it once it's reachable
        rail = tk.Frame(root, bg=PANEL)
        rail.pack(fill="x")
        self.rail_lbls = []
        for i, w in enumerate(self.WORDS):
            lbl = tk.Label(rail, text=w, bg=PANEL, fg=MUTED, font=("Segoe UI", 10, "bold"),
                           padx=18, pady=10, cursor="hand2")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, i=i: self.jump(i))
            self.rail_lbls.append(lbl)

        self.stack = tk.Frame(root, bg=INK)
        self.stack.pack(fill="both", expand=True)

        self.panel_fuel   = Panel(self.stack, "FUEL",   "What should the drive carry? Pick an ISO or fetch one.")
        self.panel_vessel = Panel(self.stack, "VESSEL", "Which USB drive receives it? Only removable drives appear here - never your internal disk.")
        self.panel_ignite = Panel(self.stack, "IGNITE", "Nothing happens until you type the drive's path yourself.")
        self._build_fuel()
        self._build_vessel()
        self._build_ignite()

        self.foot = tk.Frame(root, bg=PANEL)
        self.foot.pack(fill="x", side="bottom")
        self.status_lbl = tk.Label(self.foot, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                                    wraplength=520, justify="left", anchor="w")
        self.status_lbl.pack(fill="x", padx=20, pady=(10, 4))
        nav = tk.Frame(self.foot, bg=PANEL)
        nav.pack(fill="x", padx=20, pady=(0, 14))
        self.back_btn = tk.Button(nav, text="back", command=self.go_back, bg=PANEL, fg=MUTED,
                                   relief="flat", font=("Segoe UI", 10), activebackground=PANEL)
        self.back_btn.pack(side="left")
        self.next_btn = tk.Button(nav, text="continue", command=self.go_next, bg=ORANGE, fg="white",
                                   relief="flat", font=("Segoe UI", 10, "bold"), padx=18, pady=6,
                                   activebackground=CRIMSON, activeforeground="white")
        self.next_btn.pack(side="right")

        self._show()
        self.scan_disks()

    # ---------- helpers ----------
    def ui(self, fn, *a, **kw):
        self.root.after(0, lambda: fn(*a, **kw))

    def set_status(self, text, color=MUTED):
        self.status_lbl.config(text=text, fg=color)

    def jump(self, i):
        if i <= self.stage or (i == self.stage + 1 and self._can_advance()):
            self.stage = min(i, self._max_reachable())
            self._show()

    def _max_reachable(self):
        m = 0
        if self.iso_path:
            m = 1
        if self.iso_path and self.drives_data and self.selected_drive.get() >= 0:
            m = 2
        return m

    def _can_advance(self):
        if self.stage == 0:
            return bool(self.iso_path)
        if self.stage == 1:
            return self.drives_data and self.selected_drive.get() >= 0
        return False

    def _show(self):
        for p in (self.panel_fuel, self.panel_vessel, self.panel_ignite):
            p.pack_forget()
        [self.panel_fuel, self.panel_vessel, self.panel_ignite][self.stage].pack(fill="both", expand=True)
        for i, lbl in enumerate(self.rail_lbls):
            if i == self.stage:
                lbl.config(fg=GOLD)
            elif i <= self._max_reachable():
                lbl.config(fg=TEXT)
            else:
                lbl.config(fg=MUTED)
        self.back_btn.config(state=tk.NORMAL if self.stage > 0 else tk.DISABLED)
        self.next_btn.pack_forget()
        if self.stage < 2:
            self.next_btn.pack(side="right")
        if self.stage == 2:
            self._refresh_ignite()
        self.set_status("", MUTED)

    def go_back(self):
        if self.stage > 0:
            self.stage -= 1
            self._show()

    def go_next(self):
        if not self._can_advance():
            messagebox.showinfo("Not yet", "Finish this step first.")
            return
        self.stage += 1
        self._show()

    # ---------- FUEL ----------
    def _build_fuel(self):
        body = self.panel_fuel.body
        for name in list(self.ISO_SOURCES) + ["a file already on this computer"]:
            row = tk.Frame(body, bg=EMBER, highlightbackground="#3a2530", highlightthickness=1)
            row.pack(fill="x", pady=5)
            tk.Label(row, text=name, bg=EMBER, fg=TEXT, font=("Segoe UI", 11),
                     padx=14, pady=11).pack(side="left")
            tk.Button(row, text="use this", command=lambda n=name: self.pick_iso(n),
                      bg=PANEL, fg=GOLD, relief="flat", padx=10,
                      activebackground=PANEL, activeforeground=GOLD).pack(side="right", padx=12)
        self.fuel_lbl = tk.Label(body, text="no fuel chosen yet", bg=INK, fg=MUTED,
                                  font=("Segoe UI", 10, "italic"))
        self.fuel_lbl.pack(anchor="w", pady=(10, 0))

    def pick_iso(self, name):
        if name == "a file already on this computer":
            f = filedialog.askopenfilename(filetypes=[("ISO Files", "*.iso"), ("All Files", "*.*")])
            if f:
                self.iso_path = f
                self.fuel_lbl.config(text=f"fuel: {os.path.basename(f)}", fg=OK_GREEN)
        else:
            self.download_iso(name, self.ISO_SOURCES[name])

    def download_iso(self, name, resolver):
        if not messagebox.askyesno("Fetch fuel", f"Download {name} (~1-6 GB)?\nThis may take a while."):
            return
        self.set_status(f"Looking for the latest {name}...", GOLD)
        def worker():
            part = None
            try:
                url, fname, sha = resolver()
                dl_dir = os.path.join(real_user_home(), "Downloads")
                os.makedirs(dl_dir, exist_ok=True)
                dest = os.path.join(dl_dir, fname)
                if os.path.isfile(dest):
                    self.iso_path = dest
                    self.ui(self.fuel_lbl.config, text=f"fuel: {fname} (already on disk)", fg=OK_GREEN)
                    self.ui(self.set_status, "", MUTED)
                    return
                part = dest + ".part"
                req = urllib.request.Request(url, headers=UA)
                h = hashlib.sha256(); done = 0
                with urllib.request.urlopen(req, timeout=30) as resp, open(part, "wb") as out:
                    total = int(resp.headers.get("Content-Length") or 0)
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk: break
                        out.write(chunk); h.update(chunk); done += len(chunk)
                        if total:
                            self.ui(self.set_status, f"fetching {fname}  {done//1048576}/{total//1048576} MB", GOLD)
                if total and done != total:
                    raise Exception("The download stopped early. Check your internet and try again.")
                if sha and h.hexdigest() != sha:
                    raise Exception("The downloaded file is damaged. Try again.")
                os.replace(part, dest); part = None
                give_back_to_user(dest)
                self.iso_path = dest
                self.ui(self.fuel_lbl.config, text=f"fuel: {fname}", fg=OK_GREEN)
                self.ui(self.set_status, "", MUTED)
            except urllib.error.HTTPError as e:
                self.ui(self.set_status, f"Server error {e.code}. Try again later.", FAIL_RED)
            except urllib.error.URLError as e:
                self.ui(self.set_status, f"No connection: {e.reason}", FAIL_RED)
            except Exception as e:
                self.ui(self.set_status, f"Fetch failed: {e}", FAIL_RED)
            finally:
                if part and os.path.exists(part):
                    try: os.remove(part)
                    except OSError: pass
        threading.Thread(target=worker, daemon=True).start()

    # ---------- VESSEL ----------
    def _build_vessel(self):
        body = self.panel_vessel.body
        tk.Button(body, text="look for a vessel", command=self.scan_disks, bg=PANEL, fg=GOLD,
                  relief="flat", padx=10, pady=6, activebackground=PANEL, activeforeground=GOLD).pack(anchor="w")
        self.drive_list = tk.Frame(body, bg=INK)
        self.drive_list.pack(fill="both", expand=True, pady=(12, 0))
        self.selected_drive = tk.IntVar(value=-1)

    def scan_disks(self):
        self.set_status("looking for USB drives...", GOLD)
        self.root.update_idletasks()
        lines, err = run_engine(["scan"])
        for w in self.drive_list.winfo_children():
            w.destroy()
        if err:
            self.set_status(f"scan error: {err}", FAIL_RED)
            return
        skip = root_disk_name()
        self.drives_data = []
        for line in lines:
            if not line.startswith("DISK|"):
                continue
            parts = line.split("|")
            if len(parts) == 5 and os.path.basename(parts[1]) != skip:
                self.drives_data.append({"path": parts[1], "model": parts[2].strip(), "size": parts[3]})
        if not self.drives_data:
            tk.Label(self.drive_list, text="no vessel found - plug one in and look again",
                     bg=INK, fg=MUTED).pack(anchor="w")
        for i, d in enumerate(self.drives_data):
            row = tk.Frame(self.drive_list, bg=EMBER, highlightbackground="#3a2530", highlightthickness=1)
            row.pack(fill="x", pady=4)
            tk.Radiobutton(row, text=f"{d['path']}   {d['model']}   ({d['size']})",
                           variable=self.selected_drive, value=i, bg=EMBER, fg=TEXT,
                           selectcolor=PANEL, activebackground=EMBER, activeforeground=TEXT,
                           font=("Segoe UI", 10), anchor="w", padx=8, pady=10).pack(fill="x")
        if self.drives_data:
            self.selected_drive.set(0)
        self.set_status("", MUTED)

    # ---------- IGNITE ----------
    def _build_ignite(self):
        body = self.panel_ignite.body
        self.summary_lbl = tk.Label(body, text="", bg=INK, fg=TEXT, font=("Consolas", 10),
                                     justify="left", wraplength=460)
        self.summary_lbl.pack(anchor="w", pady=(0, 10))
        self.gauge = FlameGauge(body)
        self.gauge.pack(pady=(0, 10))
        tk.Label(body, text="type the vessel's path to confirm:", bg=INK, fg=MUTED).pack(anchor="w")
        self.confirm_entry = tk.Entry(body, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                                       relief="flat", font=("Consolas", 12))
        self.confirm_entry.pack(fill="x", pady=(4, 12), ipady=6)
        self.burn_btn = tk.Button(body, text="IGNITE", command=self.start_burn, bg=CRIMSON, fg="white",
                                   relief="flat", font=("Segoe UI", 12, "bold"), pady=10,
                                   activebackground=ORANGE, activeforeground="white")
        self.burn_btn.pack(fill="x")

    def _refresh_ignite(self):
        iso = os.path.basename(self.iso_path) if self.iso_path else "(none)"
        if self.drives_data and 0 <= self.selected_drive.get() < len(self.drives_data):
            d = self.drives_data[self.selected_drive.get()]
            drive_txt = f"{d['path']}  {d['model']} ({d['size']})"
        else:
            drive_txt = "(none)"
        self.summary_lbl.config(text=f"fuel    {iso}\nvessel  {drive_txt}\n\neverything on the vessel will be replaced.")
        self.gauge.set(0, "waiting")

    # ---------- burning ----------
    def start_burn(self):
        if not self.iso_path or not os.path.isfile(self.iso_path):
            messagebox.showerror("No fuel", "Choose or fetch a valid ISO first.")
            return
        if not self.drives_data or self.selected_drive.get() < 0:
            messagebox.showerror("No vessel", "Choose a USB drive first.")
            return
        dev = self.drives_data[self.selected_drive.get()]
        if self.confirm_entry.get().strip() != dev["path"]:
            messagebox.showwarning("Not confirmed", f"Type exactly {dev['path']} to confirm.")
            return

        errors = unmount_all(dev["path"])
        if errors:
            messagebox.showerror("Cannot unmount", "Close any window using the drive:\n\n" + "\n".join(errors))
            return

        self.burn_btn.config(state=tk.DISABLED)
        self.next_btn.config(state=tk.DISABLED)
        self.back_btn.config(state=tk.DISABLED)
        self.gauge.set(0, "writing")

        def worker():
            result = None
            try:
                proc = subprocess.Popen([get_engine(), "burn", self.iso_path, dev["path"]],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for line in proc.stdout:
                    line = line.strip()
                    if line.startswith("P:"):
                        p = int(line[2:])
                        self.ui(self.gauge.set, p, "writing")
                    elif line == "VERIFY":
                        self.ui(self.gauge.set, 0, "verifying")
                    elif line.startswith("V:"):
                        p = int(line[2:])
                        self.ui(self.gauge.set, p, "verifying")
                    elif line == "OK":
                        result = (True, "done - every byte was verified.")
                    elif line == "FAIL":
                        result = (False, "verification failed - the drive may be bad. Try again.")
                proc.wait()
                if result is None:
                    result = (False, proc.stderr.read().strip() or "the engine stopped unexpectedly.")
            except Exception as e:
                result = (False, str(e))
            self.ui(self.finish, *result)
        threading.Thread(target=worker, daemon=True).start()

    def finish(self, ok, msg):
        self.burn_btn.config(state=tk.NORMAL)
        self.next_btn.config(state=tk.NORMAL)
        self.back_btn.config(state=tk.NORMAL)
        if ok:
            self.gauge.set(100, "complete")
        self.set_status(msg, OK_GREEN if ok else FAIL_RED)
        (messagebox.showinfo if ok else messagebox.showerror)("Done" if ok else "Error", msg)


if __name__ == "__main__":
    print("PhoenixBurn - run from a terminal: sudo python3 phoenixburn.py")
    PhoenixBurnGUI(tk.Tk()).root.mainloop()
